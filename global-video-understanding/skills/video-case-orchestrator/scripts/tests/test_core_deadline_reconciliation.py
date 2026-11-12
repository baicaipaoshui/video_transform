"""A timed-out Behavior producer cannot strand or duplicate its candidate."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _lib.behavior import input_binding
from _lib.runtime import core_approximate_delivery as core
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.orchestrator import Orchestrator, QwenPawBackend, schema


class QwenPawTaskTimeoutTest(unittest.TestCase):
    def test_background_submission_keeps_current_server_default_budget(self):
        completed = subprocess.CompletedProcess([], 0,
            stdout='[TASK_ID: task-1] [SESSION: session-1]\n', stderr='')
        with patch('subprocess.run', return_value=completed) as run:
            submitted = QwenPawBackend().submit('global-video-understanding', 'prompt')
        self.assertEqual(submitted, {'task_id': 'task-1', 'session_id': 'session-1'})
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--task-timeout') + 1], '3600')

    def test_renewed_timeout_grant_continues_the_bound_session(self):
        from _lib.runtime import behavior_timeout_retry

        class Backend:
            def submit(self, agent, prompt, session_id=None):
                self.call = (agent, prompt, session_id)
                return {'task_id': 'continued-task', 'session_id': session_id}

        backend = Backend()
        runner = core.CoreApproximateOrchestrator(backend)
        runner._core_transport_retry = True
        binding_sha256 = 'renewed-binding'
        grant = {
            'status': 'consumed',
            'binding_sha256': binding_sha256,
            'binding': {
                'generation': 2,
                'resume_session_id': 'original-session',
                'failed_session': {'session_id': 'original-session'},
            },
        }
        progress = {'diagnostics': {'model_task_reservations': [{
            'stage': 'behavior',
            'behavior_timeout_retry_binding': binding_sha256,
        }]}}
        with (patch.object(runner, 'progress', return_value=progress),
              patch.object(behavior_timeout_retry, 'checked', return_value=grant)):
            result = runner.submit_model_task(Path('/unused'), 'behavior',
                                              'global-video-understanding', 'frozen prompt')
        self.assertEqual(result, {
            'task_id': 'continued-task', 'session_id': 'original-session'})
        self.assertEqual(backend.call[0], 'global-video-understanding')
        self.assertIn('frozen prompt', backend.call[1])
        self.assertEqual(backend.call[2], 'original-session')


class Runner:
    def __init__(self, case, remote):
        self.case = case
        self.backend = type('Backend', (), {'poll': lambda self, *_: remote})()
        self.submissions = 0
        self.captures = 0

    def progress(self, case):
        return read_json(case/'state/progress.json')

    def save_progress(self, case, progress):
        schema('progress.schema.json', progress)
        atomic_json(case/'state/progress.json', progress)

    def run_v3(self, *args, **kwargs):
        self.submissions += 1
        raise AssertionError('No new model task may be submitted')

    def recovery_phase(self, case):
        return 'aggregating'

    def capture_behavior_candidate(self, case, candidate, session):
        self.captures += 1
        progress = self.progress(case)
        progress.setdefault('diagnostics', {})['core_approximate_candidate'] = {'session_id': session}
        self.save_progress(case, progress)


class CoreDeadlineReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self.root/'video_extractor/cases/case_deadline'
        (self.case/'state').mkdir(parents=True)
        atomic_json(self.case/'state/core_approximate_mode.json', {'revision': core.REVISION})
        self.session = 'original-session'
        self.task = 'original-task'
        atomic_json(self.case/input_binding.SESSIONS, {self.session: 'frozen-request'})
        atomic_json(self.case/'state/progress.json', dict(schema_version='1.0',
            case_id=self.case.name, phase='failed', status='failed', attempts={},
            completed_batches=[], artifacts={}, updated_at='2026-10-20T00:00:00Z',
            error=dict(stage='behavior', code='task_deadline_exceeded'),
            sessions=[dict(stage='behavior', status='failed', attempt=1,
                           task_id=self.task, session_id=self.session,
                           error_code='task_deadline_exceeded')],
            diagnostics=dict(model_task_reservations=[dict(stage='behavior',
                task_id=self.task, recovery_request='frozen-recovery')])))
        self.patches = [
            patch.object(core, 'SCENARIO_ROOT', self.root),
            patch.object(core, '_mode'),
            patch.object(input_binding, 'enabled', return_value=True),
            patch.object(input_binding, 'verify', return_value={'request_sha256': 'frozen-request'}),
            patch.object(input_binding, 'require_session'),
            patch.object(input_binding, 'active_request', return_value={'request_sha256': 'frozen-request'}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        from _lib.runtime import bounded_recovery
        item = patch.object(bounded_recovery, 'request_digest', return_value='frozen-recovery')
        item.start()
        self.addCleanup(item.stop)

    def candidate(self, *, complete=True):
        data = dict(contract_version='video-case-world-behavior', actors=[],
                    events=[], semantic_limitations=[])
        if not complete:
            data.pop('events')
        path = self.case/'state/behavior.candidate.json'
        atomic_json(path, data)
        return path

    def test_terminal_timeout_archives_once_and_both_entries_use_original_answer(self):
        source = self.candidate()
        original_hash = sha256(source)
        for retry in (False, True):
            runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
            with patch.object(core, '_project', side_effect=RuntimeError('projection reached')):
                with self.assertRaisesRegex(RuntimeError, 'projection reached'):
                    core._run_selected(runner, Path('/unused'), {}, self.case, retry_failed=retry)
            row = runner.progress(self.case)['sessions'][-1]
            self.assertTrue(row['deadline_task_resolved'])
            self.assertEqual(row['remote_error_code'], 'timeout')
            self.assertEqual(sha256(row['invalid_candidate_path']), original_hash)
            self.assertEqual(sha256(source), original_hash)
            self.assertEqual(len(input_binding.replayable_archives(self.case)), 1)
            self.assertEqual(runner.submissions, 0)

    def test_still_running_keeps_case_and_candidate_unchanged(self):
        self.candidate()
        before = sha256(self.case/'state/progress.json')
        runner = Runner(self.case, {'status': 'running'})
        with self.assertRaisesRegex(core.ContractError, 'still active'):
            core._run_selected(runner, Path('/unused'), {}, self.case, retry_failed=True)
        self.assertEqual(sha256(self.case/'state/progress.json'), before)
        self.assertTrue((self.case/'state/behavior.candidate.json').is_file())
        self.assertEqual(runner.submissions, 0)

    def test_nontransport_remote_failure_cannot_adopt_or_retry(self):
        self.candidate()
        runner = Runner(self.case, {'status': 'failed', 'error': 'service_access_denied'})
        with self.assertRaisesRegex(core.ContractError, 'non-transport reason'):
            core._run_selected(runner, Path('/unused'), {}, self.case, retry_failed=True)
        row = runner.progress(self.case)['sessions'][-1]
        self.assertEqual(row['remote_error_code'], 'service_access_denied')
        self.assertNotIn('invalid_candidate_path', row)
        self.assertEqual(runner.submissions, 0)

    def test_completed_remote_reopens_only_for_original_candidate_validation(self):
        self.candidate()
        runner = Runner(self.case, {'status': 'completed'})
        with (patch.object(core, '_candidate', return_value=self.case/'state/behavior.candidate.json'),
              patch.object(core, '_project', side_effect=RuntimeError('projection reached'))):
            with self.assertRaisesRegex(RuntimeError, 'projection reached'):
                core._run_selected(runner, Path('/unused'), {}, self.case, retry_failed=True)
        progress = runner.progress(self.case)
        self.assertEqual(progress['phase'], 'aggregating')
        self.assertEqual(progress['sessions'][-1]['status'], 'completed')
        self.assertTrue(progress['sessions'][-1]['deadline_task_resolved'])
        self.assertEqual(runner.captures, 1)
        self.assertEqual(runner.submissions, 0)

    def test_completed_candidate_receipt_is_bound_to_original_session_and_bytes(self):
        candidate = self.candidate()
        runner = Runner(self.case, {'status': 'completed'})
        progress = runner.progress(self.case)
        progress['sessions'][-1]['status'] = 'completed'
        progress['phase'] = 'aggregating'
        runner.save_progress(self.case, progress)
        with patch.object(core, 'current_facts', return_value=type('Facts', (), {'version_id': 'v1'})()):
            core.CoreApproximateOrchestrator.capture_behavior_candidate(
                runner, self.case, candidate, self.session)
        receipt = runner.progress(self.case)['diagnostics']['core_approximate_candidate']
        self.assertEqual(receipt['session_id'], self.session)
        self.assertEqual(receipt['source_request_sha256'], 'frozen-request')
        self.assertEqual(receipt['sha256'], sha256(candidate))
        self.assertTrue((self.case/'diagnostics/candidate_snapshots'/
            f"behavior.{receipt['sha256']}.json").is_file())

    def test_reconciled_record_passes_real_progress_persistence_gate(self):
        from _lib.runtime import bounded_recovery
        progress = read_json(self.case/'state/progress.json')
        progress['sessions'][-1].update(deadline_task_resolved=True,
            remote_error_code='timeout', invalid_candidate_path='/private/tmp/original-answer.json')
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        with patch.object(bounded_recovery, 'validate_progress', return_value=False):
            Orchestrator.save_progress(runner, self.case, progress)
        self.assertEqual(runner.progress(self.case)['sessions'][-1]['remote_error_code'], 'timeout')

    def test_incomplete_candidate_does_not_authorize_a_new_task(self):
        self.candidate(complete=False)
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        with self.assertRaisesRegex(core.ContractError, 'no complete source-bound'):
            core._run_selected(runner, Path('/unused'), {}, self.case)
        self.assertEqual(input_binding.replayable_archives(self.case), [])
        self.assertEqual(runner.submissions, 0)

    def test_malformed_candidate_is_archived_without_retrying(self):
        (self.case/'state/behavior.candidate.json').write_text('{unfinished', encoding='utf-8')
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        with self.assertRaisesRegex(core.ContractError, 'no complete source-bound'):
            core._run_selected(runner, Path('/unused'), {}, self.case)
        row = runner.progress(self.case)['sessions'][-1]
        self.assertTrue(Path(row['invalid_candidate_path']).is_file())
        self.assertEqual(input_binding.replayable_archives(self.case), [])
        self.assertEqual(runner.submissions, 0)

    def test_no_candidate_needs_explicit_retry_after_terminal_timeout(self):
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        with self.assertRaisesRegex(core.ContractError, 'no complete source-bound'):
            core._run_selected(runner, Path('/unused'), {}, self.case)
        self.assertTrue(runner.progress(self.case)['sessions'][-1]['deadline_task_resolved'])
        self.assertEqual(runner.submissions, 0)
        from _lib.runtime import bounded_recovery
        with (patch.object(bounded_recovery, 'policy', return_value={'transport_retries': 2}),
              patch.object(bounded_recovery, 'remaining', return_value=1),
              patch.object(bounded_recovery, 'entries', return_value=[]),
              patch.object(bounded_recovery, 'failure_kind', return_value='transport')):
            with self.assertRaisesRegex(AssertionError, 'No new model task'):
                core._run_selected(runner, Path('/unused'), {}, self.case, retry_failed=True)
        self.assertEqual(runner.submissions, 1)

    def test_exhausted_retry_persists_timeout_authorization_boundary(self):
        from _lib.runtime import bounded_recovery
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        progress = runner.progress(self.case)
        progress['sessions'][-1]['error_code'] = 'timeout'
        progress['error'] = dict(stage='behavior', code='model_attempts_exhausted')
        runner.save_progress(self.case, progress)
        ledger = [dict(recovery_group='behavior', recovery_kind='transport') for _ in range(2)]
        with (patch.object(bounded_recovery, 'policy', return_value={
                'transport_retries': 2, 'content_repairs': 2, 'total_extra_tasks': 8}),
              patch.object(bounded_recovery, 'remaining', return_value=1),
              patch.object(bounded_recovery, 'entries', return_value=ledger),
              patch.object(bounded_recovery, 'failure_kind', return_value='transport')):
            with self.assertRaisesRegex(core.ContractError, 'budget exhausted'):
                core._require_transport_retry(runner, self.case, runner.progress(self.case))
        saved = runner.progress(self.case)
        self.assertEqual(saved['error']['code'], 'bounded_recovery_transport_exhausted')
        self.assertEqual(len(saved['sessions']), 1)

    def test_consumed_timeout_grant_does_not_bypass_exhausted_boundary(self):
        from _lib.runtime import bounded_recovery, behavior_timeout_retry
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        progress = runner.progress(self.case)
        progress['sessions'][-1]['error_code'] = 'timeout'
        progress['error'] = dict(stage='behavior', code='model_attempts_exhausted')
        runner.save_progress(self.case, progress)
        ledger = [dict(recovery_group='behavior', recovery_kind='transport') for _ in range(2)]
        with (patch.object(bounded_recovery, 'policy', return_value={
                'transport_retries': 2, 'content_repairs': 2, 'total_extra_tasks': 8}),
              patch.object(bounded_recovery, 'remaining', return_value=1),
              patch.object(bounded_recovery, 'entries', return_value=ledger),
              patch.object(bounded_recovery, 'failure_kind', return_value='transport'),
              patch.object(behavior_timeout_retry, 'checked', return_value={'status': 'consumed'})):
            with self.assertRaisesRegex(core.ContractError, 'budget exhausted'):
                core._require_transport_retry(runner, self.case, runner.progress(self.case))
        self.assertEqual(runner.progress(self.case)['error']['code'],
                         'bounded_recovery_transport_exhausted')

    def test_authorized_timeout_grant_allows_core_retry(self):
        from _lib.runtime import bounded_recovery, behavior_timeout_retry
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        progress = runner.progress(self.case)
        progress['sessions'][-1]['error_code'] = 'timeout'
        progress['error'] = dict(stage='behavior', code='bounded_recovery_transport_exhausted')
        runner.save_progress(self.case, progress)
        ledger = [dict(recovery_group='behavior', recovery_kind='transport') for _ in range(2)]
        with (patch.object(bounded_recovery, 'policy', return_value={
                'transport_retries': 2, 'content_repairs': 2, 'total_extra_tasks': 8}),
              patch.object(bounded_recovery, 'remaining', return_value=1),
              patch.object(bounded_recovery, 'entries', return_value=ledger),
              patch.object(bounded_recovery, 'failure_kind', return_value='transport'),
              patch.object(behavior_timeout_retry, 'checked', return_value={'status': 'authorized'})):
            core._require_transport_retry(runner, self.case, runner.progress(self.case))

    def test_world_closure_failure_uses_normal_case_retry_before_behavior_freeze(self):
        progress = runner_progress = read_json(self.case/'state/progress.json')
        progress['error']['stage'] = 'world_closure'
        progress['sessions'][-1]['stage'] = 'world_closure'
        atomic_json(self.case/'state/progress.json', progress)
        runner = Runner(self.case, {'status': 'failed', 'error': 'timeout'})
        with (patch.object(input_binding, 'replayable_archives',
                           side_effect=AssertionError('Behavior request is not frozen')),
              patch.object(core, '_require_transport_retry',
                           side_effect=AssertionError('Behavior-only retry must not run')),
              patch.object(runner, 'run_v3', return_value=runner_progress) as resume):
            result = core._run_selected(runner, Path('/unused'), {}, self.case,
                                        retry_failed=True)
        self.assertEqual(result['error']['stage'], 'world_closure')
        self.assertFalse(hasattr(runner, '_core_transport_retry'))
        resume.assert_called_once_with(Path('/unused'), case_id=self.case.name,
            parameters={}, retry_failed=True, stop_after_behavior_candidate=True)


if __name__ == '__main__':
    unittest.main()
