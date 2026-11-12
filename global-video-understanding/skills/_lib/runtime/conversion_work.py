"""Two text-only sessions, immutable worker saves and owner-validated continuation.

Workers publish cumulative save_NNNN.json files. A save is not an accepted final
decision. Only this adapter commits checkpoints and the ordinary motion gate
can authorize the deterministic Converter. Existing single-session Cases do
not enter this versioned workflow.
"""
import json
import hashlib
import re
import sqlite3
from pathlib import Path
from string import Template

from _lib.contracts import semantic_motion as motion
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.paths import SCENARIO_ROOT
from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'conversion-continuation.v1'
STAGES = ('conversion_relations', 'conversion_semantic_gap')


def source_witness_guidance(request):
    """Expose relation gates already enforced by semantic_motion validation."""
    from _lib.contracts.motion_witnesses import POLICY_KEY, REVISION
    if request.get('policy', {}).get(POLICY_KEY) != REVISION:
        return None
    overtake_events = [event['event_id'] for event in request.get('events', [])
                       if event.get('event_type') == 'overtake']
    return dict(
        overtake_event_ids=overtake_events,
        instruction=('An overtakes relation requires a matching event_type=overtake event and '
                     'frozen numeric endpoint positions proving full clearance. If either is absent, '
                     'passing-in-progress remains alongside/approaching and completion is unconfirmed.'))


def witness_receipt_guidance(receipts):
    if not receipts:
        return None
    return dict(
        source_paths=sorted(receipts),
        instruction=('Python already validated these exact source receipts and will add '
                     'program_verified coverage for executable text and qualitative_only coverage '
                     'for retained body-part observations. Omit them from worker '
                     'coverage and citations; complete means all remaining_sources are covered.'))
DIRECTORY = 'state/conversion_work'
RETRY_CODES = {'task_deadline_exceeded', 'task_status_unavailable', 'timeout',
               'model_stream_interrupted', 'gateway_5xx', 'empty_response', 'invalid_json', 'missing_output',
               'schema_validation_failed'}


def _session_history_rows(path, session_id):
    connection = sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)
    try:
        return connection.execute(
            'select agent_id, kind, role, name, content, tool_call_id, tool_state, tool_input '
            'from conversation_history where session_id=? order by seq', (session_id,)).fetchall()
    finally:
        connection.close()


def _session_history_sha256(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    if not path.is_file():
        return False
    policy = read_json(path)
    revision = policy.get('conversion_continuation')
    if revision is None:
        return False
    if revision != REVISION or policy.get('conversion_semantic_gap') != motion.REVISION:
        raise ContractError('Unsupported conversion continuation policy')
    return True


def validate_save(request, stage, value, request_sha256, previous=None):
    fields = {'request_sha256', 'complete'} | (
        {'relations', 'coverage'} if stage == STAGES[0] else {'plans'})
    if (not isinstance(value, dict) or set(value) != fields
            or value['request_sha256'] != request_sha256 or type(value['complete']) is not bool):
        raise ValueError('Conversion checkpoint fields/request binding invalid')
    if stage == STAGES[0]:
        motion.validate_relations(request, value['coverage'], value['relations'], partial=True)
        collection, key = 'coverage', 'source'
    else:
        from _lib.contracts.retained_motion import KEY
        owned = (request.get(KEY) or {}).get('actors', {})
        plans = value['plans']
        if not isinstance(plans, list) or any(not isinstance(p, dict) for p in plans):
            raise ValueError('Conversion plans must be objects')
        motion.validate_plans(request, [p for p in plans if p.get('actor_id') not in owned], partial=True)
        collection, key = 'plans', 'actor_id'
    if previous and not {p[key] for p in previous[collection]} <= {p[key] for p in value[collection]}:
        raise ValueError('Conversion checkpoint drops previously saved work')
    return value


class ConversionWork:
    def __init__(self, owner, case, request):
        self.owner, self.case, self.request = owner, Path(case), request
        self.root = self.case/DIRECTORY
        self.path = self.root/'workflow.json'
        if owner is None and not self.path.is_file():
            raise ContractError('Conversion continuation proof is missing')
        self.state = (read_json(self.path) if self.path.is_file() else
                      {'revision': REVISION, 'request_sha256': request['request_sha256'], 'stages': {}})
        self.bound()
        if not self.path.exists():
            self.persist()

    def bound(self):
        if (self.state.get('revision') != REVISION
                or self.state.get('request_sha256') != self.request['request_sha256']
                or sha256(self.case/'final/analysis.json') != self.request['analysis_sha256']
                or read_json(self.case/'state/conversion_semantic_gap.request.json') != self.request):
            raise ContractError('Conversion continuation frozen input changed')

    def persist(self):
        atomic_json(self.path, self.state)

    def checkpoint(self, stage):
        record = self.state['stages'].get(stage, {}).get('checkpoint')
        if not record:
            return None
        path = self.root/'accepted'/f"{record['content_sha256']}.json"
        if sha256(path) != record['sha256']:
            raise ContractError('Accepted conversion checkpoint changed')
        value = read_json(path)
        validate_save(self.request, stage, value, record['request_sha256'])
        return value

    def sessions(self, stage, run):
        return [s for s in self.owner.progress(self.case).get('sessions', [])[run['session_start']:]
                if s.get('stage') == stage]

    def terminal(self, stage, run):
        """No task can be replaced just because its local deadline elapsed."""
        records = self.sessions(stage, run)
        if not records:
            return False
        for record in records:
            uncertain = (record.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
                         and not record.get('deadline_task_resolved'))
            uncertain |= record.get('recovered_from_poll_failure', False)
            if record.get('status') in {'submitted', 'pending', 'running'}:
                return False
            if uncertain:
                remote = self.owner.backend.poll(record['task_id'], 'json-coverter')
                if remote.get('status') not in {'completed', 'failed'}:
                    return False
                progress = self.owner.progress(self.case)
                current = next(s for s in progress['sessions'] if s.get('task_id') == record['task_id'])
                current['deadline_task_resolved'] = True
                current.pop('recovered_from_poll_failure', None)
                if remote['status'] == 'failed':
                    current['error_code'] = remote.get('error') or current.get('error_code')
                self.owner.save_progress(self.case, progress)
                record.update(current)
        return True

    def collect(self, stage, run):
        """Ignore incomplete worker writes; never discard an older valid save."""
        self.bound()
        folder = self.root/stage/f"run_{run['generation']:04d}"
        if read_json(folder/'request.json') != run['request']:
            raise ContractError('Conversion session request changed')
        previous = self.checkpoint(stage)
        for seq in range(run.get('accepted_sequence', 0) + 1, 257):
            path = folder/f'save_{seq:04d}.json'
            if not path.is_file():
                continue
            try:
                if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('Checkpoint must be a regular JSON file under 8 MiB')
                # Parse one snapshot; never hash a different second file read.
                raw = path.read_bytes()
                value = json.loads(raw)
                validate_save(self.request, stage, value, run['request']['request_sha256'], previous)
            except (ValueError, TypeError, KeyError, OSError) as error:
                run.setdefault('rejected_saves', {})[str(seq)] = str(error)[:500]
                continue
            signature = digest(value)
            accepted = self.root/'accepted'/f'{signature}.json'
            if not accepted.exists():
                atomic_json(accepted, value)
            stored_sha = sha256(accepted)
            if read_json(accepted) != value:
                raise ContractError('Conversion checkpoint snapshot collision/change')
            self.state['stages'][stage]['checkpoint'] = {
                'sha256': stored_sha, 'content_sha256': signature,
                'request_sha256': run['request']['request_sha256'],
                'generation': run['generation'], 'sequence': seq}
            run['accepted_sequence'] = seq
            run.get('rejected_saves', {}).pop(str(seq), None)
            previous = value
            self.persist()
        self.persist()
        return previous

    def finish_stage(self, stage):
        if stage == STAGES[0] and self.state['stages'].get(stage, {}).get('external_completion'):
            receipt = self.state['stages'][stage]['external_completion']
            if self.verify_external_relations(receipt) != receipt:
                raise ContractError('External conversion completion proof changed')
        value = self.checkpoint(stage)
        if value is None or not value['complete']:
            raise ValueError('Conversion checkpoint is incomplete; continue remaining work')
        if stage == STAGES[0]:
            analysis = read_json(self.case/'final/analysis.json')
            from _lib.contracts.retained_motion import complete_relations
            from _lib.contracts import bounded_execution, motion_witnesses
            value = dict(value)
            value['coverage'], value['relations'] = bounded_execution.complete_relations(
                self.request, value['coverage'], value['relations'])
            value['coverage'], value['relations'] = complete_relations(
                self.request, value['coverage'], value['relations'])
            value['coverage'], value['relations'] = motion_witnesses.complete(
                self.request, value['coverage'], value['relations'])
            motion.validate_relations(self.request, value['coverage'], value['relations'],
                observation_only_ids=motion.observation_only_background_ids(analysis))
            return value
        relations = self.finish_stage(STAGES[0])
        entry = self.state['stages'][stage]
        if entry.get('program_generated'):
            from _lib.contracts.bounded_execution import plan
            if plan(self.request, relations['relations'], entry.get('program_seed', [])) != value['plans']:
                raise ContractError('Program-generated numerical checkpoint changed')
        candidate = {'revision': motion.REVISION, 'request_sha256': self.request['request_sha256'],
                     'plans': value['plans'], 'coverage': relations['coverage'], 'relations': relations['relations']}
        analysis = read_json(self.case/'final/analysis.json')
        from _lib.contracts.motion_witnesses import POLICY_KEY
        return motion.candidate_to_decision(analysis, self.request['analysis_sha256'], candidate, digest(candidate),
            witness_revision=self.request['policy'].get(POLICY_KEY))

    def verify_external_relations(self, receipt):
        """Reconcile a stopped json-coverter task with its original save files."""
        stage = STAGES[0]
        entry = self.state['stages'].get(stage, {})
        if len(entry.get('runs', [])) != 1:
            raise ContractError('External relations require one frozen stage run')
        run = entry['runs'][0]
        folder = self.root/stage/f"run_{run['generation']:04d}"
        stage_request = run['request']
        if (read_json(folder/'request.json') != stage_request
                or stage_request['root_request_sha256'] != self.request['request_sha256']
                or stage_request['request_sha256'] != digest({k: v for k, v in stage_request.items()
                                                             if k != 'request_sha256'})
                or sha256(folder/'request.json') != receipt['stage_request_sha256']
                or self.request['request_sha256'] != receipt['root_request_sha256']):
            raise ContractError('External conversion stage request changed')
        frozen = folder/'frozen_prompt.txt'
        prompt = frozen.read_text(encoding='utf-8')
        if hashlib.sha256(prompt.encode()).hexdigest() != receipt['prompt_sha256']:
            raise ContractError('External conversion frozen prompt changed')
        template = SCENARIO_ROOT/'json-coverter/skills/case-conversion/references/conversion-continuation-prompt.md'
        if sha256(template) == receipt['template_sha256'] and prompt != Template(
                template.read_text(encoding='utf-8')).substitute(
                    request_path=folder/'request.json', output_dir=folder):
            raise ContractError('External conversion prompt differs from stage template')
        attempt = Path(receipt['attempt_dir']).resolve(strict=True)
        history = Path(receipt['history_db']).resolve(strict=True)
        if not (attempt.is_relative_to(receipt['isolation_root'])
                and history.is_relative_to(receipt['isolation_root'])
                and history.parent.name == 'json-coverter'
                and self.case.resolve().is_relative_to(receipt['isolation_root'])):
            raise ContractError('External conversion proof is outside isolation root')
        names = ('post_started.json', 'payload.json', 'submission_receipt.json', 'last_status.json')
        paths = {name: attempt/name for name in names}
        paths['history.db'] = history
        if (set(receipt['file_sha256']) != set(paths)
                or any(p.is_symlink() or not p.is_file()
                       or name != 'history.db' and sha256(p) != receipt['file_sha256'][name]
                       for name, p in paths.items())):
            raise ContractError('External conversion task archive changed')
        started, payload, submitted, status = (read_json(paths[name]) for name in names)
        if (started.get('route') != '/api/console/chat/task' or started.get('post_calls_max') != 1
                or started.get('payload_sha256') != sha256(paths['payload.json'])
                or submitted.get('task_id') != receipt['task_id']
                or not re.fullmatch(r'task-[0-9a-f]+', receipt['task_id'])
                or payload.get('channel') != 'console'
                or payload.get('input') != [{'content': [{'type': 'text', 'text': prompt}]}]
                or status.get('status') != 'finished'
                or status.get('result', {}).get('status') != 'completed'
                or status['result'].get('session_id') != payload.get('session_id')
                or payload.get('session_id') != receipt['session_id']
                or status['result'].get('id') != receipt['response_id']):
            raise ContractError('External conversion task terminal/prompt binding invalid')
        calls, successes = {}, set()
        for item in status['result'].get('output', []):
            data = next((part.get('data') for part in item.get('content', [])
                         if isinstance(part, dict) and isinstance(part.get('data'), dict)), {})
            if data.get('name') != 'write_file':
                continue
            call_id = data.get('call_id')
            if item.get('type') == 'plugin_call':
                args = json.loads(data['arguments'])
                path = Path(args['file_path'])
                if (call_id in calls or path.parent != folder or '..' in path.parts
                        or not re.fullmatch(r'save_\d{4}\.json', path.name)):
                    raise ContractError('External conversion write destination invalid')
                calls[call_id] = (path, args['content'])
            elif item.get('type') == 'plugin_call_output' and data.get('state') == 'success':
                successes.add(call_id)
        if (not calls or set(calls) != successes
                or len({path for path, _ in calls.values()}) != len(calls)):
            raise ContractError('External conversion save writes lack success evidence')
        saved = {path for path, content in calls.values() if path.is_file() and not path.is_symlink()
                 and path.read_text(encoding='utf-8') == content}
        if saved != {path for path, _ in calls.values()} or saved != set(folder.glob('save_*.json')):
            raise ContractError('External conversion model saves changed')
        rows = _session_history_rows(history, payload['session_id'])
        if (receipt.get('history_session_sha256') is not None
                and _session_history_sha256(rows) != receipt['history_session_sha256']):
            raise ContractError('External conversion session history changed')
        if not rows or any(agent != 'json-coverter' for agent, *_ in rows):
            raise ContractError('External conversion session belongs to another Agent')
        if not any(kind == 'context_msg' and role == 'user' and content == prompt
                   for _, kind, role, _, content, _, _, _ in rows):
            raise ContractError('External conversion session prompt is missing')
        for call_id, (path, content) in calls.items():
            if not any(kind == 'tool_result' and name == 'write_file' and row_id == call_id
                       and state == 'success' and str(path) in result
                       for _, kind, _, name, result, row_id, state, _ in rows):
                raise ContractError('External conversion session write is missing')
        final_id = next(reversed(calls))
        if not any(kind == 'model_turn' and name == 'write_file' and row_id == final_id
                   and json.loads(tool_input)['content'] == calls[final_id][1]
                   for _, kind, _, name, _, row_id, _, tool_input in rows if tool_input):
            raise ContractError('External conversion final model save is missing')
        checkpoint = entry.get('checkpoint')
        if (not checkpoint or checkpoint.get('sha256') != receipt['checkpoint_sha256']
                or checkpoint.get('sequence') != max(int(p.stem[5:]) for p in saved)
                or checkpoint.get('request_sha256') != run['request']['request_sha256']
                or run.get('accepted_sequence') != checkpoint['sequence']):
            raise ContractError('External conversion accepted checkpoint differs from model saves')
        self.checkpoint(stage)
        final_save = folder/f"save_{checkpoint['sequence']:04d}.json"
        if read_json(final_save) != self.checkpoint(stage):
            raise ContractError('External conversion accepted snapshot differs from final model save')
        return receipt

    def adopt_external_relations(self, *, isolation_root, attempt_dir, history_db, task_id,
                                 expected_coverage=None, expected_relations=None):
        """Commit only the existing accepted checkpoint; never submit a task."""
        self.bound()
        entry = self.state['stages'].get(STAGES[0], {})
        if (not entry.get('external_completion') and any(
                row.get('stage') == STAGES[0]
                for row in read_json(self.case/'state/progress.json').get('sessions', []))):
            raise ContractError('Existing relations sessions require the original lifecycle')
        receipt = self.external_relations_receipt(isolation_root, attempt_dir, history_db, task_id)
        self.verify_external_relations(receipt)
        value = self.finish_stage(STAGES[0])
        if (expected_coverage is not None and len(value['coverage']) != expected_coverage
                or expected_relations is not None and len(value['relations']) != expected_relations):
            raise ContractError('External conversion accepted coverage/relation count differs')
        entry = self.state['stages'][STAGES[0]]
        if entry.get('complete'):
            if entry.get('external_completion') != receipt:
                raise ContractError('Conversion relations already completed by another task')
            return value
        entry['external_completion'] = receipt
        entry['complete'] = True
        self.persist()
        return value

    def external_relations_receipt(self, isolation_root, attempt_dir, history_db, task_id):
        attempt = Path(attempt_dir).resolve(strict=True)
        history = Path(history_db).resolve(strict=True)
        root = Path(isolation_root).resolve(strict=True)
        status = read_json(attempt/'last_status.json')
        template = SCENARIO_ROOT/'json-coverter/skills/case-conversion/references/conversion-continuation-prompt.md'
        prompt = self.root/STAGES[0]/'run_0001'/'frozen_prompt.txt'
        prior = self.state['stages'].get(STAGES[0], {}).get('external_completion')
        file_sha256 = {name: sha256(path) for name, path in {
            **{name: attempt/name for name in ('post_started.json', 'payload.json',
                                               'submission_receipt.json', 'last_status.json')},
            'history.db': history}.items()}
        if prior:
            # The database is appendable. Keep its original audit fingerprint;
            # replay validates the selected session, not unrelated DB pages.
            file_sha256['history.db'] = prior['file_sha256']['history.db']
        receipt = {
            'task_id': task_id, 'response_id': status.get('result', {}).get('id'),
            'session_id': status.get('result', {}).get('session_id'),
            'isolation_root': str(root), 'attempt_dir': str(attempt), 'history_db': str(history),
            'root_request_sha256': self.request['request_sha256'],
            'stage_request_sha256': sha256(prompt.parent/'request.json'),
            'checkpoint_sha256': self.state['stages'][STAGES[0]]['checkpoint']['sha256'],
            'prompt_sha256': sha256(prompt),
            'template_sha256': prior['template_sha256'] if prior else sha256(template),
            'file_sha256': file_sha256,
        }
        if prior and 'history_session_sha256' in prior:
            receipt['history_session_sha256'] = prior['history_session_sha256']
        elif not prior:
            receipt['history_session_sha256'] = _session_history_sha256(
                _session_history_rows(history, receipt['session_id']))
        return receipt

    def new_run(self, stage, entry):
        generation = len(entry['runs']) + 1
        previous = self.checkpoint(stage)
        relations = self.finish_stage(STAGES[0]) if stage == STAGES[1] else None
        from _lib.contracts.retained_motion import KEY
        program = self.request.get(KEY) or {}
        from _lib.contracts.bounded_execution import KEY as bounded_key
        inherited_sources = (self.request.get(bounded_key) or {}).get('sources', {})
        unobserved_sources = (self.request.get(bounded_key) or {}).get('unobserved_sources', {})
        from _lib.contracts.motion_witnesses import KEY as witness_key
        receipts = self.request.get(witness_key, {}).get('receipts', {})
        work = {'revision': REVISION, 'stage': stage, 'generation': generation,
                'root_request_sha256': self.request['request_sha256'],
                'root_request_path': str(self.case/'state/conversion_semantic_gap.request.json'),
                'analysis_path': str(self.case/'final/analysis.json'),
                'previous': previous, 'accepted_relations': relations,
                'feedback': entry.get('last_error'),
                'remaining_sources': [p['source'] for p in self.request['evidence']
                    if p['source'] not in program.get('sources', {})
                    and p['source'] not in inherited_sources
                    and p['source'] not in unobserved_sources
                    and p['source'] not in receipts
                    and p['source'] not in {r['source'] for r in (previous or {}).get('coverage', [])}],
                'remaining_actor_ids': [p['actor_id'] for p in self.request['eligible']
                    if p['actor_id'] not in program.get('actors', {})
                    and p['actor_id'] not in {r['actor_id'] for r in (previous or {}).get('plans', [])}]}
        if program:
            work['program_owned_unobserved_motion'] = dict(actor_ids=sorted(program['actors']),
                source_paths=sorted(program['sources']),
                instruction='Python completes these exact sources and unseen-tail plans. Preserve all other actors and actions; do not infer hidden interactions.')
        if inherited_sources:
            work['program_owned_source_relations'] = dict(source_paths=sorted(inherited_sources),
                instruction='Python inherits these confirmed point/closing relations. Review only remaining_sources; '
                            'new conflicting evidence must be reported explicitly, never overwrite the inherited relations.')
        if unobserved_sources:
            work['program_owned_motion_assumptions'] = dict(source_paths=sorted(unobserved_sources),
                actor_ids=sorted(set(unobserved_sources.values())),
                instruction='Python owns these exact pure unknown-motion limits and joint simulation envelopes. '
                            'Do not request identity proof for an unobserved prefix/tail; report genuinely new conflicts.')
        if (receipt_guidance := witness_receipt_guidance(receipts)) is not None:
            work['program_owned_witness_receipts'] = receipt_guidance
        if stage == STAGES[0] and (guidance := source_witness_guidance(self.request)) is not None:
            work['source_witness_relation_gate'] = guidance
        qualitative = [e['source'] for e in self.request['evidence'] if e.get('usage') == 'qualitative_only']
        if qualitative:
            work['qualitative_source_constraints'] = dict(source_paths=qualitative,
                instruction='Retain these exact body-part-relative observations. Only source-bound '
                'body_progress receipts may constrain an explicitly assumed trajectory; coverage remains '
                'qualitative_only. Do not cite them '
                'as whole-vehicle alongside, centre alignment, metric offsets or completed overtaking.')
        work['request_sha256'] = digest(work)
        run = {'generation': generation, 'request': work,
               'session_start': len(self.owner.progress(self.case).get('sessions', []))}
        folder = self.root/stage/f'run_{generation:04d}'
        atomic_json(folder/'request.json', work)
        entry['runs'].append(run)
        self.persist()
        return run

    def run_stage(self, stage):
        entry = self.state['stages'].setdefault(stage, {'runs': []})
        while True:
            self.bound()
            if entry.get('complete'):
                return self.finish_stage(stage)
            # Only before submitting a producer. Existing/uncertain remote
            # tasks retain their normal reconciliation and checkpoint flow.
            from _lib.contracts import bounded_execution, retained_motion
            if bounded_execution.KEY in self.request and not entry['runs']:
                previous = self.checkpoint(stage)
                value = None
                if stage == STAGES[0]:
                    coverage, relations = bounded_execution.complete_relations(self.request,
                        (previous or {}).get('coverage', []), (previous or {}).get('relations', []))
                    coverage, relations = retained_motion.complete_relations(self.request, coverage, relations)
                    if {r['source'] for r in coverage} == {r['source'] for r in self.request['evidence']}:
                        motion.validate_relations(self.request, coverage, relations)
                        value = dict(coverage=coverage, relations=relations)
                else:
                    relations = self.finish_stage(STAGES[0])['relations']
                    seed = (previous or {}).get('plans', [])
                    plans = bounded_execution.plan(self.request, relations, seed)
                    value = dict(plans=plans)
                    entry.update(program_generated=True, program_seed=seed)
                if value is not None:
                    value.update(request_sha256=self.request['request_sha256'], complete=True)
                    validate_save(self.request, stage, value, value['request_sha256'])
                    signature = digest(value)
                    path = self.root/'accepted'/f'{signature}.json'
                    atomic_json(path, value)
                    entry.update(complete=True, checkpoint=dict(sha256=sha256(path),
                        content_sha256=signature, request_sha256=value['request_sha256']))
                    self.persist()
                    return self.finish_stage(stage)
            run = entry['runs'][-1] if entry['runs'] else self.new_run(stage, entry)
            self.collect(stage, run)
            records = self.sessions(stage, run)
            if records and self.terminal(stage, run):
                latest = self.sessions(stage, run)[-1]
                if latest.get('error_code') and latest['error_code'] not in RETRY_CODES:
                    from _lib.runtime.orchestrator import BackendError
                    raise BackendError(latest['error_code'], 'Conversion task stopped: ' + latest['error_code'])
                # Recollect possible late output only after the producer stopped.
                self.collect(stage, run)
                try:
                    result = self.finish_stage(stage)
                except ValueError as error:
                    entry['last_error'] = str(error)[:4000]
                    if run.get('rejected_saves'):
                        entry['last_error'] += '; rejected saves: ' + str(run['rejected_saves'])[-2000:]
                    self.persist()
                    from _lib.contracts.retained_motion import RetainedMotionBlocked
                    if isinstance(error, RetainedMotionBlocked):
                        raise  # Deterministic infeasibility is not a model-repair task.
                    latest = self.sessions(stage, run)[-1]
                    from _lib.contracts.retained_motion import KEY
                    owned = (self.request.get(KEY) or {}).get('sources', {})
                    owned = set(owned) | set(self.request.get(bounded_execution.KEY, {}).get('unobserved_sources', {}))
                    if any(p['status'] in {'identity_unresolved', 'motion_unresolved'}
                           and (p['source'] not in owned or p['status'] == 'identity_unresolved')
                           for p in (self.checkpoint(STAGES[0]) or {}).get('coverage', [])):
                        raise ContractError('Conversion semantics unresolved; requires upstream review') from error
                    if latest.get('error_code') and latest['error_code'] not in RETRY_CODES:
                        raise ContractError(entry['last_error']) from error
                    # The reservation gate below counts BOTH stages and also
                    # enforces the existing Case recovery budget. No counters reset.
                    extras = sum(max(0, len(e['runs']) - 1) for e in self.state['stages'].values())
                    if extras >= 1:
                        raise ContractError('conversion_continuation_limit_exhausted: ' + entry['last_error']) from error
                    run = self.new_run(stage, entry)
                else:
                    entry['complete'] = True
                    self.persist()
                    return result
            folder = self.root/stage/f"run_{run['generation']:04d}"
            template = SCENARIO_ROOT/'json-coverter/skills/case-conversion/references/conversion-continuation-prompt.md'
            prompt = Template(template.read_text(encoding='utf-8')).substitute(
                request_path=folder/'request.json', output_dir=folder)

            def adopt(_session=None):
                self.collect(stage, run)
                return self.finish_stage(stage)

            attempts = self.owner.progress(self.case).get('attempts', {}).get(stage, 0)
            # An existing active task already consumed this generation's attempt.
            limit = attempts if self.sessions(stage, run) else attempts + 1
            try:
                self.owner.model_stage(self.case, stage, 'json-coverter', prompt, adopt,
                                       max_attempts=limit)
            except Exception:
                self.collect(stage, run)
                if not self.sessions(stage, run) or not self.terminal(stage, run):
                    raise
                # A terminal transport/validation error may use the single extra
                # session on the next loop. Nonretryable service failures stop.
                latest = self.sessions(stage, run)[-1]
                if latest.get('error_code') not in RETRY_CODES:
                    raise


def run(owner, case, request):
    work = ConversionWork(owner, case, request)
    work.run_stage(STAGES[0])
    decision = work.run_stage(STAGES[1])
    work.bound()
    target = Path(case)/'state/conversion_semantic_gap.json'
    if target.exists() and read_json(target) != decision:
        raise ContractError('Conversion continuation decision changed')
    atomic_json(target, decision)


def validate_committed(case, request, decision):
    """Cache/completion reuse must retain the accepted two-stage proof."""
    work = ConversionWork(None, case, request)
    if not all(work.state['stages'].get(stage, {}).get('complete') for stage in STAGES):
        raise ContractError('Conversion continuation stages not committed')
    work.finish_stage(STAGES[0])
    if work.finish_stage(STAGES[1]) != decision:
        raise ContractError('Conversion continuation final proof differs')
