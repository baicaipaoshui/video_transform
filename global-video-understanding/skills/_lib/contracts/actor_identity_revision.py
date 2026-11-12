"""Narrow, evidence-bound extension of an existing Actor's first sighting."""
import copy
import math

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json, raw_read_json, sha256, validate_registry

REVISION = 'actor-identity-link.v1'


def issues(world, behavior):
    result = []
    for collection, key, document in (
            ('questions_to_verify', 'question_id', world),
            ('ambiguities', 'ambiguity_id', world),
            ('semantic_limitations', 'limitation_id', behavior)):
        for row in document.get(collection, []):
            if ('actor_identity' not in row.get('impact_domains', [])
                    or row.get('resolution') in {'resolved', 'confirmed', 'non_core'}):
                continue
            ids = row.get('actor_ids') or (row.get('identity_binding') or {}).get('actor_ids') or []
            result.append({'rule_id': 'identity.temporal_binding', 'owner': 'behavior',
                           'actor_ids': ids, 'actor_id': ids[0] if len(ids) == 1 else None,
                           'issue_id': row[key], 'collection': collection,
                           'impact_domains': row.get('impact_domains', []),
                           'evidence_frame_ids': sorted(set(row.get('evidence_frame_ids', [])) |
                               set((row.get('identity_binding') or {}).get('evidence_frame_ids', [])) |
                               {e['frame_id'] for e in row.get('evidence', []) if e.get('frame_id')}),
                           'message': row.get('description', row.get('question', str(row))),
                           'retry_kind': 'identity_link_review'})
    return result


def verify_request(packet):
    body = dict(packet)
    signature = body.pop('request_sha256', None)
    if packet.get('revision') not in {REVISION, 'actor-identity-split.v1'} or digest(body) != signature:
        raise ContractError('Identity review request digest changed')
    for path, signature in packet['input_hashes'].items():
        if sha256(path) != signature:
            raise ContractError('Identity review input changed: ' + path)


def materialize(packet, response):
    if packet.get('revision') == 'actor-identity-split.v1':
        from _lib.contracts.actor_identity_split import materialize as split
        return split(packet, response)
    verify_request(packet)
    fields = {'revision', 'request_sha256', 'actor_id', 'outcome', 'first_seen_frame_id',
              'evidence', 'resolved_issue_ids', 'reason'}
    if (not isinstance(response, dict) or set(response) != fields
            or response['revision'] != REVISION
            or response['request_sha256'] != packet['request_sha256']
            or response['actor_id'] != packet['actor_id']
            or response['outcome'] not in {'same_vehicle', 'different_vehicle', 'insufficient_evidence'}
            or not isinstance(response['reason'], str) or not response['reason'].strip()):
        raise ContractError('Invalid identity-link response')
    frames = {f['frame_id']: f for f in packet['frames']}
    evidence = response['evidence']
    if not isinstance(evidence, list):
        raise ContractError('Identity evidence must be an array')
    seen = set()
    for row in evidence:
        if (not isinstance(row, dict) or set(row) != {'frame_id', 'time_s', 'features', 'distinct_from_actor_ids'}
                or row['frame_id'] not in frames or row['frame_id'] in seen
                or type(row['time_s']) not in (int, float) or not math.isfinite(row['time_s'])
                or row['time_s'] != frames[row['frame_id']]['time_s']
                or not isinstance(row['features'], str) or not row['features'].strip()
                or not isinstance(row['distinct_from_actor_ids'], list)
                or not set(row['distinct_from_actor_ids']) <= set(packet['other_actor_ids'])):
            raise ContractError('Identity evidence frame/time/distinction invalid')
        required = set(packet['co_visible_actor_ids'].get(row['frame_id'], []))
        if not required <= set(row['distinct_from_actor_ids']):
            raise ContractError('Identity review must distinguish co-visible registered vehicles')
        seen.add(row['frame_id'])
    if response['outcome'] != 'same_vehicle':
        if response['first_seen_frame_id'] is not None or response['resolved_issue_ids']:
            raise ContractError('Unconfirmed identity cannot modify facts')
        return None
    first = response['first_seen_frame_id']
    old_first = packet['actor']['first_seen_frame_id']
    if (first not in seen or old_first not in seen or len(seen) < 3
            or frames[first]['time_s'] >= frames[old_first]['time_s']
            or first != min(seen, key=lambda fid: frames[fid]['time_s'])
            or not any(frames[f]['time_s'] > frames[old_first]['time_s'] for f in seen)):
        raise ContractError('Identity extension requires earlier, original and later sightings')
    # A sparse pair of endpoints cannot stand in for the intervening review.
    end = max(frames[f]['time_s'] for f in seen)
    if not {fid for fid, frame in frames.items() if frames[first]['time_s'] <= frame['time_s'] <= end} <= seen:
        raise ContractError('Identity evidence omits a supplied transition frame')
    resolved = response['resolved_issue_ids']
    allowed = {i['issue_id'] for i in packet['issues']}
    if not isinstance(resolved, list) or len(resolved) != len(set(resolved)) or set(resolved) != allowed:
        raise ContractError('Identity response must resolve exactly the assigned identity issues')
    if not set(packet.get('required_frame_ids', [])) <= seen:
        raise ContractError('Identity response omits issue-bound evidence')
    world = raw_read_json(packet['world_path'])
    registry = raw_read_json(packet['registry_path'])
    new_registry, new_world = copy.deepcopy(registry), copy.deepcopy(world)
    for rows in (new_registry, new_world['actor_hypotheses']):
        matches = [a for a in rows if a['actor_id'] == packet['actor_id']]
        if len(matches) != 1 or matches[0]['first_seen_frame_id'] != old_first:
            raise ContractError('Identity source World/Registry mismatch')
        matches[0]['first_seen_frame_id'] = first
    # The ordinary identity lock remains intact. Its baseline is advanced only
    # by the single field authorized above; all other changes still fail.
    authorized = copy.deepcopy(registry)
    next(a for a in authorized if a['actor_id'] == packet['actor_id'])['first_seen_frame_id'] = first
    validate_registry(authorized, new_registry)
    new_world['evidence_epoch'] += 1
    for collection, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')):
        for row in new_world.get(collection, []):
            if row[key] in resolved:
                row['resolution'] = 'resolved'
    return {'world': new_world, 'registry': new_registry}


def validate_rebuilt(packet, response, rebuilt):
    """A fresh Behavior must incorporate the link without losing prior facts."""
    if packet.get('revision') == 'actor-identity-split.v1':
        from _lib.contracts.actor_identity_split import validate_rebuilt as split
        return split(packet, response, rebuilt)
    original = read_json(packet['behavior_path'])
    by_id = {a['actor_id']: a for a in rebuilt['actors']}
    if set(by_id) != {a['actor_id'] for a in original['actors']}:
        raise ContractError('Identity rebuild changed Actor membership')
    for old in original['actors']:
        new = by_id[old['actor_id']]
        if old['actor_id'] != packet['actor_id'] and new != old:
            raise ContractError('Identity rebuild changed an unrelated Actor')
        for key in ('actor_type', 'identity_description', 'is_static', 'simulation_role'):
            if old.get(key) != new.get(key):
                raise ContractError('Identity rebuild changed unrelated Actor facts')
        points = {p.get('frame_id', p.get('time_s')): p for p in new.get('trajectory', [])}
        for point in old.get('trajectory', []):
            current = points.get(point.get('frame_id', point.get('time_s')))
            if current is None:
                raise ContractError('Identity rebuild omitted an accepted observation')
            for key in ('time_s', 'lane_id', 'road_id', 'ordinal_from_right', 'speed_mps', 'position_ego_m'):
                value = point.get(key)
                if value is not None and not (isinstance(value, dict) and value.get('status') == 'unknown'):
                    if current.get(key) != value:
                        raise ContractError('Identity rebuild changed known observation: ' + key)
    actor = by_id[packet['actor_id']]
    if not actor['trajectory'] or actor['trajectory'][0].get('frame_id') != response['first_seen_frame_id']:
        raise ContractError('Identity rebuild omitted confirmed early sighting')
    times = {f['frame_id']: f['time_s'] for f in packet['frames']}
    prefix = {e['frame_id'] for e in response['evidence']
              if e['time_s'] < times[packet['actor']['first_seen_frame_id']]}
    if not prefix <= {p.get('frame_id') for p in actor['trajectory']}:
        raise ContractError('Identity rebuild omitted confirmed transition observations')
    if original.get('events') != rebuilt.get('events'):
        raise ContractError('Identity rebuild changed accepted events')
    resolved = set(response['resolved_issue_ids'])
    remaining = {r['limitation_id']: r for r in rebuilt.get('semantic_limitations', [])}
    for row in original.get('semantic_limitations', []):
        if row['limitation_id'] not in resolved and remaining.get(row['limitation_id']) != row:
            raise ContractError('Identity rebuild changed an unrelated limitation')
    if resolved & set(remaining):
        raise ContractError('Identity rebuild retains the superseded identity limitation')
