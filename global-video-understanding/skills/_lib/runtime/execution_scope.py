"""Versioned execution-only actor projection; accepted observations are immutable."""
import copy
import re
from pathlib import Path
from _lib.runtime.case_core import read_json, atomic_json, sha256, now
from _lib.behavior.compile import preview_digest
from _lib.contracts.actors import is_vehicle
from _lib.contracts.v3 import ContractError

POLICY = 'ego_vehicle_scene.v3'
EGO_POLICY = 'ego_vehicle_scene.v4'
BACKGROUND_POLICY = 'ego_vehicle_scene.v2'
LEGACY_POLICY = 'ego_vehicle_scene.v1'
SUPPORTED_POLICIES = (LEGACY_POLICY, BACKGROUND_POLICY, POLICY, EGO_POLICY)
# These published v1 implementations added background exclusion before a policy
# revision was introduced. Preserve their exact semantics, never infer them from
# the desired exclusions. Unknown implementations require an explicit adapter.
LEGACY_IMPLEMENTATIONS = {
    '34285dd92e4a7b1f8bf4dffef0cc13104168bea4f9e349427e83b8ed998d4b03': False,
    '68c296c5310509455de1545ce0149027676095a9b2ce0c46f88540a2e5249065': True,
    'edd2a890d9088ac5f305df221e4b52e1e2092dae41f9c93acb8ba29f19b3c6ef': True,
}
NON_VEHICLES = frozenset({'pedestrian', 'pedestrian_group', 'cyclist', 'bicyclist',
    'bicycle', 'bicycle_rider', 'cyclist_group', 'animal_group', 'animal', 'animals', 'dog', 'cat', 'horse', 'deer'})
SOURCES = ('state/world_context.json', 'state/behavior.json', 'state/actor_registry.json')


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.exists() and read_json(path).get('execution_scope') in SUPPORTED_POLICIES


def references(item):
    ids = set(item.get('actor_ids', [])) | set(item.get('affected_actor_ids', []))
    if item.get('actor_id'): ids.add(item['actor_id'])
    for key in ('identity_binding', 'scope_binding'):
        binding = item.get(key) or {}
        ids.update(binding.get('actor_ids', []))
        ids.update(binding.get('affected_actor_ids', []))
    return ids


def observed_opposing(actor, world):
    """Accept only an actor-local, frame-bound opposing-direction claim."""
    claim = actor.get('direction_evidence')
    points = actor.get('trajectory') or []
    frames = {p.get('frame_id') for p in points if isinstance(p, dict)}
    if (not isinstance(claim, dict) or claim.get('status') != 'observed'
            or claim.get('value') != 'opposite_to_ego'
            or not isinstance(claim.get('reason'), str) or not claim['reason'].strip()
            or not frames or None in frames
            or not isinstance(claim.get('frame_ids'), list)
            or not all(isinstance(frame, str) for frame in claim['frame_ids'])
            or set(claim['frame_ids']) != frames
            or len(claim['frame_ids']) != len(frames)
            or world.get('world', {}).get('road', {}).get('one_way') is True):
        return False
    lanes = {lane.get('lane_id'): lane for lane in
             world.get('world', {}).get('road', {}).get('lanes', [])}
    for point in points:
        lane_id = point.get('lane_id')
        if lane_id is not None:
            lane = lanes.get(lane_id)
            if not lane or lane.get('travel_direction') not in {
                    'backward', 'opposite_to_ego', 'opposite_direction'}:
                return False
    return True


def validate_behavior_scope(case, world, behavior):
    """Reject an unsupported v3 background claim before candidate commit."""
    from _lib.contracts.census_adjudication import validate_behavior
    validate_behavior(case, behavior)
    path = Path(case)/'state/execution_policy.json'
    if path.is_file() and read_json(path).get('execution_scope') in {POLICY, EGO_POLICY}:
        from _lib.contracts import behavior_handoff
        from _lib.behavior.compile import evidence_frames
        project(world, behavior, policy_id=read_json(path)['execution_scope'],
                handoff_frames=evidence_frames(case) if behavior_handoff.enabled(case) else None)


def opposing_preservation_ids(world, behavior, excluded_group_ids=(), *, inherited=None):
    """Recompute the old opposing filter's safe subset from raw sources.

    This is never an omission list supplied by a model or a saved audit. The
    group set must come from the independently verified census scope review.
    Other omission policies do not gain permission through this adapter.
    """
    from _lib.contracts.unused_association import mentions
    groups = set(excluded_group_ids)
    result = set()
    actors = behavior.get('actors', [])
    known = {a['actor_id'] for a in actors}
    for actor in actors:
        aid = actor['actor_id']
        from _lib.contracts.inherited_opposing import compatible
        inherited_match = aid in (inherited or {}) and compatible(actor, inherited[aid])
        if (aid == 'ego' or not inherited_match and (actor.get('simulation_role') != 'background'
                or actor.get('is_static') or not observed_opposing(actor, world))
                or not (is_vehicle(actor) or actor.get('actor_type') == 'vehicle_group' and aid in groups)):
            continue
        dependencies = [behavior.get(k, []) for k in
                        ('events', 'executor_primitives', 'video_fidelity', 'challenge_resolutions')]
        dependencies.extend(other for other in actors if other['actor_id'] != aid)
        if mentions(dependencies, {aid}):
            continue
        result.add(aid)
    limits = [*world.get('questions_to_verify', []), *world.get('ambiguities', []),
              *behavior.get('semantic_limitations', [])]
    # A pure missing lane/motion value can go with the verified background
    # set. A grouped limitation must not pull a retained Actor into that set.
    # Recheck to a fixed point when dependencies connect several backgrounds.
    while True:
        blocked = set()
        for item in limits:
            # "Separated from ego's carriageway" is not an ego dependency.
            # Explicit Actor IDs and typed ego references still are.
            affected = (references(item) | set(item.get('involved_actor_ids', []))
                        | {aid for aid in known - {'ego'} if mentions(item, {aid})})
            domains = set(item.get('impact_domains', []))
            if separable_opposing_limitation(item, result):
                continue
            motion_only = (not domains - {'actor_lane', 'actor_motion', 'speed', 'position'}
                           or item.get('subject_field') == 'actor_motion' and domains == {'executor_primitive'})
            if (not references(item) or affected - result or not motion_only
                    or any(item.get(k) for k in ('world_paths', 'related_world_paths',
                        'affected_event_ids', 'event_ids', 'event_id', 'identity_binding', 'scope_binding'))):
                blocked.update(affected & result)
        if not blocked:
            break
        result -= blocked
    return result


def separable_opposing_limitation(item, opposing_ids):
    """Actor-local unknowns can be split when a reviewed opposing group leaves simulation."""
    domains = set(item.get('impact_domains', []))
    return (bool(set(item.get('actor_ids', [])) & set(opposing_ids))
            and bool(domains)
            and domains <= {'actor_identity', 'actor_lane', 'actor_motion', 'speed', 'position',
                            'appearance', 'lane_count'}
            and not any(item.get(k) for k in ('world_paths', 'related_world_paths',
                'affected_event_ids', 'event_ids', 'event_id', 'identity_binding',
                'scope_binding')))


def append_representatives(w, b, choices, peripheral):
    """Materialize validated execution choices in private World/Behavior copies."""
    representatives = []
    if choices:
        lanes = {l['lane_id']:l for l in w['world']['road'].get('lanes', [])}
        created_representatives = set()
        for row in choices:
            if row['handling'] != 'single_representative':
                continue
            if row['actor_id'] in created_representatives:
                if not peripheral or row['actor_id'] not in peripheral['proxies']:
                    raise ContractError('Duplicate execution representative without shared source proof')
                continue
            lane = lanes.get(row['lane_id'])
            if lane is None or row['actor_id'] in {a['actor_id'] for a in b['actors']}:
                raise ContractError('Execution representative lane or Actor ID changed')
            aid, fid = row['actor_id'], row['observation_frame_id']
            description = 'Execution representative for unresolved observation '+row['issue_id']
            actor = dict(actor_id=aid, actor_type='vehicle', identity_description=description,
                         first_seen_frame_id=fid, simulation_role='participant', is_static=False,
                         color=dict(status='observed', value=peripheral['proxies'][aid]['color'],
                                    frame_id=fid, time_s=row['time_s'],
                                    reason='Observed appearance; physical identity remains unresolved')
                         if peripheral and aid in peripheral['proxies'] else
                         dict(status='unknown', value=None, reason='Representative appearance is not an observed identity'),
                         vehicle_class=dict(status='unknown', value=None, reason='Representative vehicle class is uncertain'),
                         trajectory=[dict(frame_id=fid, time_s=row['time_s'], lane_id=row['lane_id'],
                                          road_id=lane.get('road_id'),
                                          ordinal_from_right=lane.get('ordinal_from_right'),
                                          speed_mps=dict(status='unknown', value=None,
                                                         reason='No measured speed for execution representative'),
                                          detail=fid+' execution lane assumption for '+row['issue_id'])])
            b['actors'].append(actor)
            w['actor_hypotheses'].append(dict(actor_id=aid, actor_type='vehicle',
                first_seen_frame_id=fid, identity_description=description, is_static=False,
                hypothesized_lane=row['lane_id']))
            representatives.append(dict(actor_id=aid, source_issue_id=row['issue_id'],
                                        source_issue_ids=peripheral['proxies'][aid]['issue_ids']
                                            if peripheral and aid in peripheral['proxies'] else [row['issue_id']],
                                        lane_id=row['lane_id'], frame_id=fid))
            created_representatives.add(aid)
    if representatives:
        from _lib.behavior.compile import derive_initial_states
        b = derive_initial_states(b)
    return b, representatives


def project(world, behavior, bindings=None, *, policy_id=POLICY, legacy_background=False, excluded_group_ids=(),
            lane_resolution_enabled=False, execution_dispositions=None, handoff_frames=None,
            question_authorization=None):
    """Explicit associations supplement missing references, never override them."""
    if policy_id not in SUPPORTED_POLICIES:
        raise ContractError('Unsupported execution scope')
    w, b = copy.deepcopy(world), copy.deepcopy(behavior)
    referenced = {aid for event in b.get('events', [])
                  for aid in event.get('involved_actor_ids', [])}
    referenced.update(p.get('actor_id') for p in b.get('executor_primitives', [])
                      if p.get('actor_id'))
    groups = set(excluded_group_ids)
    by_id = {a['actor_id']:a for a in b['actors']}
    from _lib.contracts import unused_association, execution_assumptions, inherited_opposing
    choices = (execution_dispositions or {}).get('choices', [])
    if choices and policy_id != EGO_POLICY:
        raise ContractError('Census execution choices require versioned ego scope')
    if execution_dispositions and execution_dispositions.get('sha256') != preview_digest({
            k:v for k,v in execution_dispositions.items() if k != 'sha256'}):
        raise ContractError('Census execution disposition hash mismatch')
    unused_program = unused_association.validate_projection(world, behavior, handoff_frames, execution_dispositions)
    assumptions = execution_assumptions.validate_projection(world, behavior, handoff_frames, execution_dispositions)
    from _lib.contracts import peripheral_uncertainty
    peripheral = peripheral_uncertainty.validate_projection(world, behavior, handoff_frames,
                                                            execution_dispositions)
    inherited = (assumptions or {}).get(inherited_opposing.KEY, {})
    from _lib.contracts.retained_motion import KEY as RETAINED_MOTION
    retained_motion = (assumptions or {}).get(RETAINED_MOTION, {})
    groups.update(aid for aid,r in inherited.items() if r['registry']['actor_type'] == 'vehicle_group')
    if policy_id in {POLICY, EGO_POLICY}:
        referenced.update(aid for event in b.get('events', []) for aid in references(event))
        referenced.update(aid for primitive in b.get('executor_primitives', [])
                          for aid in references(primitive))
    if any(aid not in by_id or by_id[aid].get('actor_type') != 'vehicle_group'
           or aid in referenced or aid not in inherited and (
               by_id[aid].get('simulation_role') != 'background'
               or not observed_opposing(by_id[aid], w)) for aid in groups):
        raise ContractError('Group scope requires reviewed opposing background evidence without interaction')
    # v2 excluded every unreferenced background Actor. v3 retains vehicles
    # without opposing-direction proof. An explicit opposing claim must be
    # complete before it can authorize exclusion.
    if policy_id in {POLICY, EGO_POLICY}:
        for actor in b['actors']:
            if (actor['actor_id'] != 'ego' and is_vehicle(actor)
                    and not actor.get('is_static')
                    and actor.get('simulation_role') == 'background'
                    and actor['actor_id'] not in inherited
                    and (actor.get('direction_evidence') or {}).get('value') == 'opposite_to_ego'
                    and not observed_opposing(actor, w)):
                raise ContractError(
                    'Moving background vehicle has invalid frame-bound opposing-direction evidence',
                    rule_id='scope.background_direction_unproven',
                    actor_id=actor['actor_id'])
    background = {a['actor_id'] for a in b['actors']
                  if a['actor_id'] != 'ego'
                  and a.get('simulation_role') == 'background'
                  and a['actor_id'] not in referenced
                  and (policy_id not in {POLICY, EGO_POLICY} or
                       (is_vehicle(a) and not a.get('is_static')
                        and observed_opposing(a, w)))}
    if policy_id == LEGACY_POLICY and not legacy_background:
        background = set()
    opposing_scope = None
    if unused_program or assumptions:
        opposing_scope = (world, behavior, groups, assumptions) if inherited else (world, behavior, groups)
        safe_opposing = opposing_preservation_ids(world, behavior, groups, inherited=inherited)
        background.update(inherited)
        background &= safe_opposing
        if groups - safe_opposing:
            raise ContractError('Opposing group exclusion conflicts with a retained dependency')
    retained = {aid for row in choices if row['handling'] == 'assumed_existing'
                for aid in row['candidate_actor_ids']}
    omit_candidates = {aid for row in choices
                       if row['handling'] in {'observation_only_unknown', 'single_representative'}
                       for aid in row['candidate_actor_ids']} - retained
    explicit_omits = set()
    for aid in omit_candidates:
        actor = by_id.get(aid)
        if actor is None or aid in referenced:
            raise ContractError('Execution omission conflicts with a referenced Actor')
        if all(point.get('lane_id') is None for point in actor.get('trajectory', [])):
            explicit_omits.add(aid)
    if explicit_omits:
        for collection in ('questions_to_verify','ambiguities'):
            if any(set(q.get('actor_ids', [])) & explicit_omits and q.get('world_paths')
                   for q in w.get(collection, [])):
                raise ContractError('Execution omission conflicts with a road dependency')
    transient = set()
    if handoff_frames is not None:
        from _lib.contracts.behavior_handoff import transient_actors
        # Already verified inherited scope must not request a second model
        # relevance claim. All retained participants still pass the old gate.
        transient_input = dict(behavior, actors=[a for a in behavior['actors']
                               if a['actor_id'] not in inherited]) if inherited else behavior
        transient = transient_actors(world, transient_input, handoff_frames, choices,
                                     retained=retained_motion)
        if unused_program or assumptions:
            # The source-bound preservation contract outranks optional transient
            # omission. Validate claims above, but do not first delete a known
            # same-direction vehicle and then fail preserve() below.
            transient &= safe_opposing
    reviewed_omits = set()
    scope_proof = (execution_dispositions or {}).get('downstream_scope_proof')
    from _lib.contracts.downstream_scope import HANDLING
    scope_choices = [r for r in choices if r['handling'] == HANDLING]
    if scope_proof or scope_choices:
        if handoff_frames is None or policy_id != EGO_POLICY or not scope_proof:
            raise ContractError('Downstream scope exclusions require a complete versioned proof')
        from _lib.contracts.downstream_scope import validate
        reviewed_omits = validate(world, behavior, handoff_frames, scope_proof)
        if ({r['issue_id'] for r in scope_choices} != set(scope_proof['pending'])
                or any(r.get('scope_proof_sha256') != scope_proof['sha256']
                       or r.get('candidate_actor_ids') != scope_proof['pending'][r['issue_id']]['candidate_actor_ids']
                       for r in scope_choices)):
            raise ContractError('Downstream scope choices differ from their proof')
    core_omits = set()
    core_receipt = (execution_dispositions or {}).get('core_scene_receipt')
    from _lib.contracts.core_scene_scope import HANDLING as CORE_HANDLING, validate as validate_core
    core_choices = [r for r in choices if r['handling'] == CORE_HANDLING]
    if core_receipt or core_choices:
        if handoff_frames is None or policy_id != EGO_POLICY or not core_receipt or scope_proof:
            raise ContractError('Core scene omissions require a separate bound execution policy')
        core_omits = validate_core(world, behavior, handoff_frames, core_receipt)
        if ({r['issue_id'] for r in core_choices} != set(core_receipt['covered_issue_ids'])
                or any(r.get('core_scene_sha256') != core_receipt['sha256']
                       or r.get('identity_resolution') != 'unresolved'
                       or r.get('candidate_actor_ids') != core_receipt['pending'][r['issue_id']]['candidate_actor_ids']
                       for r in core_choices)):
            raise ContractError('Core scene choices differ from their receipt')
    excluded = ({a['actor_id'] for a in b['actors']
                 if a['actor_id'] != 'ego' and a['actor_type'] in NON_VEHICLES}
                | background | groups | explicit_omits | transient | reviewed_omits | core_omits)
    retained_unresolved = {r['actor_id'] for r in choices
                           if r['handling'] == 'retained_unresolved'}
    if excluded & retained_unresolved:
        raise ContractError('Retained unresolved physical object was omitted from execution',
                            rule_id='identity.census.retained_actor_omitted')
    if peripheral:
        registered = {a['actor_id'] for a in behavior['actors']
                      if a['actor_type'] == 'vehicle' and a['actor_id'] != 'ego'}
        if excluded & registered - set(inherited):
            peripheral_uncertainty.fail('Peripheral uncertainty cannot remove a registered vehicle')
    if core_receipt and excluded & set(core_receipt['retained_actor_ids']):
        raise ContractError('Existing scope rules would omit a required core Actor')
    if excluded & set(retained_motion):
        raise ContractError('Existing scope rules would omit a retained unseen-motion Actor')
    associations = bindings or {}
    known = {a['actor_id'] for a in b['actors']}
    issues = []
    from _lib.contracts.census_relevance import observation_issue_disposed, EXECUTION as RELEVANCE_EXECUTION
    relevance_scope = (execution_dispositions or {}).get('revision') == RELEVANCE_EXECUTION
    def omit(item, key):
        iid = item[key]
        ids = references(item) | set(associations.get(iid, []))
        lane_assumptions = (peripheral or {}).get('lane_assumptions', {})
        if (len(ids) == 1 and ids <= set(lane_assumptions)
                and set(item.get('impact_domains', [])) == {'actor_lane'}
                and not any(item.get(k) for k in ('world_paths', 'related_world_paths',
                    'affected_event_ids', 'event_ids', 'event_id', 'scope_binding'))):
            issues.append(dict(issue_id=iid, actor_ids=sorted(ids),
                decision='bounded_lane_assumption',
                lane_id=lane_assumptions[next(iter(ids))]['lane_id']))
            return True
        event_ids = set(item.get('affected_event_ids', [])) | set((item.get('scope_binding') or {}).get('affected_event_ids', []))
        for event in behavior.get('events', []):
            if event['event_id'] in event_ids: ids.update(event.get('involved_actor_ids', []))
        if core_receipt and not ids:
            from _lib.contracts.census_relevance import observation_issue_only
            if (observation_issue_only(item)
                    and set(item['census_issue_ids']) <= set(core_receipt['covered_issue_ids'])):
                issues.append(dict(issue_id=iid, actor_ids=[], decision=CORE_HANDLING,
                                   census_issue_ids=list(item['census_issue_ids'])))
                return True
        if not associations.get(iid) and observation_issue_disposed(item, execution_dispositions):
            issues.append(dict(issue_id=iid, actor_ids=sorted(ids), decision='census_observation_only',
                               census_issue_ids=list(item['census_issue_ids'])))
            return True
        # Actor scope never authorizes dropping a road/facility dependency.
        scene_dependency = bool(item.get('world_paths') or item.get('related_world_paths'))
        if ids and ids <= excluded and not scene_dependency:
            issues.append({'issue_id': iid, 'actor_ids': sorted(ids), 'decision': 'observation_only'})
            return True
        return False
    for collection, key in (('questions_to_verify','question_id'), ('ambiguities','ambiguity_id')):
        w[collection] = [i for i in w.get(collection, []) if not omit(i, key)]
    disposed = bool(choices) and all(r['handling'] != 'needs_evidence' for r in choices)
    candidate_ids = {aid for r in choices for aid in r['candidate_actor_ids']}
    census_issue_ids = {row['issue_id'] for row in choices}
    def resolved_census_limitation(item):
        ids = set(item.get('actor_ids', []))
        domains = set(item.get('impact_domains', []))
        if not disposed or relevance_scope:
            return False
        if (ids and bool(ids & excluded) and bool(ids-excluded) and ids <= candidate_ids
                and domains == {'actor_identity','actor_lane'}
                and not any(set(q.get('actor_ids', [])) & (ids-excluded)
                            and q.get('resolution') == 'unresolved'
                            and not set(q.get('actor_ids', [])) <= excluded
                            and not set(q.get('census_issue_ids', []))
                            for collection in ('questions_to_verify','ambiguities')
                            for q in w.get(collection, []))):
            return True
        # Legacy Behavior can summarize every pending speck in one unbound
        # limitation. Match the exact signed issue set; arbitrary identity
        # uncertainty must remain an execution blocker.
        description = item.get('description', '')
        mentioned = set(re.findall(r'batch_[0-9]+:frame_[0-9]+:[A-Za-z0-9_-]+', description))
        return (not ids and not item.get('actor_id') and domains == {'actor_identity'}
                and item.get('execution_effect') == 'bounded_uncertainty'
                and bool(census_issue_ids) and mentioned == census_issue_ids
                and all(row['handling'] == 'observation_only_unknown' for row in choices)
                and not re.search(r'\bactor_[0-9]+\b', description, re.IGNORECASE))
    retained_limitations = []
    scoped_observations = set()
    if handoff_frames is not None:
        from _lib.behavior.compile import observation_scope_exclusions
        limitations = {i['limitation_id']: i for i in b['semantic_limitations']}
        scoped_observations = {r['limitation_id'] for r in observation_scope_exclusions(w, b)
                               if not references(limitations[r['limitation_id']])
                               and not limitations[r['limitation_id']].get('affected_event_ids')
                               and not limitations[r['limitation_id']].get('related_world_paths')}
    for item in b.get('semantic_limitations', []):
        if omit(item, 'limitation_id'):
            continue
        if handoff_frames is not None:
            from _lib.contracts.behavior_handoff import census_limitation_disposed
            census_omitted = (observation_issue_disposed(item, execution_dispositions) if relevance_scope
                             else census_limitation_disposed(item, choices))
            if census_omitted or item['limitation_id'] in scoped_observations:
                issues.append(dict(issue_id=item['limitation_id'], actor_ids=[],
                    decision='census_observation_only' if census_omitted else 'isolated_opposing_observations',
                    census_issue_ids=item.get('census_issue_ids', [])))
                continue
        if resolved_census_limitation(item):
            issues.append(dict(issue_id=item['limitation_id'], actor_ids=item.get('actor_ids', []),
                               decision='census_execution_disposition',
                               census_issue_ids=sorted(census_issue_ids) if not item.get('actor_ids') else []))
            continue
        retained_limitations.append(item)
    b['semantic_limitations'] = retained_limitations
    removed_issues = {i['issue_id'] for i in issues}
    b['challenge_resolutions'] = [i for i in b.get('challenge_resolutions', [])
                                if i.get('challenge_id') not in removed_issues]
    events = [e for e in b.get('events', []) if set(e.get('involved_actor_ids', [])) & excluded]
    event_ids = {e['event_id'] for e in events}
    b['events'] = [e for e in b.get('events', []) if e['event_id'] not in event_ids]
    b['executor_primitives'] = [p for p in b.get('executor_primitives', [])
        if p['actor_id'] not in excluded and p.get('event_id') not in event_ids
        and not references(p) & excluded]
    # An omitted interaction may still establish vehicle braking/stopping.
    # Preserve that motion requirement when this executor cannot encode it separately.
    motion_events = {'yield', 'yielding', 'stop', 'stopping', 'brake', 'braking',
                     'deceleration', 'decelerate', 'acceleration', 'accelerate', 'speed_change'}
    for event in events:
        retained = set(event.get('involved_actor_ids', [])) - excluded
        if retained and event.get('event_type') in motion_events:
            b['semantic_limitations'].append(dict(limitation_id='execution_motion_'+event['event_id'],
                description='Retained vehicle motion from '+event['event_id']+' requires an independent supported action; omitted interaction must not become constant-speed motion.',
                actor_ids=sorted(retained), impact_domains=['event','path'], execution_effect='blocks_execution'))
    b['actors'] = [a for a in b['actors'] if a['actor_id'] not in excluded]
    # Source preservation is checked before any execution-only representative
    # is appended; the representative has no source Actor to preserve.
    unused_association.preserve(unused_program, b, opposing_scope=opposing_scope)
    b = execution_assumptions.apply(assumptions, b, opposing_scope=opposing_scope)
    if peripheral:
        lanes = {lane['lane_id']:lane for lane in w['world']['road'].get('lanes', [])}
        for actor in b['actors']:
            record = peripheral.get('lane_assumptions', {}).get(actor['actor_id'])
            if record:
                point = actor['trajectory'][0]
                if len(actor['trajectory']) != 1 or point.get('frame_id') != record['frame_id'] or point.get('lane_id') is not None:
                    peripheral_uncertainty.fail('Registered lane assumption no longer matches the source Actor')
                lane = lanes[record['lane_id']]
                point.update(lane_id=record['lane_id'], road_id=lane['road_id'],
                             ordinal_from_right=lane['ordinal_from_right'])
    # The same limitation may cover a retained Actor and a separately reviewed
    # opposing group. Keep the retained Actor's uncertainty without leaving a
    # reference to a group absent from the execution scene.
    for collection in ('questions_to_verify', 'ambiguities'):
        w[collection] = [item for item in w.get(collection, [])
                         if not _trim_opposing_limitation(item, safe_opposing if opposing_scope else set())]
    b['semantic_limitations'] = [item for item in b.get('semantic_limitations', [])
                                 if not _trim_opposing_limitation(item, safe_opposing if opposing_scope else set())]
    if explicit_omits or reviewed_omits or core_omits:
        w['actor_hypotheses'] = [a for a in w.get('actor_hypotheses', [])
                                 if a['actor_id'] not in explicit_omits | reviewed_omits | core_omits]
    b, representatives = append_representatives(w, b, choices, peripheral)
    if lane_resolution_enabled:
        from _lib.contracts.lane_resolution import project_world
        w = project_world(w, b, authorization=question_authorization)
    for interval in w.get('critical_intervals', []):
        for key in ('question_ids','ambiguity_ids'):
            if key in interval: interval[key] = [i for i in interval[key] if i not in removed_issues]
    # Point bindings are another index of questions, just like intervals. Only
    # remove bindings for questions actually excluded by this projection;
    # unrelated malformed/dangling input must still fail World validation.
    removed_questions = ({q['question_id'] for q in world.get('questions_to_verify', [])}
                         - {q['question_id'] for q in w.get('questions_to_verify', [])})
    point_review = w.get('world', {}).get('metadata', {}).get('single_frame_review')
    if isinstance(point_review, dict) and isinstance(point_review.get('questions'), dict):
        point_review['questions'] = {qid: row for qid, row in point_review['questions'].items()
                                     if qid not in removed_questions}
    valid_ids = {i[key] for source, collection, key in (
        (world,'questions_to_verify','question_id'),(world,'ambiguities','ambiguity_id'),
        (behavior,'semantic_limitations','limitation_id')) for i in source.get(collection, [])}
    if set(associations) - valid_ids or any(not ids or not set(ids) <= known for ids in associations.values()):
        raise ContractError('Execution scope association refers to unknown issue/actor')
    audit = dict(policy_id=policy_id, excluded_actors=[dict(actor_id=a['actor_id'], actor_type=a['actor_type'],
        reason=('Inherited verified barrier isolation; identity unchanged, source sightings retained'
                if a['actor_id'] in inherited else
                'Omitted by explicit core-scene approximation; identity unresolved and visual independence not proven'
                if a['actor_id'] in core_omits else
                'Independent downstream execution review excludes this Actor; identity remains unresolved'
                if a['actor_id'] in reviewed_omits else
                'Frame-bound transient observation has no identity or interaction dependency'
                if a['actor_id'] in transient else
                'Unknown-lane Actor omitted by signed census execution disposition'
                if a['actor_id'] in explicit_omits else
                'Reviewed opposing vehicle group is physically separated and has no interaction dependency'
                if a['actor_id'] in groups else
                ('Frame-bound opposing traffic has no event/action dependency'
                 if policy_id in {POLICY, EGO_POLICY} else
                 'Background observation has no event/action dependency')
                if a['actor_id'] in background else
                'Non-vehicle observation excluded from ego vehicle simulation'))
        for a in behavior['actors'] if a['actor_id'] in excluded], excluded_issues=issues,
        excluded_event_ids=sorted(event_ids), associations=copy.deepcopy(associations))
    if choices or assumptions:
        audit['execution_dispositions'] = copy.deepcopy(execution_dispositions)
        audit['representative_actors'] = representatives
    if lane_resolution_enabled:
        from _lib.contracts.execution_gaps import REVISION
        audit['lane_resolution_revision'] = REVISION
    if handoff_frames is not None:
        audit['behavior_handoff'] = 'behavior-handoff.v1'
        audit['handoff_frames'] = [dict(frame_id=f['frame_id'], time_s=f['time_s']) for f in handoff_frames]
    from _lib.contracts.bounded_execution import KEY
    if (assumptions or {}).get(KEY):
        w['world'].setdefault('metadata', {})[KEY] = copy.deepcopy(assumptions[KEY])
    return w, b, audit


def _trim_opposing_limitation(item, opposing_ids):
    if not separable_opposing_limitation(item, opposing_ids):
        return False
    item['actor_ids'] = [aid for aid in item['actor_ids'] if aid not in opposing_ids]
    return not item['actor_ids']


def certificate_background(audit):
    if audit.get('policy_id') in (BACKGROUND_POLICY, POLICY, EGO_POLICY):
        return True
    if audit.get('policy_id') != LEGACY_POLICY:
        raise ContractError('Unsupported execution scope certificate policy')
    implementation = audit.get('implementation_sha256')
    # New certificates explicitly retain v1 semantics; old ones are identified
    # by their recorded implementation, not by a mutable current file hash.
    if audit.get('scope_semantics') == LEGACY_POLICY:
        return False
    if implementation not in LEGACY_IMPLEMENTATIONS:
        raise ContractError('Unsupported historical execution scope implementation: ' + str(implementation))
    return LEGACY_IMPLEMENTATIONS[implementation]


def _case_for_census_certificate(path):
    certificate = Path(path).resolve()
    state = next((parent for parent in certificate.parents if parent.name == 'state'), None)
    if state is None:
        raise ContractError('Invalid census scope certificate path')
    case = state.parent
    from _lib.runtime.fact_versions import census_path
    if certificate != census_path(case).resolve():
        raise ContractError('Invalid census scope certificate path')
    return case


def validate_certificate(world, behavior, audit):
    if audit.get('sha256') != preview_digest({k:v for k,v in audit.items() if k!='sha256'}):
        raise ContractError('Execution scope certificate hash mismatch')
    from _lib.contracts import census_scope
    from _lib.contracts.behavior_census_scope import excluded_groups
    link = audit.get('census_scope_binding')
    groups = set()
    dispositions = None
    if link:
        case = _case_for_census_certificate(link['certificate_path'])
        checked = census_scope.verified_review(case, behavior)
        if not checked or checked['sha256'] != link.get('review_sha256'):
            raise ContractError('Execution group scope review binding changed')
        groups = excluded_groups(case, checked)
        dispositions = checked.get('execution_dispositions')
    authorization = None
    effective = audit.get('effective_facts')
    if effective:
        from _lib.behavior.input_binding import closure_request
        case = Path(effective['manifest_path']).parents[3]
        request = closure_request(case)
        if (request is None or request['request_sha256'] != effective['behavior_request_sha256']
                or preview_digest(request.get('question_authorization'))
                   != effective['authorization_sha256']):
            raise ContractError('Versioned execution authorization changed', rule_id='stale_version')
        authorization = request.get('question_authorization')
    projected = project(world, behavior, audit.get('associations'),
                        policy_id=audit.get('policy_id'), legacy_background=certificate_background(audit),
                        excluded_group_ids=groups,
                        lane_resolution_enabled=audit.get('lane_resolution_revision') == 'execution-gap-recovery.v1',
                        question_authorization=authorization,
                        execution_dispositions=dispositions,
                        handoff_frames=audit.get('handoff_frames') if audit.get('behavior_handoff') == 'behavior-handoff.v1' else None)
    for key, value in projected[2].items():
        if audit.get(key) != value:
            raise ContractError('Execution scope exclusions changed')
    return projected


def frozen_legacy_audit(case, hashes, policy_hash):
    """Reuse an input-bound historical certificate, including its review path."""
    matches = []
    for path in sorted((case/'diagnostics/execution_scope').glob('*/scope.json')):
        audit = read_json(path)
        if audit.get('input_hashes') == hashes and audit.get('policy_sha256') == policy_hash:
            certificate_background(audit)
            if audit.get('sha256') != preview_digest({k:v for k,v in audit.items() if k!='sha256'}):
                raise ContractError('Execution scope certificate hash mismatch')
            matches.append((path, audit))
    if len({certificate_background(a) for _, a in matches}) > 1:
        raise ContractError('Conflicting historical execution scope semantics')
    matches.sort(key=lambda item: (item[0].parent/'reviewed.json').exists(), reverse=True)
    return matches[0][1] if matches else None


def inputs(case, *, persist=True):
    case = Path(case)
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    w = copy.deepcopy(view.world) if view else read_json(case/SOURCES[0])
    b = read_json(case/SOURCES[1])
    from _lib.contracts import census_scope
    from _lib.contracts.vehicle_census import require_resolved
    policy_path = case/'state/execution_policy.json'
    if policy_path.is_file() and read_json(policy_path).get('world_census_adjudication') in {'world-census-adjudication.v4', 'world-census-adjudication.v5', 'world-census-adjudication.v6'}:
        require_resolved(case)
    scope_review = census_scope.verified_review(case, b)
    if not enabled(case): return w, b, None
    hashes = {name: sha256(path) for name, path in (
        (SOURCES[0], view.world_path if view else case/SOURCES[0]),
        (SOURCES[1], case/SOURCES[1]),
        (SOURCES[2], view.registry_path if view else case/SOURCES[2]))}
    binding_path = case/'state/execution_scope_bindings.json'
    bindings = {}
    if binding_path.exists():
        record = read_json(binding_path)
        if record['input_hashes'] != hashes:
            raise ContractError('Execution scope associations are stale; re-review required')
        bindings = record['associations']
    policy_id = read_json(case/'state/execution_policy.json')['execution_scope']
    policy_hash = sha256(case/'state/execution_policy.json')
    frozen = (frozen_legacy_audit(case, hashes, policy_hash)
              if view is None and policy_id in (LEGACY_POLICY, BACKGROUND_POLICY) else None)
    if frozen is not None:
        if frozen.get('associations') != bindings:
            raise ContractError('Execution scope associations differ from frozen certificate')
        ew, eb, _ = validate_certificate(w, b, frozen)
        audit = copy.deepcopy(frozen)
    else:
        from _lib.contracts.behavior_census_scope import excluded_groups
        from _lib.contracts.execution_gaps import enabled as gap_recovery
        from _lib.contracts import behavior_handoff
        from _lib.behavior.compile import evidence_frames
        from _lib.behavior import input_binding
        binding = input_binding.closure_request(case)
        validation = input_binding.accepted_validation(case, view.version_id) if view else None
        if view and (binding is None or binding.get('fact_version_id') != view.version_id):
            raise ContractError('stale_version: Execution scope lacks current Behavior authorization',
                                rule_id='stale_version')
        authorization = binding.get('question_authorization') if binding else None
        ew, eb, audit = project(w, b, bindings, policy_id=policy_id,
                               handoff_frames=evidence_frames(case) if behavior_handoff.enabled(case) else None,
                               excluded_group_ids=excluded_groups(case, scope_review),
                               lane_resolution_enabled=gap_recovery(case),
                               question_authorization=authorization,
                               execution_dispositions=(scope_review or {}).get('execution_dispositions'))
        audit['scope_semantics'] = policy_id
        if view:
            audit['effective_facts'] = dict(version_id=view.version_id,
                manifest_path=str(view.directory/'manifest.json'),
                manifest_sha256=sha256(view.directory/'manifest.json'),
                world_sha256=hashes[SOURCES[0]], registry_sha256=hashes[SOURCES[2]],
                behavior_sha256=hashes[SOURCES[1]],
                behavior_request_sha256=binding['request_sha256'],
                authorization_sha256=preview_digest(authorization),
                behavior_validation_sha256=validation['validation_sha256'])
    if scope_review:
        from _lib.contracts.census_adjudication import ACCEPTED
        from _lib.runtime.fact_versions import census_path
        census_scope.bind_audit(case, b, audit)
        hashes.update({ACCEPTED:sha256(census_path(case)), census_scope.REVIEW:sha256(case/census_scope.REVIEW)})
    from _lib.contracts.fidelity import enabled as fidelity_enabled, execution_copy, digest
    if fidelity_enabled(case):
        ew, eb = execution_copy(ew, eb, read_json(case/'state/probe.json')['probe']['duration_s'])
        if eb.get('video_fidelity'):
            ids={a['actor_id'] for a in eb['actors']}
            eb['video_fidelity']['observations']=[o for o in eb['video_fidelity']['observations'] if o['actor_id'] in ids]
        projected_digest = digest({'world':ew,'behavior':eb})
        if frozen is not None and audit.get('video_fidelity_sha256') != projected_digest:
            raise ContractError('Historical execution fidelity projection changed')
        audit['video_fidelity_sha256']=projected_digest
        if frozen is None:
            audit['fidelity_implementation_sha256']=sha256(Path(__file__).parents[1]/'contracts/fidelity.py')
    if view:
        audit['effective_facts']['projection_world_sha256'] = preview_digest(ew)
        audit['effective_facts']['projection_behavior_sha256'] = preview_digest(eb)
    audit['input_hashes'] = hashes
    audit['policy_sha256'] = sha256(case/'state/execution_policy.json')
    if frozen is None:
        audit['implementation_sha256'] = sha256(__file__)
    audit.pop('sha256', None)
    audit['sha256'] = preview_digest(audit)
    root = case/'diagnostics/execution_scope'/audit['sha256']
    for name, data in (('world.json',ew), ('behavior.json',eb), ('scope.json',audit)):
        if (root/name).exists() and view and read_json(root/name) != data:
            raise ContractError('Versioned execution projection changed: ' + name,
                                rule_id='stale_version')
        if persist and not (root/name).exists(): atomic_json(root/name,data)
    # A preview review can refine the execution view, never its source facts.
    reviewed = root/'reviewed.json'
    if reviewed.exists():
        value=read_json(reviewed)
        if value.get('scope_sha256') != audit['sha256']: raise ContractError('Stale execution review')
        ew, eb = value['world'], value['behavior']
    if view:
        current_facts(case, view.version_id)
    return ew, eb, audit


def registry(case, behavior):
    source=read_json(Path(case)/SOURCES[2])
    if not enabled(case): return source
    ids = {a['actor_id'] for a in behavior['actors']}
    result = [a for a in source if a['actor_id'] in ids]
    from _lib.contracts.census_scope import verified_review
    checked = verified_review(case, read_json(Path(case)/SOURCES[1]))
    authorized = {row['actor_id'] for row in
                  ((checked or {}).get('execution_dispositions') or {}).get('choices', [])
                  if row['handling'] == 'single_representative'}
    for actor in behavior['actors']:
        if actor['actor_id'] in authorized and actor['actor_id'] not in {
                a['actor_id'] for a in result}:
            result.append(dict(actor_id=actor['actor_id'], actor_type='vehicle',
                first_seen_frame_id=actor['first_seen_frame_id'],
                identity_description=actor['identity_description'], is_static=False,
                hypothesized_lane=actor['trajectory'][0]['lane_id']))
    return result


def observation_source(case):
    """Return validation-ready source snapshots without mutating checkpoints."""
    case = Path(case)
    source_world, source_behavior = (read_json(case/name) for name in SOURCES[:2])
    from _lib.behavior.compile import (restore_registry_actor_evidence,
                                       materialize_inherited_unknown_reasons,
                                       normalize_optional_color_claims)
    source_world, normalizations = restore_registry_actor_evidence(
        source_world, read_json(case/SOURCES[2]), collection='actor_hypotheses')
    source_behavior, color_repairs = normalize_optional_color_claims(source_behavior)
    normalizations.extend({
        'action': item['action'], 'rule_id': item['rule_id'],
        'actor_id': item['actor_id'], 'field_path': item['field_path'],
        'source_claim_sha256': preview_digest(item['source_claim']),
        'source_status': item['source_claim'].get('status'),
        'source_value': item['source_claim'].get('value'),
        'admitted_status': 'unknown', 'execution_effect': 'non_blocking',
    } for item in color_repairs)
    source_behavior, inherited = materialize_inherited_unknown_reasons(source_behavior)
    normalizations.extend({
        'action': 'materialize_inherited_unknown_reason',
        **binding,
    } for binding in inherited)
    return {SOURCES[0]: source_world, SOURCES[1]: source_behavior}, normalizations


def annotate(case, analysis, status, *, persist=True):
    _, _, audit = inputs(case, persist=persist)
    if not audit:
        from _lib.runtime.execution_plan import finalize
        finalize(case,analysis,persist=persist)
        return status
    q = analysis['quality_report']
    from _lib.contracts.fidelity import enabled as fidelity_enabled
    if fidelity_enabled(case) and 'video_fidelity' not in q:
        from _lib.behavior.compile import evidence_frames
        _, eb, _ = inputs(case,persist=persist)
        q['video_fidelity']={'revision':'video_fidelity.v1','facts':eb['video_fidelity'],
            'frames':evidence_frames(case),'termination':'not_guaranteed_by_target_schema',
            'target_semantics_verification':'source_contract_prediction_only'}
    q['execution_actor_scope'] = audit
    q['observation_source'], normalizations = observation_source(case)
    if normalizations:
        q['observation_source_normalizations'] = normalizations
    if audit['excluded_actors'] or audit.get('excluded_observations') or audit.get('execution_dispositions'):
        for key in ('required_degradations','applied_degradations','allowed_degradations'):
            q[key] = sorted(set(q.get(key, [])) | {audit['policy_id']})
        if status == 'done': status = 'done_degraded'
    from _lib.runtime.execution_plan import finalize
    finalize(case,analysis,persist=persist)
    return status


def activate(case, policy, bindings_path=None):
    case=Path(case)
    if policy not in SUPPORTED_POLICIES: raise ContractError('Unsupported execution scope')
    state=read_json(case/'state/progress.json')
    if state.get('status') in {'done','done_degraded'} or state.get('phase') == 'done':
        raise ContractError('Completed Case execution scope is immutable')
    if any(s.get('status') in {'submitted','pending','running'} for s in state.get('sessions', [])):
        raise ContractError('Reconcile active model tasks before changing execution scope')
    path=case/'state/execution_policy.json'
    if not path.exists(): raise ContractError('Execution scope requires standard v3')
    old=read_json(path); record=None
    if old.get('behavior_census_scope') == 'behavior-census-scope.v2' and policy != EGO_POLICY:
        raise ContractError('Behavior census scope v2 requires ego execution scope v4')
    if bindings_path:
        record=read_json(Path(bindings_path))
        expected={name:sha256(case/name) for name in SOURCES}
        if record.get('input_hashes') != expected: raise ContractError('Execution scope association input hash mismatch')
        project(read_json(case/SOURCES[0]),read_json(case/SOURCES[1]),record['associations'], policy_id=policy)
    binding_target=case/'state/execution_scope_bindings.json'
    changed=old.get('execution_scope') != policy or (record is not None and
        (not binding_target.exists() or read_json(binding_target)!=record))
    if not changed: return
    if record: atomic_json(binding_target,record)
    atomic_json(path,{**old,'execution_scope':policy})
    diagnostics=state.setdefault('diagnostics',{})
    diagnostics.setdefault('execution_scope_changes',[]).append(dict(at=now(),before=old,
        after=read_json(path),bindings=record))
    for key in ('preview_review','preview_separation','execution_localization','execution_verification'):
        if key in diagnostics:
            diagnostics.setdefault('superseded_execution_reviews',[]).append({key:diagnostics.pop(key)})
    # Archive only derived execution certificates; retain sessions and budget.
    for name in ('diagnostics/preview_relations.json','diagnostics/intersection_review.json',
                 'state/preview_review.request.json','state/preview_review.candidate.json',
                 'diagnostics/preview_review.prepared.json'):
        source=case/name
        if source.exists():
            target=case/'diagnostics/superseded_execution_scope'/sha256(source)/source.name
            target.parent.mkdir(parents=True,exist_ok=True)
            source.replace(target)
    state.update(phase='aggregating',substage='behavior',status='needs_review',
        generation_status='not_generated',artifacts={},error=None)
    atomic_json(case/'state/progress.json',state)


def view_paths(case):
    """Stable execution files for scoped visual review and closure journals."""
    w,b,audit=inputs(case)
    if not audit: return Path(case)/SOURCES[0], Path(case)/SOURCES[1]
    root=Path(case)/'diagnostics/execution_scope'/audit['sha256']
    for name,data in (('active_world.json',w),('active_behavior.json',b)):
        path=root/name
        if not path.exists() or read_json(path)!=data: atomic_json(path,data)
    return root/'active_world.json',root/'active_behavior.json'


def save_review(case, world, behavior):
    _,_,audit=inputs(case)
    root=Path(case)/'diagnostics/execution_scope'/audit['sha256']
    atomic_json(root/'reviewed.json',dict(scope_sha256=audit['sha256'],world=world,behavior=behavior))


def review_straight(runner, case):
    """Use the existing visual contract on the execution copy only."""
    from _lib.behavior.repair import preview_review_prompt, materialize_preview
    from _lib.behavior.compile import validate_compiled_behavior, evidence_frames
    w,b,audit=inputs(case)
    root=Path(case)/'diagnostics/execution_scope'/audit['sha256']
    candidate=root/'preview_candidate.json'
    base=root/'preview_base.json'
    if not base.exists(): atomic_json(base,b)
    prompt=preview_review_prompt(case,candidate,base)
    state=runner.progress(case)
    state['substage']='preview_review'
    state.setdefault('diagnostics',{})['preview_review']=dict(status='pending',task_stage='preview_review',
        base_path=str(base),candidate_path=str(candidate),scope_sha256=audit['sha256'],max_attempts=3)
    runner.save_progress(case,state)
    def adopt(_session=None):
        world,data,certificate=materialize_preview(case,read_json(candidate))
        validate_compiled_behavior(data,world,registry(case,data),evidence_frames(case),execution=False)
        save_review(case,world,data)
        atomic_json(Path(case)/'diagnostics/preview_relations.json',certificate)
        state=runner.progress(case)
        state['diagnostics']['preview_review'].update(status='completed',finished_at=now())
        runner.save_progress(case,state)
    runner.model_stage(case,'preview_review','global-video-understanding',prompt,adopt,
        precheck=adopt if candidate.exists() else None,candidate_path=candidate,max_attempts=3)


def validate_analysis_scope(analysis):
    q=analysis.get('quality_report',{})
    audit=q.get('execution_actor_scope')
    if audit is None: return set()
    source=q.get('observation_source',{})
    if audit.get('policy_id') not in SUPPORTED_POLICIES or set(source) != set(SOURCES[:2]):
        raise ContractError('Invalid execution scope provenance')
    effective = audit.get('effective_facts')
    if effective:
        manifest_path = Path(effective['manifest_path'])
        if (manifest_path.name != 'manifest.json' or
                manifest_path.parent.name != effective['version_id'] or
                manifest_path.parent.parent.name != 'fact_versions' or
                manifest_path.parent.parent.parent.name != 'state'):
            raise ContractError('Invalid effective fact manifest provenance')
        case = manifest_path.parents[3]
        from _lib.runtime.fact_versions import current_facts
        view = current_facts(case, effective['version_id'])
        if (manifest_path != view.directory/'manifest.json' or
                sha256(manifest_path) != effective['manifest_sha256'] or
                inputs(case, persist=False)[2] != audit):
            raise ContractError('Analysis execution scope differs from current effective facts',
                                rule_id='stale_version')
    link = audit.get('census_scope_binding')
    if link:
        # The report source expands inherited unknown reasons. The census
        # review instead signs the exact committed Behavior bytes, so validate
        # those bytes and independently check the report-only normalization.
        case = _case_for_census_certificate(link['certificate_path'])
        normalized, _ = observation_source(case)
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
        view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
        sources = (view.world_path, case/'state/behavior.json', view.registry_path) if view else tuple(
            case/name for name in SOURCES)
        if source != normalized or link.get('source_hashes') != {
                str(path.relative_to(case)):sha256(path) for path in sources}:
            raise ContractError('Analysis observation source differs from its signed Case inputs')
        world, behavior = (read_json(case/name) for name in SOURCES[:2])
    else:
        world, behavior = source[SOURCES[0]], source[SOURCES[1]]
    _,_,expected=validate_certificate(world,behavior,audit)
    ids={a['actor_id'] for a in expected['excluded_actors']}
    present={a['actor_id'] for a in analysis.get('actors',[])} | {analysis.get('ego',{}).get('actor_id')}
    if ids & present: raise ContractError('Excluded observation instantiated in execution')
    for event in analysis.get('events',[]):
        if set(event.get('involved_actor_ids',[])) & ids:
            raise ContractError('Excluded observation referenced by execution event')
    for scenario in analysis.get('simulation_scenarios', []):
        if ids & (set(scenario.get('involved_actor_ids', [])) | set(scenario.get('initial_conditions', {}))):
            raise ContractError('Excluded observation referenced by simulation scenario')
    return ids
