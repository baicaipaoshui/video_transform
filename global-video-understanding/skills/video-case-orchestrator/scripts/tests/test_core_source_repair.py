"""NO-LLM Core source handoff checks using distinct frozen Cases."""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts.core_lane_continuity import validate_source_coverage
from _lib.contracts.census_scope import split
from _lib.contracts.v3 import ContractError
from _lib.behavior import repair, input_binding
from _lib.runtime import core_approximate_delivery as core
from _lib.runtime import bounded_recovery
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.v3 import V3Orchestrator

ROOT = SKILLS.parents[1]/'video_extractor/cases'
FROZEN = ('803e13de', '7916ef73', '0794326f', '065638b5')


def frozen(suffix):
    case = next(ROOT.glob('case_*' + suffix))
    return case, read_json(case/'state/behavior.candidate.json'), \
        read_json(case/'state/behavior_input.request.json')['evidence_timeline']


class CoreSourceRepairTests(unittest.TestCase):
    def _repair_packet(self, suffix, actor_id, change):
        source, original, timeline = frozen(suffix)
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)/'case_frozen'
            (case/'state').mkdir(parents=True)
            (case/'diagnostics/invalid_candidates').mkdir(parents=True)
            for name in ('world_context.json', 'actor_registry.json', 'behavior_frames.json'):
                shutil.copy2(source/'state'/name, case/'state'/name)
            broken = copy.deepcopy(original)
            change(broken)
            archived = case/'diagnostics/invalid_candidates'/f'behavior.attempt_1.{suffix}.json'
            atomic_json(archived, broken)
            candidate = case/'state/behavior.candidate.json'
            bound = read_json(source/'state/behavior_input.request.json')
            error = ContractError('Unknown assigned source',
                                  rule_id='core_scene.source_coverage', actor_id=actor_id)
            with (patch.object(input_binding, 'active_request', return_value=bound),
                  patch.object(input_binding, 'timeline_enabled', return_value=True),
                  patch.object(input_binding, 'request_path',
                        return_value=source/'state/behavior_input.request.json'),
                  patch.object(input_binding, 'current_evidence_timeline',
                        return_value=timeline),
                  patch('_lib.contracts.census_scope.bind_request'),
                  patch('_lib.contracts.census_scope.verify_request')):
                initial_prompt = repair.repair_prompt(case, candidate, archived, error)
                self.assertIsNotNone(initial_prompt)
                packet = read_json(case/'state/behavior_repair.request.json')
                self.assertEqual(packet['repair_contract'], 'behavior-source-coverage.v1')
                self.assertEqual(packet['actor_id'], actor_id)
                frozen_hash = sha256(case/'state/behavior_repair.request.json')
                candidate.write_text('{}')
                self.assertEqual(repair.repair_prompt(case, candidate, archived, error),
                                 initial_prompt)
                self.assertEqual(sha256(case/'state/behavior_repair.request.json'), frozen_hash)
                candidate.unlink()
                yield case, original, timeline, packet

    def test_frozen_positive_and_negative_source_coverage(self):
        for suffix in FROZEN:
            case, behavior, timeline = frozen(suffix)
            with self.subTest(case=suffix):
                if suffix == '803e13de':
                    with self.assertRaises(ContractError) as caught:
                        validate_source_coverage(case, behavior, timeline=timeline)
                    self.assertEqual(caught.exception.rule_id, 'core_scene.source_coverage')
                    self.assertEqual(caught.exception.actor_id, 'ego')
                else:
                    self.assertTrue(validate_source_coverage(case, behavior, timeline=timeline))
                    wrong = copy.deepcopy(behavior)
                    actor = next(a for a in wrong['actors'] if a.get('trajectory'))
                    point = actor['trajectory'][0]
                    point['source_observation_ids'] = [
                        f'actor-frame:{actor["actor_id"]}:{point["frame_id"]}:missing']
                    with self.assertRaises(ContractError) as caught:
                        validate_source_coverage(case, wrong, timeline=timeline)
                    self.assertEqual(caught.exception.actor_id, actor['actor_id'])

    def test_core_capture_rejects_bad_source_before_receipt(self):
        class NoModel:
            def submit(self, *_args, **_kwargs):
                raise AssertionError('A NO-LLM test must not submit a task')

        runner = core.CoreApproximateOrchestrator(NoModel())
        for suffix in FROZEN:
            source, behavior, timeline = frozen(suffix)
            with self.subTest(case=suffix), tempfile.TemporaryDirectory() as folder:
                case = Path(folder)/'case_frozen'
                (case/'state').mkdir(parents=True)
                shutil.copy2(source/'state/world_context.json', case/'state/world_context.json')
                path = case/'state/behavior.candidate.json'
                path.write_text(json.dumps(behavior))
                progress = {'sessions': [dict(stage='behavior', session_id='session-frozen',
                                              status='returned', attempt=1)], 'diagnostics': {}}
                with (patch.object(runner, 'progress', return_value=progress),
                      patch.object(runner, 'save_progress'),
                      patch('_lib.behavior.input_binding.require_session'),
                      patch('_lib.behavior.input_binding.active_request',
                            return_value={'request_sha256': 'frozen-request'}),
                      patch('_lib.behavior.input_binding.timeline_enabled', return_value=True),
                      patch('_lib.behavior.input_binding.current_evidence_timeline',
                            return_value=timeline),
                      patch.object(core, 'current_facts',
                            return_value=SimpleNamespace(version_id='frozen-version'))):
                    if suffix == '803e13de':
                        with self.assertRaises(ContractError) as caught:
                            runner.capture_behavior_candidate(case, path, 'session-frozen')
                        self.assertEqual(caught.exception.actor_id, 'ego')
                        self.assertNotIn('core_approximate_candidate', progress['diagnostics'])
                    else:
                        runner.capture_behavior_candidate(case, path, 'session-frozen')
                        self.assertEqual(progress['diagnostics']['core_approximate_candidate']
                                         ['session_id'], 'session-frozen')

    def test_assigned_patch_repairs_only_a_real_same_frame_source(self):
        source, original, _ = frozen('7916ef73')
        actor = next(a for a in original['actors'] if any(
            p.get('source_observation_ids') for p in a.get('trajectory', [])))
        index = original['actors'].index(actor)
        point_index = next(i for i, p in enumerate(actor['trajectory'])
                           if p.get('source_observation_ids'))
        legal = actor['trajectory'][point_index]['source_observation_ids']
        def break_reference(value):
            value['actors'][index]['trajectory'][point_index]['source_observation_ids'] = ['missing']
        for case, before, timeline, packet in self._repair_packet(
                '7916ef73', actor['actor_id'], break_reference):
            path = f'/actors/{index}/trajectory/{point_index}/source_observation_ids'
            self.assertIn(path, packet['allowed_paths'])
            restored = repair.materialize(case, dict(base_sha256=packet['base_sha256'],
                replacements=[dict(path=path, value=legal)]))
            self.assertEqual(restored, split(before)[0])
            self.assertTrue(validate_source_coverage(source, restored, timeline=timeline))
            with self.assertRaisesRegex(ValueError, 'not assigned'):
                repair.materialize(case, dict(base_sha256=packet['base_sha256'],
                    replacements=[dict(path=f'/actors/{index}/trajectory/{point_index}/lane_id',
                                       value='lane_fake')]))
            packet['scope_context'] = {'observations': ['frozen-review-required']}
            if 'request_sha256' in packet:
                # Construct the scope-review fixture before binding its hash.
                # A real frozen request cannot be edited after authorization.
                from _lib.contracts.conversion_policy import digest
                packet['request_sha256'] = digest({k:v for k,v in packet.items()
                                                   if k != 'request_sha256'})
            atomic_json(case/'state/behavior_repair.request.json', packet)
            with self.assertRaisesRegex(ContractError, 'scope review'):
                core._source_candidate_parts(case, dict(base_sha256=packet['base_sha256'],
                    replacements=[dict(path=path, value=legal)]))
            review = {'certificate_sha256': 'frozen', 'decisions': []}
            projected, carried = core._source_candidate_parts(case, dict(
                base_sha256=packet['base_sha256'],
                replacements=[dict(path=path, value=legal)], census_scope_review=review))
            self.assertEqual(projected, restored)
            self.assertEqual(carried, review)

    def test_ego_without_same_frame_group_stays_blocked(self):
        def unchanged(_value):
            pass
        for case, original, timeline, packet in self._repair_packet(
                '803e13de', 'ego', unchanged):
            path = '/actors/0/trajectory/3/source_observation_ids'
            self.assertIn(path, packet['allowed_paths'])
            with self.assertRaisesRegex(ValueError, 'cannot erase'):
                repair.materialize(case, dict(base_sha256=packet['base_sha256'],
                    replacements=[dict(path=path, value=[])]))
            earlier = next(row['source_observation_id'] for row in timeline['groups']
                           if row['actor_id'] == 'ego' and row['frame_id'] != 'frame_0008')
            patched = repair.materialize(case, dict(base_sha256=packet['base_sha256'],
                replacements=[dict(path=path, value=[earlier])]))
            with self.assertRaises(ContractError) as caught:
                validate_source_coverage(case, patched, timeline=timeline)
            self.assertEqual(caught.exception.rule_id, 'core_scene.source_coverage')
            self.assertEqual(caught.exception.actor_id, 'ego')

    def test_completed_raw_answer_reenters_existing_invalid_candidate_archive(self):
        source, answer, timeline = frozen('803e13de')
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)/source.name
            (case/'state').mkdir(parents=True)
            (case/'diagnostics/candidate_snapshots').mkdir(parents=True)
            shutil.copy2(source/'state/world_context.json', case/'state/world_context.json')
            candidate = case/'state/behavior.candidate.json'
            atomic_json(candidate, answer)
            snapshot = case/'diagnostics/candidate_snapshots'/'original.json'
            shutil.copy2(candidate, snapshot)
            progress = dict(sessions=[dict(stage='behavior', status='completed',
                                           session_id='frozen-session', attempt=1)],
                            diagnostics={'core_approximate_candidate': {'sha256': sha256(candidate)}})

            class Runner:
                def progress(self, _case):
                    return progress
                def save_progress(self, _case, _progress):
                    pass

            with (patch.object(core, '_candidate', return_value=snapshot),
                  patch.object(input_binding, 'timeline_enabled', return_value=True),
                  patch.object(input_binding, 'current_evidence_timeline',
                        return_value=timeline)):
                self.assertTrue(core._queue_source_repair(Runner(), case))
            row = progress['sessions'][0]
            self.assertEqual(row['status'], 'invalid_output')
            self.assertEqual(row['actor_id'], 'ego')
            self.assertTrue(Path(row['invalid_candidate_path']).is_file())
            self.assertFalse(candidate.exists())
            self.assertNotIn('core_approximate_candidate', progress['diagnostics'])

    def test_repaired_candidate_archives_only_the_incomplete_raw_copy(self):
        frozen_case, answer, _ = frozen('803e13de')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'delivery'
            source = Path(folder)/'source'/frozen_case.name
            isolated = root/'cases'/source.name
            for case in (source, isolated):
                (case/'state').mkdir(parents=True)
                (case/'batches').mkdir()
                for name in ('case.json', 'manifest.json'):
                    shutil.copy2(frozen_case/name, case/name)
                for name in ('binding.json', 'execution_policy.json', 'world_context.json',
                             'actor_registry.json', 'behavior_input.request.json',
                             'behavior_frames.json'):
                    shutil.copy2(frozen_case/'state'/name, case/'state'/name)
                for item in (frozen_case/'batches').glob('batch_*.json'):
                    shutil.copy2(item, case/'batches'/item.name)
            atomic_json(isolated/'state/behavior.candidate.json', answer)
            (source/'diagnostics').mkdir()
            archived = source/'diagnostics'/'original.json'
            shutil.copy2(isolated/'state/behavior.candidate.json', archived)
            atomic_json(source/'state/behavior_repair.request.json', dict(
                repair_contract='behavior-source-coverage.v1', base_path=str(archived),
                base_sha256=sha256(archived)))
            patch_file = source/'diagnostics/repaired.json'
            atomic_json(patch_file, dict(base_sha256=sha256(archived), replacements=[dict(
                path='/actors/0/trajectory/3/source_observation_ids', value=[])]))
            core._archive_pre_repair_copy(source, root, isolated, patch_file)
            self.assertFalse(isolated.exists())
            self.assertTrue((root/'diagnostics'/f'pre_source_repair.{sha256(archived)[:16]}').is_dir())

    def test_extra_task_requires_the_existing_actor_bound_request_and_budget(self):
        frozen_case, _, _ = frozen('803e13de')
        self.assertEqual(bounded_recovery.policy(frozen_case)['content_repairs'], 2)
        self.assertEqual(bounded_recovery.remaining(
            frozen_case, read_json(frozen_case/'state/progress.json')), 8)
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)/'case_frozen'
            (case/'state').mkdir(parents=True)
            archive = case/'diagnostics/invalid_candidates/original.json'
            archive.parent.mkdir(parents=True)
            archive.write_text('{}')
            packet = dict(repair_contract='behavior-source-coverage.v1', actor_id='ego',
                          base_path=str(archive),
                          allowed_paths=['/actors/0/trajectory/3/source_observation_ids',
                                         '/semantic_limitations'])
            atomic_json(case/'state/behavior_repair.request.json', packet)
            progress = dict(attempts={'behavior': 1}, sessions=[dict(
                stage='behavior', status='invalid_output', rule_id='core_scene.source_coverage',
                actor_id='ego', invalid_candidate_path=str(archive))])
            runner = core.CoreApproximateOrchestrator(object())
            with (patch.object(runner, 'progress', return_value=progress),
                  patch.object(V3Orchestrator, 'before_model_attempt') as reserved):
                runner.before_model_attempt(case, 'behavior')
                reserved.assert_called_once_with(case, 'behavior')
                packet['actor_id'] = 'actor_elsewhere'
                atomic_json(case/'state/behavior_repair.request.json', packet)
                with self.assertRaisesRegex(ContractError, 'assigned source coverage'):
                    runner.before_model_attempt(case, 'behavior')

    def test_assigned_source_repair_resumes_only_after_a_terminal_quota_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)/'case_frozen'
            (case/'state').mkdir(parents=True)
            archive = case/'diagnostics/invalid_candidates/original.json'
            failed_patch = case/'diagnostics/invalid_candidates/repair.json'
            archive.parent.mkdir(parents=True)
            archive.write_text('{}')
            failed_patch.write_text('{"untrusted": true}')
            atomic_json(case/'state/behavior_repair.request.json', dict(
                repair_contract='behavior-source-coverage.v1', actor_id='ego',
                base_path=str(archive), allowed_paths=[
                    '/actors/0/trajectory/3/source_observation_ids']))
            origin = dict(stage='behavior', status='invalid_output',
                rule_id='core_scene.source_coverage', actor_id='ego',
                invalid_candidate_path=str(archive))
            quota = dict(stage='behavior', status='failed', attempt=2,
                error_code='backend_protocol_error', task_id='task-quota',
                session_id='session-quota', invalid_candidate_path=str(failed_patch),
                candidate_validation_error='Behavior candidate has no terminal producer',
                output_file_size_bytes=20,
                message=("Quota exceeded for model 'qwen3.8-max'. Reason: Your token-plan "
                         "1-month quota has been exhausted. The quota will reset at "
                         "11-16 16:00:00 UTC. [dump: /tmp/provider.json]"))
            progress = dict(attempts={'behavior': 2}, sessions=[origin, quota])
            runner = core.CoreApproximateOrchestrator(object())
            with (patch.object(runner, 'progress', return_value=progress),
                  patch.object(V3Orchestrator, 'before_model_attempt') as reserved):
                runner.before_model_attempt(case, 'behavior')
                reserved.assert_called_once_with(case, 'behavior')
                self.assertFalse((case/'state/behavior.candidate.json').exists())

                quota['message'] = 'temporary backend failure'
                with self.assertRaisesRegex(ContractError, 'assigned source coverage'):
                    runner.before_model_attempt(case, 'behavior')
                quota['message'] = ("Quota exceeded for model 'qwen3.8-max'. Reason: Your "
                    "token-plan 1-month quota has been exhausted. The quota will reset at "
                    "11-16 16:00:00 UTC.")
                atomic_json(case/'state/behavior.candidate.json', {'untrusted': True})
                with self.assertRaisesRegex(ContractError, 'assigned source coverage'):
                    runner.before_model_attempt(case, 'behavior')

    def test_resumed_source_patch_goes_directly_to_behavior_then_projection(self):
        class StopAtProjection(Exception):
            pass
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            case = base/'video_extractor/cases/case_resume'
            (case/'state').mkdir(parents=True)
            atomic_json(case/'state/behavior_repair.request.json', dict(
                repair_contract='behavior-source-coverage.v1'))
            progress = dict(phase='aggregating', status='aggregating',
                            diagnostics={}, sessions=[])

            class Runner:
                def __init__(self):
                    self.calls = []
                def progress(self, _case):
                    return progress
                def _behavior(self, _case):
                    self.calls.append('behavior_patch')
                    progress['diagnostics']['core_approximate_candidate'] = {'bound': True}
                def run_v3(self, *_args, **_kwargs):
                    raise AssertionError('World, Batch and full Behavior must not rerun')

            runner = Runner()
            with (patch.object(core, 'SCENARIO_ROOT', base),
                  patch.object(core, '_candidate', return_value=case/'candidate'),
                  patch.object(core, '_queue_source_repair', return_value=False),
                  patch.object(core, '_project', side_effect=StopAtProjection) as project):
                with self.assertRaises(StopAtProjection):
                    core._run_selected(runner, base/'video.mp4', {}, case)
            self.assertEqual(runner.calls, ['behavior_patch'])
            project.assert_called_once()
            (case/'state/behavior_repair.request.json').unlink()
            progress['diagnostics'].clear()
            progress['sessions'] = [dict(stage='behavior', status='invalid_output',
                rule_id='core_scene.source_coverage', actor_id='ego',
                invalid_candidate_path=str(case/'diagnostics/invalid.json'))]
            runner.calls.clear()
            with (patch.object(core, 'SCENARIO_ROOT', base),
                  patch.object(core, '_candidate', return_value=case/'candidate'),
                  patch.object(core, '_queue_source_repair', return_value=False),
                  patch.object(core, '_project', side_effect=StopAtProjection)):
                with self.assertRaises(StopAtProjection):
                    core._run_selected(runner, base/'video.mp4', {}, case)
            self.assertEqual(runner.calls, ['behavior_patch'])


if __name__ == '__main__':
    unittest.main()
