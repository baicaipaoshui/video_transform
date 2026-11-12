"""NO-LLM source-only World review across independent archived Case inputs."""
import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from _lib.contracts.v3 import ContractError
from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime import question_review


ROOT = Path(__file__).resolve().parents[5]
CASES = {
    '1f7c1391': ROOT/'video_extractor/core_approximate_deliveries/case_20261022T102302324Z_1f7c1391/cases/case_20261022T102302324Z_1f7c1391',
    'a2219ae2': ROOT/'video_extractor/core_approximate_deliveries/case_20261018T143241657Z_a2219ae2/cases/case_20261018T143241657Z_a2219ae2',
    '065638b5': ROOT/'audit_reports/general_recovery_20261022/core_065638b5/cases/case_20261022T035340201Z_065638b5',
}


def clone(source, parent):
    # Hard links keep the large original images byte-identical. atomic_json
    # replaces only the isolated destination inode when a test changes input.
    path = Path(parent)/source.name
    shutil.copytree(source, path, copy_function=os.link)
    return path


class UnregisteredObservationReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unregistered-world-')

    def tearDown(self):
        self.temp.cleanup()

    def case(self, key, *, synthetic=False):
        case = clone(CASES[key], self.temp.name)
        if synthetic:
            path = case/'state/behavior.candidate.json'
            if not path.is_file():
                sources = [*(case/'diagnostics/candidate_snapshots').glob('behavior.*.json'),
                           *(case/'diagnostics/invalid_candidates').glob('behavior.*.json')]
                snapshot = next(source for source in sources
                                if 'semantic_limitations' in read_json(source))
                atomic_json(path, read_json(snapshot))
            candidate = read_json(path)
            candidate['semantic_limitations'].append(dict(
                limitation_id='fixture_unregistered_source',
                limitation_kind='other',
                description='Unregistered visual candidate at frame_0001; identity and core relevance unknown.',
                impact_domains=['actor_identity', 'actor_lane', 'actor_motion'],
                execution_effect='bounded_uncertainty'))
            atomic_json(path, candidate)
        return case

    def answer(self, request, **changes):
        frames = request['source_frames']
        answer = dict(request_sha256=request['request_sha256'],
            vehicle_status='uncertain', identity_status='uncertain', existing_actor_id=None,
            ego_path_relation='uncertain', motion_evidence='insufficient',
            interaction_evidence='insufficient', physical_boundary_id=None,
            time_range_s=[frames[0]['time_s'], frames[-1]['time_s']],
            evidence=[dict(frame_id=row['frame_id'], time_s=row['time_s'],
                           status='observed', reason='Mock: visual fact remains undecided')
                      for row in frames])
        answer.update(changes)
        return answer

    def test_three_cases_locate_source_without_fabricated_identity(self):
        for key in CASES:
            with self.subTest(key=key):
                case = self.case(key, synthetic=key != '1f7c1391')
                paths = question_review.prepare_unregistered(case)
                self.assertEqual(len(paths), 1)
                request = question_review.verify_source_observation(case, paths[0])
                self.assertEqual(request['source_sha256'], sha256(case/'state/behavior.candidate.json'))
                self.assertNotIn('question_id', request)
                self.assertNotIn('observation_id', request)
                self.assertEqual(request['source_frames'][0]['frame_id'],
                                 'frame_0007' if key == '1f7c1391' else 'frame_0001')
                self.assertEqual(request['observation_frame_ids'],
                                 ['frame_0010', 'frame_0021'] if key == '1f7c1391'
                                 else ['frame_0001'])
                candidate = paths[0].with_name(paths[0].name.replace('.request.json', '.candidate.json'))
                atomic_json(candidate, self.answer(request))
                result = question_review.commit_source_observation(case, paths[0], candidate)
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(result['disposition'], 'needs_semantic_review')
                self.assertEqual(question_review.commit_source_observation(case, paths[0], candidate), result)

    def test_real_case_routes_registered_and_structured_opposing_scope(self):
        case = self.case('1f7c1391')
        paths = question_review.prepare_unregistered(case)
        self.assertEqual(len(paths), 1)
        self.assertEqual(read_json(paths[0])['source_pointer'], '/semantic_limitations/3')
        source = case/'state/behavior.candidate.json'
        with self.assertRaises(ContractError):
            question_review.prepare_source_observation(
                case, source_path=source, source_pointer='/semantic_limitations/4',
                source_sha256=sha256(source), frame_ids=['frame_0001', 'frame_0002'])

    def test_positive_mock_receipt_is_scoped_across_other_cases(self):
        for key in ('a2219ae2', '065638b5'):
            with self.subTest(key=key):
                case = self.case(key, synthetic=True)
                path = question_review.prepare_unregistered(case)[0]
                request = read_json(path)
                candidate = path.with_name(path.name.replace('.request.json', '.candidate.json'))
                atomic_json(candidate, self.answer(request, vehicle_status='not_vehicle',
                                                   identity_status='not_applicable'))
                question_review.commit_source_observation(case, path, candidate)
                original = read_json(case/'state/behavior.candidate.json')
                projected, audits = question_review.apply_source_observation_reviews(case, original)
                self.assertEqual(len(original['semantic_limitations']) - len(projected['semantic_limitations']), 1)
                self.assertEqual(audits[0]['decisions'][0]['disposition'], 'not_vehicle')
                self.assertFalse((case/'final/target.json').exists())

    def test_core_copy_replays_original_source_review_once(self):
        import importlib.util
        from unittest.mock import patch
        from _lib.behavior.compile import evidence_frames
        from _lib.runtime import core_approximate_delivery as core
        from _lib.runtime.fact_versions import current_facts

        case = self.case('065638b5', synthetic=True)
        scenario = Path(self.temp.name).resolve()/'scenario'
        owner_case = scenario/'video_extractor/cases'/case.name
        owner_case.parent.mkdir(parents=True)
        shutil.move(case, owner_case)
        case = owner_case
        path = question_review.prepare_unregistered(case)[0]
        request = read_json(path)
        candidate = path.with_name(path.name.replace('.request.json', '.candidate.json'))
        atomic_json(candidate, self.answer(request, vehicle_status='not_vehicle',
                                           identity_status='not_applicable'))
        question_review.commit_source_observation(case, path, candidate)
        isolated = Path(self.temp.name)/'delivery/cases'/case.name
        shutil.copytree(case, isolated)
        isolated = isolated.resolve()
        original = read_json(case/'state/behavior.candidate.json')
        before = sha256(case/'state/behavior.candidate.json')
        # Frozen absolute paths belong to the owner, not its copied directory.
        self.assertEqual(question_review.apply_source_observation_reviews(isolated, original),
                         (original, []))
        raw, review = core.split(original)
        view, frames = current_facts(isolated), evidence_frames(isolated)
        producer = core.prepare_source_behavior(case, view, raw, frames)
        replay = core.prepare_source_behavior(case, view, raw, frames)
        self.assertEqual(producer, replay)
        self.assertEqual(sum(row.get('rule_id') == 'behavior.world_source_observation.v1'
                             for row in replay[1]), 1)
        self.assertEqual(len(original['semantic_limitations']) -
                         len(replay[0]['semantic_limitations']), 1)
        self.assertEqual(sha256(case/'state/behavior.candidate.json'), before)
        # Exercise the independent consumer, including its real source-set and
        # Census disposition validation; only the test workspace root changes.
        saved = core.census_adjudication.proof(isolated)
        pending = core.behavior_census_scope.pending(isolated, saved)
        dispositions = core.validated_dispositions(isolated, producer[0], pending, review, frames)
        copied = isolated/'state/behavior.candidate.json'
        projection = dict(revision=core.PROJECTION_REVISION,
            raw_candidate_sha256=sha256(copied),
            source_hashes=core._source_hashes(isolated, view, copied, frames),
            normalized_behavior_digest=digest(producer[0]), normalizations=producer[1],
            census_dispositions_digest=dispositions['sha256'],
            observation_risks=core.disposition_risks(pending, review, dispositions))
        script = Path(__file__).resolve().parents[1]/'accept_core_approximate_target.py'
        spec = importlib.util.spec_from_file_location('core_review_acceptance', script)
        acceptance = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(acceptance)
        with patch.object(acceptance, 'SCENARIO_ROOT', scenario):
            consumed = acceptance.replay_dispositions(isolated.parents[1], isolated, projection, view)
        self.assertEqual(consumed[0], producer[0])
        answer = read_json(candidate)
        answer['vehicle_status'] = 'uncertain'
        atomic_json(candidate, answer)
        with self.assertRaises(ContractError):
            core.prepare_source_behavior(case, view, raw, frames)

    def test_forged_ambiguous_and_stale_sources_are_rejected(self):
        case = self.case('1f7c1391')
        source = case/'state/behavior.candidate.json'
        signature = sha256(source)
        kwargs = dict(source_path=source, source_pointer='/semantic_limitations/3',
                      source_sha256=signature, frame_ids=['frame_0010', 'frame_0021'])
        for change in (dict(source_sha256='0'*64), dict(source_pointer='/semantic_limitations/999'),
                       dict(frame_ids=['frame_0010']), dict(frame_ids=['frame_0010', 'frame_9999'])):
            with self.subTest(change=change), self.assertRaises(ContractError):
                question_review.prepare_source_observation(case, **dict(kwargs, **change))
        request_path = question_review.prepare_source_observation(case, **kwargs)
        candidate = read_json(source)
        candidate['semantic_limitations'][3]['description'] += ' Changed after freeze.'
        atomic_json(source, candidate)
        with self.assertRaises(ContractError):
            question_review.verify_source_observation(case, request_path)

    def test_downstream_pointer_must_replay_exact_behavior_source(self):
        case = self.case('1f7c1391')
        original = read_json(case/'state/behavior.candidate.json')['semantic_limitations'][3]
        analysis_path = case/'final/analysis.json'
        atomic_json(analysis_path, dict(limitations=[original], events=[]))
        request_path = question_review.prepare_source_observation(
            case, source_path=analysis_path, source_pointer='/limitations/0',
            source_sha256=sha256(analysis_path), frame_ids=['frame_0010', 'frame_0021'])
        self.assertEqual(question_review.verify_source_observation(case, request_path)['source_value'], original)
        changed = copy.deepcopy(original); changed['description'] += ' invented'
        atomic_json(analysis_path, dict(limitations=[changed], events=[]))
        with self.assertRaises(ContractError):
            question_review.prepare_source_observation(
                case, source_path=analysis_path, source_pointer='/limitations/0',
                source_sha256=sha256(analysis_path), frame_ids=['frame_0010', 'frame_0021'])

    def test_resigned_request_cannot_swap_original_frame(self):
        case = self.case('1f7c1391')
        path = question_review.prepare_unregistered(case)[0]
        packet = read_json(path)
        packet['source_frames'][0] = copy.deepcopy(packet['source_frames'][1])
        body = dict(packet); body.pop('request_sha256')
        packet['request_sha256'] = digest(body)
        atomic_json(path, packet)
        with self.assertRaises(ContractError):
            question_review.verify_source_observation(case, path)

    def test_mock_world_answer_cannot_fake_identity_or_path_coverage(self):
        case = self.case('1f7c1391')
        path = question_review.prepare_unregistered(case)[0]
        request = read_json(path)
        candidate = path.with_name(path.name.replace('.request.json', '.candidate.json'))
        for answer in (
            self.answer(request, identity_status='existing', existing_actor_id='actor_fake'),
            self.answer(request, vehicle_status='vehicle', identity_status='distinct',
                        ego_path_relation='separated', interaction_evidence='sufficient',
                        physical_boundary_id='marking_003'),
            self.answer(request, evidence=[]),
        ):
            with self.subTest(answer=answer['identity_status']), self.assertRaises(ContractError):
                atomic_json(candidate, answer)
                question_review.commit_source_observation(case, path, candidate)
        # A mock nonvehicle verdict exercises acceptance only. It is not a
        # visual judgment about this real Case or permission to make a Target.
        atomic_json(candidate, self.answer(request, vehicle_status='not_vehicle',
                                           identity_status='not_applicable'))
        result = question_review.commit_source_observation(case, path, candidate)
        self.assertEqual(result['disposition'], 'not_vehicle')
        projected, audits = question_review.apply_source_observation_reviews(
            case, read_json(case/'state/behavior.candidate.json'))
        self.assertEqual(len(audits), 1)
        self.assertNotIn('lim_004', {row['limitation_id'] for row in projected['semantic_limitations']})
        self.assertIn('lim_005', {row['limitation_id'] for row in projected['semantic_limitations']})
        self.assertIn('lim_006', {row['limitation_id'] for row in projected['semantic_limitations']})
        self.assertIn('lim_004', {row['limitation_id'] for row in
                                  read_json(case/'state/behavior.candidate.json')['semantic_limitations']})
        self.assertFalse((case/'final/target.json').exists())

    def test_existing_world_task_entry_and_receipt_replay(self):
        case = self.case('1f7c1391')
        path = question_review.prepare_unregistered(case)[0]
        request = read_json(path)
        calls = []

        class MockRunner:
            def model_stage(self, case_dir, stage, agent, prompt, adopt, recover, *, candidate_path):
                calls.append((stage, agent, prompt, candidate_path))
                atomic_json(candidate_path, self_answer)
                return adopt()

        self_answer = self.answer(request)
        first = question_review.run_source_observation(MockRunner(), case, path)
        second = question_review.run_source_observation(MockRunner(), case, path)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ('world_closure', 'global-video-understanding'))
        self.assertFalse((case/'final/target.json').exists())


if __name__ == '__main__':
    unittest.main()
