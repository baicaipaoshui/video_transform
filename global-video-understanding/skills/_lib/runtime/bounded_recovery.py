"""Versioned finite recovery accounting. No scene facts or provider calls here."""
import copy
from contextlib import contextmanager
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import now, read_json, sha256

REVISION = 'bounded-recovery.v1'
KEY = 'bounded_recovery'
ADOPTION = 'state/bounded_recovery_adoption.json'
LIMITS = dict(transport_retries=2, content_repairs=2, total_extra_tasks=8)
TRANSPORT = {'timeout', 'stream_error', 'gateway_5xx', 'gateway_429',
             'task_deadline_exceeded', 'task_status_unavailable', 'empty_response'}
FREE = {'abandoned_before_task_id', 'credited_pipeline_contract_fault'}
LOCAL_ERRORS = {'internal_pipeline_error', 'local_dependency_missing', 'local_io_error'}


class RecoveryStopped(ContractError):
    def __init__(self, code, **details):
        super().__init__(code, rule_id='recovery.bounded')
        self.code = code
        self.diagnostics = dict(stop_reason=code, **details)


def policy(case):
    case = Path(case)
    path = case/'state/execution_policy.json'
    frozen = read_json(path) if path.is_file() else {}
    if frozen.get(KEY) == REVISION:
        limits = frozen.get('recovery_limits', LIMITS)
    elif (case/ADOPTION).is_file():
        receipt = read_json(case/ADOPTION)
        body = dict(receipt); signature = body.pop('sha256', None)
        if (signature != digest(body) or receipt.get('revision') != REVISION
                or receipt.get('case_id') != case.name):
            raise RecoveryStopped('bounded_recovery_input_changed')
        for name, expected in receipt['input_hashes'].items():
            if not (case/name).is_file() or sha256(case/name) != expected:
                raise RecoveryStopped('bounded_recovery_input_changed', path=name)
        limits = receipt['limits']
    else:
        return None
    if (not isinstance(limits, dict) or set(limits) != set(LIMITS)
            or any(type(v) is not int or v < 1 for v in limits.values())):
        raise RecoveryStopped('bounded_recovery_policy_invalid')
    return limits


def enabled(case):
    return policy(case) is not None


def validate_progress(case, value):
    """Compose the attempt bound without changing historical frozen schemas."""
    limits = policy(case)
    if limits is None: return False
    from _lib.contracts.fidelity import validator
    base = validator('progress.schema.json')
    document = copy.deepcopy(base.schema)
    document['properties']['sessions']['items']['properties']['attempt']['maximum'] = 1 + limits['total_extra_tasks']
    base.evolve(schema=document).validate(value)
    return True


def require_terminal(progress):
    if progress.get('diagnostics', {}).get('unresolved_submissions') or any(
            s.get('status') in {'submitted', 'running', 'pending', 'returned'} or
            (s.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
             or s.get('recovered_from_poll_failure')) and not s.get('deadline_task_resolved')
            for s in progress.get('sessions', [])):
        raise RecoveryStopped('bounded_recovery_reconcile_required')


def family(stage):
    if stage.startswith('behavior') or stage.startswith('actor_'):
        return 'behavior'
    if stage in {'world_closure', 'geometry_readjudication', 'post_behavior_world_readjudication'}:
        return 'world_closure'
    return stage


def failure_kind(session):
    # An archived answer does not turn a local compiler/IO failure into a
    # model-content failure. Keep its task history and charged usage intact.
    if session.get('error_code') in LOCAL_ERRORS:
        return 'local'
    # A transport envelope with a rejected candidate is a content failure.
    if session.get('invalid_candidate_path') or session.get('candidate_validation_error'):
        return 'content'
    return 'transport' if session.get('error_code') in TRANSPORT else 'content'


def failure_disposition(session):
    """Explain the existing bounded route without creating another task ledger."""
    kind = failure_kind(session)
    details = session.get('validation_details')
    if not isinstance(details, dict):
        details = {}
    rule = session.get('rule_id') or details.get('rule_id') or ''
    retry = details.get('retry_kind')
    code = session.get('error_code') or ''
    if kind == 'transport':
        return dict(category='transport_fault', recovery='reconcile_task_then_bounded_transport_retry',
                    stop='task_unresolved_or_transport_quota_exhausted',
                    quota='transport_retries_and_total_extra_tasks')
    if kind == 'local':
        return dict(category='program_contract_error', recovery='fix_local_contract_then_revalidate_archive',
                    stop='local_contract_still_fails', quota='no_model_task')
    if retry == 'unsupported_capability' or rule.startswith('capability.') or code.startswith('unsupported_'):
        return dict(category='capability_limit', recovery='stop_with_capability_diagnostic',
                    stop='unsupported_capability', quota='no_model_task')
    if (retry in {'new_evidence', 'fact_conflict', 'identity_link_review',
                  'world_closure_adjudication'} or
            rule.startswith(('identity.', 'evidence.', 'lane.', 'motion.', 'census.'))):
        return dict(category='fact_evidence_gap', recovery='owner_evidence_revision_if_authorized',
                    stop='evidence_unresolved_or_content_quota_exhausted',
                    quota='content_repairs_and_total_extra_tasks')
    if code in {'invalid_json', 'missing_output', 'schema_validation_failed'}:
        return dict(category='expression_problem',
                    recovery='revalidate_immutable_archive_after_proven_normalization_else_bounded_content_repair',
                    stop='original_contract_still_fails_or_content_quota_exhausted',
                    quota='zero_for_deterministic_replay_else_content_repairs_and_total_extra_tasks')
    return dict(category='fact_evidence_gap', recovery='owner_evidence_revision_if_authorized',
                stop='evidence_unresolved_or_content_quota_exhausted',
                quota='content_repairs_and_total_extra_tasks')


@contextmanager
def content_revalidation(runner, case, stage, failure):
    """Use a fresh content verdict for this call without rewriting old sessions.

    A repaired compiler may expose a real evidence gap in the preserved answer.
    The verdict expires when this call ends or another task changes the ledger.
    """
    previous = getattr(runner, '_content_revalidation', None)
    current = None
    if failure is not None:
        if not failure.get('error_code') or failure['error_code'] in LOCAL_ERRORS | TRANSPORT:
            raise ValueError('Content revalidation requires a content failure')
        current = dict(case=str(Path(case).resolve()), stage=stage,
            sessions_sha256=digest(runner.progress(case).get('sessions', [])),
            failure=copy.deepcopy(failure))
    runner._content_revalidation = current
    try:
        yield
    finally:
        runner._content_revalidation = previous


def entries(progress):
    """Derive legacy classifications without rewriting the original ledger."""
    result = []
    offsets = {}
    sessions = progress.get('sessions', [])
    for row in progress.get('diagnostics', {}).get('model_task_reservations', []):
        stage = row['stage']
        index = offsets.get(stage, 0)
        matching = [s for s in sessions if s.get('stage') == stage]
        if row.get('status') != 'abandoned_before_task_id':
            offsets[stage] = index + 1
        if not row.get('additional') or row.get('status') == 'abandoned_before_task_id':
            continue
        prior = matching[index-1] if 0 < index <= len(matching) else {}
        kind = ('local_contract' if row.get('status') == 'credited_pipeline_contract_fault'
                else row.get('recovery_kind', failure_kind(prior)))
        result.append(dict(row, recovery_kind=kind,
                           recovery_group=row.get('recovery_group', family(stage))))
    diagnostics = progress.get('diagnostics', {})
    for name in ('world_contract_repair_reservations','intersection_structured_closure_reservations'):
        for row in diagnostics.get(name, []):
            if row.get('status') != 'abandoned_before_task_id':
                stage = row.get('model_stage',row.get('stage',name))
                result.append(dict(row,recovery_kind='content',recovery_group=family(stage)))
    for name in ('behavior_scope_review','behavior_scope_quota_retry','behavior_scope_quota_retry_2'):
        row = diagnostics.get(name,{})
        if row.get('status') in {'reserved','dispatching','submitted','completed'}:
            result.append(dict(row,recovery_kind='content',recovery_group='behavior'))
    return result


def preflight_total(case, progress, stage):
    limits = policy(case)
    if limits is None: return
    planned = stage in {'world','world_topology','behavior','world_closure','preview_review',
                        'intersection_review','analysis_reconciliation',
                        'conversion_relations','conversion_semantic_gap'} or (
                        stage.startswith('batch_') and stage[6:].isdigit())
    seen = any(s.get('stage') == stage for s in progress.get('sessions', []))
    if (not planned or seen) and remaining(case, progress) == 0:
        raise RecoveryStopped('bounded_recovery_total_exhausted', total_extra_tasks=limits['total_extra_tasks'])


def remaining(case, progress):
    limits = policy(case)
    return max(0, limits['total_extra_tasks'] - len(entries(progress))) if limits else None


def available(case, progress):
    value = remaining(case, progress)
    return value if value is not None else progress.get('diagnostics', {}).get('model_recovery_budget', {}).get('remaining', 2)


def require_content_slots(case, progress, groups):
    limits = policy(case)
    if limits is None: return
    ledger = entries(progress)
    if remaining(case, progress) < len(groups):
        raise RecoveryStopped('bounded_recovery_total_exhausted')
    for group in groups:
        count = sum(r['recovery_group'] == group and r['recovery_kind'] == 'content' for r in ledger)
        if count + groups.count(group) > limits['content_repairs']:
            raise RecoveryStopped('bounded_recovery_content_exhausted', group=group)


def fingerprint(session):
    details = session.get('validation_details') or {}
    def structural(node):
        if isinstance(node, list):
            return sorted((structural(x) for x in node), key=lambda x: str(x))
        if isinstance(node, dict):
            return {k:structural(v) for k,v in sorted(node.items())
                    if k not in {'message','reason','description','rationale','retry_kind'}}
        return node
    if not details and not session.get('rule_id'):
        return None  # Generic schema errors alone do not identify the same fault.
    return digest(dict(code=session.get('error_code'), rule=session.get('rule_id'),
                       actor=session.get('actor_id'), details=structural(details)))


def request_digest(case, stage):
    names = (['state/behavior_repair.request.json', 'state/behavior_input.request.json']
             if family(stage) == 'behavior' else ['state/'+stage+'.request.json'])
    for name in names:
        if (Path(case)/name).is_file(): return sha256(Path(case)/name)
    return None


def reserve(runner, case, stage, reservations, additional, extra):
    limits = policy(case)
    progress = runner.progress(case)
    diagnostics = progress.setdefault('diagnostics', {})
    # Caller has reconciled/reconstructed reservations, including old epochs.
    diagnostics['model_task_reservations'] = reservations
    require_terminal(progress)
    for row in reservations:
        if ('session_count_at_reserve' in row and row.get('status') not in FREE
                and not row.get('task_id') and not row.get('session_id')
                and sum(s.get('stage') == row['stage'] for s in progress.get('sessions', []))
                    <= row['session_count_at_reserve']):
            raise RecoveryStopped('bounded_recovery_reconcile_required', reservation=row)
    group = family(stage)
    prior = [s for s in progress.get('sessions', []) if family(s.get('stage','')) == group]
    last = prior[-1] if prior else {}
    kind = failure_kind(last)
    revalidation = getattr(runner, '_content_revalidation', None)
    fresh_content = (kind == 'local' and revalidation is not None
        and revalidation['case'] == str(Path(case).resolve()) and revalidation['stage'] == stage
        and revalidation['sessions_sha256'] == digest(progress.get('sessions', [])))
    if fresh_content:
        last = dict(last, **revalidation['failure'])
        kind = 'content'
    request_hash = request_digest(case, stage)
    ledger = entries(progress)
    used = len(ledger)
    count = sum(r['recovery_group'] == group and r['recovery_kind'] == kind
                and (kind != 'transport' or r.get('recovery_request') in {None, request_hash}) for r in ledger)
    disposition = failure_disposition(last)
    diagnostic = dict(revision=REVISION, limits=limits, used=used,
        remaining=max(0, limits['total_extra_tasks']-used), next_stage=stage,
        next_kind=kind, failure_disposition=disposition, group=group, group_used=count,
        primary_failure={k:last[k] for k in ('error_code','rule_id','message','validation_details','task_id') if k in last})
    diagnostics[KEY] = diagnostic
    def stop(code):
        diagnostic['stop_reason'] = code
        runner.save_progress(case, progress)
        raise RecoveryStopped(code, primary_failure=diagnostic['primary_failure'], recovery=diagnostic)
    if kind == 'local':
        # The stage must revalidate its preserved output before reaching this
        # reservation gate. Reopening a Case cannot authorize a model repair
        # of a still-failing local dependency or compiler.
        stop(last['error_code'])
    if disposition['category'] == 'capability_limit':
        stop('bounded_recovery_capability_limit')
    from _lib.runtime.behavior_timeout_retry import available as timeout_grant
    grant = timeout_grant(case, progress, stage) if additional else None
    if additional:
        if used >= limits['total_extra_tasks']:
            stop('bounded_recovery_total_exhausted')
        if count >= limits['transport_retries' if kind == 'transport' else 'content_repairs'] and not (grant and kind == 'transport'):
            stop('bounded_recovery_' + kind + '_exhausted')
        if (not fresh_content and len(prior) > 1 and prior[-1].get('status') == prior[-2].get('status') == 'invalid_output'
                and fingerprint(prior[-1]) is not None and fingerprint(prior[-1]) == fingerprint(prior[-2])):
            stop('bounded_recovery_no_progress')
    if grant:
        if kind != 'transport':
            stop('behavior_timeout_retry_failure_changed')
        grant.update(status='consumed', consumed_at=now())
        extra = dict(extra, behavior_timeout_retry_binding=grant['binding_sha256'])
    authorization = next((r for r in diagnostics.get('transport_recovery_authorizations', [])
                          if r.get('stage') == stage and r.get('status') == 'authorized'), None)
    if additional and authorization:
        if (stage == 'conversion_semantic_gap'
                and authorization.get('conversion_binding') != runner._conversion_retry_binding(case, progress)):
            raise RecoveryStopped('bounded_recovery_input_changed')
        # Preserve one-use grants/session choice, while every task still obeys
        # the finite category and total limits checked above.
        authorization.update(status='consumed', consumed_at=now())
        extra = dict(extra, budget_scope='authorized_transport_recovery',
                     authorization_id=authorization['authorization_id'])
    reservations.append(dict(stage=stage, additional=additional, at=now(),
        origin='reserved_before_submission', recovery_kind=kind,
        recovery_group=group, recovery_request=request_hash,
        session_count_at_reserve=sum(s.get('stage') == stage for s in progress.get('sessions', [])), **extra))
    diagnostic.update(used=used+int(additional), remaining=max(0,limits['total_extra_tasks']-used-int(additional)))
    # The legacy budget is preserved on adopted Cases; consumers use remaining().
    runner.save_progress(case, progress)


def bind_receipt(progress, stage, receipt):
    for row in reversed(progress.get('diagnostics', {}).get('model_task_reservations', [])):
        if (row.get('stage') == stage and 'recovery_kind' in row
                and not row.get('task_id') and not row.get('session_id')):
            row.update({k:receipt[k] for k in ('task_id','session_id') if k in receipt})
            return
