"""Observation-level relevance; deferred distinctions never certify identity.

World supplies provisional scope. The normal Behavior review discharges each
obligation using the complete sampled window, without deleting candidate Actors.
"""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json

REVISION = 'world-census-adjudication.v6'
BEHAVIOR = 'behavior-census-scope.v3'
RETAIN_BEHAVIOR = 'behavior-census-scope.v4'
EXECUTION = 'census-execution.v2'


def retain_enabled(case):
    policy = Path(case)/'state/execution_policy.json'
    return policy.is_file() and read_json(policy).get('behavior_census_scope') == RETAIN_BEHAVIOR


def fail(message, issue_id=None):
    error = ContractError(message, rule_id='identity.census.relevance')
    error.diagnostics = dict(rule_id=error.rule_id, issue_ids=[issue_id] if issue_id else [],
                             retry_kind='scope_adjudication')
    raise error


def subjects(packet):
    rows = {r['issue_id']: dict(r) for r in packet['records']}
    frames = {f['frame_id'] for f in packet['frames']}
    for fid, ids in packet['confirmed_sightings'].items():
        if fid in frames:
            for aid in ids:
                iid = f'confirmed:{fid}:{aid}'
                rows[iid] = dict(issue_id=iid, frame_id=fid, actor_id=aid,
                                 candidate_actor_ids=[aid], registered=True)
    return rows


def extend_schema(schema):
    schema['required'].append('relevance')
    schema['properties']['relevance'] = dict(type='array', items=dict(type='object',
        additionalProperties=False, required=['issue_id','assessment','reason','evidence_frame_ids'],
        properties=dict(issue_id=dict(type='string', minLength=1),
            assessment=dict(enum=['retain','background_candidate','unknown']),
            reason=dict(type='string', minLength=1), evidence_frame_ids=dict(type='array',
                minItems=1, uniqueItems=True, items=dict(type='string', minLength=1)))))


def compile_scope(packet, response):
    expected = subjects(packet)
    rows = {r['issue_id']: r for r in response['relevance']}
    if len(rows) != len(response['relevance']) or set(rows) != set(expected):
        fail('World relevance must cover every observation and registered frame object exactly once')
    frames = {f['frame_id'] for f in packet['frames']}
    for iid, row in rows.items():
        if (not row['reason'].strip() or expected[iid]['frame_id'] not in row['evidence_frame_ids']
                or not set(row['evidence_frame_ids']) <= frames):
            fail('World relevance lacks its source frame or cites foreign evidence', iid)
    return copy.deepcopy(response['relevance'])


def pending(saved):
    """Pair obligations are additional to unresolved Batch observations."""
    from _lib.contracts.census_history import rounds
    result = {}
    for item in rounds(saved):
        for issue in item['result'].get('pending_distinctions', []):
            result[issue['issue_id']] = copy.deepcopy(issue)
    return result


def obligations(packet, issues):
    result = []
    for issue in issues:
        # New/split identities must pass their original complete proof first.
        if not issue['actor_id']:
            fail('A new identity cannot defer its duplicate checks')
        for other in issue['missing_actor_ids']:
            iid = f"distinction:{issue['frame_id']}:{issue['actor_id']}:{other}"
            result.append(dict(issue_id=iid, frame_id=issue['frame_id'], time_s=issue['time_s'],
                actor_id=issue['actor_id'], compared_actor_id=other, candidate_actor_ids=[other],
                distinction=True, identity_resolution='pending', evidence_frame_ids=[issue['frame_id']],
                field_path=issue['field_path']))
    return result


def enabled(case):
    from _lib.runtime.fact_versions import census_path
    path = census_path(case)
    if path.is_file():
        # Consumers replay the certificate before making decisions. This is
        # only revision selection; it grants no exclusion on its own.
        return read_json(path).get('request', {}).get('revision') == REVISION
    policy = Path(case)/'state/execution_policy.json'
    return policy.is_file() and read_json(policy).get('world_census_adjudication') == REVISION


def _mentions(node, ids):
    if isinstance(node, str): return node in ids
    if isinstance(node, list): return any(_mentions(v, ids) for v in node)
    if isinstance(node, dict): return any(_mentions(v, ids) for v in node.values())
    return False


def observation_issue_only(item):
    """An observation question is not authority over an Actor or road fact."""
    ids = item.get('census_issue_ids')
    return (isinstance(ids, list) and bool(ids)
        and all(isinstance(iid, str) and iid for iid in ids) and len(set(ids)) == len(ids)
        and bool(item.get('impact_domains'))
        and set(item['impact_domains']) <= {'actor_identity', 'actor_lane'}
        and item.get('execution_effect') in {None, 'bounded_uncertainty'}
        and not any(item.get(k) for k in ('actor_id', 'actor_ids', 'affected_actor_ids',
            'identity_binding', 'scope_binding', 'world_paths', 'related_world_paths',
            'localization', 'affected_event_ids', 'event_id', 'event_ids', 'involved_actor_ids')))


def observation_issue_disposed(item, receipt):
    """Consume source-bound observation dispositions, not Actor identity claims.

    Callers verify the receipt and replay its source evidence before projection.
    Neither mechanism resolves an Actor identity. Old revisions stay unchanged.
    """
    if not receipt or receipt.get('revision') != EXECUTION:
        return False
    from _lib.contracts.peripheral_uncertainty import disposes as peripheral_disposes
    if peripheral_disposes(item, receipt):
        return True
    from _lib.contracts.execution_assumptions import disposes as assumption_disposes
    if assumption_disposes(item, receipt):
        return True
    from _lib.contracts.unused_association import disposes as unused_disposes
    if unused_disposes(item, receipt):
        return True
    from _lib.contracts.early_association import disposes
    if disposes(item, receipt):
        return True
    rows = {r['issue_id']:r for r in receipt['choices']}
    if observation_issue_only(item) and all(iid in rows and
            rows[iid]['handling'] in {'retained_unresolved', 'observation_excluded'} and
            rows[iid].get('identity_resolution') == 'unresolved'
            for iid in item['census_issue_ids']):
        return True
    if receipt.get('execution_assumptions') and observation_issue_only(item):
        # Compose dispositions without treating a grouped observation sentence
        # as an unbound Actor dispute. No unknown ID or mixed domain is dropped.
        return all(iid in rows and (
            rows[iid]['handling'] == 'observation_excluded'
            and rows[iid].get('identity_resolution') == 'unresolved'
            or unused_disposes(dict(item, census_issue_ids=[iid]), receipt)
            or assumption_disposes(dict(item, census_issue_ids=[iid]), receipt))
            for iid in item['census_issue_ids'])
    if not observation_issue_only(item):
        # An explicitly proved optional early association is narrower than an
        # Actor identity dispute. Never remove a generic/mixed Actor limitation.
        ids = item.get('census_issue_ids', [])
        actors = item.get('actor_ids', [])
        if (not ids or not actors or not isinstance(actors, list)
                or len(set(actors)) != len(actors)
                or item.get('execution_effect') != 'bounded_uncertainty'
                or not observation_issue_only({k:v for k,v in item.items() if k != 'actor_ids'})
                or any(iid not in rows or set(actors) != {rows[iid].get('independent_actor_id')}
                       for iid in ids)):
            return False
    return all(iid in rows and rows[iid]['handling'] == 'observation_excluded'
               and rows[iid].get('identity_resolution') == 'unresolved'
               for iid in item['census_issue_ids'])


def grouped_views(behavior, observations, rows):
    """Local checking views, never rewritten observations or accepted facts.

    Callers must validate every supplied row before issuing a receipt. A group
    can discharge its limitation only when all independently checked choices
    survive observation_issue_disposed. Missing/mixed members fail closed.
    """
    groups = []
    for item in behavior.get('semantic_limitations', []):
        ids = item.get('census_issue_ids')
        if not isinstance(ids, list) or len(ids) < 2 or not item.get('actor_ids'):
            continue
        aid = item['actor_ids']
        if (len(aid) != 1 or len(ids) != len(set(ids))
                or not observation_issue_only({k:v for k,v in item.items() if k != 'actor_ids'})
                or item.get('execution_effect') != 'bounded_uncertainty'
                or any(iid not in observations or iid not in rows
                       or observations[iid].get('distinction')
                       or observations[iid].get('candidate_actor_ids') != aid
                       or rows[iid].get('outcome') != 'irrelevant'
                       or ((rows[iid].get('scope_proof') or {}).get('candidate_independence') or {}).get('actor_id') != aid[0]
                       for iid in ids)):
            # Other kinds of grouped limitations retain their existing checks.
            continue
        groups.append(item)
    views = {}
    for iid in rows:
        applicable = [item for item in groups if iid in item['census_issue_ids']]
        if applicable:
            view = copy.deepcopy(behavior)
            for original, item in zip(behavior['semantic_limitations'], view['semantic_limitations']):
                if original in applicable:
                    item['census_issue_ids'] = [iid]
            views[iid] = view
    return views


def candidate_independence(case, behavior, observation, proof, frames, world, *, registry=None):
    """Prove an optional early association, never an assumed identity or motion."""
    claim = proof.get('candidate_independence')
    if 'candidate_independence' not in proof:
        return {}
    iid = observation['issue_id']
    candidates = observation['candidate_actor_ids']
    if (observation.get('distinction') or len(candidates) != 1
            or proof['rule_id'] != 'unrelated_observation' or not isinstance(claim, dict)
            or set(claim) != {'actor_id','trajectory_frame_ids','observation_used','reason'}
            or claim['actor_id'] != candidates[0] or claim['observation_used'] is not False
            or not isinstance(claim['reason'], str) or not claim['reason'].strip()):
        fail('Optional early association requires one unchanged independent candidate', iid)
    aid = candidates[0]
    actor = next((a for a in behavior['actors'] if a['actor_id'] == aid), None)
    if registry is None: registry = read_json(Path(case)/'state/actor_registry.json')
    registered = next((a for a in registry if a['actor_id'] == aid), None)
    trajectory = (actor or {}).get('trajectory', [])
    fids = claim['trajectory_frame_ids']
    if (not registered or not trajectory or not isinstance(fids, list)
            or any(not isinstance(fid, str) for fid in fids) or len(set(fids)) != len(fids)
            or set(fids) != {p['frame_id'] for p in trajectory} or not set(fids) <= set(frames)
            or registered['first_seen_frame_id'] not in frames
            or frames[observation['frame_id']]['time_s'] >= frames[registered['first_seen_frame_id']]['time_s']
            or any(frames[fid]['time_s'] <= frames[observation['frame_id']]['time_s'] for fid in fids)):
        fail('Early association cannot supply or replace an accepted trajectory point', iid)
    first_time = min(frames[fid]['time_s'] for fid in fids)
    for event in behavior.get('events', []) + behavior.get('executor_primitives', []):
        if not _mentions(event, {aid}):
            continue
        if event.get('type') == 'initial_state' and event.get('actor_id') == aid:
            from _lib.behavior.compile import derive_initial_states
            derived = derive_initial_states(dict(actors=[actor], executor_primitives=[]))['executor_primitives']
            if (len(derived) == 1 and all(event.get(k) == derived[0][k]
                    for k in ('lane_id','position_ego_m','speed_kmh'))
                    and not any(k in event for k in ('time_s','time_range_s','evidence'))):
                continue  # Its initial point is the unchanged later trajectory.
        interval = event.get('time_range_s')
        start = interval[0] if isinstance(interval, list) and interval else event.get('time_s')
        evidence = event.get('evidence', [])
        if (type(start) not in (int, float) or start < first_time
                or any(s.get('actor_id') == aid and
                       (s.get('frame_id') not in frames or frames[s['frame_id']]['time_s'] < first_time)
                       for s in evidence if isinstance(s, dict))):
            fail('Candidate action or relation may depend on its early association', iid)
    # Later Actor events remain valid; an event depending on this observation,
    # or a separate unresolved identity dispute, cannot be discharged here.
    for collection in (world.get('questions_to_verify', []), world.get('ambiguities', []),
                       behavior.get('semantic_limitations', [])):
        for item in collection:
            if (item.get('resolution') in {'resolved','confirmed','non_core'}
                    or 'actor_identity' not in item.get('impact_domains', [])
                    or not _mentions(item, {aid})):
                continue
            if (item.get('census_issue_ids') != [iid] or item.get('actor_ids') != [aid]
                    or item.get('execution_effect') != 'bounded_uncertainty'
                    or not observation_issue_only({k:v for k,v in item.items() if k != 'actor_ids'})):
                fail('Candidate has a separate or mixed identity dependency', iid)
    return dict(independent_actor_id=aid)


def validate(case, behavior, row, observation, frames, world, *, registry=None):
    iid = observation['issue_id']
    fields = {'issue_id','outcome','reason','evidence_frame_ids','affected_actor_ids',
              'affected_event_ids','scope_proof'}
    if 'identity_assessment' in row:
        fields.add('identity_assessment')
        from _lib.contracts.behavior_handoff import validate_identity
        validate_identity(row, observation, {a['actor_id'] for a in behavior['actors']}, frames)
    allowed = {'irrelevant','distinct','needs_identity','unknown'}
    if retain_enabled(case): allowed.add('retain')
    if (set(row) != fields or row['outcome'] not in allowed
            or not isinstance(row['reason'], str) or not row['reason'].strip()):
        fail('Invalid observation relevance answer', iid)
    for name, allowed in (('evidence_frame_ids', set(frames)),
            ('affected_actor_ids', {a['actor_id'] for a in behavior['actors']}),
            ('affected_event_ids', {e['event_id'] for e in behavior['events']})):
        values = row[name]
        if (not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                or len(values) != len(set(values)) or not set(values) <= allowed):
            fail('Relevance answer has duplicate or foreign references', iid)
    if observation['frame_id'] not in row['evidence_frame_ids']:
        fail('Relevance answer omits its source observation', iid)
    if row['outcome'] in {'unknown','needs_identity'}:
        if row['scope_proof'] is not None: fail('Unresolved relevance cannot assert a scope proof', iid)
        return None
    proof = row['scope_proof']
    if row['outcome'] == 'retain':
        return retained_observation(case, behavior, row, observation, proof, frames, world, registry)
    if row['outcome'] == 'distinct':
        if (not observation.get('distinction') or not isinstance(proof, dict)
                or set(proof) != {'frame_id','time_s','actor_id','compared_actor_id',
                                  'actor_features','compared_features','distinct'}
                or any(proof[k] != observation[k] for k in
                       ('frame_id','time_s','actor_id','compared_actor_id'))
                or proof['distinct'] is not True
                or any(not isinstance(proof[k], str) or not proof[k].strip()
                       for k in ('actor_features','compared_features'))):
            fail('Deferred distinction requires both co-visible identities and visual features', iid)
        return dict(issue_id=iid, handling='verified_distinct', proof=copy.deepcopy(proof))
    proof_fields = {'rule_id','window_s','sightings',
                    'identity_dependency','path_dependency','interaction_dependency'}
    if isinstance(proof, dict) and 'candidate_independence' in proof:
        proof_fields.add('candidate_independence')
    if (not isinstance(proof, dict) or set(proof) != proof_fields
            or proof['rule_id'] not in {'isolated_opposing','unrelated_observation'}
            or proof['window_s'] != observation['window_s']
            or any(proof[k] is not False for k in
                   ('identity_dependency','path_dependency','interaction_dependency'))
            or row['affected_actor_ids'] or row['affected_event_ids']):
        fail('Observation exclusion needs explicit absence of identity/path/interaction dependency', iid)
    sights = proof['sightings']
    sight_fields = {'frame_id','time_s','object_visible','location_evidence','relation_to_ego_path',
                    'interaction_evidence','opposite_to_ego','physically_separated','access_to_ego_carriageway'}
    if (not isinstance(sights, list) or any(not isinstance(s, dict) or set(s) != sight_fields for s in sights)
            or any(not isinstance(s['frame_id'], str) for s in sights)):
        fail('Invalid relevance evidence sightings', iid)
    by_id = {s['frame_id']:s for s in sights}
    if len(by_id) != len(sights) or not set(by_id) <= set(frames):
        fail('Relevance sightings have duplicate or foreign frames', iid)
    from _lib.contracts.census_scope import ScopeReviewCoverageError
    if set(by_id) != set(frames):
        raise ScopeReviewCoverageError(iid, 'scope_proof/sightings', frames, set(by_id))
    if set(row['evidence_frame_ids']) != set(frames):
        raise ScopeReviewCoverageError(iid, 'evidence_frame_ids', frames, row['evidence_frame_ids'])
    from _lib.contracts.census_scope import MAX_GAP_S
    times = sorted(f['time_s'] for f in frames.values())
    if (not times or times[0] > MAX_GAP_S or observation['window_s'][1]-times[-1] > MAX_GAP_S
            or any(b-a > MAX_GAP_S for a,b in zip(times,times[1:]))):
        fail('Relevance exclusion lacks full sampled window coverage', iid)
    isolated = proof['rule_id'] == 'isolated_opposing'
    if observation.get('distinction') and not isolated:
        fail('A registered identity distinction requires visual distinction or isolated opposing proof', iid)
    for fid, sight in by_id.items():
        if (type(sight['time_s']) not in (int,float) or sight['time_s'] != frames[fid]['time_s']
                or type(sight['object_visible']) is not bool
                or fid == observation['frame_id'] and not sight['object_visible']
                or any(not isinstance(sight[k], str) or not sight[k].strip()
                       for k in ('location_evidence','interaction_evidence'))
                or sight['relation_to_ego_path'] not in {'outside_path','remote_non_interacting'}
                or any(type(sight[k]) is not bool for k in
                       ('opposite_to_ego','physically_separated','access_to_ego_carriageway'))
                or isolated and (not sight['opposite_to_ego'] or not sight['physically_separated']
                                 or sight['access_to_ego_carriageway'])):
            fail('Relevance evidence conflicts with source visibility or ego path', iid)
    road = world['world']['road']
    opposing = any(s['opposite_to_ego'] for s in sights)
    separated_opposing = any(s['opposite_to_ego'] and s['physically_separated'] for s in sights)
    if (opposing and road.get('one_way') is True or separated_opposing and any(
            (r.get('road_geometry') or {}).get('opposing_separation', {}).get('physically_separated') is False
            for r in road.get('roads', []))):
        fail('Opposing exclusion contradicts known road separation', iid)
    from _lib.contracts.execution_gaps import dependencies
    ids = {iid}
    if observation.get('distinction'):
        aid = observation['compared_actor_id']; ids.add(aid)
        from _lib.runtime.execution_scope import observed_opposing
        actor = next((a for a in behavior['actors'] if a['actor_id'] == aid), None)
        if not actor or actor.get('simulation_role') != 'background' or not observed_opposing(actor, world):
            fail('Registered opposing comparison lacks matching Behavior direction evidence', iid)
    if _mentions([dependencies(case, behavior), behavior.get('events', []),
                  behavior.get('executor_primitives', [])], ids):
        fail('Observation exclusion conflicts with an execution dependency', iid)
    independent = candidate_independence(case, behavior, observation, proof, frames, world, registry=registry)
    choice = dict(issue_id=iid, handling='observation_excluded', identity_resolution='unresolved', **independent)
    receipt = dict(revision=EXECUTION, choices=[choice])
    for collection in ('questions_to_verify','ambiguities'):
        if any(iid in q.get('census_issue_ids', []) and not observation_issue_only(q)
               and not observation_issue_disposed(q, receipt)
               for q in world.get(collection, [])):
            fail('Observation exclusion cannot discharge an Actor, road or execution dependency', iid)
    return dict(choice, proof=copy.deepcopy(proof))


def retained_observation(case, behavior, row, observation, proof, frames, world, registry):
    """Bind an observation to an already observed physical object for execution only."""
    iid = observation['issue_id']
    fields = {'rule_id','actor_id','frame_id','source_description','registered_anchor_reason','source_features',
              'actor_features','co_visible_distinctions','trajectory_frame_ids',
              'relation_to_ego_path','interaction_evidence'}
    if (observation.get('distinction') or not isinstance(proof, dict) or set(proof) != fields
            or proof['rule_id'] != 'registered_observation.v1'
            or proof['frame_id'] != observation['frame_id']
            or not observation.get('description')
            or proof['source_description'] != observation['description']
            or observation['candidate_actor_ids'] != [proof['actor_id']]
            or row['affected_actor_ids'] != [proof['actor_id']]
            or any(not isinstance(proof[k], str) or not proof[k].strip() for k in
                   ('registered_anchor_reason','source_features','actor_features','interaction_evidence'))
            or not isinstance(proof['relation_to_ego_path'], str)
            or proof['relation_to_ego_path'] not in {'ego_lane','adjacent_lane','crossing_path'}):
        fail('Retained observation lacks a source-bound physical object and interaction proof', iid)
    aid, fid = proof['actor_id'], proof['frame_id']
    actors = {a['actor_id']:a for a in behavior['actors']}
    actor = actors.get(aid)
    registered_actors = {a['actor_id']:a for a in (registry if registry is not None
                         else read_json(Path(case)/'state/actor_registry.json'))}
    registered = registered_actors.get(aid)
    world_actor = next((a for a in world.get('actor_hypotheses', []) if a['actor_id'] == aid), None)
    points = (actor or {}).get('trajectory', [])
    anchors = (registered or {}).get('evidence', [])
    if (not actor or actor.get('actor_type') != 'vehicle' or not registered or not world_actor
            or registered.get('actor_type') != 'vehicle'
            or world_actor.get('first_seen_frame_id') != registered.get('first_seen_frame_id')
            or not any(p.get('frame_id') == fid and p.get('time_s') == frames[fid]['time_s']
                       and p.get('lane_id') is not None for p in points)
            or not any(a.get('frame_id') == fid and a.get('status') == 'observed'
                       and a.get('reason') == proof['registered_anchor_reason'] for a in anchors)
            or len({p.get('frame_id') for p in points}) < 2
            or proof['trajectory_frame_ids'] != [p.get('frame_id') for p in points]
            or len(set(proof['trajectory_frame_ids'])) != len(points)
            or not set(proof['trajectory_frame_ids']) <= set(frames)):
        fail('Retained object lacks an independent registered anchor and observed trajectory', iid)
    lanes = {lane['lane_id']:lane for lane in world['world']['road'].get('lanes', [])}
    point = next(p for p in points if p['frame_id'] == fid)
    lane = lanes.get(point['lane_id'])
    if (not lane or lane.get('travel_direction') in {'backward','opposite_to_ego','opposite_direction'}
            or point.get('road_id') not in {None, lane.get('road_id')}):
        fail('Retained object road relation conflicts with the observed trajectory', iid)
    others = {other['actor_id'] for other in behavior['actors']
              if other['actor_id'] not in {'ego', aid} and other.get('actor_type') == 'vehicle'
              and any(p.get('frame_id') == fid for p in other.get('trajectory', []))}
    distinctions = proof['co_visible_distinctions']
    if (not isinstance(distinctions, list) or any(not isinstance(item, dict)
            or set(item) != {'actor_id','registered_anchor_reason','features'}
            or not isinstance(item['actor_id'], str)
            or not isinstance(item['features'], str) or not item['features'].strip()
            or not isinstance(item['registered_anchor_reason'], str)
            or not any(e.get('frame_id') == fid and e.get('status') == 'observed'
                       and e.get('reason') == item['registered_anchor_reason']
                       for e in registered_actors.get(item['actor_id'], {}).get('evidence', []))
            for item in distinctions)
            or [item['actor_id'] for item in distinctions] != sorted(others)):
        fail('Retained object must distinguish every co-visible registered vehicle', iid)
    events = {e['event_id']:e for e in behavior.get('events', [])}
    if any(eid not in events or aid not in events[eid].get('involved_actor_ids', [])
           for eid in row['affected_event_ids']):
        fail('Retained interaction cites an event without the physical object', iid)
    from _lib.contracts.execution_gaps import dependencies
    if _mentions(dependencies(case, behavior), {iid}):
        fail('Retained observation still has a separate execution dependency', iid)
    return dict(issue_id=iid, handling='retained_unresolved', identity_resolution='unresolved',
                actor_id=aid, proof=copy.deepcopy(proof))


def prompt(case=None):
    revision = RETAIN_BEHAVIOR if case and retain_enabled(case) else BEHAVIOR
    prefix = ('\n范围合同 '+revision+'：每个 pending_scope 项使用 issue_id,outcome,'
        'reason,evidence_frame_ids,affected_actor_ids,affected_event_ids,scope_proof。'
        'outcome=irrelevant/needs_identity/unknown；distinction 项还可 distinct。'
        'unknown/needs_identity 的 scope_proof=null。不得用远小、缺少事件或无法辨认代替无关证据。'
        'irrelevant 只排除该条观察，不删除候选 Actor，不确认身份。scope_proof='
        '{rule_id:isolated_opposing或unrelated_observation,window_s:复制输入,'
        'identity_dependency:false,path_dependency:false,interaction_dependency:false,sightings:[...]};'
        'sightings 与 evidence_frame_ids 覆盖本次全部冻结帧，每帧包含 frame_id,time_s,object_visible,'
        'location_evidence,relation_to_ego_path(outside_path或remote_non_interacting),interaction_evidence,'
        'opposite_to_ego,physically_separated,access_to_ego_carriageway。后三字段为布尔；'
        'isolated_opposing 必须 true,true,false。原观察帧必须可见；其他帧允许仅证明道路上下文，'
        '不可虚构目标持续可见。说明该观察为什么不影响路径、变道、前后关系或候选身份。'
        '候选 Actor 后来的相关证据必须保留；其车道已知并不自动说明此观察相关。'
        '同向且无物理隔离并不禁止 unrelated_observation：逐帧核查自车路径、换道、跟驰、避让和碰撞约束，'
        '有完整无关证据才回答 irrelevant；远小、未列事件或遮挡看不清本身均不足。'
        '早期斑点是否属于某已登记车辆，与该斑点是否参与执行分别判断。若不采用提前关联仍可使用该车后来独立确认的轨迹，'
        '可保持身份未确认并回答 irrelevant，不得因此删除该车或补造提前轨迹。此时 affected_actor_ids=[]（候选不是受影响对象），'
        '不填仅供 needs_identity 的 identity_assessment；unrelated_observation 的完整 scope_proof 可另含 '
        'candidate_independence={actor_id:唯一候选,trajectory_frame_ids:该车全部后续轨迹帧,observation_used:false,reason:独立成立依据}。'
        '只适用早于登记首见及全部轨迹的可选关联；多个候选、实际车辆身份争议或事件依赖仍须裁定。'
        '所有排除分支仍校验已知道路事实；不能把与单向道路或隔离事实矛盾的描述换成 unrelated_observation 绕过。'
        '仅该观察的身份/车道未知用 census_issue_ids 精确绑定，Behavior limitation 标记 bounded_uncertainty；'
        '已有 Actor 身份、道路或事件依赖必须单独保留，不得混入观察排除。'
        'distinction 项只可用 isolated_opposing 排除 compared_actor_id 的比较义务，且该 Actor '
        '须有 background 和完整 direction_evidence、无行为依赖；否则 distinct 逐项提供 '
        'scope_proof={frame_id,time_s,actor_id,compared_actor_id,actor_features,compared_features,distinct:true}，'
        '对应源帧两辆不同车辆的视觉特征。无法确认则 needs_identity，不可把待查当已验证。')
    if revision == BEHAVIOR: return prefix
    return prefix + ('\nv4 还允许 outcome=retain：仅当该观察在源帧已由登记 Actor 的 observed 锚点和 Behavior '
        '同帧真实轨迹点承载，且有至少两帧独立轨迹时使用；身份仍 unresolved。'
        'scope_proof={rule_id:registered_observation.v1,actor_id,frame_id,source_description:'
        '逐字引用原观察 description,registered_anchor_reason:'
        '逐字引用 Registry 同帧 observed reason,source_features:原图目标特征,actor_features:登记对象同帧特征,'
        'co_visible_distinctions:[{actor_id,registered_anchor_reason,features}]:逐一与其他同帧车辆作视觉区分并引用登记锚点，'
        'trajectory_frame_ids:该 Actor 的全部真实轨迹帧顺序,relation_to_ego_path:'
        'ego_lane|adjacent_lane|crossing_path,interaction_evidence:原图中的相对位置或动态关系}。'
        'affected_actor_ids=[actor_id]；affected_event_ids 只列实际含此 Actor 的事件。'
        '唯一候选或没有候选本身均不构成证明；不能匹配同帧锚点及轨迹时回答 needs_identity/unknown。'
        '不得补造车辆、轨迹或把未确认身份写成事实。')


def world_prompt():
    return ('\n本请求 v6 必须输出 relevance 数组，覆盖 records 每个 issue_id 和 confirmed_sightings '
        '中每个已供帧的对象；后者 issue_id=confirmed:<frame_id>:<actor_id>。每项 '
        'issue_id,assessment(retain/background_candidate/unknown),reason,evidence_frame_ids；证据含源帧。'
        '这是初步范围判断，不改变身份，不删除 Actor。未知同帧区分可保留为待查，交 Behavior '
        '在完整行为窗口判断，但仍必须完成正向关联、合并、拆分、反证所需证据。'
        '首次登记继承 v5；candidate_refutations 优先使用 subject_sighting_frame_ids 引用共享目击，'
        '不要重复大段 sightings，保持48KiB回答上限。只有后续逐项范围/区分验收后才可交付。')
