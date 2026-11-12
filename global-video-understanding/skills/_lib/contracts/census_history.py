"""Replay immutable census rounds without comparing ancestors to live state."""
import copy
from contextvars import ContextVar
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import read_json, raw_read_json, sha256

ARCHIVE = 'diagnostics/world_census_adjudication/certificates'
_replaying = ContextVar('census_replaying', default=())
_replay_cache = ContextVar('census_replay_cache', default=None)


def rounds(saved):
    return saved.get('rounds', [saved]) if saved else []


def replay(saved):
    from _lib.contracts import census_adjudication as contract
    body = dict(saved); signature = body.pop('sha256', None)
    if signature != digest(body):
        contract.fail('Census certificate changed')
    visiting = _replaying.get()
    if signature in visiting or len(visiting) >= 8:
        contract.fail('Census certificate lineage is cyclic or too deep')
    cache = _replay_cache.get()
    cache_token = None
    if cache is None:
        cache = {}; cache_token = _replay_cache.set(cache)
    if signature in cache:
        return cache[signature]
    token = _replaying.set((*visiting, signature))
    try:
        from _lib.runtime.world_adjudication import compile_response
        contract.verify_group_projection(saved)
        result = compile_response(saved['request'], saved['response'])
        if digest(result) != saved['result_sha256'] or saved.get('result') != contract.summary(result):
            contract.fail('Census certificate differs from its reviewed response')
        prior = parent(saved['request'])
        current = dict(saved, result=result)
        value = dict(current, rounds=[*rounds(prior), current])
        cache[signature] = value
        return value
    finally:
        _replaying.reset(token)
        if cache_token is not None:
            _replay_cache.reset(cache_token)


def parent(packet):
    from _lib.contracts import census_adjudication as contract
    link = packet.get('parent_certificate')
    if link is None:
        return None
    case = Path(packet['case_dir']).resolve()
    path = Path(link['path']).resolve()
    if (path.parent != (case/ARCHIVE).resolve() or not path.is_file()
            or sha256(path) != link['sha256']
            or packet['input_hashes'].get(str(path)) != link['sha256']):
        contract.fail('Census parent certificate binding changed')
    saved = read_json(path)
    if (path.name != saved.get('sha256', '')+'.json'
            or Path(saved['request']['case_dir']).resolve() != case
            or saved['request']['case_id'] != packet['case_id']):
        contract.fail('Census parent belongs to another Case')
    return replay(saved)


def aliases(saved):
    result = {}
    for item in rounds(saved):
        next_map = item['result'].get('canonical_actor_ids', {})
        result = {old:next_map.get(target, target) for old, target in result.items()}
        result.update(next_map)
    return result


def discharged(saved):
    from _lib.contracts.census_scope import active
    return {iid for item in rounds(saved) for key in ('resolved_issue_ids', 'excluded_issue_ids')
            for iid in item['result'][key]} | set(active(saved))


def remaining_records(case, saved, *, reopen=()):
    from _lib.contracts import census_adjudication as contract
    from _lib.contracts.census_identity import remap
    from _lib.contracts.census_scope import active
    if not set(reopen) <= set(active(saved)):
        contract.fail('Only active observation-only scope decisions can reopen')
    done, mapping = discharged(saved)-set(reopen), aliases(saved)
    result = []
    for row in contract.records(case):
        if row['issue_id'] not in done:
            row = copy.deepcopy(row)
            row['candidate_actor_ids'] = remap(row['candidate_actor_ids'], mapping)
            result.append(row)
    if saved and {r['issue_id'] for r in result} != set(saved['result']['remaining_issue_ids']) | set(reopen):
        contract.fail('Census remaining issues differ from verified history')
    return result


def confirmed(case, saved):
    """Keep Batch-local object identity and all previously proved sightings."""
    mapping = aliases(saved); objects = []; sightings = {}
    def sight(fid, aid):
        sightings.setdefault(fid, set()).add(mapping.get(aid, aid))
    def obj(batch, frame, object_id, aid):
        value = dict(batch_id=batch, frame_id=frame, object_id=object_id,
                     actor_id=mapping.get(aid, aid))
        if value not in objects:
            objects.append(value)
        sight(frame, aid)
    for path in sorted((Path(case)/'batches').glob('batch_*.json')):
        for frame in read_json(path).get('vehicle_census', []):
            for row in frame['vehicles']:
                if row['binding'] != 'unresolved':
                    obj(path.stem, frame['frame_id'], row['object_id'], row['actor_id'])
    for item in rounds(saved):
        records = {r['issue_id']:r for r in item['request']['records']}
        for binding in item['response']['bindings']:
            if binding['outcome'] in {'existing', 'new'}:
                row = records[binding['issue_id']]
                obj(row['batch_id'], row['frame_id'], row['object_id'],
                    item['result']['vehicle_ids'][binding['vehicle_key']])
        for vehicle in item['response']['vehicles']:
            if vehicle.get('role') != 'comparison':
                aid = item['result']['vehicle_ids'][vehicle['key']]
                for observation in vehicle['sightings']:
                    sight(observation['frame_id'], aid)
    return {fid:sorted(ids) for fid, ids in sightings.items()}, objects


def validate_scope(packet, saved):
    from _lib.contracts import census_adjudication as contract
    if (not (saved['result']['remaining_issue_ids'] or packet.get('scope_revocations')) or packet['mode'] != 'census_after_layout'
            or packet['layout_request'] is not None or packet['revision'] != saved['request']['revision']):
        contract.fail('Census continuation must preserve its accepted contract and layout')
    for name in ('world', 'registry'):
        if raw_read_json(packet[name+'_path']) != saved['result'][name]:
            contract.fail('Census continuation baseline differs from parent ' + name)
    if packet['actors'] != saved['result']['registry']:
        contract.fail('Census continuation Actor context differs from parent')
    sightings, objects = confirmed(packet['case_dir'], saved)
    if (packet['confirmed_sightings'] != sightings or packet['confirmed_objects'] != objects
            or packet.get('inherited_actor_aliases') != aliases(saved)):
        contract.fail('Census inherited identity constraints changed')


def preserve(saved, response, result):
    """A remaining issue cannot revise an accepted identity or known sighting."""
    from _lib.contracts import census_adjudication as contract
    if not saved:
        return
    old = {a['actor_id']:a for a in saved['result']['registry']}
    new = {a['actor_id']:a for a in result['registry']}
    # Only fields with explicit, compiled correction proofs may advance. The
    # parent certificate retains the previous registration and observations.
    corrected = {a['actor_id']: {c['field'] for c in a['corrections']}
                 for a in result.get('information_updates', {}).get('actors', [])}
    locked = {}; mapping = aliases(saved)
    for item in rounds(saved):
        for vehicle in item['response']['vehicles']:
            if vehicle.get('role') == 'comparison':
                continue
            aid = item['result']['vehicle_ids'][vehicle['key']]
            aid = mapping.get(aid, aid)
            locked.setdefault(aid, []).extend(vehicle['sightings'])
    for aid in locked:
        if (aid not in new or aid in result.get('canonical_actor_ids', {})
                or any(v['split_from_actor_id'] == aid for v in response['vehicles'])):
            contract.fail('Census continuation revises an accepted identity')
        for field in ('actor_type', 'identity_description', 'color', 'vehicle_class',
                      'road_reference', 'is_static', 'hypothesized_lane'):
            if (field in old[aid] and old[aid][field] != new[aid].get(field)
                    and field not in corrected.get(aid, set())):
                contract.fail('Census continuation changes an accepted identity field')
        for sight in locked[aid]:
            for other in sight['distinct_from_actor_ids']:
                other = mapping.get(other, other)
                if result.get('canonical_actor_ids', {}).get(other, other) == aid:
                    contract.fail('Census continuation merges previously distinct identities')
    for vehicle in response['vehicles']:
        if vehicle.get('role') == 'comparison':
            continue
        aid = result['vehicle_ids'][vehicle['key']]
        for sight in vehicle['sightings']:
            for prior in locked.get(aid, []):
                if sight['frame_id'] == prior['frame_id'] and (
                        sight['time_s'] != prior['time_s'] or prior['lane_id'] is not None
                        and sight['lane_id'] != prior['lane_id']):
                    contract.fail('Census continuation changes an accepted sighting')


def certificate_paths(saved):
    return {item['request']['parent_certificate']['path'] for item in rounds(saved)
            if item['request'].get('parent_certificate')}
