"""Local, explicit adoption of a core-scene approximation after a failed scope review."""
from pathlib import Path
import re

from _lib.contracts import core_scene_scope as contract
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime import downstream_scope_review as strict
from _lib.runtime.case_core import read_json, atomic_json, sha256, now

REQUEST = 'state/core_scene_scope.accepted.json'


def active(case):
    return (Path(case)/REQUEST).is_file()


def source(case):
    case = Path(case).resolve()
    packet = strict.request(case)
    state = read_json(case/'state/progress.json')
    sessions = [s for s in state['sessions'] if s['stage'] == strict.KEY]
    if (len(sessions) != 1 or sessions[0].get('status') != 'invalid_output'
            or sessions[0].get('rule_id') != 'execution_scope.review'
            or sessions[0].get('message') != 'Execution scope remains relevant/unknown or lacks explicit independence evidence'
            or not sessions[0].get('task_id') or not sessions[0].get('session_id')
            or sessions[0].get('recovered_from_poll_failure') and not sessions[0].get('deadline_task_resolved')):
        raise ContractError('Core scene requires a terminal, inconclusive execution scope review')
    row = sessions[0]
    archive = Path(row.get('invalid_candidate_path', '')).resolve()
    if not archive.is_relative_to(case/'diagnostics/invalid_candidates') or not archive.is_file():
        raise ContractError('Core scene scope archive is outside this Case or missing')
    name = re.fullmatch(r'execution_scope_review\.attempt_(\d+)\.([0-9a-f]{16})(?:\.\d+)?\.json', archive.name)
    if not name or int(name[1]) != row.get('attempt') or name[2] != sha256(archive)[:16]:
        raise ContractError('Core scene scope archive differs from its quarantined output')
    ledger = state.get('diagnostics', {}).get(strict.KEY, {})
    if ledger.get('request_sha256') != packet['request_sha256'] or ledger.get('task_limit') != 1:
        raise ContractError('Core scene source review authorization changed')
    behavior_sessions = [s for s in state['sessions'] if s.get('stage') == 'behavior']
    if (not behavior_sessions or {k: behavior_sessions[-1].get(k) for k in ('task_id', 'session_id')} != packet['producer']
            or behavior_sessions[-1].get('status') != 'invalid_output'
            or Path(behavior_sessions[-1].get('invalid_candidate_path', '')).resolve() != Path(packet['base_path']).resolve()):
        raise ContractError('Core scene source Behavior producer changed')
    producer = {k: row[k] for k in ('task_id', 'session_id', 'status', 'rule_id')}
    return packet, archive, producer


def request(case):
    """Replay on restart and on every live receipt consumption; never submit a task."""
    case = Path(case).resolve()
    value = read_json(case/REQUEST)
    if (value.get('revision') != contract.REVISION or value.get('case_id') != case.name
            or value.get('sha256') != digest({k: v for k, v in value.items() if k != 'sha256'})):
        raise ContractError('Core scene authorization changed')
    packet, archive, producer = source(case)
    if (value['scope_request_sha256'] != packet['request_sha256'] or value['producer'] != producer
            or value['archive_path'] != str(archive) or value['archive_sha256'] != sha256(archive)):
        raise ContractError('Core scene source response/producer changed')
    world, behavior, review, frames = strict.inputs(case, read_json(packet['base_path']))
    if (digest(world) != packet['world_digest'] or digest(behavior) != packet['behavior_digest']
            or review != packet['review']):
        raise ContractError('Core scene normalized Behavior or World changed')
    receipt = contract.make(world, behavior, frames, packet, read_json(archive), value['core_actor_ids'])
    if receipt != value['receipt']:
        raise ContractError('Core scene receipt changed')
    return value


def authorize(runner, case, core):
    case = Path(case).resolve()
    if active(case):
        value = request(case)
        if sorted(core) != value['core_actor_ids']:
            raise ContractError('Existing core scene selection cannot be silently changed')
        return value
    from _lib.runtime.bounded_recovery import require_terminal
    state = runner.progress(case)
    require_terminal(state)
    if (state.get('status') not in {'failed', 'needs_review'} or state.get('artifacts')
            or any((case/p).exists() for p in ('state/behavior.json', 'final/analysis.json',
                   'state/behavior_repair.request.json', 'diagnostics/behavior_scope_review/request.json', strict.RESULT))):
        raise ContractError('Core scene adoption requires an unaccepted failed Behavior')
    packet, archive, producer = source(case)
    if state['sessions'][-1].get('task_id') != producer['task_id']:
        raise ContractError('Core scene adoption requires the latest failed scope producer')
    world, behavior, review, frames = strict.inputs(case, read_json(packet['base_path']))
    from _lib.behavior.compile import validate_compiled_behavior
    validate_compiled_behavior(behavior, world, read_json(case/'state/actor_registry.json'),
                              frames, execution=False, allow_pending_identity=True)
    receipt = contract.make(world, behavior, frames, packet, read_json(archive), core)
    value = dict(revision=contract.REVISION, case_id=case.name, core_actor_ids=sorted(core),
        scope_request_sha256=packet['request_sha256'], archive_path=str(archive),
        archive_sha256=sha256(archive), producer=producer, receipt=receipt, authorized_at=now())
    value['sha256'] = digest(value)
    # One atomic local artifact. No recovery grant, task, budget reset or rewrite
    # of the rejected answer/strict-review ledger accompanies this authorization.
    atomic_json(case/REQUEST, value)
    return value


def load(case, behavior, review):
    if not active(case): return None
    value = request(case)
    receipt = value['receipt']
    if digest(behavior) != receipt['behavior_digest'] or review != receipt['review']:
        raise ContractError('Core scene receipt belongs to different Behavior evidence')
    return receipt


def completed_candidate(case):
    if not active(case): return None
    request(case)
    return read_json(strict.request(case)['base_path'])
