"""One explicit, task-bound Behavior timeout retry; never reset any budget."""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import now, read_json, sha256
from _lib.runtime import bounded_recovery as budget

KEY = 'behavior_timeout_retry'
REVISION = 'behavior-timeout-retry.v1'


def fail(reason):
    raise budget.RecoveryStopped('behavior_timeout_retry_' + reason)


def checked(state):
    value = state.get('diagnostics', {}).get(KEY)
    if value is None:
        return None
    binding = value.get('binding', {})
    if (value.get('binding_sha256') != digest(binding) or binding.get('revision') != REVISION
            or value.get('status') not in {'authorized', 'consumed'}):
        fail('binding_changed')
    count = binding['session_count']
    if digest(state.get('sessions', [])[:count]) != binding['sessions_sha256']:
        fail('history_changed')
    return value


def verify_inputs(case, value):
    case = Path(case).resolve()
    binding = value['binding']
    if binding['case_id'] != case.name or binding['case_dir'] != str(case):
        fail('binding_changed')
    for name, signature in binding['input_hashes'].items():
        if not Path(name).is_file() or sha256(name) != signature:
            fail('input_changed')
    if budget.request_digest(case, 'behavior') != binding['recovery_request']:
        fail('input_changed')
    if any((case/name).exists() for name in ('state/behavior.candidate.json', 'state/behavior.json', 'final/analysis.json')):
        fail('output_exists')


def authorize(runner, case, failed_task_id):
    """Authorize one task, plus one bound continuation after its runtime timeout."""
    case = Path(case).resolve()
    state = runner.progress(case)
    existing = checked(state)
    if existing:
        if existing['binding']['failed_task_id'] == failed_task_id:
            if existing['status'] == 'authorized':
                verify_inputs(case, existing)
            return existing
        if (existing['status'] != 'consumed'
                or existing['binding'].get('generation', 1) != 1):
            fail('already_bound')
        verify_inputs(case, existing)
    budget.require_terminal(state)
    limits = budget.policy(case)
    sessions = state.get('sessions', [])
    last = next((s for s in reversed(sessions) if s.get('stage') == 'behavior'), {})
    resolved_timeout = (last.get('error_code') == 'timeout'
                        or (last.get('error_code') == 'task_deadline_exceeded'
                            and last.get('deadline_task_resolved')
                            and last.get('remote_error_code') == 'timeout'))
    if (not limits or state.get('status') not in {'failed','needs_review'}
            or state.get('artifacts') or last.get('task_id') != failed_task_id
            or not failed_task_id or not last.get('session_id')
            or last.get('status') != 'failed' or not resolved_timeout
            or last.get('invalid_candidate_path') or last.get('candidate_validation_error')
            or (state.get('error') or {}).get('code') != 'bounded_recovery_transport_exhausted'
            or (state.get('error') or {}).get('stage') != 'behavior'):
        fail('ineligible')
    if budget.remaining(case, state) < 1:
        fail('total_exhausted')
    from _lib.behavior import input_binding, bounded_repair
    request = input_binding.verify(case)
    if not request:
        fail('unbound_request')
    input_binding.require_session(case, last['session_id'])
    bounded_repair.request(case)  # Any optional patch must retain its own proof.
    recovery_request = budget.request_digest(case, 'behavior')
    used = sum(r['recovery_group'] == 'behavior' and r['recovery_kind'] == 'transport'
        and r.get('recovery_request') in {None, recovery_request} for r in budget.entries(state))
    if used < limits['transport_retries']:
        fail('ordinary_retry_available')
    if existing:
        reservation = next((r for r in reversed(state.get('diagnostics', {}).get(
                'model_task_reservations', [])) if r.get('stage') == 'behavior'), {})
        if (reservation.get('task_id') != failed_task_id
                or reservation.get('session_id') != last['session_id']
                or reservation.get('behavior_timeout_retry_binding') != existing['binding_sha256']):
            fail('history_changed')
    paths = [case/'case.json', case/'state/binding.json', case/'state/execution_policy.json',
             case/input_binding.REQUEST]
    paths += [case/name for name in ('state/behavior_repair.request.json', budget.ADOPTION)
              if (case/name).is_file()]
    binding = dict(revision=REVISION, case_id=case.name, case_dir=str(case),
        failed_task_id=failed_task_id, failed_session=copy.deepcopy(last),
        session_count=len(sessions), sessions_sha256=digest(sessions),
        recovery_request=recovery_request,
        input_hashes=request['input_hashes'] | {str(p):sha256(p) for p in paths},
        authorized_at=now(), original_error=copy.deepcopy(state['error']))
    if existing:
        binding.update(generation=2, supersedes_binding_sha256=existing['binding_sha256'],
                       resume_session_id=last['session_id'])
    value = dict(binding=binding, binding_sha256=digest(binding), status='authorized')
    verify_inputs(case, value)
    state.setdefault('diagnostics', {})[KEY] = value
    runner.save_progress(case, state)
    return value


def available(case, state, stage):
    """Validate before reservation; consumed grants cannot fund another attempt."""
    if stage != 'behavior':
        return None
    value = checked(state)
    if value is None:
        return None
    if value['status'] == 'consumed':
        fail('consumed')
    budget.require_terminal(state)
    if len(state.get('sessions', [])) != value['binding']['session_count']:
        fail('history_changed')
    verify_inputs(case, value)
    return value
