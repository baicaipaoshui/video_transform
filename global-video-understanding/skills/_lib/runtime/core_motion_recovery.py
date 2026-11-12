"""Source-bound recovery of a missing core lane change in an isolated draft."""
from __future__ import annotations

import copy

from _lib.contracts.conversion_policy import digest
from _lib.contracts.core_lane_continuity import (_BETWEEN, _BOUND_LANE, _LANE, _ORDINAL,
                                                  _source_world, _source_world_path,
                                                  witnesses, validate)
from _lib.runtime.case_core import read_json, sha256
from _lib.contracts.v3 import ContractError

REVISION = 'core-motion-recovery.v2'
STAGE = 'conversion_core_motion_recovery'


class NeedsSemanticMotion(ValueError):
    code = 'core_motion_semantic_recovery_required'

    def __init__(self, request):
        super().__init__('Archived core motion requires a source-bound text decision')
        self.request = request


def _physical_lane(description, actor_id, lanes, bounds):
    """Only explicit lane/ordinal or adjacent physical-boundary claims count."""
    named, ordinal = _LANE.search(description), _ORDINAL.search(description)
    if named and ordinal and lanes.get(named.group(1)) == int(ordinal.group(1)):
        return named.group(1)
    named = next((match.group(2) for match in _BOUND_LANE.finditer(description)
                  if match.group(1) == actor_id and match.group(2) in lanes), None)
    if named:
        return named
    between = _BETWEEN.search(description)
    if between and all(name in bounds for name in between.groups()):
        pair = {bounds.index(name) for name in between.groups()}
        if len(pair) == 2 and max(pair)-min(pair) == 1:
            return next((lane for lane, value in lanes.items() if value == min(pair)), None)
    return None


def _bound_observations(case, bundle, actor_id, times):
    """Index source text only after a frozen object/frame/Actor binding is verified."""
    world = _source_world(case)
    bounds = (world.get('layout_observation') or {}).get('boundaries_right_to_left', [])
    lanes = {lane['lane_id']: lane['ordinal_from_right']
             for lane in bundle['world']['world']['road']['lanes']}
    registered = {row['actor_id'] for row in bundle['registry']}
    if actor_id not in registered:
        raise ContractError('Core motion Actor is not registered')
    result = {}
    for path in sorted((case/'batches').glob('batch_*.json')):
        batch = read_json(path)
        census = {}
        for frame in batch.get('vehicle_census', []):
            fid = frame.get('frame_id')
            if fid not in times or frame.get('time_s') != times[fid]:
                continue
            matches = [v for v in frame.get('vehicles', [])
                       if v.get('actor_id') == actor_id and v.get('binding') == 'existing'
                       and v.get('object_id')]
            if len(matches) != 1:
                continue
            vehicle = matches[0]
            census[fid] = vehicle
            description = vehicle.get('description', '')
            result.setdefault(fid, []).append(dict(
                observation_id=f'{path.stem}:{fid}:{vehicle["object_id"]}',
                source_path=str(path), actor_id=actor_id, frame_id=fid,
                time_s=times[fid], attribute='lane_id',
                lane_id=_physical_lane(description, actor_id, lanes, bounds),
                quote=description, binding='existing'))
        for index, observation in enumerate(batch.get('observations', [])):
            for evidence in observation.get('evidence', []):
                fid = evidence.get('frame_id')
                if (fid not in census or evidence.get('status') != 'observed'
                        or evidence.get('time_s') != times[fid]):
                    continue
                quote = evidence.get('reason', '')
                if not any(m.group(1) == actor_id for m in _BOUND_LANE.finditer(quote)):
                    continue
                result.setdefault(fid, []).append(dict(
                    observation_id=f'{path.stem}:observation_{index}:{fid}:{census[fid]["object_id"]}',
                    source_path=str(path), actor_id=actor_id, frame_id=fid,
                    time_s=times[fid], attribute='lane_id',
                    lane_id=_physical_lane(quote, actor_id, lanes, bounds),
                    quote=quote, binding='existing'))
    from _lib.contracts.census_history import rounds
    for index, item in enumerate(rounds(bundle['census'])) if bundle.get('census') else []:
        for vehicle in item['response']['vehicles']:
            if item['result']['vehicle_ids'].get(vehicle['key']) != actor_id:
                continue
            for sight in vehicle.get('sightings', []):
                fid = sight.get('frame_id')
                if fid not in times or sight.get('time_s') != times[fid]:
                    continue
                lane = sight.get('lane_id')
                if lane not in lanes:
                    continue
                result.setdefault(fid, []).append(dict(
                    observation_id=f'census_round_{index}:{vehicle["key"]}:{fid}',
                    source_path=str(case/'state/census_adjudication.accepted.json'),
                    actor_id=actor_id, frame_id=fid, time_s=times[fid],
                    attribute='lane_id', lane_id=lane,
                    quote=sight.get('features', ''), binding='signed_census'))
    return result


def restore_ego_execution(case, bundle, behavior):
    """Correct only the isolated ego execution track when two first-frame sources agree.

    Independently bound later Batch samples remain observations. Other samples
    continue the latest verified lane only as simulation assumptions. The
    archived World and Behavior remain in the source bundle.
    """
    world = _source_world(case)
    raw = (world.get('layout_observation') or world.get('world', {}).get('metadata', {})
           .get('lane_layout_review', {}).get('observation', {})).get('ego_initial', {})
    frame_times = {frame['frame_id']: frame['time_s'] for frame in bundle['frames']}
    initial_frame = min(bundle['frames'], key=lambda frame: frame['time_s'])['frame_id']
    first = raw.get('evidence_frame_ids', [])
    if initial_frame not in first or type(raw.get('ordinal_from_right')) is not int:
        raise ContractError('Initial ego lane lacks a bound raw World observation')
    result = copy.deepcopy(behavior)
    ego = next((actor for actor in result['actors'] if actor.get('actor_id') == 'ego'), None)
    if not ego or not ego.get('trajectory'):
        raise ContractError('Archived Behavior has no ego execution track')
    initial_point = next((point for point in ego['trajectory']
                          if point.get('frame_id') == initial_frame), None)
    if not initial_point or not initial_point.get('road_id'):
        raise ContractError('Archived ego track lacks a first-frame road binding')
    road_id = initial_point['road_id']
    lanes_by_ordinal = {}
    lanes_by_id = {}
    for lane in bundle['world']['world']['road']['lanes']:
        if (lane.get('travel_direction') != 'same_as_ego'
                or lane.get('road_id') != road_id):
            continue
        ordinal = lane.get('ordinal_from_right')
        if type(ordinal) is not int or ordinal in lanes_by_ordinal:
            raise ContractError('Current road has ambiguous ego-direction lane positions')
        lanes_by_ordinal[ordinal] = lane
        lanes_by_id[lane['lane_id']] = lane
    if sorted(lanes_by_ordinal) != list(range(len(lanes_by_ordinal))):
        raise ContractError('Current ego-direction lane positions are incomplete')
    initial_lane = lanes_by_ordinal.get(raw['ordinal_from_right'])
    if initial_lane is None:
        raise ContractError('Corroborated ego ordinal does not identify one current lane')
    corroborating, later = [], {}
    for path in sorted((case/'batches').glob('batch_*.json')):
        batch = read_json(path)
        for index, observation in enumerate(batch.get('observations', [])):
            item = (observation.get('layout_observation') or {}).get('ego_initial')
            if (item and initial_frame in item.get('evidence_frame_ids', [])
                    and type(item.get('ordinal_from_right')) is int):
                if item.get('ordinal_from_right') != raw['ordinal_from_right']:
                    raise ContractError('Independent Batch initial ego lane conflicts with World')
                corroborating.append((path, item))
            if observation.get('status') != 'observed':
                continue
            for evidence_index, evidence in enumerate(observation.get('evidence', [])):
                fid = evidence.get('frame_id')
                if (evidence.get('status') != 'observed' or evidence.get('actor_id') != 'ego'
                        or fid not in frame_times
                        or evidence.get('time_s') != frame_times[fid]):
                    continue
                lane_id = evidence.get('lane_id')
                ordinal = evidence.get('ordinal_from_right')
                if lane_id is None and ordinal is None:
                    continue
                if lane_id is None and type(ordinal) is int:
                    lane_id = lanes_by_ordinal.get(ordinal, {}).get('lane_id')
                lane = lanes_by_id.get(lane_id)
                if (lane is None or ordinal is not None
                        and (type(ordinal) is not int or lane['ordinal_from_right'] != ordinal)):
                    raise ContractError('Independent Batch ego lane conflicts with road definition')
                later.setdefault(fid, []).append(dict(
                    observation_id=f'{path.stem}:observation_{index}:evidence_{evidence_index}:{fid}',
                    source_path=str(path), source_sha256=sha256(path), actor_id='ego',
                    frame_id=fid, time_s=frame_times[fid], attribute='lane_id',
                    lane_id=lane_id, ordinal_from_right=lane['ordinal_from_right'],
                    quote=evidence.get('reason', ''), binding='explicit_ego'))
    if not corroborating:
        raise ContractError('Initial ego lane is not corroborated by an independent Batch')
    by_frame = {point['frame_id']: point for point in ego['trajectory']}
    if len(by_frame) != len(ego['trajectory']) or initial_frame not in by_frame:
        raise ContractError('Archived ego track lacks a unique first-frame point')
    if set(by_frame) - set(frame_times):
        raise ContractError('Archived ego track contains an unbound frame')
    if any(point.get('road_id') != road_id for point in ego['trajectory']):
        raise ContractError('Ego lane continuation crosses an unreviewed road')
    if set(later) - set(by_frame):
        raise ContractError('Independent later ego observation is absent from execution track')
    verified = {initial_frame: initial_lane['lane_id']}
    for fid, rows in later.items():
        observed = {row['lane_id'] for row in rows}
        if len(observed) != 1:
            raise ContractError('Independent same-frame ego observations disagree')
        if fid == initial_frame and observed != {initial_lane['lane_id']}:
            raise ContractError('Independent Batch initial ego lane conflicts with World')
        verified[fid] = next(iter(observed))
    contested = [dict(frame_id=point['frame_id'], time_s=frame_times.get(point['frame_id']),
        lane_id=point.get('lane_id'), ordinal_from_right=point.get('ordinal_from_right'),
        detail=point.get('detail')) for point in ego['trajectory']]
    timeline = []
    anchor = None
    for point in sorted(ego['trajectory'], key=lambda point: frame_times[point['frame_id']]):
        fid = point['frame_id']
        if fid in verified:
            lane_id = verified[fid]
            anchor = fid
            status = 'verified_observation'
        elif anchor is not None:
            lane_id = verified[anchor]
            status = 'unverified_continuation_assumption'
        else:
            raise ContractError('Ego execution point precedes its initial observation')
        ordinal = lanes_by_id[lane_id]['ordinal_from_right']
        point['lane_id'] = lane_id
        point['ordinal_from_right'] = ordinal
        point['detail'] = (f'Independent ego lane observation at {frame_times[fid]} s: '
            f'{lane_id}, ordinal_from_right={ordinal} of {len(lanes_by_ordinal)} '
            'same-direction lanes.' if status == 'verified_observation' else
            f'Unverified simulation assumption at {frame_times[fid]} s: ego continues '
            f'in {lane_id}, ordinal_from_right={ordinal} of {len(lanes_by_ordinal)} '
            f'same-direction lanes since observed {anchor}; no independent lane observation here.')
        timeline.append(dict(frame_id=fid, time_s=frame_times[fid], lane_id=lane_id,
            ordinal_from_right=ordinal, status=status, anchor_frame_id=anchor,
            observations=later.get(fid, [])))
    source_world_path = _source_world_path(case)
    record = dict(action='restore_ego_lane_in_isolated_analysis', actor_id='ego',
        observed_frame_id=initial_frame,
        source_lane_id=next(row['lane_id'] for row in contested
                            if row['frame_id'] == initial_frame),
        execution_lane_id=initial_lane['lane_id'],
        initial_ordinal_from_right=initial_lane['ordinal_from_right'],
        same_direction_lane_count=len(lanes_by_ordinal), timeline=timeline,
        contested_behavior=contested,
        source_world_sha256=sha256(source_world_path),
        corroborating_batch_sha256=[sha256(path) for path, _ in corroborating],
        later_track_status=('mixed_verified_and_assumed' if set(later) - {initial_frame} and any(
            row['status'] == 'unverified_continuation_assumption' for row in timeline) else
            'verified_followup_observations' if set(later) - {initial_frame} else
            'unverified_continuation_assumption'))
    return result, record


def request(case, bundle, behavior):
    """Separate bound observations from legacy Behavior in one frozen text task."""
    rows = witnesses(case)
    if len(rows) != 1:
        raise ContractError('Core motion recovery requires exactly one bound transition')
    row = rows[0]
    actor = next((a for a in behavior['actors'] if a['actor_id'] == row['actor_id']), None)
    if actor is None:
        raise ContractError('Core motion Actor is absent from archived Behavior')
    times = {f['frame_id']: f['time_s'] for f in bundle['frames']}
    points = actor['trajectory']
    by_frame = {p['frame_id']: p for p in points}
    if len(by_frame) != len(points) or any(f not in by_frame for f in
            (row['source_frame_id'], row['target_frame_id'])):
        raise ContractError('Core motion witness does not bind unique archived trajectory points')
    source_world_path = _source_world_path(case)
    world = read_json(source_world_path)
    fixed_proof = [e for e in world.get('field_evidence', {}).get(
        f"world.actors.{row['actor_id']}.hypothesized_lane", [])
        if e.get('status') == 'observed' and e.get('frame_id') in by_frame
        and e.get('frame_id') in times and
        times[e['frame_id']] < times[row['source_frame_id']]]
    fixed_source_frames = [e['frame_id'] for e in fixed_proof]
    observations = _bound_observations(case, bundle, row['actor_id'], times)
    intermediate = [dict(frame_id=p['frame_id'], time_s=times[p['frame_id']],
                         observations=observations.get(p['frame_id'], []),
                         contested_behavior=dict(lane_id=p.get('lane_id'),
                             ordinal_from_right=p.get('ordinal_from_right'),
                             detail=p.get('detail', ''),
                             source_path=str(case/'state/behavior.candidate.json')))
                    for p in points if p['frame_id'] in times and p['frame_id'] not in
                    {row['source_frame_id'], row['target_frame_id'], *fixed_source_frames}]
    result = dict(revision=REVISION, actor_id=row['actor_id'], witness=row,
                  bundle_sha256=digest(bundle), intermediate=intermediate,
                  fixed_source_observations=[dict(
                      observation_id=f'world:{row["actor_id"]}:hypothesized_lane:{e["frame_id"]}',
                      source_path=str(source_world_path),
                      actor_id=row['actor_id'], frame_id=e['frame_id'], time_s=times[e['frame_id']],
                      attribute='lane_id', lane_id=row['source_lane_id'], quote=e.get('reason', ''),
                      binding='registered_world_actor') for e in fixed_proof],
                  fixed_source_frames=sorted(set(fixed_source_frames)),
                  allowed_lanes=[row['source_lane_id'], row['target_lane_id'], None])
    result['request_sha256'] = digest(result)
    return result


def decision_audit(req, answer=None):
    """Accept fields independently; old Behavior prose never proves a lane."""
    if answer is not None and (not isinstance(answer, dict)
                               or answer.get('request_sha256') != req['request_sha256']
                               or not isinstance(answer.get('decisions'), list)):
        raise ContractError('Core motion text decision is unbound')
    offered = {}
    for decision in answer['decisions'] if answer else []:
        if isinstance(decision, dict) and isinstance(decision.get('frame_id'), str):
            offered.setdefault(decision['frame_id'], []).append(decision)
    result = []
    source_time, target_time = req['witness']['observation_envelope_s']
    for point in req['intermediate']:
        supported = {o['lane_id'] for o in point['observations'] if o['lane_id'] is not None}
        if len(supported) > 1:
            raise ContractError('Bound same-frame observations disagree on core Actor lane')
        verified = next(iter(supported), None)
        proposals = offered.get(point['frame_id'], [])
        proposal = proposals[0] if len(proposals) == 1 else None
        quote = proposal.get('quote') if proposal else None
        proposed_lane = proposal.get('lane_id') if proposal else None
        cited = (isinstance(quote, str) and bool(quote.strip()) and any(
            quote in observation['quote'] and observation['lane_id'] == proposed_lane
            for observation in point['observations']))
        accepted = (proposal is not None and set(proposal) == {'frame_id', 'lane_id', 'quote'}
                    and proposed_lane in req['allowed_lanes'] and cited and
                    proposed_lane == verified)
        result.append(dict(frame_id=point['frame_id'], lane_id=verified,
                           status=('verified_observation' if verified is not None else
                                   'unknown_no_bound_lane'),
                           execution_placement=('audit_only' if verified is None and
                               not source_time < point['time_s'] < target_time else 'trajectory'),
                           time_s=point['time_s'],
                           model_field_status=('accepted' if accepted else
                                               'rejected_unsupported' if proposals else 'not_proposed'),
                           proposed=proposals, observations=point['observations'],
                           contested_behavior=point['contested_behavior']))
    return result


def recover(case, bundle, behavior, req, answer=None, *, allow_fallback=False):
    if req != request(case, bundle, behavior):
        raise ContractError('Core motion recovery request differs from frozen source')
    intermediate = req['intermediate']
    if intermediate and answer is None and not allow_fallback:
        raise NeedsSemanticMotion(req)
    decisions = {row['frame_id']: row for row in decision_audit(req, answer)}
    result = copy.deepcopy(behavior)
    actor = next(a for a in result['actors'] if a['actor_id'] == req['actor_id'])
    row = req['witness']
    times = {f['frame_id']: f['time_s'] for f in bundle['frames']}
    ordinal = {row['source_lane_id']: row['source_ordinal'],
               row['target_lane_id']: row['target_ordinal']}
    fixed = {o['frame_id']: o for o in req['fixed_source_observations']}
    for point in actor['trajectory']:
        fid = point['frame_id']
        lane = (row['source_lane_id'] if fid in {row['source_frame_id'], *req['fixed_source_frames']} else
                row['target_lane_id'] if fid == row['target_frame_id'] else
                decisions[fid]['lane_id'] if fid in decisions else point.get('lane_id'))
        if fid in decisions or fid in {row['source_frame_id'], row['target_frame_id'],
                                      *req['fixed_source_frames']}:
            point['lane_id'] = lane
            point['ordinal_from_right'] = ordinal.get(lane)
            point['detail'] = (
                fixed[fid]['quote'] if fid in fixed else
                row['source_quote'] if fid == row['source_frame_id'] else
                row['target_quote'] if fid == row['target_frame_id'] else
                next((o['quote'] for o in decisions[fid]['observations']
                      if o['lane_id'] == lane),
                     'Unverified execution state: no independently bound lane observation at this frame.')
                if lane is not None else
                'Unverified execution state: lane at this frame is unknown; archived Behavior lane text is contested.')
    # The original lane-change executor requires a known lane before its
    # witnessed source boundary. Preserve unsupported earlier/later samples
    # in the request/audit, not as invented executable lane positions.
    actor['trajectory'] = [point for point in actor['trajectory']
                           if point['frame_id'] not in decisions or
                           decisions[point['frame_id']]['execution_placement'] == 'trajectory']
    if any(e.get('event_type') == 'lane_change' and req['actor_id'] in
           e.get('involved_actor_ids', []) for e in result.get('events', [])):
        raise ContractError('Existing core lane-change event requires ordinary validation')
    if any(e.get('event_id') == 'event_core_motion_recovery' for e in result.get('events', [])):
        raise ContractError('Core motion recovery event ID collides with archived event')
    known = [p for p in actor['trajectory'] if p.get('lane_id') in ordinal and p['frame_id'] in times]
    transitions = [(a, b) for a, b in zip(known, known[1:]) if a['lane_id'] != b['lane_id']]
    if len(transitions) != 1 or [transitions[0][0]['lane_id'], transitions[0][1]['lane_id']] != [
            row['source_lane_id'], row['target_lane_id']]:
        raise ContractError('Core motion decisions do not form one witnessed lane transition')
    result.setdefault('events', []).append(dict(event_id='event_core_motion_recovery',
        event_type='lane_change', involved_actor_ids=[req['actor_id']],
        from_lane_id=row['source_lane_id'], to_lane_id=row['target_lane_id'],
        time_range_s=list(row['observation_envelope_s']), evidence=[
            dict(status='observed', actor_id=req['actor_id'], frame_id=p['frame_id'],
                 time_s=times[p['frame_id']], lane_id=p['lane_id'], boundary_state=state)
            for p, state in ((next(p for p in actor['trajectory']
                                   if p['frame_id'] == row['source_frame_id']), 'before'),
                             (next(p for p in actor['trajectory']
                                   if p['frame_id'] == row['target_frame_id']), 'after'))]))
    validate(case, result)
    return result
