"""Reuse verified regional isolation, without equating objects or resolving identity.

Only execution-assumptions opt-in consumers call this module. The census
certificate is replayed before use; an archived omission list is not authority.
"""
import copy
import re

KEY = 'inherited_opposing'
REVISION = 'inherited-isolation.v1'


def region(text, side, *, direction=True, require_side=True, inherit_barrier=False):
    """Conservative vocabulary for a positive location, not a prose classifier."""
    text = text.lower()
    if re.search(r'\b(not|possibly|maybe|uncertain|unproven)\b.*\b(beyond|opposing|barrier)|'
                 r'same.direction|ego.s (?:lane|path)|crossing|merging|opening|gap in|未确认|同向|缺口', text):
        return False
    side_seen = side in text or bool(re.search('左侧|左边' if side == 'left' else '右侧|右边', text))
    other_side = 'right' if side == 'left' else 'left'
    if other_side in text or re.search('右侧|右边' if side == 'left' else '左侧|左边', text):
        return False
    barrier = bool(re.search(r'beyond (?:the )?(?:continuous )?(?:concrete )?(?:median )?barrier|'
                             r'\bbarrier\s*外侧|护栏外侧', text))
    return ((barrier or inherit_barrier and side_seen and '对向车行道' in text)
            and (not direction or bool(re.search(r'\bopposing\b|opposite direction|对向', text)))
            and (side_seen or not require_side))


def source_options(saved, pending, frames):
    from _lib.contracts.census_history import rounds
    from _lib.world.readiness import _observed_separator
    world = saved['result']['world']
    scene = world.get('world', {})
    road = scene.get('road', {})
    roads = road.get('roads', [])
    empty = dict(observations={}, actors={})
    if (scene.get('scene_type') != 'highway' or scene.get('junctions')
            or road.get('one_way') is not False or len(roads) != 1
            or not _observed_separator(world)):
        return empty
    side = roads[0]['road_geometry']['opposing_separation']['side']
    witnesses, bindings = [], {}
    for turn in rounds(saved):
        records = {r['issue_id']:r for r in turn['request']['records']}
        frozen = {f['frame_id']:f for f in turn['request']['frames']}
        for binding in turn['response']['bindings']:
            bindings[binding['issue_id']] = binding
            row = records.get(binding['issue_id'], {})
            separation = binding.get('separation') or {}
            fid = row.get('frame_id')
            proof_frames = separation.get('evidence_frame_ids', [])
            if (binding['outcome'] != 'excluded_opposing' or row.get('candidate_actor_ids')
                    or separation.get('direction') != 'opposite_to_ego'
                    or separation.get('no_interaction') is not True
                    or separation.get('separator') != 'barrier' or separation.get('side') != side
                    or fid not in proof_frames or not set(proof_frames) <= set(frozen)
                    or not region(row.get('description', ''), side)):
                continue
            # Match frozen image variants, not just coincident frame names.
            if any(f not in frames or frames[f]['time_s'] != frozen[f]['time_s']
                   or frozen[f]['sha256'] not in {v.get('sha256') for v in
                       [frames[f], *frames[f].get('variants', [])]} for f in proof_frames):
                continue
            times = [f['time_s'] for f in frozen.values()]
            if not {min(times), max(times)} <= {frozen[f]['time_s'] for f in proof_frames}:
                continue
            witnesses.append(dict(certificate_sha256=turn['sha256'], observation=copy.deepcopy(row),
                binding=copy.deepcopy(binding), frames=[copy.deepcopy(frozen[f]) for f in proof_frames],
                window_s=[min(times), max(times)]))
    # A later adjudication may supersede an earlier exclusion.
    witnesses = [w for w in witnesses if bindings.get(w['observation']['issue_id']) == w['binding']]
    actors = {}
    for actor in saved['result']['registry']:
        if (actor.get('actor_type') not in {'vehicle', 'vehicle_group'}
                or actor.get('hypothesized_lane') is not None
                or not region(actor.get('identity_description', ''), side)):
            continue
        evidence = actor.get('evidence', [])
        if not evidence or any(e.get('status') != 'observed' or e.get('frame_id') not in frames
                or not region(e.get('reason', ''), side, direction=False) for e in evidence):
            continue
        anchors = {e['frame_id'] for e in evidence}
        supporting = [w for w in witnesses if w['observation']['frame_id'] in anchors
            and all(w['window_s'][0] <= frames[f]['time_s'] <= w['window_s'][1] for f in anchors)]
        if supporting:
            actors[actor['actor_id']] = dict(registry=copy.deepcopy(actor),
                anchor_frames={f:frames[f]['time_s'] for f in sorted(anchors)}, witnesses=supporting,
                side=side, identity_resolution='unchanged',
                fidelity_loss='Isolated opposing background motion is not simulated; source sightings remain recorded')
    observations = {}
    for iid, row in pending.items():
        candidates = row.get('candidate_actor_ids', [])
        if (row.get('distinction') or row.get('actor_id') or len(candidates) != 1
                or candidates[0] not in actors
                or actors[candidates[0]]['registry']['actor_type'] != 'vehicle_group'
                or bindings.get(iid, {}).get('outcome') != 'unresolved'
                or not region(row.get('description', ''), side,
                              require_side=False, inherit_barrier=True)):
            continue
        matches = [w for w in actors[candidates[0]]['witnesses']
                   if w['observation']['frame_id'] == row['frame_id']]
        if matches:
            observations[iid] = dict(issue_id=iid, observation=copy.deepcopy(row),
                binding=copy.deepcopy(bindings[iid]), basis=REVISION, witnesses=matches,
                identity_resolution='unresolved', fidelity_loss=actors[candidates[0]]['fidelity_loss'])
    return dict(observations=observations, actors=actors)


def compatible(actor, record):
    """Source evidence can replace missing repetition, never contradictory facts."""
    source = record['registry']
    points = actor.get('trajectory', [])
    direction = actor.get('direction_evidence') or {}
    if (actor.get('actor_type') != source['actor_type'] or actor.get('is_static')
            or actor.get('identity_description') != source['identity_description']
            or (actor.get('execution_scope_evidence') or {}).get('kind') == 'execution_relevant'
            or direction and (direction.get('value') != 'opposite_to_ego'
                or direction.get('status') != 'observed'
                or set(direction.get('frame_ids', [])) != set(record['anchor_frames']))
            or len(points) != len(record['anchor_frames'])
            or {p.get('frame_id') for p in points} != set(record['anchor_frames'])):
        return False
    return all(p.get('time_s') == record['anchor_frames'][p['frame_id']]
        and p.get('lane_id') is None and p.get('road_id') is None
        and p.get('ordinal_from_right') is None
        and not any(v is not None for v in (p.get('position_ego_m') or {}).values())
        and (not p.get('detail') or region(str(p['detail']), record['side'])) for p in points)


def select(world, behavior, options, records, pending):
    """Use the existing dependency fixed point for both old and inherited scope."""
    from _lib.runtime.execution_scope import opposing_preservation_ids
    from _lib.contracts.unused_association import mentions
    actors = {a['actor_id']:a for a in behavior.get('actors', [])}
    def typed_ego_reference(value):
        if isinstance(value, str):
            return value == 'ego'
        if isinstance(value, list):
            return any(typed_ego_reference(v) for v in value)
        if isinstance(value, dict):
            return any(typed_ego_reference(v) for v in value.values())
        return False
    eligible = {aid:r for aid,r in options['actors'].items()
                if aid in actors and compatible(actors[aid], r)
                and not typed_ego_reference(actors[aid])
                and not mentions(actors[aid], set(actors) - {aid, 'ego'})
                and (r['registry']['actor_type'] == 'vehicle_group' or not any(
                    aid in row['candidate_actor_ids'] for row in pending.values()))}
    groups = {aid for aid,r in eligible.items() if r['registry']['actor_type'] == 'vehicle_group'
              and all(iid in records for iid,row in pending.items() if aid in row['candidate_actor_ids'])}
    safe = opposing_preservation_ids(world, behavior, groups, inherited=eligible)
    selected = {aid:r for aid,r in eligible.items() if aid in safe}
    return selected


def replay(program, world, behavior):
    """Preservation independently rebuilds the source subset, even after filtering."""
    from _lib.contracts import census_adjudication, behavior_census_scope, execution_assumptions
    from _lib.behavior.compile import evidence_frames
    case = program['case_dir']
    saved = census_adjudication.proof(case)
    expected = execution_assumptions.build(case, behavior, behavior_census_scope.pending(case, saved),
        {f['frame_id']:f for f in evidence_frames(case)}, saved, program['review'])
    if not expected or program != expected:
        execution_assumptions.fail('Inherited isolation sources or dependencies changed')
    return expected.get(KEY, {})
