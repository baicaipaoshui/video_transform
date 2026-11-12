"""Opt-in pending census review within the normal Behavior task."""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.contracts.census_adjudication import SCOPE_REVISIONS
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.world.gaps import IdentityEvidenceGap

REVISION = 'behavior-census-scope.v1'
EXECUTION_REVISION = 'behavior-census-scope.v2'
ADOPTION = 'state/behavior_census_scope.adoption.json'
RULE = 'physically_isolated_opposing.v1'


def enabled(case):
    case = Path(case).resolve()
    policy = case/'state/execution_policy.json'
    if not policy.is_file():
        return False
    frozen = read_json(policy)
    from _lib.contracts.census_relevance import enabled as relevance_enabled
    native = relevance_enabled(case) or frozen.get('behavior_census_scope') in {REVISION, EXECUTION_REVISION, 'behavior-census-scope.v3', 'behavior-census-scope.v4'}
    if native:
        if frozen.get('world_census_adjudication') not in SCOPE_REVISIONS:
            raise ContractError('Deferred Behavior scope requires frozen v4 census')
        if (frozen.get('behavior_census_scope') == EXECUTION_REVISION
                and frozen.get('execution_scope') != 'ego_vehicle_scene.v4'):
            raise ContractError('Behavior census scope v2 requires ego execution scope v4')
    path = case/ADOPTION
    if not path.is_file():
        return native
    value = read_json(path); body = dict(value); signature = body.pop('sha256', None)
    if signature != digest(body) or body.get('revision') != REVISION or body.get('case_id') != case.name:
        raise ContractError('Behavior census scope adoption changed')
    if body.get('policy_sha256') != sha256(policy) or body.get('case_sha256') != sha256(case/'case.json'):
        raise ContractError('Behavior census scope adoption frozen policy changed')
    return True


def adopt(case):
    """Explicit saved-Case adoption; never migrate facts, task history or budgets."""
    case = Path(case).resolve()
    if enabled(case):
        return
    state = read_json(case/'state/progress.json')
    if (state.get('status') != 'needs_review'
            or (state.get('error') or {}).get('rule_id') != 'identity.census.unresolved'
            or any((case/name).exists() for name in ('state/behavior.json', 'state/behavior.candidate.json',
                'state/behavior_input.request.json', 'final/analysis.json'))
            or any(s.get('stage') == 'behavior' or s.get('status') in {'submitted','pending','running'}
                or (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
                    or s.get('recovered_from_poll_failure')) and not s.get('deadline_task_resolved')
                for s in state.get('sessions', []))
            or state.get('diagnostics', {}).get('unresolved_submissions')):
        raise ContractError('Behavior census scope adoption requires an idle census stop before first Behavior')
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    if not saved or saved['request']['revision'] not in SCOPE_REVISIONS:
        raise ContractError('Behavior census scope adoption requires accepted v4 census')
    value = dict(revision=REVISION, case_id=case.name, case_sha256=sha256(case/'case.json'),
                 policy_sha256=sha256(case/'state/execution_policy.json'),
                 original_certificate_sha256=saved['sha256'])
    atomic_json(case/ADOPTION, dict(value, sha256=digest(value)))


def pending(case, saved):
    if not enabled(case) or not saved:
        return {}
    if saved['request']['revision'] not in SCOPE_REVISIONS:
        raise ContractError('Deferred Behavior scope requires accepted v4 census')
    from _lib.contracts.census_history import remaining_records, rounds
    from _lib.contracts.census_execution import enabled as execution_enabled
    adoption = Path(case)/ADOPTION
    if adoption.is_file() and read_json(adoption).get('original_certificate_sha256') not in {
            r['sha256'] for r in rounds(saved)}:
        raise ContractError('Behavior census scope adoption certificate is outside this lineage')
    result = {r['issue_id']:dict(copy.deepcopy(r), pending_scope=True,
            evidence_frame_ids=[r['frame_id']], rule_id=EXECUTION_REVISION if execution_enabled(case) else REVISION,
            window_s=[0., read_json(Path(case)/'state/probe.json')['probe']['duration_s']])
            for r in remaining_records(case, saved)}
    from _lib.contracts import census_relevance
    if saved['request']['revision'] == census_relevance.REVISION:
        for iid, row in census_relevance.pending(saved).items():
            result[iid] = dict(row, pending_scope=True,
                window_s=[0., read_json(Path(case)/'state/probe.json')['probe']['duration_s']])
        from _lib.contracts.census_history import rounds
        prelim = {r['issue_id']:r for item in rounds(saved) for r in item['result'].get('relevance', [])}
        for iid, row in result.items():
            row['preliminary_relevance'] = prelim.get(iid)
    return result


def validate_decision(case, behavior, row, observation, frames, *, world=None, registry=None):
    """Identity stays unresolved; exclusion needs per-frame direction/barrier proof."""
    from _lib.contracts import census_relevance
    if census_relevance.enabled(case):
        checked = census_relevance.validate(case, behavior, row, observation, frames,
            world if world is not None else read_json(Path(case)/'state/world_context.json'), registry=registry)
        return dict(observation, **{k:v for k,v in checked.items() if k not in observation}) if checked and checked['handling'] == 'observation_excluded' else None
    fields = {'issue_id','outcome','reason','evidence_frame_ids','affected_actor_ids','affected_event_ids','scope_proof'}
    from _lib.contracts import behavior_handoff
    if behavior_handoff.enabled(case) and 'identity_assessment' in row:
        fields.add('identity_assessment')
    if (set(row) != fields or row['outcome'] not in {'excluded','needs_identity','unknown'}
            or not isinstance(row['reason'], str) or not row['reason'].strip()
            or any(not isinstance(row[k], list) or any(not isinstance(v, str) or not v for v in row[k])
                   or len(set(row[k])) != len(row[k]) for k in
                   ('evidence_frame_ids','affected_actor_ids','affected_event_ids'))
            or observation['frame_id'] not in row['evidence_frame_ids']
            or not set(row['evidence_frame_ids']) <= set(frames)
            or not set(row['affected_actor_ids']) <= {a['actor_id'] for a in behavior['actors']}
            or not set(row['affected_event_ids']) <= {e['event_id'] for e in behavior['events']}):
        raise ContractError('Pending census scope answer has invalid fields or references')
    if 'identity_assessment' in fields:
        behavior_handoff.validate_identity(row, observation,
            {a['actor_id'] for a in behavior['actors']}, frames)
    if row['outcome'] != 'excluded':
        if row['scope_proof'] is not None:
            raise ContractError('Unconfirmed census scope cannot claim exclusion proof')
        return None
    proof = row['scope_proof']
    if (not isinstance(proof, dict) or set(proof) != {'rule_id','window_s','sightings'}
            or proof['rule_id'] != RULE or proof['window_s'] != observation['window_s']
            or not isinstance(proof['sightings'], list)
            or row['affected_actor_ids'] or row['affected_event_ids']):
        raise ContractError('Pending census exclusion requires isolated opposing proof')
    from _lib.contracts.census_scope import MAX_GAP_S
    ordered = sorted(frames.values(), key=lambda f:f['time_s'])
    duration = observation['window_s'][1]
    if (not ordered or ordered[0]['time_s'] > MAX_GAP_S or duration-ordered[-1]['time_s'] > MAX_GAP_S
            or any(b['time_s']-a['time_s'] > MAX_GAP_S for a,b in zip(ordered,ordered[1:]))):
        raise ContractError('Pending census scope lacks complete sampled Case coverage')
    sightings = proof['sightings']
    fields = {'frame_id','time_s','object_visible','location_evidence','direction_evidence','barrier_evidence',
              'opposite_to_ego','physically_separated','access_to_ego_carriageway'}
    if any(not isinstance(s, dict) or set(s) != fields or not isinstance(s['frame_id'], str)
           for s in sightings):
        raise ContractError('Pending census scope sightings have invalid fields')
    sighting_ids = {s['frame_id'] for s in sightings}
    if len(sighting_ids) != len(sightings) or not sighting_ids <= set(frames):
        raise ContractError('Pending census scope sightings have duplicate or unknown frames')
    from _lib.contracts.census_scope import ScopeReviewCoverageError
    if sighting_ids != set(frames):
        raise ScopeReviewCoverageError(observation['issue_id'], 'scope_proof/sightings', frames, sighting_ids)
    if set(row['evidence_frame_ids']) != set(frames):
        raise ScopeReviewCoverageError(observation['issue_id'], 'evidence_frame_ids', frames, row['evidence_frame_ids'])
    for sight in sightings:
        if (type(sight['time_s']) not in (int, float) or sight['time_s'] != frames[sight['frame_id']]['time_s'] or type(sight['object_visible']) is not bool
                or sight['opposite_to_ego'] is not True or sight['physically_separated'] is not True
                or sight['access_to_ego_carriageway'] is not False
                or sight['frame_id'] == observation['frame_id'] and not sight['object_visible']
                or any(not isinstance(sight[k], str) or not sight[k].strip()
                       for k in ('location_evidence','direction_evidence','barrier_evidence'))):
            raise ContractError('Pending census scope direction/barrier/source witness invalid')
    world = world if world is not None else read_json(Path(case)/'state/world_context.json')
    road = world['world']['road']
    if road.get('one_way') is True or any((r.get('road_geometry') or {}).get('opposing_separation', {}).get(
            'physically_separated') is False for r in road.get('roads', [])):
        raise ContractError('Pending census opposing proof contradicts known World facts')
    registry = {a['actor_id']:a for a in (registry if registry is not None
                else read_json(Path(case)/'state/actor_registry.json'))}
    from _lib.contracts.census_execution import enabled as execution_enabled
    if not execution_enabled(case) and any(registry.get(aid, {}).get('actor_type') != 'vehicle_group'
                                           for aid in observation['candidate_actor_ids']):
        raise ContractError('Pending census exclusion cannot discharge an individual identity candidate')
    from _lib.contracts.actors import is_vehicle
    if any(not (is_vehicle(registry.get(aid))
                or registry.get(aid, {}).get('actor_type') == 'vehicle_group')
           for aid in observation['candidate_actor_ids']):
        raise ContractError('Pending census opposing candidate must be a vehicle')
    from _lib.runtime.execution_scope import observed_opposing
    actors = {a['actor_id']:a for a in behavior['actors']}
    if any(actors[aid].get('simulation_role') != 'background' or not observed_opposing(actors[aid], world)
           for aid in observation['candidate_actor_ids']):
        raise ContractError('Pending opposing observation requires matching Behavior background evidence')
    def references(node):
        if isinstance(node, str):
            return {node}
        if isinstance(node, list):
            return set().union(*(references(v) for v in node)) if node else set()
        if isinstance(node, dict):
            return references(list(node.values()))
        return set()
    from _lib.contracts.execution_gaps import dependencies as execution_dependencies
    dependencies = execution_dependencies(case, behavior)
    ids = {observation['issue_id'], *observation['candidate_actor_ids']}
    if ids & references(dependencies):
        raise ContractError('Pending census exclusion conflicts with Behavior execution dependencies')
    for collection in ('questions_to_verify','ambiguities'):
        for question in world.get(collection, []):
            if observation['issue_id'] in question.get('census_issue_ids', []) and question.get('world_paths'):
                raise ContractError('Pending census scope cannot discharge a road/facility question')
    return dict(issue_id=observation['issue_id'], batch_id=observation['batch_id'],
        frame_id=observation['frame_id'], object_id=observation['object_id'], rule_id=RULE,
        reason=row['reason'], evidence_frame_ids=row['evidence_frame_ids'], window_s=proof['window_s'],
        proof=copy.deepcopy(proof), identity_resolution='unresolved')


def blocked(observations, decisions, behavior, review, *, case=None, snapshot=None, frames=None, program=None, assumptions=None, allow_pending=False):
    from _lib.contracts.census_execution import decisions as execution_decisions
    receipt = execution_decisions(case, behavior, observations, review,
        **({'snapshot':snapshot, 'frames':frames} if snapshot is not None else {}),
        **({'program':program} if program else {}),
        **({'assumptions':assumptions} if assumptions else {})) if case else None
    accepted = {r['issue_id'] for r in receipt['choices']
                if r['handling'] != 'needs_evidence'} if receipt else set()
    issues = [dict(rule_id='identity.census.scope_pending', owner='world',
        issue_id=iid, actor_ids=observations[iid]['candidate_actor_ids'],
        evidence_frame_ids=[observations[iid]['frame_id']], impact_domains=['actor_identity'],
        message=decisions[iid]['reason'], retry_kind='world_closure_adjudication')
        for iid in observations if iid not in accepted and decisions[iid]['outcome'] != 'excluded']
    if issues and not allow_pending:
        error = IdentityEvidenceGap(issues)
        if assumptions:
            error.execution_dispositions = receipt
        from _lib.contracts.census_relevance import retain_enabled
        needed = [i['issue_id'] for i in issues
                  if decisions[i['issue_id']]['outcome'] in (
                      {'needs_identity', 'unknown'} if case and retain_enabled(case)
                      else {'needs_identity'})
                  and not observations[i['issue_id']].get('distinction')]
        from _lib.contracts.execution_gaps import enabled
        mixed = bool(case and enabled(case) and needed and len(needed) != len(issues))
        if (len(needed) == len(issues) or mixed) and needed:
            error.pending_census = True
            error.scope_review = review
            error.behavior = behavior
            error.diagnostics['issue_ids'] = needed
            if mixed:
                error.deferred_issue_ids = [i['issue_id'] for i in issues if i['issue_id'] not in needed]
                error.diagnostics['deferred_issue_ids'] = error.deferred_issue_ids
            from _lib.contracts.behavior_handoff import enabled as handoff_enabled
            if case and handoff_enabled(case):
                # Accepted execution dispositions are outside this recovery's
                # authority too, even though they are absent from `issues`.
                deferred_ids = sorted(set(observations) - set(needed))
                if deferred_ids:
                    error.deferred_issue_ids = deferred_ids
                    error.diagnostics['deferred_issue_ids'] = deferred_ids
        raise error
    return receipt


def excluded_groups(case, saved_review):
    if not saved_review or not saved_review.get('excluded_observations') or not enabled(case):
        return set()
    from _lib.contracts.census_adjudication import proof
    observations = pending(case, proof(case))
    covered = {o['issue_id'] for o in saved_review['excluded_observations']}
    registry = {a['actor_id']:a for a in read_json(Path(case)/'state/actor_registry.json')}
    groups = {aid for o in observations.values() for aid in o['candidate_actor_ids']
              if registry.get(aid, {}).get('actor_type') == 'vehicle_group'}
    return {aid for aid in groups if all(iid in covered for iid,o in observations.items()
                                       if aid in o['candidate_actor_ids'])}


def prompt(required_frame_ids, *, case=None):
    import json
    from _lib.contracts.census_execution import enabled as execution_enabled
    execution = bool(case and execution_enabled(case))
    from _lib.contracts import census_relevance
    if case and census_relevance.enabled(case):
        from _lib.contracts import early_association
        return census_relevance.prompt(case) + early_association.prompt(case)
    return ('\n' + (EXECUTION_REVISION if execution else REVISION)
        + '：带 pending_scope=true 的观察尚未获排除，不继承原身份结论。'
        '同次 census_scope_review 对每项输出 issue_id、outcome(excluded/needs_identity/unknown)、reason、'
        'evidence_frame_ids、affected_actor_ids、affected_event_ids、scope_proof。需要参与执行但身份未定写 needs_identity；'
        '无法判断写 unknown，两者 scope_proof=null。excluded 仅允许 physically_isolated_opposing.v1，'
        'scope_proof={rule_id,window_s,sightings}；window_s 复制观察范围，sightings 覆盖本次全部冻结帧，'
        '输入 observation.evidence_frame_ids 仅表示原观察来源，不是完整排除依据。'
        '输出 excluded 时，sightings 的帧集合和 evidence_frame_ids 都必须与 excluded_required_frame_ids 完全一致；'
        '不得只复制原观察的单帧列表。unknown/needs_identity 不要求全帧排除证明。冻结输出要求：'
        + json.dumps(dict(excluded_required_frame_ids=required_frame_ids), ensure_ascii=False) + '。'
        '每帧写 frame_id,time_s,object_visible,location_evidence,direction_evidence,barrier_evidence,'
        'opposite_to_ego=true,physically_separated=true,access_to_ego_carriageway=false。'
        '原观察帧 object_visible=true，其他帧可以仅证明隔离道路上下文，不能虚构跨帧身份。'
        '所有位置/方向/屏障依据必须来自原图；单凭远、小、未出现在事件中不能排除。'
        'excluded 的 affected 两数组为空，且不能存在相关事件/动作依赖。'
        + ('' if execution else '个体候选身份不得用范围结论抹去；')
        + ('单车或群体候选均保留原 Actor；隔离对向候选给 simulation_role=background 与 direction_evidence='
           if execution else '群体候选保留原群体 Actor，并给 simulation_role=background 与 direction_evidence=')
        + '{status:observed,value:opposite_to_ego,frame_ids:覆盖该群体全部轨迹帧,reason:原图依据}；'
        '仅排除有独立隔离证据的观察。已登记 Actor 的颜色和车型对象逐字保留完整结构，不简写为字符串。'
        '不修改 World/Registry 或伪造 Actor ID。')
