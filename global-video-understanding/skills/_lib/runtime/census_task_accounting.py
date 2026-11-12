"""Durable reservation/dispatch/receipt binding for new v5 initial closures.

Legacy unbound reservations are never guessed away. Explicit continuation and
contract repair already have their own durable authorization/receipt protocol.
"""
from pathlib import Path

from _lib.runtime.case_core import read_json, now

KEY = 'census_task_binding'
FREE = {'abandoned_before_task_id', 'credited_pipeline_contract_fault'}


def binding(runner, case, key, progress):
    from _lib.runtime.world_adjudication import ROOT, ACTIVE
    from _lib.contracts.census_adjudication import INFORMATION_REVISIONS
    if (key != 'world_closure' or getattr(runner, '_reconciliation_point_task', False)
            or (Path(case)/ACTIVE).exists()
            or not hasattr(runner.backend, 'submit') or not hasattr(runner.backend, 'poll')):
        return None
    path = Path(case)/ROOT/'initial/request.json'
    if not path.is_file(): return None
    packet = read_json(path)
    if packet.get('revision') not in INFORMATION_REVISIONS: return None
    return dict(request_sha256=packet['request_sha256'],
        planned_attempt=progress.get('attempts', {}).get(key, 0)+1,
        retry_epoch=progress.get('retry_epochs', {}).get(key, 0))


def reuse(progress, value):
    if value is None: return False
    from _lib.runtime.orchestrator import BackendError
    rows = [r for r in progress.get('diagnostics', {}).get('model_task_reservations', [])
            if r.get(KEY) and r.get('status') not in FREE]
    if any(r.get('status') == 'dispatching' for r in rows):
        raise BackendError('submission_unresolved',
            'Reserved census dispatch has no durable task receipt; reconcile before another submission')
    matches = [r for r in rows if r[KEY] == value]
    if any(r.get('status') == 'reserved' and r[KEY] != value for r in rows):
        raise BackendError('submission_unresolved', 'Pending census reservation binding changed; reconcile before retry')
    if not matches: return False
    if len(matches) != 1 or matches[0].get('status') != 'reserved':
        raise BackendError('submission_unresolved', 'Census reservation already submitted or has conflicting bindings')
    return True


def submit(runner, case, key, operation):
    state = runner.progress(case); value = binding(runner, case, key, state)
    if value is None: return operation()
    from _lib.runtime.world_adjudication import ROOT
    from _lib.contracts.census_adjudication import verify_packet
    from _lib.runtime.orchestrator import BackendError, submission_not_sent
    packet = read_json(Path(case)/ROOT/'initial/request.json')
    try: verify_packet(packet)
    except ValueError as error:
        raise BackendError('source_changed', str(error), submission_not_sent=True) from error
    if not reuse(state, value):
        raise BackendError('source_changed', 'Census dispatch lacks its reservation', submission_not_sent=True)
    row = next(r for r in state['diagnostics']['model_task_reservations']
               if r.get(KEY) == value and r.get('status') == 'reserved')
    row.update(status='dispatching', dispatch_started_at=now())
    runner.save_progress(case, state)
    try:
        return operation()
    except Exception as error:
        if submission_not_sent(error):
            row.update(status='reserved', rejection_at=now())
            runner.save_progress(case, state)
        raise


def bind_receipt(progress, key, receipt):
    rows = [r for r in progress.get('diagnostics', {}).get('model_task_reservations', [])
            if r.get('stage') == key and r.get(KEY) and r.get('status') == 'dispatching']
    if not rows: return
    from _lib.runtime.orchestrator import BackendError
    if (len(rows) != 1 or rows[0][KEY]['planned_attempt'] != receipt['attempt']
            or rows[0][KEY]['retry_epoch'] != receipt.get('retry_epoch', 0)):
        raise BackendError('submission_unresolved', 'Census receipt does not match its dispatch reservation')
    rows[0].update(status='submitted', task_id=receipt['task_id'], session_id=receipt['session_id'])


def abandon(progress, key, reason):
    rows = [r for r in progress.get('diagnostics', {}).get('model_task_reservations', [])
            if r.get('stage') == key and r.get(KEY) and r.get('status') in {'reserved', 'dispatching'}]
    if not rows: return False
    if len(rows) != 1:
        from _lib.contracts.v3 import ContractError
        raise ContractError('Cannot abandon ambiguous census reservations')
    rows[0].update(status='abandoned_before_task_id', reason=str(reason)[:200], at_abandoned=now())
    used = sum(r.get('additional', False) for r in progress['diagnostics']['model_task_reservations']
               if r.get('status') not in FREE and r.get('budget_scope') != 'authorized_transport_recovery')
    progress['diagnostics'].setdefault('model_recovery_budget', {}).update(used=used, remaining=max(0, 2-used))
    return True
