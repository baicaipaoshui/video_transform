"""NO-LLM v2 prompt/intake/recovery integration with synthetic worker outputs."""
import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.legacy.v2 import batch_contract as contract
from _lib.runtime.case_core import Cases, atomic_json, batch_request_text, read_json, sha256
from _lib.runtime.orchestrator import Orchestrator, BackendError, schema, validation_feedback


def candidate(case_id, observation=None):
    return dict(schema_version='1.0', case_id=case_id, batch_id='batch_0001',
                observations=[observation or dict(status='observed', frame_id='frame_0001',
                                                time_s=0, reason='Synthetic visible fact')],
                challenges=[], registry_after=[])


def nested_observation(reason='Synthetic multi-frame summary'):
    return dict(status='observed', reason=reason, evidence=[
        dict(status='observed', frame_id='frame_0001', time_s=0, reason='Synthetic first frame'),
        dict(status='observed', frame_id='frame_0002', time_s=1, reason='Synthetic second frame')])


class FakeBackend:
    """Every answer is test-owned fixture data, never a provider response."""
    poll_interval = 0
    timeout = 30

    def __init__(self, case, outputs):
        self.case = case
        self.outputs = outputs
        self.prompts = []
        self.results = {}

    def submit(self, agent, prompt):
        number = len(self.prompts)
        self.prompts.append(prompt)
        output = self.outputs[number]
        task = 'synthetic-task-' + str(number)
        if output == 'cancelled':
            self.results[task] = dict(status='failed', error='task_cancelled', message='Synthetic cancellation')
        elif isinstance(output, tuple):
            atomic_json(self.case/'batches/.batch_0001.candidate.json', output[1])
            self.results[task] = dict(status='failed', error='timeout', message='Synthetic transport failure after save')
        else:
            atomic_json(self.case/'batches/.batch_0001.candidate.json', output)
            self.results[task] = dict(status='completed')
        return dict(task_id=task, session_id='synthetic-session-' + str(number))

    def poll(self, task, agent):
        return self.results[task]


class V2BatchContractTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = Path(self.folder.name)/'synthetic.mp4'
        self.source.write_bytes(b'NO-LLM synthetic source, never decoded')
        self.cases = Cases(Path(self.folder.name)/'cases')
        self.case, _ = self.cases.bind(self.source, {'workflow_version': 'v2'}, force_new=True)
        progress = read_json(self.case/'state/progress.json')
        progress.update(phase='batching', status='batching')
        atomic_json(self.case/'state/progress.json', progress)
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, 'connect', side_effect=AssertionError('NO-LLM: no network')).start()
        patch('_lib.runtime.orchestrator.QwenPawBackend.submit', side_effect=AssertionError('NO-LLM')).start()
        patch('_lib.runtime.orchestrator.QwenPawBackend.poll', side_effect=AssertionError('NO-LLM')).start()

    def bad(self, reason='Synthetic summary'):
        return candidate(self.case.name, nested_observation(reason))

    def validate(self, session=None):
        path = self.case/'batches/.batch_0001.candidate.json'
        if not path.is_file() and (self.case/'batches/batch_0001.json').is_file():
            path = self.case/'batches/batch_0001.json'
        data = read_json(path)
        data['session_id'] = session or 'synthetic-checkpoint'
        schema('batch.schema.json', data)
        atomic_json(self.case/'batches/batch_0001.json', data)
        if path.name.startswith('.'):
            path.unlink()

    def run_batch(self, owner):
        owner.model_stage(self.case, 'batch_0001', 'video_extractor',
                          batch_request_text(self.case, 'batch_0001'), self.validate,
                          lambda: self.validate(), candidate_path=self.case/'batches/.batch_0001.candidate.json')

    def error(self, value):
        value = copy.deepcopy(value)
        value['session_id'] = 'synthetic'
        try:
            schema('batch.schema.json', value)
        except Exception as error:
            return error
        self.fail('Fixture should violate the original Schema')

    def prepare_recovery(self, owner, code='task_cancelled'):
        atomic_json(self.case/'manifest.json', {})
        atomic_json(self.case/'state/global_context.json', {})
        owner.fail(self.case, code, 'Synthetic recovery')
        return owner.recover_failed(self.case)

    def test_new_v2_policy_and_reuse_never_migrate_an_old_case(self):
        self.assertTrue(contract.enabled(self.case))
        (self.case/contract.POLICY).unlink()
        reused, created = self.cases.bind(self.source, {'workflow_version': 'v2'})
        self.assertFalse(created)
        self.assertEqual(reused, self.case)
        self.assertFalse((self.case/contract.POLICY).exists())

    def test_prompt_schema_is_exact_formal_schema_except_injected_session(self):
        prompt = contract.prompt_contract(self.case)
        private = json.loads(prompt.splitlines()[2])
        formal = read_json(SKILLS/'schemas/batch.schema.json')
        private['required'].insert(formal['required'].index('session_id'), 'session_id')
        private['properties']['session_id'] = formal['properties']['session_id']
        self.assertEqual(private, formal)
        self.assertIn('嵌套 evidence[] 的帧/时间不能代替', prompt)

    def test_old_case_has_no_added_prompt_or_guard(self):
        (self.case/contract.POLICY).unlink()
        self.assertEqual(contract.prompt_contract(self.case), '')
        self.assertNotIn('候选格式', batch_request_text(self.case, 'batch_0001'))
        owner = Orchestrator(None)
        error = self.error(self.bad())
        self.assertEqual(owner.stage_validation_feedback(self.case, 'batch_0001', error), validation_feedback(error))
        contract.require_attempt(self.case, 'batch_0001', {'sessions': [{}]*20})

    def test_v3_and_other_stages_do_not_adopt_v2_rules(self):
        record = read_json(self.case/'case.json')
        record['workflow_contract'] = 'video-case-world-behavior'
        atomic_json(self.case/'case.json', record)
        self.assertFalse(contract.enabled(self.case))
        self.assertIsNone(contract.feedback(self.case, 'batch_0001', self.error(self.bad())))
        self.assertFalse(contract.enabled(self.case, 'behavior'))

    def test_observed_requires_direct_position_and_no_facts_are_rewritten(self):
        original = self.bad()
        snapshot = copy.deepcopy(original)
        self.error(original)
        corrected = copy.deepcopy(original)
        corrected['observations'] = original['observations'][0]['evidence']
        corrected['session_id'] = 'synthetic'
        schema('batch.schema.json', corrected)
        self.assertEqual(original, snapshot)
        for observation in corrected['observations']:
            self.assertEqual(observation['status'], 'observed')

    def test_estimated_and_unknown_require_original_schema_fields(self):
        for observation in (dict(status='estimated'), dict(status='unknown')):
            error = self.error(candidate(self.case.name, observation))
            details = contract.failure_details(self.case, 'batch_0001', error)
            self.assertEqual(details['v2_batch_contract']['issue']['rule'], 'required')

    def test_feedback_names_path_and_required_rule_before_long_instance(self):
        error = self.error(self.bad('Long fact ' * 1000))
        feedback = contract.feedback(self.case, 'batch_0001', error)
        self.assertIn('/observations/0', feedback[:300])
        self.assertIn('frame_id', feedback[:300])
        self.assertIn('time_s', feedback[:300])
        self.assertIn('schema_path', feedback)
        self.assertNotIn('Long fact', feedback)

    def test_same_structural_error_has_same_signature_despite_rewording(self):
        signatures = [contract.failure_details(self.case, 'batch_0001', self.error(self.bad(reason)))
                      ['v2_batch_contract']['signature'] for reason in ('First description', 'Reworded description')]
        self.assertEqual(*signatures)

    def test_background_same_error_stops_after_one_repair(self):
        backend = FakeBackend(self.case, [self.bad(), self.bad('Reworded')])
        owner = Orchestrator(backend, sleep=lambda _: None)
        with self.assertRaises(contract.RepairStopped) as stopped:
            self.run_batch(owner)
        self.assertEqual(stopped.exception.code, 'v2_batch_repeated_error')
        self.assertEqual(len(backend.prompts), 2)
        self.assertIn('/observations/0', backend.prompts[1])
        self.assertEqual(len(list((self.case/'diagnostics/invalid_candidates').glob('*.json'))), 2)
        self.assertFalse((self.case/'final/target.json').exists())

    def test_different_error_can_progress_to_valid_original_schema(self):
        backend = FakeBackend(self.case, [self.bad(), candidate(self.case.name, dict(status='estimated')),
                                         candidate(self.case.name)])
        owner = Orchestrator(backend, sleep=lambda _: None)
        self.run_batch(owner)
        self.assertEqual(len(backend.prompts), 3)
        schema('batch.schema.json', read_json(self.case/'batches/batch_0001.json'))
        self.assertEqual(owner.progress(self.case)['sessions'][-1]['status'], 'completed')

    def test_invalid_saved_output_after_transport_failure_also_stops(self):
        backend = FakeBackend(self.case, [('failed', self.bad()), ('failed', self.bad())])
        owner = Orchestrator(backend, sleep=lambda _: None)
        with self.assertRaises(contract.RepairStopped):
            self.run_batch(owner)
        self.assertEqual(len(backend.prompts), 2)
        self.assertIn('/observations/0', backend.prompts[1])

    def test_synchronous_backend_also_stops_repeated_format_error(self):
        case = self.case
        outputs = [self.bad(), self.bad('Reworded')]

        class SyncBackend:
            def __init__(self):
                self.prompts = []

            def call(self, agent, prompt):
                number = len(self.prompts)
                self.prompts.append(prompt)
                atomic_json(case/'batches/.batch_0001.candidate.json', outputs[number])
                return 'synthetic-sync-' + str(number)

        backend = SyncBackend()
        owner = Orchestrator(backend, sleep=lambda _: None)
        with self.assertRaises(contract.RepairStopped):
            self.run_batch(owner)
        self.assertEqual(len(backend.prompts), 2)
        self.assertIn('/observations/0', backend.prompts[1])

    def test_cancellation_recovery_does_not_reset_lifetime_budget(self):
        backend = FakeBackend(self.case, ['cancelled']*3)
        owner = Orchestrator(backend, sleep=lambda _: None)
        for expected in (1, 2, 3):
            with self.assertRaises(BackendError):
                self.run_batch(owner)
            progress = self.prepare_recovery(owner)
            self.assertEqual(progress['attempts']['batch_0001'], expected)
        with self.assertRaises(RuntimeError):
            self.run_batch(owner)
        self.assertEqual(len(backend.prompts), 3)

    def test_repeated_error_survives_explicit_recovery(self):
        backend = FakeBackend(self.case, [self.bad(), self.bad()])
        owner = Orchestrator(backend, sleep=lambda _: None)
        with self.assertRaises(contract.RepairStopped):
            self.run_batch(owner)
        self.prepare_recovery(owner, 'v2_batch_repeated_error')
        with self.assertRaises(contract.RepairStopped):
            self.run_batch(owner)
        self.assertEqual(len(backend.prompts), 2)

    def test_valid_checkpoint_is_adopted_before_retry_guard(self):
        backend = FakeBackend(self.case, [self.bad(), self.bad()])
        owner = Orchestrator(backend, sleep=lambda _: None)
        with self.assertRaises(contract.RepairStopped):
            self.run_batch(owner)
        atomic_json(self.case/'batches/.batch_0001.candidate.json', candidate(self.case.name))
        self.run_batch(owner)
        self.assertEqual(len(backend.prompts), 2)

    def test_policy_tampering_stops_without_submission(self):
        policy = read_json(self.case/contract.POLICY)
        policy['max_submissions'] = 100
        atomic_json(self.case/contract.POLICY, policy)
        with self.assertRaises(contract.RepairStopped):
            contract.require_attempt(self.case, 'batch_0001', {})

    def test_reset_attempt_counter_cannot_bypass_session_history(self):
        with self.assertRaises(contract.RepairStopped):
            contract.require_attempt(self.case, 'batch_0001',
                                     dict(attempts={'batch_0001': 0}, sessions=[
                                         dict(stage='batch_0001', status='failed')]*3))


if __name__ == '__main__':
    unittest.main()
