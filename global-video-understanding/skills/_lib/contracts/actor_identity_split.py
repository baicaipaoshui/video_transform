"""One evidenced two-vehicle split; ordinary registry immutability is unchanged."""
import copy

from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json, validate_registry

REVISION = 'actor-identity-split.v1'


def _fail(message):
    raise ContractError(message, rule_id='identity.split.contract')


def materialize(packet, response):
    from _lib.contracts.actor_identity_revision import verify_request
    verify_request(packet)
    fields = {'revision', 'request_sha256', 'actor_id', 'outcome', 'actors', 'sightings',
              'trajectory_assignments', 'resolved_issue_ids', 'reason'}
    if (not isinstance(response, dict) or set(response) != fields
            or response['revision'] != REVISION
            or response['request_sha256'] != packet['request_sha256']
            or response['actor_id'] != packet['actor_id']
            or response['outcome'] not in {'split_confirmed', 'insufficient_evidence'}
            or not isinstance(response['reason'], str) or not response['reason'].strip()):
        _fail('Invalid identity-split response')
    if response['outcome'] == 'insufficient_evidence':
        if any(response[k] != [] for k in ('actors', 'sightings', 'trajectory_assignments', 'resolved_issue_ids')):
            _fail('Unconfirmed split cannot change facts')
        return None
    world, registry, behavior = [read_json(packet[k+'_path']) for k in ('world', 'registry', 'behavior')]
    old_id, new_id = packet['actor_id'], packet['new_actor_id']
    ids = {old_id, new_id}
    if any(a['actor_id'] == new_id for a in registry + world['actor_hypotheses']):
        _fail('Split would overwrite an existing Actor')
    descriptors = response['actors']
    if (not isinstance(descriptors, list) or len(descriptors) != 2
            or any(not isinstance(a, dict) or set(a) != {'actor_id', 'identity_description', 'color', 'vehicle_class'}
                   or any(not isinstance(a.get(k), str) or not a[k].strip()
                          for k in ('actor_id', 'identity_description', 'color', 'vehicle_class')) for a in descriptors)
            or {a['actor_id'] for a in descriptors} != ids):
        _fail('Split requires exactly the retained Actor and compiler-allocated new Actor')
    frames = {f['frame_id']: f for f in packet['frames']}
    sightings, seen_frames, observations = response['sightings'], set(), {aid: {} for aid in ids}
    lanes = {l['lane_id'] for l in world['world']['road']['lanes']}
    coexist = False
    if not isinstance(sightings, list):
        _fail('Split sightings must be an array')
    for row in sightings:
        if (not isinstance(row, dict) or set(row) != {'frame_id', 'time_s', 'vehicles'}
                or row['frame_id'] not in frames or row['frame_id'] in seen_frames
                or type(row['time_s']) not in (int, float)
                or row['time_s'] != frames[row['frame_id']]['time_s']
                or not isinstance(row['vehicles'], list)):
            _fail('Split must inspect each supplied frame exactly once with bound time')
        seen_frames.add(row['frame_id']); present = set()
        for vehicle in row['vehicles']:
            if (not isinstance(vehicle, dict)
                    or set(vehicle) != {'actor_id', 'features', 'lane_id', 'distinct_from_actor_ids'}
                    or vehicle['actor_id'] not in ids or vehicle['actor_id'] in present
                    or not isinstance(vehicle['features'], str) or not vehicle['features'].strip()
                    or (vehicle['lane_id'] is not None and vehicle['lane_id'] not in lanes)
                    or not isinstance(vehicle['distinct_from_actor_ids'], list)
                    or any(not isinstance(a, str) for a in vehicle['distinct_from_actor_ids'])
                    or not set(vehicle['distinct_from_actor_ids']) <= set(packet['other_actor_ids'])):
                _fail('Invalid split vehicle/frame distinction')
            if not set(packet['co_visible_actor_ids'].get(row['frame_id'], [])) <= set(vehicle['distinct_from_actor_ids']):
                _fail('Split must distinguish unrelated co-visible registered vehicles')
            present.add(vehicle['actor_id'])
            observations[vehicle['actor_id']][row['frame_id']] = dict(vehicle, time_s=row['time_s'])
        coexist |= present == ids
    if seen_frames != set(frames) or not coexist or any(len(rows) < 2 for rows in observations.values()):
        _fail('Split needs complete supplied-frame review, two sightings per vehicle and co-visibility')
    # Retain the identity of the original first sighting; old unrelated Actor
    # references are never silently reinterpreted to mean the new vehicle.
    if packet['actor']['first_seen_frame_id'] not in observations[old_id]:
        _fail('Retained Actor must own the original first sighting')
    resolved = response['resolved_issue_ids']
    if (not isinstance(resolved, list) or any(not isinstance(i, str) for i in resolved)
            or len(resolved) != len(set(resolved))
            or set(resolved) != {i['issue_id'] for i in packet['issues']}):
        _fail('Split must resolve exactly its assigned issues')
    original_actor = next((a for a in behavior.get('actors', []) if a['actor_id'] == old_id), {})
    points = original_actor.get('trajectory', [])
    assignments = response['trajectory_assignments']
    if not isinstance(assignments, list) or len(assignments) != len(points):
        _fail('Split must assign every original trajectory sample')
    indices = set()
    for item in assignments:
        if (not isinstance(item, dict) or set(item) != {'index', 'actor_id'}
                or type(item['index']) is not int or item['index'] not in range(len(points))
                or item['index'] in indices or item['actor_id'] not in ids):
            _fail('Invalid original trajectory assignment')
        indices.add(item['index'])
        point = points[item['index']]
        if point.get('frame_id') == packet['actor']['first_seen_frame_id'] and item['actor_id'] != old_id:
            _fail('Original first-sighting sample must retain its Actor identity')
        witness = observations[item['actor_id']].get(point.get('frame_id'))
        if not witness or witness['time_s'] != point['time_s']:
            _fail('Assigned trajectory sample has no reviewed vehicle sighting')
        if point.get('lane_id') is not None and witness['lane_id'] != point['lane_id']:
            _fail('Split cannot silently change a known trajectory lane')
    if any(old_id in event.get('involved_actor_ids', []) for event in behavior.get('events', [])):
        _fail('Split involving accepted events requires event-owner review')
    actors = []
    for desc in descriptors:
        aid = desc['actor_id']; rows = observations[aid]
        first = min(rows, key=lambda f: frames[f]['time_s'])
        actor = dict(desc, actor_type=packet['actor']['actor_type'] if aid == old_id else 'vehicle',
                     first_seen_frame_id=first,
                     evidence=[dict(status='observed', frame_id=fid, time_s=row['time_s'], reason=row['features'])
                               for fid, row in sorted(rows.items(), key=lambda pair: pair[1]['time_s'])])
        actor['vehicle_class'] = (dict(status='unknown', value=None, reason='Not visually distinguishable')
            if desc['vehicle_class'] == 'unknown' else dict(status='observed', value=desc['vehicle_class'],
                frame_id=first, time_s=rows[first]['time_s'], reason=rows[first]['features']))
        if rows[first]['lane_id'] is not None:
            actor['hypothesized_lane'] = rows[first]['lane_id']
        actors.append(actor)
    # Old combined identity prose/evidence is superseded in full and remains
    # hash-bound in before snapshots. Never clone polluted fields onto both cars.
    replacement = {a['actor_id']: a for a in actors}
    result = {'world': copy.deepcopy(world), 'registry': copy.deepcopy(registry)}
    for collection in (result['registry'], result['world']['actor_hypotheses']):
        target = next((a for a in collection if a['actor_id'] == old_id), None)
        if target is None:
            _fail('Split target missing from World/Registry')
        target.clear(); target.update(copy.deepcopy(replacement[old_id]))
        collection.append(copy.deepcopy(replacement[new_id]))
    authorized = copy.deepcopy(registry)
    next(a for a in authorized if a['actor_id'] == old_id).update(
        {k: replacement[old_id][k] for k in ('identity_description', 'first_seen_frame_id')})
    validate_registry(authorized, result['registry'])
    result['world']['evidence_epoch'] += 1
    for name, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')):
        for row in result['world'].get(name, []):
            if row[key] in resolved:
                row['resolution'] = 'resolved'
    return result


def validate_rebuilt(packet, response, rebuilt):
    result = materialize(packet, response)
    if result is None:
        _fail('Unconfirmed split cannot rebuild Behavior')
    original = read_json(packet['behavior_path'])
    ids = {packet['actor_id'], packet['new_actor_id']}
    actors = {a['actor_id']: a for a in rebuilt['actors']}
    expected = {a['actor_id'] for a in result['registry']} | {'ego'}
    if set(actors) != expected or len(actors) != len(rebuilt['actors']):
        _fail('Split rebuild must preserve membership and include the new Actor')
    for actor in original.get('actors', []):
        if actor['actor_id'] not in ids and actors.get(actor['actor_id']) != actor:
            _fail('Split rebuild changed an unrelated Actor')
    for actor in result['registry']:
        aid = actor['actor_id']
        if aid not in ids:
            continue
        if any(actors[aid].get(k) != actor[k] for k in ('identity_description', 'actor_type', 'color', 'vehicle_class')):
            _fail('Split rebuild did not use revised identity facts')
        observed = {row['frame_id']: vehicle for row in response['sightings']
                    for vehicle in row['vehicles'] if vehicle['actor_id'] == aid}
        samples = actors[aid]['trajectory']
        if len(samples) != len(observed) or {p.get('frame_id') for p in samples} != set(observed):
            _fail('Split rebuild lost or invented a reviewed vehicle sighting')
        for point in samples:
            lane = observed[point['frame_id']]['lane_id']
            if lane is not None and point.get('lane_id') != lane:
                _fail('Split rebuild changed a reviewed lane')
    old_actor = next((a for a in original.get('actors', []) if a['actor_id'] == packet['actor_id']), {})
    assigned_metrics = {(r['actor_id'], old_actor['trajectory'][r['index']]['frame_id']):
                        old_actor['trajectory'][r['index']] for r in response['trajectory_assignments']}
    for aid in ids:
        for point in actors[aid]['trajectory']:
            prior = assigned_metrics.get((aid, point['frame_id']), {})
            for key in ('speed_mps', 'position_ego_m'):
                value = point.get(key)
                if value is not None and not (isinstance(value, dict) and value.get('status') == 'unknown'):
                    if value != prior.get(key):
                        _fail('Identity rebuild cannot invent metric motion: ' + key)
    for item in response['trajectory_assignments']:
        old = old_actor['trajectory'][item['index']]
        current = next(p for p in actors[item['actor_id']]['trajectory'] if p['frame_id'] == old['frame_id'])
        for key in ('time_s', 'lane_id', 'road_id', 'ordinal_from_right', 'speed_mps', 'position_ego_m'):
            value = old.get(key)
            if value is not None and not (isinstance(value, dict) and value.get('status') == 'unknown'):
                if current.get(key) != value:
                    _fail('Split rebuild changed a known reassigned observation: ' + key)
    if packet['had_behavior'] and original.get('events') != rebuilt.get('events'):
        _fail('Split rebuild changed unrelated accepted events')
    resolved = set(response['resolved_issue_ids'])
    remaining = {r['limitation_id']: r for r in rebuilt.get('semantic_limitations', [])}
    for row in original.get('semantic_limitations', []):
        if row['limitation_id'] not in resolved and remaining.get(row['limitation_id']) != row:
            _fail('Split rebuild changed an unrelated limitation')
    if resolved & set(remaining):
        _fail('Split rebuild retained superseded identity issues')
