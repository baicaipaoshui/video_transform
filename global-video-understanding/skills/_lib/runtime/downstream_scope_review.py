"""Explicit, single-task execution-scope review of an immutable failed Behavior."""
from pathlib import Path

from _lib.behavior import input_binding
from _lib.contracts import downstream_scope as contract, census_scope
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json, sha256, now

KEY = 'execution_scope_review'
REQUEST = 'state/execution_scope_review.request.json'
RESULT = 'diagnostics/execution_scope_review/result.json'


def active(case):
    return (Path(case)/REQUEST).is_file()


def inputs(case, raw):
    from _lib.behavior.compile import evidence_frames, prepare_behavior_candidate
    from _lib.behavior.bounded_repair import normalize_index
    behavior, review = census_scope.split(raw)
    world = read_json(Path(case)/'state/world_context.json')
    frames = evidence_frames(case)
    behavior, _ = prepare_behavior_candidate(behavior, frames,
        read_json(Path(case)/'state/actor_registry.json'), world, case=case)
    review, _ = normalize_index(case, behavior, review, frames)
    return world, behavior, review, frames


def request(case):
    case = Path(case).resolve()
    value = read_json(case/REQUEST)
    if (value.get('revision') != contract.REVISION or value.get('case_id') != case.name
            or value.get('request_sha256') != digest({k:v for k,v in value.items() if k != 'request_sha256'})
            or value.get('output_path') != str(case/'diagnostics/execution_scope_review/candidate.json')):
        raise input_binding.InputChanged('Downstream scope request changed')
    for path, expected in value['input_hashes'].items():
        if not Path(path).is_file() or sha256(path) != expected:
            raise input_binding.InputChanged('Downstream scope source changed: '+path)
    input_binding.verify(case)
    return value


def authorize(runner, case):
    case = Path(case).resolve()
    state = runner.progress(case)
    prepared = request(case) if active(case) else None
    ledger = state.get('diagnostics', {}).get(KEY)
    if prepared and ledger:
        if ledger.get('request_sha256') != prepared['request_sha256'] or ledger.get('task_limit') != 1:
            raise input_binding.InputChanged('Downstream scope authorization changed')
        return prepared
    from _lib.runtime.bounded_recovery import require_terminal, enabled, preflight_total
    from _lib.contracts import census_relevance, behavior_handoff, behavior_census_scope, census_adjudication
    require_terminal(state)
    if (state.get('status') not in {'failed', 'needs_review'} or state.get('artifacts')
            or not enabled(case) or not census_relevance.enabled(case) or not behavior_handoff.enabled(case)
            or any((case/p).exists() for p in ('state/behavior.json', 'final/analysis.json',
                'state/behavior_repair.request.json', 'diagnostics/behavior_scope_review/request.json'))):
        raise ContractError('Downstream scope review requires an unaccepted bounded-recovery Behavior')
    sessions = state.get('sessions', [])
    source = sessions[-1] if sessions else {}
    if (source.get('stage') != 'behavior' or source.get('status') != 'invalid_output'
            or source.get('error_code') != 'identity_evidence_insufficient' or not source.get('task_id')):
        raise ContractError('Downstream scope review requires the terminal identity-failure producer')
    base = Path(source.get('invalid_candidate_path', '')).resolve()
    if not base.is_relative_to(case/'diagnostics') or not input_binding.archive_matches(case, base):
        raise input_binding.InputChanged('Downstream scope source archive is not bound to this Case')
    bound = input_binding.verify(case)
    world, behavior, review, frames = inputs(case, read_json(base))
    from _lib.behavior.compile import validate_compiled_behavior
    validate_compiled_behavior(behavior, world, read_json(case/'state/actor_registry.json'),
                              frames, execution=False, allow_pending_identity=True)
    pending = behavior_census_scope.pending(case, census_adjudication.proof(case))
    ids = contract.candidates(world, behavior, frames)
    if not pending or not ids:
        raise ContractError('No downstream scope candidate set to review')
    from _lib.world.gaps import IdentityEvidenceGap
    try:
        census_scope.validate_review(case, behavior, review)
    except IdentityEvidenceGap:
        pass
    else:
        raise ContractError('Downstream scope request no longer has an unresolved identity boundary')
    preflight_total(case, state, KEY)
    packet = dict(revision=contract.REVISION, case_id=case.name, base_path=str(base),
        producer={k:source[k] for k in ('task_id','session_id')},
        output_path=str(case/'diagnostics/execution_scope_review/candidate.json'),
        candidate_actor_ids=ids, mandatory_actor_ids=sorted(contract.mandatory(behavior)),
        pending=pending, review=review, frames=frames,
        behavior_digest=digest(behavior), world_digest=digest(world),
        input_hashes=dict(bound['input_hashes']))
    for path in (base, case/input_binding.REQUEST, case/input_binding.SESSIONS):
        packet['input_hashes'][str(path)] = sha256(path)
    packet['request_sha256'] = digest(packet)
    if prepared and prepared != packet:
        raise input_binding.InputChanged('Interrupted downstream scope preparation differs from its source')
    atomic_json(case/REQUEST, packet)
    state.setdefault('diagnostics', {})[KEY] = dict(status='authorized', task_limit=1,
        request_sha256=packet['request_sha256'], authorized_at=now())
    runner.save_progress(case, state)
    return packet


def before_attempt(case, state):
    packet = request(case)
    ledger = state.get('diagnostics', {}).get(KEY, {})
    if ledger.get('request_sha256') != packet['request_sha256'] or ledger.get('task_limit') != 1:
        raise ContractError('Downstream scope task has no bound authorization')
    if any(s.get('stage') == KEY for s in state.get('sessions', [])):
        raise ContractError('Downstream scope task already spent; cannot create a second review')


def load(case, behavior, review):
    case = Path(case)
    if not active(case) or not (case/RESULT).is_file():
        return None
    packet = request(case)
    result = read_json(case/RESULT)
    state = read_json(case/'state/progress.json')
    sessions = [s for s in state['sessions'] if s['stage'] == KEY]
    if (len(sessions) != 1 or sessions[0].get('status') != 'completed'
            or sessions[0].get('recovered_from_poll_failure') and not sessions[0].get('deadline_task_resolved')):
        return None  # A prepared result cannot precede producer reconciliation.
    producer = sessions[0]
    ledger = state.get('diagnostics', {}).get(KEY, {})
    if (result.get('producer') != {k:producer.get(k) for k in ('task_id','session_id')}
            or ledger.get('request_sha256') != packet['request_sha256'] or ledger.get('task_limit') != 1
            or ledger.get('result_sha256') != sha256(case/RESULT)
            or result.get('request_sha256') != packet['request_sha256']
            or digest(behavior) != packet['behavior_digest'] or review != packet['review']):
        raise input_binding.InputChanged('Downstream scope result/producer/Behavior binding changed')
    world, original, original_review, frames = inputs(case, read_json(packet['base_path']))
    if original != behavior or original_review != review or digest(world) != packet['world_digest']:
        raise input_binding.InputChanged('Downstream scope normalized source changed')
    proof = contract.make(world, behavior, frames, packet, result['response'])
    if proof != result['proof']:
        raise input_binding.InputChanged('Downstream scope proof changed')
    return proof


def completed_candidate(case):
    if not active(case): return None
    packet = request(case)
    raw = read_json(packet['base_path'])
    _, behavior, review, _ = inputs(case, raw)
    return raw if load(case, behavior, review) is not None else None


def run(runner, case):
    case = Path(case)
    packet = request(case)
    # A saved candidate can survive a polling outage. Reconcile that same task
    # before using it; never issue a replacement or consume another grant.
    state = runner.progress(case)
    uncertain = [s for s in state['sessions'] if s['stage'] == KEY
                 and s.get('recovered_from_poll_failure') and not s.get('deadline_task_resolved')]
    for row in uncertain:
        remote = runner.backend.poll(row['task_id'], 'global-video-understanding')
        if remote.get('status') != 'completed':
            raise ContractError('Downstream scope producer completion remains unconfirmed')
        row['deadline_task_resolved'] = True
        runner.save_progress(case, state)
    if completed_candidate(case) is not None:
        state = runner.progress(case)
        state['diagnostics'][KEY].update(status='completed')
        runner.save_progress(case, state)
        return
    if not hasattr(runner.backend, 'submit') or not hasattr(runner.backend, 'poll'):
        raise ContractError('Downstream scope review requires durable task receipts')
    candidate = Path(packet['output_path'])
    prompt = ('下游执行范围复核。读取冻结请求 '+str(case/REQUEST)+'、base_path 和 frames 中全部图像。'
        '只判断是否参与执行，不裁决身份，不改 Behavior/World/Registry，不合并、不补造轨迹。'
        'mandatory_actor_ids 必须保留。candidate_actor_ids 每辆车及 pending 每条观察均逐项复核；'
        '同帧多候选全部保留歧义，只有每个候选独立无关才可排除整个组。'
        '没有事件、目标远小、轨迹短或无法辨认本身不是无关证据。核查全窗口的路径、变道、'
        '跟驰、前后关系、避让与碰撞约束；不可确认无关时 decision=unknown 或 retain，保留阻塞。'
        '输出 JSON 且只含 request_sha256（复制请求）、actors、observations。actors 每项 actor_id，'
        'observations 每项 issue_id；其余字段均为 decision(exclude/retain/unknown),reason,'
        'identity_dependency,path_dependency,interaction_dependency（布尔）和 sightings。'
        'sightings 恰好覆盖全部冻结帧，每项 frame_id,time_s,object_visible（布尔）,'
        'relation_to_ego_path(outside_path/remote_non_interacting/unknown),location_evidence,interaction_evidence。'
        '原轨迹/观察首帧须承认可见；其他帧不得虚构持续可见，可依据道路上下文说明约束。'
        '排除需三类 dependency 均 false 且逐帧证明无关；不得假装解决原身份问题。'
        '写入 '+str(candidate)+' 后停止。')
    def adopt(session_id=None):
        current = request(case)
        state = runner.progress(case)
        rows = [s for s in state['sessions'] if s['stage'] == KEY]
        if (len(rows) != 1 or not rows[0].get('task_id') or not rows[0].get('session_id')
                or rows[0].get('status') == 'invalid_output'
                or rows[0].get('status') == 'failed' and not rows[0].get('deadline_task_resolved')
                or session_id is not None and rows[0]['session_id'] != session_id):
            raise ContractError('Downstream scope output lacks its unique producer')
        world, behavior, review, frames = inputs(case, read_json(current['base_path']))
        response = read_json(candidate)
        proof = contract.make(world, behavior, frames, current, response)
        result = dict(request_sha256=current['request_sha256'], response=response, proof=proof,
                      producer={k:rows[0][k] for k in ('task_id','session_id')})
        if (case/RESULT).is_file() and read_json(case/RESULT) != result:
            raise input_binding.InputChanged('Prepared downstream scope result changed')
        atomic_json(case/RESULT, result)
        state.setdefault('diagnostics', {}).setdefault(KEY, {}).update(
            status='prepared', result_sha256=sha256(case/RESULT))
        runner.save_progress(case, state)
    runner.model_stage(case, KEY, 'global-video-understanding', prompt, adopt,
        precheck=adopt if candidate.is_file() else None, candidate_path=candidate, max_attempts=1)
    if completed_candidate(case) is None:
        raise ContractError('Downstream scope producer has not completed')
    state = runner.progress(case)
    state['diagnostics'][KEY].update(status='completed', completed_at=now())
    runner.save_progress(case, state)
