"""NO-LLM recovery checks. All Cases and stage outputs here are synthetic."""
import copy
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.runtime import bounded_recovery as recovery
from _lib.runtime import v3
from _lib.runtime.case_core import Cases, atomic_json, read_json, sha256


class NoBackend:
    def submit(self, *args, **kwargs):
        raise AssertionError('NO-LLM: provider submission forbidden')

    def poll(self, *args, **kwargs):
        raise AssertionError('NO-LLM: provider polling forbidden')


class BoundedRecoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root/'synthetic.mp4'
        self.source.write_bytes(b'Synthetic input, never decoded')
        self.case, created = Cases(self.root/'cases').bind(
            self.source, {'workflow_version': 'v3',
                          'fallback_policy': {'straight_preview': {'enabled': False}}},
            force_new=True)
        self.assertTrue(created)
        self.owner = v3.V3Orchestrator(NoBackend())

    def reserve(self, stage, additional=True):
        progress = self.owner.progress(self.case)
        rows = progress.get('diagnostics', {}).get('model_task_reservations', [])
        recovery.reserve(self.owner, self.case, stage, rows, additional, {})

    def failure(self, stage, *, additional=False, kind='transport', rule=None):
        self.reserve(stage, additional)
        progress = self.owner.progress(self.case)
        receipt = dict(task_id='synthetic-task-'+str(len(progress['sessions'])),
                       session_id='synthetic-session-'+str(len(progress['sessions'])))
        recovery.bind_receipt(progress, stage, receipt)
        row = dict(stage=stage, **receipt,
            attempt=1+sum(s['stage']==stage for s in progress['sessions']),
            status='failed' if kind=='transport' else 'invalid_output',
            error_code='timeout' if kind=='transport' else 'schema_validation_failed')
        if rule:
            row['rule_id'] = rule
        progress['sessions'].append(row)
        self.owner.save_progress(self.case, progress)

    def assert_stop(self, code, action):
        with self.assertRaises(recovery.RecoveryStopped) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_new_v3_automatically_has_frozen_recovery_policy(self):
        policy = read_json(self.case/'state/execution_policy.json')
        self.assertEqual(policy['bounded_recovery'], recovery.REVISION)
        self.assertEqual(policy['recovery_limits'], recovery.LIMITS)
        self.assertEqual(recovery.policy(self.case), recovery.LIMITS)
        self.assertFalse((self.case/recovery.ADOPTION).exists())

    def test_transport_allows_two_retries_then_stops(self):
        self.failure('world')
        for _ in range(2):
            self.failure('world', additional=True)
        self.assertEqual(recovery.remaining(self.case, self.owner.progress(self.case)), 6)
        self.assert_stop('bounded_recovery_transport_exhausted', lambda:self.reserve('world'))
        self.assertEqual(len(self.owner.progress(self.case)['sessions']), 3)

    def test_content_repairs_have_a_separate_two_task_limit(self):
        self.failure('behavior', kind='content', rule='schema.initial')
        self.failure('behavior', additional=True, kind='content', rule='schema.next')
        self.failure('behavior', additional=True, kind='content', rule='schema.last')
        self.assert_stop('bounded_recovery_content_exhausted', lambda:self.reserve('behavior'))
        ledger = recovery.entries(self.owner.progress(self.case))
        self.assertEqual([r['recovery_kind'] for r in ledger], ['content', 'content'])

    def test_repeated_content_error_stops_without_progress(self):
        self.failure('behavior', kind='content', rule='schema.same')
        self.failure('behavior', additional=True, kind='content', rule='schema.same')
        self.assert_stop('bounded_recovery_no_progress', lambda:self.reserve('behavior'))

    def test_total_extra_task_limit_remains_eight(self):
        for stage in ('batch_0001','batch_0002','batch_0003','batch_0004'):
            self.failure(stage)
            self.failure(stage, additional=True)
            self.failure(stage, additional=True)
        self.assertEqual(recovery.remaining(self.case, self.owner.progress(self.case)), 0)
        self.assert_stop('bounded_recovery_total_exhausted', lambda:self.reserve('behavior'))

    def test_restart_keeps_unresolved_reservation_and_budget(self):
        self.failure('world')
        self.reserve('world')  # Synthetic crash between reservation and receipt.
        saved = sha256(self.case/'state/progress.json')
        self.owner = v3.V3Orchestrator(NoBackend())
        self.assert_stop('bounded_recovery_reconcile_required', lambda:self.reserve('world'))
        self.assertEqual(sha256(self.case/'state/progress.json'), saved)
        progress = self.owner.progress(self.case)
        receipt = dict(task_id='reconciled-task', session_id='reconciled-session')
        recovery.bind_receipt(progress, 'world', receipt)
        progress['sessions'].append(dict(stage='world', **receipt, attempt=2,
                                         status='failed', error_code='timeout'))
        self.owner.save_progress(self.case, progress)
        self.reserve('world')
        self.assertEqual(recovery.remaining(self.case, self.owner.progress(self.case)), 6)

    def test_running_remote_task_does_not_allow_another_reservation(self):
        self.failure('world')
        progress = self.owner.progress(self.case)
        progress['sessions'][-1]['status'] = 'running'
        self.owner.save_progress(self.case, progress)
        saved = sha256(self.case/'state/progress.json')
        self.assert_stop('bounded_recovery_reconcile_required', lambda:self.reserve('world'))
        self.assertEqual(sha256(self.case/'state/progress.json'), saved)

    def test_local_code_failure_does_not_spend_a_model_retry(self):
        self.failure('world')
        progress = self.owner.progress(self.case)
        progress['sessions'][-1]['error_code'] = 'local_dependency_missing'
        self.owner.save_progress(self.case, progress)
        self.assert_stop('local_dependency_missing', lambda:self.reserve('world'))
        self.assertEqual(recovery.remaining(self.case, self.owner.progress(self.case)), 8)

    def historical_receipt(self):
        policy = read_json(self.case/'state/execution_policy.json')
        policy.pop('bounded_recovery'); policy.pop('recovery_limits')
        atomic_json(self.case/'state/execution_policy.json', policy)
        packet = dict(repair_contract='behavior-scope-completion.v1', old_request='retained')
        atomic_json(self.case/'state/behavior_repair.request.json', packet)
        names = ['case.json','state/execution_policy.json','state/binding.json']
        receipt = dict(revision=recovery.REVISION, case_id=self.case.name,
            adopted_at='2026-10-13T00:00:00Z', limits=copy.deepcopy(recovery.LIMITS),
            original_progress=self.owner.progress(self.case), source_task_id='synthetic-old-task',
            behavior_request=dict(request_sha256='0'*64),
            input_hashes={name:sha256(self.case/name) for name in names},
            retired_completion=dict(sha256=sha256(self.case/'state/behavior_repair.request.json'), packet=packet))
        receipt['sha256'] = recovery.digest(receipt)
        atomic_json(self.case/recovery.ADOPTION, receipt)
        return receipt

    def test_historical_receipt_still_reads_without_writing_or_retiring(self):
        self.historical_receipt()
        before = {str(p.relative_to(self.case)):sha256(p) for p in self.case.rglob('*') if p.is_file()}
        self.assertTrue(recovery.enabled(self.case))
        self.assertEqual(recovery.policy(self.case), recovery.LIMITS)
        self.assertEqual(before, {str(p.relative_to(self.case)):sha256(p) for p in self.case.rglob('*') if p.is_file()})
        self.assertTrue((self.case/'state/behavior_repair.request.json').is_file())

    def test_historical_receipt_rejects_tampering(self):
        receipt = self.historical_receipt()
        bad = copy.deepcopy(receipt); bad['limits']['total_extra_tasks'] = 100
        atomic_json(self.case/recovery.ADOPTION, bad)
        self.assert_stop('bounded_recovery_input_changed', lambda:recovery.policy(self.case))
        bad = copy.deepcopy(receipt); bad['case_id'] = 'different-case'
        bad.pop('sha256'); bad['sha256'] = recovery.digest(bad)
        atomic_json(self.case/recovery.ADOPTION, bad)
        self.assert_stop('bounded_recovery_input_changed', lambda:recovery.policy(self.case))
        atomic_json(self.case/recovery.ADOPTION, receipt)
        atomic_json(self.case/'state/binding.json', dict(changed='synthetic drift'))
        self.assert_stop('bounded_recovery_input_changed', lambda:recovery.policy(self.case))

    def test_world_batch_behavior_analysis_dispatch_order(self):
        """Exercise run_v3 control flow; visual stage results are local test doubles."""
        events = []
        class AnalysisReached(BaseException):
            pass
        class StageRunner(v3.V3Orchestrator):
            def world_understanding(owner, case):
                if not (case/'state/world_context.json').is_file():
                    events.append('World'); atomic_json(case/'state/world_context.json', {})
            def v3_batches(owner, case):
                events.append('Batch'); atomic_json(case/'state/actor_registry.json', [])
            def reconcile(owner, case):
                return True
            def ensure_world_ready(owner, case, **kwargs):
                pass
            def behavior(owner, case):
                events.append('Behavior'); atomic_json(case/'state/behavior.json', {})
            def build(owner, case):
                events.append('Analysis')
                raise AnalysisReached('Stop before conversion or publication')
        def freeze(case):
            atomic_json(case/'manifest.json', dict(batches=[]))
            progress = read_json(case/'state/progress.json')
            progress.update(phase='batching', status='batching')
            atomic_json(case/'state/progress.json', progress)
        runner = StageRunner(NoBackend())
        with ExitStack() as stack:
            stack.enter_context(patch.object(v3, 'EXTRACTOR', self.root))
            stack.enter_context(patch.object(v3, 'freeze_v3', side_effect=freeze))
            stack.enter_context(patch.object(v3.subprocess, 'run', return_value=None))
            stack.enter_context(patch('_lib.runtime.routing.check_world_capabilities'))
            stack.enter_context(patch('_lib.world.gaps.enabled', return_value=False))
            stack.enter_context(patch('_lib.runtime.fact_versions.enabled', return_value=False))
            stack.enter_context(patch('_lib.runtime.reconciliation_review.run'))
            stack.enter_context(patch('_lib.runtime.routing.automatic', return_value=False))
            with self.assertRaises(AnalysisReached):
                runner.run_v3(self.source, case_id=self.case.name,
                    parameters=read_json(self.case/'case.json')['parameters'])
        self.assertEqual(events, ['World','Batch','Behavior','Analysis'])


class RetiredBoundedRecoveryEntryTests(unittest.TestCase):
    def test_direct_entry_rejects_retired_option(self):
        import contextlib
        import io
        from _lib.runtime.orchestrator import main
        stderr = io.StringIO()
        with (patch.object(sys, 'argv', ['process.py', '--enable-bounded-recovery']),
              contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised):
            main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('unrecognized arguments: --enable-bounded-recovery', stderr.getvalue())

    def test_retired_keyword_is_not_accepted_by_run_v3(self):
        import inspect
        self.assertNotIn('enable_bounded_recovery', inspect.signature(v3.V3Orchestrator.run_v3).parameters)
        with self.assertRaisesRegex(TypeError, 'enable_bounded_recovery'):
            v3.V3Orchestrator(NoBackend()).run_v3('not-read', enable_bounded_recovery=True)


if __name__ == '__main__':
    unittest.main()
