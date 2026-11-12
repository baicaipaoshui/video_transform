"""Explicit one-task observation-scope supplement; preserve the failed Behavior.

This is not a World retry or a new budget for general Behavior generation.
The immutable request and separate ledger survive interruption and repeated CLI
invocations. Unchanged facts and unselected decisions cannot be overwritten.
"""
import copy
import re
from pathlib import Path

from _lib.behavior import input_binding
from _lib.contracts import census_scope, census_relevance, behavior_census_scope
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json, atomic_json, sha256, now

KEY = 'behavior_scope_review'
QUOTA_RETRY = 'behavior_scope_quota_retry'
SECOND_QUOTA_RETRY = 'behavior_scope_quota_retry_2'
QUOTA_RETRIES = (QUOTA_RETRY, SECOND_QUOTA_RETRY)
STAGES = {KEY, *QUOTA_RETRIES}
REQUEST = 'diagnostics/behavior_scope_review/request.json'
RESULT = 'diagnostics/behavior_scope_review/result.json'


def active(case):
    return (Path(case)/REQUEST).is_file()


def task_key(state):
    return next((key for key in reversed(QUOTA_RETRIES) if key in state.get('diagnostics', {})), KEY)


def _quota_source(state, packet, source_key=KEY):
    """Accept only a terminal provider quota rejection, never ambiguous transport."""
    ledger = state.get('diagnostics', {}).get(source_key, {})
    rows = [s for s in state.get('sessions', []) if s.get('stage') == source_key]
    if len(rows) != 1:
        raise ContractError('Quota reauthorization requires exactly one original scope task')
    row = rows[0]
    pattern = (r"Quota exceeded for model '[^'\n]+'\. Reason: Your token-plan [^\n.]+ "
        r"quota has been exhausted\. The quota will reset at \d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\."
        r"(?: \[dump: [^\n\]]+\])?")
    if (row.get('status') != 'failed' or row.get('error_code') != 'backend_protocol_error'
            or not re.fullmatch(pattern, row.get('message', ''))
            or not row.get('task_id') or not row.get('session_id')
            or row.get('candidate_exists') or row.get('output_file_size_bytes')
            or row.get('invalid_candidate_path') or row.get('candidate_validation_error')
            or ledger.get('status') != 'submitted' or ledger.get('task_limit') != 1
            or ledger.get('task_id') != row['task_id'] or ledger.get('session_id') != row['session_id']
            or ledger.get('request_sha256') != packet['request_sha256']):
        raise ContractError('Quota reauthorization requires a bound terminal quota failure without output')
    return row, ledger


def _verify_quota_retry(state, packet, *, reading_completed=False):
    active_key = task_key(state)
    if active_key == KEY:
        return
    source_key = KEY
    for key in QUOTA_RETRIES:
        grant = state.get('diagnostics', {}).get(key)
        if grant is None:
            raise input_binding.InputChanged('Quota retry authorization chain is incomplete')
        source, ledger = _quota_source(state, packet, source_key)
        if (grant.get('request_sha256') != packet['request_sha256']
                or grant.get('prior_task_id') != source['task_id']
                or grant.get('prior_session') != source or grant.get('prior_ledger') != ledger
                or grant.get('task_limit') != 1):
            raise input_binding.InputChanged('Quota retry authorization source changed')
        if key == active_key:
            break
        source_key = key
    # A completed result remains readable while its downstream consumer runs.
    # Dispatch and new authorization still require exclusive task ownership.
    if not reading_completed and (any(key != active_key for key in state.get('diagnostics', {}).get('unresolved_submissions', {}))
            or any(s.get('stage') != active_key and (s.get('status') in {'submitted','pending','running'}
                or s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
                and not s.get('deadline_task_resolved')) for s in state.get('sessions', []))):
        raise ContractError('Quota retry cannot proceed while another task is unresolved')


def authorize_quota_retry(runner, case, prior_task_id, *, generation=1):
    """One task per explicitly selected generation; never extend a grant implicitly."""
    if generation not in (1, 2):
        raise ContractError('Scope quota retry generation must be 1 or 2')
    key = QUOTA_RETRIES[generation-1]
    source_key = KEY if generation == 1 else QUOTA_RETRY
    case = Path(case); packet = request(case); state = runner.progress(case)
    _verify_quota_retry(state, packet)
    source, ledger = _quota_source(state, packet, source_key)
    if prior_task_id != source['task_id']:
        raise ContractError('Quota reauthorization task ID does not match the original failed task')
    if key in state.get('diagnostics', {}):
        return  # Same grant survives success, failure, interruption and repeated CLI use.
    if (state.get('status') not in {'failed','needs_review'} or state.get('artifacts')
            or state.get('sessions', [])[-1] != source
            or state.get('diagnostics', {}).get('unresolved_submissions')
            or any(s.get('status') in {'submitted','pending','running'}
                or s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
                and not s.get('deadline_task_resolved') for s in state.get('sessions', []))
            or state.get('attempts', {}).get(source_key) != 1
            or state.get('attempts', {}).get(key)
            or any(s.get('stage') == key for s in state.get('sessions', []))
            or any((case/p).exists() for p in (RESULT, 'state/behavior.json', 'final/analysis.json'))
            or Path(packet['output_path']).exists()):
        raise ContractError('Quota reauthorization requires no output, no unresolved task and unchanged failed scope')
    state['diagnostics'][key] = dict(status='authorized', task_limit=1,
        authorized_at=now(), request_sha256=packet['request_sha256'],
        prior_task_id=prior_task_id, prior_session=copy.deepcopy(source), prior_ledger=copy.deepcopy(ledger),
        budget_scope='explicit_observation_scope_quota_retry')
    runner.save_progress(case, state)


def eligible(observations, review):
    result = []
    for row in review['decisions']:
        obs = observations[row['issue_id']]
        candidates = obs['candidate_actor_ids']
        if (obs.get('distinction') or row['affected_event_ids']
                or row['outcome'] not in {'unknown','needs_identity'}):
            continue
        if ((not candidates and not row['affected_actor_ids']) or
                (len(candidates) == 1 and set(row['affected_actor_ids']) <= set(candidates))):
            result.append(row['issue_id'])
    return sorted(result)


def request(case):
    case = Path(case).resolve()
    if not active(case):
        raise ContractError('Observation scope review requires explicit authorization')
    packet = read_json(case/REQUEST)
    body = dict(packet); signature = body.pop('request_sha256', None)
    if signature != digest(body) or packet['case_id'] != case.name:
        raise input_binding.InputChanged('Observation scope review request changed')
    for path, expected in packet['input_hashes'].items():
        if not Path(path).is_file() or sha256(path) != expected:
            raise input_binding.InputChanged('Observation scope review input changed: ' + path)
    if Path(packet['output_path']).resolve() != case/'diagnostics/behavior_scope_review/candidate.json':
        raise input_binding.InputChanged('Observation scope review output path changed')
    input_binding.verify(case)
    census_scope.verify_request(case, packet)
    return packet


def authorize(runner, case):
    case = Path(case).resolve()
    if active(case):
        packet = request(case)
        ledger = runner.progress(case).get('diagnostics', {}).get(KEY, {})
        if ledger and ledger.get('request_sha256') != packet['request_sha256']:
            raise ContractError('Observation scope review authorization binding changed')
        if ledger:
            return packet
    progress = runner.progress(case)
    if (progress.get('status') not in {'failed','needs_review'}
            or (case/'state/behavior.json').exists() or (case/'final/analysis.json').exists()
            or progress.get('artifacts') or not census_relevance.enabled(case)
            or progress.get('diagnostics', {}).get(KEY)):
        raise ContractError('Observation scope review requires an unaccepted failed v6 Behavior')
    sessions = progress.get('sessions', [])
    if (not sessions or sessions[-1].get('stage') != 'behavior'
            or sessions[-1].get('status') != 'invalid_output'
            or sessions[-1].get('error_code') != 'identity_evidence_insufficient'
            or progress.get('diagnostics', {}).get('unresolved_submissions')
            or any(s.get('status') in {'submitted','running','pending'} or
                   s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
                   and not s.get('deadline_task_resolved') for s in sessions)):
        raise ContractError('Observation scope review requires a terminal bound identity/scope failure')
    base = Path(sessions[-1].get('invalid_candidate_path', '')).resolve()
    if not base.is_relative_to(case/'diagnostics') or not input_binding.archive_matches(case, base):
        raise input_binding.InputChanged('Observation scope review archive is not bound to this Case')
    raw = read_json(base); behavior, review = census_scope.split(raw)
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    observations = behavior_census_scope.pending(case, saved)
    from _lib.world.gaps import IdentityEvidenceGap
    try:
        census_scope.validate_review(case, behavior, review)
    except IdentityEvidenceGap:
        pass
    else:
        raise ContractError('Archived Behavior no longer has the eligible scope failure')
    ids = eligible(observations, review)
    if not ids:
        raise ContractError('No independent observation scope questions to supplement')
    bound = input_binding.verify(case)
    packet = dict(revision='behavior-observation-scope-review.v1', case_id=case.name,
        base_path=str(base), base_sha256=sha256(base), issue_ids=ids,
        output_path=str(case/'diagnostics/behavior_scope_review/candidate.json'),
        input_hashes=dict((bound or {}).get('input_hashes', {})),
        producer={k:sessions[-1][k] for k in ('task_id','session_id')})
    for path in (base, case/input_binding.REQUEST, case/input_binding.SESSIONS):
        packet['input_hashes'][str(path)] = sha256(path)
    census_scope.bind_request(case, packet)
    packet['request_sha256'] = digest(packet)
    # Freeze before authorization. A interrupted half-preparation never dispatches.
    if active(case) and read_json(case/REQUEST) != packet:
        raise input_binding.InputChanged('Prepared observation scope review differs from the current source')
    atomic_json(case/REQUEST, packet)
    progress.setdefault('diagnostics', {})[KEY] = dict(status='authorized',
        request_sha256=packet['request_sha256'], authorized_at=now(), task_limit=1,
        issue_ids=ids, budget_scope='explicit_observation_scope_supplement')
    runner.save_progress(case, progress)
    return packet


def reserve(runner, case, key):
    if key not in STAGES:
        return False
    packet = request(case); state = runner.progress(case)
    _verify_quota_retry(state, packet)
    ledger = state.get('diagnostics', {}).get(key, {})
    if (key != task_key(state) or ledger.get('request_sha256') != packet['request_sha256']
            or ledger.get('status') not in {'authorized','reserved'}
            or any(s.get('stage') == key for s in state.get('sessions', []))):
        raise ContractError('Observation scope supplement task already spent or unresolved')
    ledger.update(status='reserved', reserved_at=now())
    runner.save_progress(case, state)
    return True


def submit(runner, case, operation):
    packet = request(case); state = runner.progress(case)
    _verify_quota_retry(state, packet)
    key = task_key(state)
    ledger = state.get('diagnostics', {}).get(key, {})
    if ledger.get('status') != 'reserved' or ledger.get('request_sha256') != packet['request_sha256']:
        raise ContractError('Observation scope supplement has no unused reservation')
    ledger.update(status='dispatching', dispatch_at=now()); runner.save_progress(case, state)
    try:
        return operation()
    except Exception as error:
        from _lib.runtime.orchestrator import submission_not_sent
        if submission_not_sent(error):
            state = runner.progress(case)
            state['diagnostics'][key]['status'] = 'reserved'
            runner.save_progress(case, state)
        raise


def bind_receipt(progress, key, receipt):
    if key not in STAGES:
        return
    ledger = progress.get('diagnostics', {}).get(key, {})
    if ledger.get('status') != 'dispatching':
        raise ContractError('Observation scope supplement has no dispatch binding')
    ledger.update(status='submitted', task_id=receipt.get('task_id'), session_id=receipt['session_id'])


def materialize(case, packet, response):
    if (not isinstance(response, dict) or set(response) != {'base_sha256','decisions'}
            or response['base_sha256'] != packet['base_sha256']
            or not isinstance(response['decisions'], list)
            or any(not isinstance(r, dict) or not isinstance(r.get('issue_id'), str)
                   for r in response['decisions'])):
        raise ContractError('Scope supplement must contain only base_sha256 and selected decisions')
    rows = {r['issue_id']:r for r in response['decisions']}
    if len(rows) != len(response['decisions']) or set(rows) != set(packet['issue_ids']):
        raise ContractError('Scope supplement must cover exactly the selected observations')
    raw = read_json(packet['base_path']); behavior, review = census_scope.split(raw)
    frames = {f['frame_id']:f for f in packet['frames']}
    observations = {r['issue_id']:r for r in packet['scope_context']['observations']}
    world = read_json(Path(case)/'state/world_context.json')
    for iid, row in rows.items():
        census_relevance.validate(case, behavior, row, observations[iid], frames, world)
    review['decisions'] = [copy.deepcopy(rows.get(r['issue_id'], r)) for r in review['decisions']]
    return dict(behavior, census_scope_review=review)


def completed_candidate(case):
    if not active(case) or not (Path(case)/RESULT).is_file():
        return None
    packet = request(case); result = read_json(Path(case)/RESULT)
    state = read_json(Path(case)/'state/progress.json')
    ledger = state.get('diagnostics', {}).get(task_key(state), {})
    _verify_quota_retry(state, packet, reading_completed=ledger.get('status') == 'completed')
    if ledger.get('status') != 'completed':
        return None  # Resume the persisted producer before adopting a prepared result.
    if (result.get('request_sha256') != packet['request_sha256']
            or ledger.get('request_sha256') != packet['request_sha256']
            or ledger.get('result_sha256') != sha256(Path(case)/RESULT)
            or result.get('candidate') != materialize(case, packet, result.get('response'))):
        raise input_binding.InputChanged('Observation scope supplement result changed')
    return result['candidate']


def run(runner, case):
    state = runner.progress(case)
    packet = request(case)
    key = task_key(state)
    unresolved = state.get('diagnostics', {}).get('unresolved_submissions', {})
    pending = any(s.get('stage') in STAGES and (s.get('status') in {'submitted','running','pending'}
        or s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
        and not s.get('deadline_task_resolved')) for s in state.get('sessions', []))
    if (completed_candidate(case) is not None and not pending
            and not any(stage in STAGES for stage in unresolved)):
        return
    _verify_quota_retry(state, packet)
    state['substage'] = 'behavior'
    runner.save_progress(case, state)
    candidate = Path(packet['output_path'])
    if not hasattr(runner.backend, 'submit') or not hasattr(runner.backend, 'poll'):
        raise ContractError('Observation scope review requires durable task receipts')
    prompt = ('Skill: behavior-understanding\n'
        '仅补充观察执行范围，读取冻结请求 '+str(Path(case)/REQUEST)+' 及其中 base_path、frames 全部图像。'
        '只输出 {base_sha256,decisions}，只回答 issue_ids 列出的项。不得输出完整 Behavior、修改其他回答、'
        'Actor、轨迹、初始状态、事件、World 或 Registry；本次不做身份关联或假定。'
        '区分“可能属于某候选”与“执行必须使用该观察”。无法证明无关时保留 unknown/needs_identity，'
        '不得为了通过而删证据。原已确认轨迹和候选车辆均保留。'
        '写入 '+packet['output_path']+' 后停止。'+census_relevance.prompt(case))
    def adopt(_session=None):
        current = request(case)
        ledger = runner.progress(case).get('diagnostics', {}).get(key, {})
        if (ledger.get('status') not in {'submitted','completed'} or not ledger.get('task_id')
                or _session is not None and ledger.get('session_id') != _session):
            raise ContractError('Observation scope supplement candidate lacks its producer')
        if ledger.get('status') == 'completed':
            completed_candidate(case)
            if read_json(candidate) != read_json(Path(case)/RESULT)['response']:
                raise input_binding.InputChanged('Completed scope supplement candidate changed')
            return
        response = read_json(candidate)
        raw = materialize(case, current, response)
        atomic_json(Path(case)/RESULT, dict(request_sha256=current['request_sha256'],
            response=response, candidate=raw))
        state = runner.progress(case)
        state['diagnostics'][key].update(status='completed', completed_at=now(),
            result_sha256=sha256(Path(case)/RESULT))
        runner.save_progress(case, state)
    runner.model_stage(case, key, 'global-video-understanding', prompt, adopt,
        precheck=adopt if candidate.is_file() else None, candidate_path=candidate, max_attempts=1)
