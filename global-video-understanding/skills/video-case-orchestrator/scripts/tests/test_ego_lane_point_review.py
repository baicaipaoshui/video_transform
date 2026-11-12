"""NO-LLM checks for routing explicit Ego evidence through point review."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_reconciliation_review import SKILLS, MockWorld, fixture

from _lib.behavior.input_binding import evidence_timeline
from _lib.contracts.reconciliation_alignment import inspect
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, raw_read_json, sha256
from _lib.runtime.fact_versions import (commit_reconciliation_lane_points, current_facts,
                                         establish_baseline, verified_reconciliation_lane_points)
from _lib.runtime.reconciliation_review import prepare, run
from _lib.world.actor_facts import EGO_LANE_OBSERVATIONS, EGO_LANE_OBSERVATIONS_KEY


ARCHIVED = SKILLS.parents[1] / 'video_extractor/cases/case_20261110T152017408Z_8e67615b'
ORIGINAL_WORLD = 'diagnostics/world_census_adjudication/initial/before/world.json'
EGO_SOURCE_FIELD = 'world.actor_hypotheses.actor_001.hypothesized_lane'
FRAME = 'frame_0007'
FIRST = 'frame_0001'
POINT = f'point:ego:{FRAME}:actor_lane'
LANES = ('lane_001', 'lane_002', 'lane_003')
BOUNDS = ('marking_001', 'marking_002', 'marking_003', 'marking_004')


class EgoLanePointReviewTests(unittest.TestCase):
    def make_case(self, *, world=True, batch=False, typed=True, ordinal=2,
                  status='observed', extra_points=False, enabled=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Adjust the existing point-review fixture before it freezes the
        # baseline, then use the real baseline publisher for this test Case.
        with patch('test_reconciliation_review.establish_baseline'):
            case = fixture(Path(temporary.name).resolve(), actor='actor_001', first=FIRST,
                           later=FRAME, lanes=LANES, bounds=BOUNDS)
        original = case/ORIGINAL_WORLD
        source_world = raw_read_json(original)
        source_world['questions_to_verify'] = []
        source_world['actor_hypotheses'][0]['hypothesized_lane'] = LANES[1]
        road = source_world['world']['road']
        road['lanes'] = [dict(lane_id=lane, road_id='road_main', ordinal_from_right=i,
                              right_boundary=BOUNDS[i], left_boundary=BOUNDS[i+1])
                         for i, lane in enumerate(LANES)]
        source_world['world']['metadata']['lane_layout_review']['observation']['ego_initial'].update(
            ordinal_from_right=1, right_boundary_id=BOUNDS[1], left_boundary_id=BOUNDS[2])

        frames = []
        for fid, time_s in ((FIRST, 0.0), (FRAME, 3.0)):
            source = ARCHIVED/'frames/probe'/f'{fid}.png'
            path = case/'frames'/source.name
            shutil.copyfile(source, path)
            frames.append(dict(frame_id=fid, time_s=time_s, path=str(path),
                               source_path=str(path), sha256=sha256(path)))
        archived_world = raw_read_json(ARCHIVED/ORIGINAL_WORLD)
        original_row = next(r for r in archived_world['field_evidence'][EGO_SOURCE_FIELD]
                            if r['frame_id'] == FRAME)
        row = dict(original_row)
        if typed:
            row.update(actor_id='ego', status=status,
                       lane_id=None if status == 'unknown' else LANES[ordinal],
                       ordinal_from_right=None if status == 'unknown' else ordinal)
        observations = [row]
        if extra_points:
            for i in range(1, 13):
                fid = f'frame_{100+i:04d}'
                path = case/'frames'/f'{fid}.png'
                path.write_bytes(fid.encode())
                frames.append(dict(frame_id=fid, time_s=3.0+i, path=str(path),
                                   source_path=str(path), sha256=sha256(path)))
                lane = 1 if i % 2 else 2
                observations.append(dict(row, frame_id=fid, time_s=3.0+i, lane_id=LANES[lane],
                                         ordinal_from_right=lane))
        if world:
            source_world['field_evidence'][EGO_SOURCE_FIELD] = [original_row]
            if typed:
                source_world['field_evidence']['ego_lane_observations'] = observations
        atomic_json(original, source_world)
        atomic_json(case/'state/lane_layout.commit.json', dict(old_world_sha256=sha256(original)))
        atomic_json(case/'state/world_context.json', source_world)

        batch_path = case/'batches/batch_007.json'
        batch_document = raw_read_json(batch_path)
        batch_document['registry_after'][0]['hypothesized_lane'] = LANES[1]
        batch_document['vehicle_census'] = []
        batch_document['observations'] = [dict(evidence=observations)] if batch else []
        atomic_json(batch_path, batch_document)
        batch_only = batch and not world
        atomic_json(case/'state/global_frames.json', dict(frames=[] if batch_only else frames))
        atomic_json(case/'state/probe.json', dict(decoded_frames=frames))
        atomic_json(case/'manifest.json', dict(batches=[dict(
            batch_id='batch_007', frames=frames if batch_only else [])]))
        atomic_json(case/'state/reconciliation.json', dict(status='reconciled', world=source_world,
            batch_sha256={batch_path.name: sha256(batch_path)}, unresolved_conflict_ids=[]))
        policy_path = case/'state/execution_policy.json'
        policy = raw_read_json(policy_path)
        if enabled:
            policy[EGO_LANE_OBSERVATIONS_KEY] = EGO_LANE_OBSERVATIONS
        atomic_json(policy_path, policy)
        establish_baseline(case)
        return case

    def test_world_and_batch_observations_share_one_original_frame_review(self):
        case = self.make_case(batch=True)
        before = current_facts(case)
        self.assertNotIn(POINT, before.claims)
        request_path = prepare(case)
        request = raw_read_json(request_path)
        self.assertTrue(request['ego_lane_observation_review'])
        group, = request['groups']
        self.assertEqual((group['actor_id'], group['frame_id'], group['current_lane_id']),
                         ('ego', FRAME, None))
        self.assertEqual(len(request['source_frames']), 1)
        self.assertEqual(group['frame']['time_s'], 3.0)
        self.assertEqual(group['frame']['sha256'], sha256(ARCHIVED/'frames/probe'/f'{FRAME}.png'))
        source_ids = {row['source_id'] for row in group['ego_lane_observations']}
        self.assertEqual(source_ids, {'world:original#/field_evidence/ego_lane_observations/0',
                                      'batch_007#/observations/0/evidence/0'})
        runner = MockWorld()
        result = run(runner, case)
        self.assertEqual(result['status'], 'committed')
        view = current_facts(case)
        self.assertNotEqual(view.version_id, before.version_id)
        self.assertTrue(verified_reconciliation_lane_points(case, view))
        claim = view.claims[POINT]
        self.assertEqual(claim['value'], LANES[2])
        self.assertTrue(source_ids <= set(claim['review_source_ids']))
        self.assertEqual(claim['frame_sha256'], group['frame']['sha256'])
        self.assertEqual(runner.calls, [('world_closure', 'global-video-understanding')])
        self.assertIsNone(run(runner, case))
        self.assertEqual(len(runner.calls), 1)

    def test_batch_only_observation_publishes_with_batch_source(self):
        case = self.make_case(world=False, batch=True)
        runner = MockWorld()
        self.assertEqual(run(runner, case)['status'], 'committed')
        claim = current_facts(case).claims[POINT]
        self.assertEqual(claim['source_path'], 'batches/batch_007.json')
        self.assertEqual(claim['value'], LANES[2])
        self.assertEqual(len(runner.calls), 1)

    def test_prose_same_lane_unknown_and_legacy_inputs_do_not_schedule_review(self):
        variants = ({'typed': False}, {'ordinal': 1}, {'status': 'unknown'}, {'enabled': False})
        for options in variants:
            with self.subTest(options=options):
                case = self.make_case(**options)
                runner = MockWorld()
                self.assertFalse(inspect(case)['blocking'])
                self.assertIsNone(run(runner, case))
                self.assertFalse(runner.calls)
                self.assertNotIn(POINT, current_facts(case).claims)
                candidates = evidence_timeline(case).get('ego_lane_candidates', [])
                self.assertEqual(bool(candidates), options in ({'ordinal': 1}, {'status': 'unknown'}))

    def test_compatible_new_source_with_effective_point_needs_no_review(self):
        case = self.make_case()
        before = current_facts(case)
        view = commit_reconciliation_lane_points(case, before.version_id, [dict(
            actor_id='ego', frame_id=FRAME, lane_id=LANES[2], ordinal_from_right=2,
            right_boundary_id=BOUNDS[2], left_boundary_id=BOUNDS[3],
            source_path=ORIGINAL_WORLD, source_sha256=sha256(case/ORIGINAL_WORLD),
            reason='Prior point review identifies the left corridor.', review_source_ids=['world:original'])])
        runner = MockWorld()
        self.assertTrue(verified_reconciliation_lane_points(case, view))
        self.assertIsNone(run(runner, case))
        self.assertFalse(runner.calls)
        self.assertEqual(current_facts(case).claims[POINT]['review_source_ids'], ['world:original'])

    def test_insufficient_original_frame_preserves_unknown_without_repeating_task(self):
        case = self.make_case()
        before = current_facts(case)
        runner = MockWorld(outcome='evidence_insufficient')
        with self.assertRaisesRegex(ContractError, 'has not closed its assigned facts'):
            run(runner, case)
        self.assertEqual(current_facts(case).version_id, before.version_id)
        self.assertNotIn(POINT, current_facts(case).claims)
        self.assertEqual(len(evidence_timeline(case)['ego_lane_candidates']), 1)
        with self.assertRaisesRegex(ContractError, 'new evidence is required'):
            run(runner, case)
        self.assertEqual(len(runner.calls), 1)

    def test_over_capacity_stops_without_splitting_or_publishing(self):
        case = self.make_case(extra_points=True)
        runner = MockWorld()
        self.assertEqual(len(inspect(case)['blocking']), 13)
        with self.assertRaisesRegex(ContractError, 'exceeds twelve original frames'):
            run(runner, case)
        self.assertFalse(runner.calls)
        self.assertFalse(current_facts(case).claims)
        self.assertFalse(list((case/'diagnostics/question_review').glob('alignment-*.request.json')))


if __name__ == '__main__':
    unittest.main()
