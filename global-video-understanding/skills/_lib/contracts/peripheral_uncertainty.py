"""Source-bound simulation choices for unresolved, non-core census observations.

The receipt records execution assumptions. It never changes a World binding or
turns an unresolved observation into a confirmed physical identity.
"""
import copy
import re
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'peripheral-uncertainty.v1'
KEY = 'peripheral_uncertainty'
ADOPTION = 'state/peripheral_uncertainty.adoption.json'
HANDLINGS = {'assumed_existing', 'observation_only_unknown', 'single_representative'}


def fail(message):
    error = ContractError(message, rule_id='execution_scope.peripheral_uncertainty')
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
            or not isinstance(value.get('source_hashes'), dict)):
        fail('Peripheral uncertainty adoption is malformed')
    for name, expected in value['source_hashes'].items():
        if not (case/name).is_file() or sha256(case/name) != expected:
            fail('Peripheral uncertainty adoption source changed: '+name)
    return True


def adopt(case):
    """Explicitly opt a copied, unfinished Case into offline replay."""
    case = Path(case)
    if enabled(case):
        return read_json(case/ADOPTION) if (case/ADOPTION).is_file() else None
    state = read_json(case/'state/progress.json')
    if (state.get('status') in {'done', 'done_degraded'}
            or (case/'state/behavior.json').is_file()
            or not (case/'state/behavior.candidate.json').is_file()):
        fail('Peripheral uncertainty adoption requires an unfinished Behavior candidate')
    names = ('case.json', 'state/execution_policy.json', 'state/actor_registry.json',
             'state/world_context.json', 'state/census_adjudication.accepted.json')
    body = dict(revision=REVISION, case_id=case.name,
                source_hashes={name:sha256(case/name) for name in names})
    value = dict(body, sha256=digest(body))
    atomic_json(case/ADOPTION, value)
    return value


def _mentioned(value, names):
    if isinstance(value, str):
        return any(name in value for name in names)
    if isinstance(value, list):
        return any(_mentioned(item, names) for item in value)
    if isinstance(value, dict):
        return any(_mentioned(item, names) for item in value.values())
    return False


def _pure_limits(behavior, ids):
    from _lib.contracts.census_relevance import observation_issue_only
    for item in behavior.get('semantic_limitations', []):
        if not set(item.get('census_issue_ids', [])) & ids:
            continue
        if (item.get('execution_effect') not in {None, 'bounded_uncertainty'}
                or set(item.get('impact_domains', [])) - {'actor_identity', 'actor_lane'}
                or item.get('world_paths') or item.get('related_world_paths')
                or item.get('affected_event_ids') or item.get('scope_binding')):
            return False
        # An Actor-bound limitation may describe only the exact candidate
        # identity. The receipt never discharges motion or event uncertainty.
        if item.get('actor_ids') and set(item['actor_ids']) - {a['actor_id'] for a in behavior['actors']}:
            return False
        if not item.get('actor_ids') and not observation_issue_only(item):
            return False
    return True


def _color(text):
    for value, cues in (('blue', ('蓝', '青', 'blue', 'cyan')),
                        ('red', ('红', 'red')),
                        ('black', ('黑', '深色', 'dark', 'black')),
                        ('white', ('白', 'white'))):
        if any(cue in text.lower() for cue in cues):
            return value
    return None


def _remote(text):
    text = text.lower()
    return (any(cue in text for cue in ('远处', '地平线', '消失点', 'distant', 'horizon', 'far'))
            and any(cue in text for cue in ('轮廓', '点状', '细节不可辨', '仅见', 'tiny', 'silhouette', 'blob')))


def _same_direction(text):
    return (any(cue in text.lower() for cue in ('同向', '车尾', 'ahead', 'rear'))
            and not any(cue in text.lower() for cue in ('对向', '隔离 barrier 外', 'opposite')))


def _right(text):
    return any(cue in text.lower() for cue in ('右侧', '偏右', '右前', 'right'))


def _depth(text):
    text = text.lower()
    if any(cue in text for cue in ('近处', '近距', 'near')):
        return 'near'
    if any(cue in text for cue in ('远处', '远距', '远端', 'far', 'distant')):
        return 'far'
    if any(cue in text for cue in ('中距', '中等距离', 'mid-distance')):
        return 'middle'
    return None


def _explicit_lane(text):
    text = text.lower()
    for lane, cues in (('rightmost', ('最右车道', '最右侧车道', 'rightmost lane')),
                       ('middle', ('中间车道', '中车道', 'middle lane')),
                       ('leftmost', ('最左车道', '最左侧车道', 'leftmost lane'))):
        if any(cue in text for cue in cues):
            return lane
    return None


def _lane(world, behavior, frame_id):
    road = world['world']['road']
    if world['world'].get('scene_type') != 'highway' or world['world'].get('junctions'):
        return None
    ego = next((a for a in behavior['actors'] if a['actor_id'] == 'ego'), {})
    point = next((p for p in ego.get('trajectory', []) if p.get('frame_id') == frame_id), {})
    ego_lane = next((l for l in road.get('lanes', []) if l['lane_id'] == point.get('lane_id')), None)
    if not ego_lane or type(ego_lane.get('ordinal_from_right')) is not int:
        return None
    candidates = [l for l in road.get('lanes', [])
                  if l.get('road_id') == ego_lane.get('road_id')
                  and l.get('travel_direction') == 'same_as_ego'
                  and type(l.get('ordinal_from_right')) is int
                  and l['ordinal_from_right'] < ego_lane['ordinal_from_right']]
    return min(candidates, key=lambda l:l['ordinal_from_right']) if candidates else None


def _registered_lane_assumptions(world, behavior, registry, frames):
    """Choose only a bound single-frame, non-interacting lane for execution."""
    lanes = {l['lane_id']:l for l in world['world']['road'].get('lanes', [])}
    ego = next((a for a in behavior['actors'] if a['actor_id'] == 'ego'), None)
    if not ego:
        return {}
    protected = {aid for e in behavior.get('events', []) for aid in e.get('involved_actor_ids', [])}
    protected.update(p.get('actor_id') for p in behavior.get('executor_primitives', []) if p.get('actor_id'))
    result = {}
    for actor in behavior['actors']:
        aid = actor['actor_id']
        points = actor.get('trajectory', [])
        source = registry.get(aid)
        if (aid in protected or actor.get('simulation_role') != 'background'
                or actor.get('actor_type') != 'vehicle' or not source
                or len(points) != 1 or points[0].get('lane_id') is not None
                or not any(e.get('status') == 'observed' and e.get('frame_id') == points[0]['frame_id']
                           for e in source.get('evidence', []))):
            continue
        point = points[0]
        detail = point.get('detail', '').lower()
        if any(cue in detail for cue in ('对向', 'opposite', '隔离 barrier 外')):
            continue
        ego_point = next((p for p in ego.get('trajectory', [])
                          if p.get('frame_id') == point['frame_id']), None)
        ego_lane = lanes.get((ego_point or {}).get('lane_id'))
        if not ego_lane:
            continue
        lane = lanes.get(source.get('hypothesized_lane'))
        selection = 'registered_lane_hypothesis'
        if lane is None and any(cue in detail for cue in ('正前方偏右', '右前方远处', 'ahead-right')):
            lane = next((l for l in lanes.values()
                         if l.get('road_id') == ego_lane.get('road_id')
                         and l.get('ordinal_from_right') == ego_lane.get('ordinal_from_right')-1), None)
            selection = 'adjacent_right_from_observed_ego_lane'
        if (not lane or lane.get('road_id') != ego_lane.get('road_id')
                or lane.get('travel_direction') != 'same_as_ego'):
            continue
        result[aid] = dict(actor_id=aid, frame_id=point['frame_id'], time_s=point['time_s'],
            lane_id=lane['lane_id'], selection=selection, identity_resolution='registered',
            assumption='Single-frame lane is unconfirmed; use this supported lane only for simulation')
    return result


def build(case, behavior, pending, frames, saved, review, choices):
    if not enabled(case) or not choices:
        return None
    from _lib.contracts.census_history import rounds
    case = Path(case)
    registry = {a['actor_id']:a for a in saved['result']['registry']}
    actors = {a['actor_id']:a for a in behavior['actors']}
    if any(a.get('actor_type') == 'vehicle' and aid not in actors for aid,a in registry.items()):
        return None
    world = saved['result']['world']
    bindings = {b['issue_id']:b for r in rounds(saved) for b in r['response']['bindings']}
    rows = {r['issue_id']:r for r in (review or {}).get('decisions', [])}
    used = [behavior.get(k, []) for k in ('events', 'executor_primitives', 'video_fidelity', 'challenge_resolutions')]
    records = {}; proxy_groups = {}
    last_time = max(f['time_s'] for f in frames.values())
    for choice in choices:
        iid = choice['issue_id']
        if choice['handling'] != 'needs_evidence' or iid not in pending or iid not in rows:
            continue
        obs = pending[iid]; row = rows[iid]
        if (row['outcome'] not in {'unknown', 'needs_identity'} or row['affected_event_ids']
                or row.get('scope_proof') or obs.get('distinction')
                or _mentioned(used, {iid, obs['object_id']})
                or not _pure_limits(behavior, {iid})):
            continue
        assessment = row.get('identity_assessment') or {}
        if assessment.get('alternative_actor_ids') or assessment.get('counterevidence'):
            continue
        candidates = obs.get('candidate_actor_ids', [])
        observed = [aid for aid in candidates if aid in actors and aid in registry
                    and registry[aid].get('actor_type') == 'vehicle'
                    and any(e.get('status') == 'observed' and e.get('frame_id') == obs['frame_id']
                            for e in registry[aid].get('evidence', []))
                    and any(p.get('frame_id') == obs['frame_id'] for p in actors[aid].get('trajectory', []))]
        if (len(observed) == 1 and assessment.get('candidate_status') != 'rejected'
                and not any(r.get('actor_id') == observed[0]
                            for r in bindings.get(iid, {}).get('candidate_refutations', []))):
            aid = observed[0]
            records[iid] = dict(handling='assumed_existing', actor_id=aid,
                source_frame_id=obs['frame_id'], source_object_id=obs['object_id'],
                identity_resolution='unresolved',
                world_binding_reason=bindings[iid]['reason'],
                assumption='Use the registered same-frame Actor for simulation; cross-layer identity remains unresolved')
            continue
        text = obs.get('description', '')+' '+row.get('reason', '')
        if (not observed and _remote(text) and not _mentioned(used, set(candidates))
                and not any(aid in actors and any(p.get('frame_id') == obs['frame_id']
                            for p in actors[aid].get('trajectory', [])) for aid in candidates)):
            records[iid] = dict(handling='observation_only_unknown',
                source_frame_id=obs['frame_id'], source_object_id=obs['object_id'],
                identity_resolution='unresolved',
                fidelity_loss='A distant unidentifiable silhouette may be absent from the simulation; interaction is unknown')
            continue
        if (frames[obs['frame_id']]['time_s'] != last_time
                or not _same_direction(text) or not _right(text)
                or assessment.get('candidate_status') not in {None, 'rejected'}):
            continue
        color = _color(obs.get('description', ''))
        lane = _lane(world, behavior, obs['frame_id'])
        if not color or not lane:
            continue
        if any(aid in registry and (registry[aid].get('color') or '').lower() == color
               and any(p.get('frame_id') == obs['frame_id'] for p in actors[aid].get('trajectory', []))
               for aid in candidates):
            continue
        group = (obs['frame_id'], color, 'right')
        proxy_groups.setdefault(group, []).append(iid)
    # Two co-visible detections in one Batch cannot be folded into one Actor.
    # A different Batch may describe the same target with different words, but
    # explicit near/far disagreement is still a physical-identity conflict.
    co_visible = {}
    for iid, record in records.items():
        if record['handling'] == 'assumed_existing':
            key = (pending[iid]['batch_id'], pending[iid]['frame_id'], record['actor_id'])
            co_visible.setdefault(key, []).append(iid)
    for ids in co_visible.values():
        if len(ids) > 1:
            for iid in ids:
                records.pop(iid)
    proxies = {}
    for (fid,color,zone), ids in proxy_groups.items():
        # Two detections from one Batch could be two physical cars. Only one
        # observation per independent Batch may share this execution proxy.
        batches = [pending[iid]['batch_id'] for iid in ids]
        if len(batches) != len(set(batches)):
            continue
        depths = {_depth(pending[iid].get('description', '')) for iid in ids} - {None}
        explicit_lanes = {_explicit_lane(pending[iid].get('description', '')) for iid in ids} - {None}
        if len(depths) > 1 or len(explicit_lanes) > 1:
            continue
        lane = _lane(world, behavior, fid)
        if explicit_lanes:
            required_ordinal = {'rightmost': 0, 'middle': 1, 'leftmost': 2}[next(iter(explicit_lanes))]
            ego = next(a for a in behavior['actors'] if a['actor_id'] == 'ego')
            ego_point = next(p for p in ego['trajectory'] if p['frame_id'] == fid)
            ego_lane = next(l for l in world['world']['road']['lanes']
                            if l['lane_id'] == ego_point['lane_id'])
            lane = next((l for l in world['world']['road']['lanes']
                         if l.get('road_id') == ego_lane.get('road_id')
                         and l.get('travel_direction') == 'same_as_ego'
                         and l.get('ordinal_from_right') == required_ordinal), None)
            if lane is None:
                continue
        aid = 'execution_proxy_'+digest(dict(frame_id=fid,color=color,zone=zone,ids=sorted(ids)))[:12]
        if aid in actors:
            fail('Execution proxy ID collides with a registered Actor')
        proxies[aid] = dict(actor_id=aid, issue_ids=sorted(ids), frame_id=fid,
            time_s=frames[fid]['time_s'], lane_id=lane['lane_id'], color=color,
            identity_resolution='unresolved',
            assumption='One visible same-direction terminal-frame car; rightmost supported lane and unobserved motion are simulation choices')
        for iid in ids:
            records[iid] = dict(handling='single_representative', actor_id=aid,
                source_frame_id=fid, source_object_id=pending[iid]['object_id'],
                identity_resolution='unresolved', assumption=proxies[aid]['assumption'])
    lane_assumptions = _registered_lane_assumptions(world, behavior, registry, frames)
    if not records and not lane_assumptions:
        return None
    body = dict(revision=REVISION, case_dir=str(case.resolve()),
        certificate_sha256=saved['sha256'], behavior_sha256=digest(behavior),
        world_sha256=digest(world), registry_sha256=digest(saved['result']['registry']),
        policy_sha256=sha256(case/'state/execution_policy.json'),
        adoption_sha256=sha256(case/ADOPTION) if (case/ADOPTION).is_file() else None,
        records=records, proxies=proxies, lane_assumptions=lane_assumptions,
        review=copy.deepcopy(review),
        fidelity_loss='Optional identity and motion details are approximated; no irrelevance is claimed')
    return dict(body, sha256=digest(body))


def choice(iid, observation, record, proxies):
    result = dict(issue_id=iid, candidate_actor_ids=observation['candidate_actor_ids'],
        observation_frame_id=observation['frame_id'], outcome='unresolved',
        handling=record['handling'], identity_resolution='unresolved',
        peripheral_assumption_sha256=digest(record))
    if record['handling'] == 'assumed_existing':
        result['actor_id'] = record['actor_id']
    if record['handling'] == 'single_representative':
        proxy = proxies[record['actor_id']]
        result.update(actor_id=proxy['actor_id'], lane_id=proxy['lane_id'], time_s=proxy['time_s'])
    return result


def validate_projection(world, behavior, frames, receipt):
    program = (receipt or {}).get(KEY)
    choices = [r for r in (receipt or {}).get('choices', [])
               if r.get('peripheral_assumption_sha256')]
    if not program and not choices:
        return None
    if not program or frames is None:
        fail('Peripheral assumptions require a complete source-bound receipt')
    from _lib.contracts import census_adjudication, behavior_census_scope
    case = Path(program['case_dir'])
    saved = census_adjudication.proof(case)
    pending = behavior_census_scope.pending(case, saved)
    initial = [dict(issue_id=iid, handling='needs_evidence') for iid in sorted(program['records'])]
    expected = build(case, behavior, pending, {f['frame_id']:f for f in frames},
                     saved, program['review'], initial)
    if expected is None:
        fail('Peripheral assumption no longer matches its source observations')
    expected_choices = [choice(iid, pending[iid], record, expected['proxies'])
                        for iid,record in sorted(expected['records'].items())]
    if (expected != program or digest(world) != program['world_sha256']
            or sorted(choices, key=lambda r:r['issue_id']) != expected_choices):
        fail('Peripheral assumption source, Actor or action changed')
    return program


def disposes(item, receipt):
    program = (receipt or {}).get(KEY)
    if not program:
        return False
    ids = item.get('census_issue_ids', [])
    records = program['records']
    if (not ids or not set(ids) <= set(records) or len(ids) != len(set(ids))
            or item.get('execution_effect') not in {None, 'bounded_uncertainty'}
            or set(item.get('impact_domains', [])) - {'actor_identity', 'actor_lane'}
            or any(item.get(k) for k in ('world_paths', 'related_world_paths', 'actor_id',
                'affected_actor_ids', 'identity_binding', 'localization', 'involved_actor_ids',
                'affected_event_ids', 'scope_binding', 'event_id', 'event_ids'))):
        return False
    retained = {records[i]['actor_id'] for i in ids if records[i]['handling'] == 'assumed_existing'}
    return set(item.get('actor_ids', [])) <= retained
