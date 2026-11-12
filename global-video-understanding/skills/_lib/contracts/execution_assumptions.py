"""Bounded execution assumptions; source observations remain unresolved.

No identity links, extra vehicles, actions or samples are generated. A receipt
is replayed from the raw Behavior at every consumer, not trusted as evidence.
"""
import copy
import re
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.contracts.unused_association import mentions
from _lib.runtime.case_core import atomic_json, read_json, sha256

KEY = 'execution_assumptions'
REVISION = 'execution-assumptions.v1'
HANDLING = 'observation_only_by_assumption'
ADOPTION = 'state/execution_assumptions.adoption.json'
SOURCES = ('case.json', 'state/execution_policy.json', 'state/world_context.json',
           'state/actor_registry.json', 'state/census_adjudication.accepted.json')


def lane_words(text):
    """A finite vocabulary; unknown prose never invents a lane bound."""
    text = text.lower()
    if re.search(r'same.direction.*middle/right lane|同向.*中间[/或]右侧车道', text):
        return {0, 1}
    for ordinal, pattern in ((1, r'middle same.direction lane|同向中间车道'),
            (0, r'right(?:most)? same.direction lane|同向最?右侧车道'),
            (2, r'left(?:most)? same.direction lane|同向最?左侧车道')):
        if re.search(pattern, text):
            return {ordinal}
    return None


def fail(message):
    error = ContractError(message, rule_id='execution.assumption_binding')
    error.code = 'internal_pipeline_error'
    error.diagnostics = dict(rule_id=error.rule_id, retry_kind='code_change', message=message)
    raise error


def enabled(case):
    case = Path(case)
    policy = case/'state/execution_policy.json'
    if policy.is_file() and read_json(policy).get(KEY) == REVISION:
        return True
    path = case/ADOPTION
    if not path.is_file():
        return False
    value = read_json(path)
    body = {k:v for k,v in value.items() if k != 'sha256'}
    if (value.get('sha256') != digest(body) or value.get('revision') != REVISION
            or value.get('case_id') != read_json(case/'case.json')['case_id']
            or not set(SOURCES) <= set(value.get('source_hashes', {}))):
        fail('Execution-assumption adoption is malformed')
    for name, signature in value['source_hashes'].items():
        if not (case/name).is_file() or sha256(case/name) != signature:
            fail('Execution-assumption adoption source changed: ' + name)
    return True


def adopt(case):
    """Explicit opt-in for a saved Case before its first Behavior attempt."""
    case = Path(case)
    if enabled(case):
        return read_json(case/ADOPTION) if (case/ADOPTION).is_file() else None
    from _lib.runtime.bounded_recovery import require_terminal
    state = read_json(case/'state/progress.json')
    require_terminal(state)
    if (state.get('attempts', {}).get('behavior', 0)
            or any(s.get('stage') == 'behavior' for s in state.get('sessions', []))
            or any((case/n).exists() for n in ('state/behavior.json', 'state/behavior.candidate.json',
                'state/behavior_scope_review.accepted.json', 'final/analysis.json'))):
        fail('Execution assumptions require an unattempted Behavior stage')
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    if not saved or saved['request']['revision'] != 'world-census-adjudication.v6':
        fail('Execution assumptions require a verified v6 census')
    names = list(SOURCES)
    if (case/'state/behavior_input.request.json').is_file():
        from _lib.behavior.input_binding import verify
        verify(case)
        names.append('state/behavior_input.request.json')
    body = dict(revision=REVISION, case_id=read_json(case/'case.json')['case_id'],
                source_hashes={name:sha256(case/name) for name in names})
    value = dict(body, sha256=digest(body))
    atomic_json(case/ADOPTION, value)
    return value


def source_options(saved, pending, frames):
    """Evidence bounds only. Actual Behavior non-use must still be checked."""
    from _lib.contracts.census_history import rounds
    from _lib.contracts.lane_locations import lane_locations
    world = saved['result']['world']
    registry = saved['result']['registry']
    bindings = {b['issue_id']:b for r in rounds(saved) for b in r['response']['bindings']}
    observations = {}
    for iid, row in pending.items():
        binding = bindings.get(iid, {})
        candidates = set(row.get('candidate_actor_ids', []))
        refuted = {r['actor_id'] for r in binding.get('candidate_refutations', [])}
        text = (row.get('description', '') + ' ' +
                (row.get('preliminary_relevance') or {}).get('reason', '')).lower()
        if (row.get('distinction') or binding.get('outcome') != 'unresolved'
                or row.get('actor_id') or candidates - refuted
                or not re.search(r'\b(distant|far|horizon|vanishing point)\b|远处|消失点', text)
                or not re.search(r'\b(tiny|small|blob|vanishing point)\b|微小|小目标|消失点', text)):
            continue
        observations[iid] = dict(issue_id=iid, observation=copy.deepcopy(row),
            binding=copy.deepcopy(binding), identity_resolution='unresolved',
            fidelity_loss='Unconfirmed traffic may be absent from simulation; interaction is not proved irrelevant')
    lanes = {}
    road = world['world']['road']
    if world['world'].get('scene_type') != 'highway' or world['world'].get('junctions'):
        return dict(observations=observations, lanes=lanes)
    locations = lane_locations(road)
    definitions = {l['lane_id']:l for l in road.get('lanes', [])}
    # Only the exact three-lane ego carriageway supports these verbal bounds.
    ids = [lid for lid,(rid,ordinal) in locations.items() if rid == 'road_001'
           and ordinal in {0,1,2} and definitions[lid].get('travel_direction') == 'same_as_ego']
    arm = next((r for r in road.get('roads', []) if r['road_id'] == 'road_001'), {})
    if (len(ids) != 3 or set(arm.get('lane_ids', [])) != set(ids)
            or (arm.get('lane_knowledge') or {}).get('kind') != 'exact'
            or arm['lane_knowledge'].get('count') != 3):
        return dict(observations=observations, lanes=lanes)
    by_ordinal = {locations[lid][1]:lid for lid in ids}
    for actor in registry:
        if actor.get('actor_type') != 'vehicle' or actor.get('hypothesized_lane') is not None:
            continue
        evidence = [e for e in actor.get('evidence', [])
                    if e.get('status') == 'observed' and e.get('frame_id') in frames]
        anchors = sorted({e['frame_id'] for e in evidence})
        bounds = []
        for e in evidence:
            bound = lane_words(e.get('reason', ''))
            if bound is not None:
                bounds.append({by_ordinal[i] for i in bound})
        if len(anchors) < 2 or not bounds:
            continue
        allowed = set.intersection(*bounds)
        if not allowed or not allowed <= {by_ordinal[0], by_ordinal[1]}:
            continue
        lanes[actor['actor_id']] = dict(actor_id=actor['actor_id'], anchor_frame_ids=anchors,
            candidate_lane_ids=sorted(allowed, key=lambda lid:locations[lid][1]),
            evidence=copy.deepcopy(evidence),
            fidelity_loss='Lane occupancy and interactions may differ; no unobserved lane change is modeled')
    return dict(observations=observations, lanes=lanes)


def pure_observation(item, records):
    from _lib.contracts.census_relevance import observation_issue_only
    return (observation_issue_only(item) and set(item['census_issue_ids']) <= set(records)
            and set(item['impact_domains']) <= {'actor_identity', 'actor_lane'})


def pure_lane(item, lanes):
    ids = item.get('actor_ids', [])
    return (len(ids) == 1 and ids[0] in lanes and item.get('impact_domains') == ['actor_lane']
            and item.get('execution_effect') in {None, 'bounded_uncertainty'}
            and not any(item.get(k) for k in ('actor_id', 'census_issue_ids', 'world_paths',
                'related_world_paths', 'affected_actor_ids', 'affected_event_ids', 'event_id',
                'event_ids', 'identity_binding', 'scope_binding', 'localization', 'involved_actor_ids')))


def build(case, behavior, pending, frames, saved, review=None):
    if not saved or not enabled(case):
        return None
    options = source_options(saved, pending, frames)
    from _lib.contracts import inherited_opposing
    inherited = inherited_opposing.source_options(saved, pending, frames)
    options['observations'].update(inherited['observations'])
    rows = {r['issue_id']:r for r in (review or {}).get('decisions', [])}
    records, lanes = {}, {}
    actors = {a['actor_id']:a for a in behavior.get('actors', [])}
    registry = {a['actor_id']:a for a in saved['result']['registry']}
    # Never rescue a candidate that lost a confirmed vehicle.
    if any(a.get('actor_type') == 'vehicle' and aid not in actors for aid,a in registry.items()):
        return None
    dependencies = [behavior.get(k, []) for k in
        ('actors', 'events', 'executor_primitives', 'video_fidelity', 'challenge_resolutions')]
    limits = [*saved['result']['world'].get('questions_to_verify', []),
              *saved['result']['world'].get('ambiguities', []), *behavior.get('semantic_limitations', [])]
    for iid, record in options['observations'].items():
        row = rows.get(iid, {})
        assessment = row.get('identity_assessment') or {}
        names = {iid, record['observation']['object_id']}
        if (row.get('outcome') not in {None, 'unknown', 'needs_identity'}
                or any(row.get(k) for k in ('affected_actor_ids', 'affected_event_ids', 'scope_proof'))
                or assessment.get('alternative_actor_ids') or assessment.get('counterevidence')
                or assessment.get('candidate_status') == 'compatible'
                or record.get('basis') == inherited_opposing.REVISION
                   and assessment.get('candidate_status') == 'rejected'
                or mentions(dependencies, names)):
            continue
        records[iid] = record
    # Precise observation restrictions can be retained in audit. Mixed claims
    # cannot be hidden by a matching issue ID somewhere inside their text.
    for item in limits:
        if item.get('resolution') in {'resolved', 'confirmed', 'non_core'}:
            continue
        # Grouping several observation IDs in one sentence is not an Actor
        # dispute. Projection still requires every ID to have a disposition.
        if pure_observation(item, pending):
            continue
        for iid, record in list(records.items()):
            if mentions(item, {iid, record['observation']['object_id']}):
                records.pop(iid)
    for aid, option in options['lanes'].items():
        actor = actors.get(aid, {})
        points = actor.get('trajectory', [])
        if (actor.get('identity_description') != registry[aid].get('identity_description')
                or {p.get('frame_id') for p in points} != set(option['anchor_frame_ids'])
                or any(p.get('time_s') != frames[p['frame_id']]['time_s'] for p in points)
                or not any(p.get('lane_id') is None for p in points)
                or any(p.get('road_id') not in {None, 'road_001'} or p.get('ordinal_from_right') is not None
                       or type((p.get('position_ego_m') or {}).get('y')) in (int, float) for p in points)):
            continue
        known = {p['lane_id'] for p in points if p.get('lane_id') is not None}
        if len(known) > 1 or known - set(option['candidate_lane_ids']):
            continue
        # Do not move a vehicle participating in an accepted action, or rewrite
        # an initial-state declaration. Missing coordinates alone are allowed.
        if mentions([behavior.get('events', []), behavior.get('executor_primitives', []),
                     behavior.get('video_fidelity', {}), behavior.get('challenge_resolutions', [])], {aid}):
            continue
        if mentions([other.get('trajectory', []) for other in behavior.get('actors', [])
                     if other['actor_id'] != aid], {aid}):
            continue
        # New point descriptions cannot be contradicted by a Registry-derived
        # assumption. Ambiguous/conflicting explicit lane references stay open.
        descriptions = ' '.join(str(p.get('detail', '')) for p in points).lower()
        other_lanes = {l['lane_id'] for l in saved['result']['world']['world']['road'].get('lanes', [])} - set(option['candidate_lane_ids'])
        if (any(lid in descriptions for lid in other_lanes)
                or re.search(r'left(?:most)? same.direction lane|同向最?左侧车道', descriptions)):
            continue
        if any(mentions(item, {aid}) and 'actor_lane' in item.get('impact_domains', [])
               and item.get('resolution') not in {'resolved', 'confirmed', 'non_core'}
               and not pure_lane(item, {aid:option}) for item in limits):
            continue
        # A more specific accepted point description may narrow the source
        # bounds. Inconsistent samples cannot be flattened into constant lane.
        from _lib.contracts.lane_locations import lane_locations
        locations = lane_locations(saved['result']['world']['world']['road'])
        candidates = set(option['candidate_lane_ids'])
        for point in points:
            bound = lane_words(str(point.get('detail', '')))
            if bound is not None:
                candidates &= {lid for lid in candidates if locations[lid][1] in bound}
        if not candidates or known - candidates:
            continue
        selected = [lid for lid in option['candidate_lane_ids'] if lid in candidates]
        lane = next(iter(known)) if known else selected[0]
        lanes[aid] = dict(option, status='simulation_assumption', selected_lane_id=lane,
            execution_candidate_lane_ids=selected,
            selection='existing_anchor' if known else 'lowest_supported_ordinal',
            replacements=[dict(frame_id=p['frame_id'], time_s=p['time_s'], lane_id=lane)
                          for p in points if p.get('lane_id') is None])
    inherited_actors = inherited_opposing.select(saved['result']['world'], behavior,
                                                inherited, records, pending)
    records = {iid:r for iid,r in records.items() if r.get('basis') != inherited_opposing.REVISION
               or set(r['observation']['candidate_actor_ids']) <= set(inherited_actors)}
    from _lib.contracts import retained_motion
    retained = retained_motion.select(case, saved, behavior, pending, frames)
    from _lib.contracts import bounded_execution
    reconstruction = bounded_execution.build(case, saved, behavior,
        pending=pending, frames=frames, review=review)
    if not records and not lanes and not inherited_actors and not retained and not reconstruction:
        return None
    body = dict(revision=REVISION, case_dir=str(Path(case).resolve()),
        certificate_sha256=saved['sha256'], behavior_sha256=digest(behavior),
        world_sha256=digest(saved['result']['world']), registry_sha256=digest(saved['result']['registry']),
        policy_sha256=sha256(Path(case)/'state/execution_policy.json'),
        adoption_sha256=sha256(Path(case)/ADOPTION) if (Path(case)/ADOPTION).is_file() else None,
        observations=records, lanes=lanes, review=copy.deepcopy(review),
        actors_sha256=digest(behavior['actors']), events_sha256=digest(behavior.get('events', [])),
        primitives_sha256=digest(behavior.get('executor_primitives', [])))
    if inherited_actors:
        body[inherited_opposing.KEY] = inherited_actors
    if retained:
        body[retained_motion.KEY] = retained
    if reconstruction:
        body[bounded_execution.KEY] = reconstruction
    return dict(body, sha256=digest(body))


def validate_projection(world, behavior, frames, receipt):
    program = (receipt or {}).get(KEY)
    choices = [r for r in (receipt or {}).get('choices', []) if r['handling'] == HANDLING]
    if not program and not choices:
        return None
    if not program or frames is None:
        fail('Execution assumptions require a complete source-bound record')
    from _lib.contracts import census_adjudication, behavior_census_scope
    case = Path(program['case_dir'])
    saved = census_adjudication.proof(case)
    if program.get('inherited_opposing'):
        # Scope certificates retain only frame IDs/times. Reload the bound
        # image variants for replay instead of dropping image verification.
        from _lib.behavior.compile import evidence_frames
        bound = evidence_frames(case)
        if {f['frame_id']:f['time_s'] for f in frames} != {f['frame_id']:f['time_s'] for f in bound}:
            fail('Inherited isolation frame catalog changed')
        frames = bound
    expected = build(case, behavior, behavior_census_scope.pending(case, saved),
                     {f['frame_id']:f for f in frames}, saved, program['review'])
    expected_choices = [choice(iid, r['observation']) for iid,r in sorted(
        (expected or {}).get('observations', {}).items())]
    if (expected is None or program != expected or digest(world) != program['world_sha256']
            or sorted(choices, key=lambda r:r['issue_id']) != expected_choices):
        fail('Execution-assumption sources, selection or actions changed')
    return program


def choice(iid, observation):
    return dict(issue_id=iid, candidate_actor_ids=observation['candidate_actor_ids'],
        observation_frame_id=observation['frame_id'], outcome='unresolved',
        handling=HANDLING, identity_resolution='unresolved')


def disposes(item, receipt):
    program = (receipt or {}).get(KEY)
    return bool(program and (pure_observation(item, program['observations'])
                             or pure_lane(item, program['lanes'])))


def apply(program, behavior, *, opposing_scope=None):
    """Run after existing preservation checks; allow only recorded null lanes."""
    if not program:
        return behavior
    from _lib.contracts.unused_association import preserve
    preserve(program, behavior, opposing_scope=opposing_scope)
    result = copy.deepcopy(behavior)
    for actor in result['actors']:
        record = program['lanes'].get(actor['actor_id'])
        if record:
            for point in actor['trajectory']:
                if point.get('lane_id') is None:
                    point['lane_id'] = record['selected_lane_id']
    from _lib.contracts.retained_motion import KEY
    limits = {r['limitation_id']:r for record in program.get(KEY, {}).values()
              for r in record['limitations']}
    for item in result.get('semantic_limitations', []):
        if item.get('limitation_id') in limits:
            if item != limits[item['limitation_id']]:
                fail('Retained unseen-motion limitation changed')
            item['execution_effect'] = 'bounded_uncertainty'
    return result
