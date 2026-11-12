"""NO-LLM checks for preserving published Ego points through Behavior."""

import copy
import shutil
import unittest
from unittest.mock import patch

import test_ego_lane_point_review as point_review

from _lib.behavior import input_binding
from _lib.behavior.compile import write_evidence_index
from _lib.contracts.core_lane_continuity import obligations
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, raw_read_json, sha256
from _lib.runtime.core_approximate_delivery import _check_source_candidate
from _lib.runtime.fact_versions import (current_facts, establish_baseline,
                                         verified_reconciliation_lane_points)
from _lib.runtime.reconciliation_review import run


FIRST, BEFORE, AFTER = point_review.FIRST, 'frame_0004', point_review.FRAME
MIDDLE, LEFT = point_review.LANES[1:]


class EgoLanePointHandoffTests(unittest.TestCase):
    def make_case(self, *, renumber=False):
        # Share A0's source-backed fixture, but freeze both policies before
        # the baseline and give the initial corridor its 1.5 s source frame.
        with patch.object(point_review, 'establish_baseline'):
            case = point_review.EgoLanePointReviewTests.make_case(self)
        policy_path = case/'state/execution_policy.json'
        policy = raw_read_json(policy_path)
        policy.update(ego_lane_point_handoff='ego-lane-point-handoff.v1',
                      behavior_input_binding='behavior-input.v1')
        atomic_json(policy_path, policy)
        atomic_json(case/'case.json', dict(case_id=case.name, parameters={}))

        path = case/'frames'/f'{BEFORE}.png'
        shutil.copyfile(point_review.ARCHIVED/'frames/probe'/path.name, path)
        frame = dict(frame_id=BEFORE, time_s=1.5, path=str(path),
                     source_path=str(path), sha256=sha256(path))
        for filename, key in (('global_frames.json', 'frames'), ('probe.json', 'decoded_frames')):
            index_path = case/'state'/filename
            index = raw_read_json(index_path)
            index[key].insert(1, frame)
            atomic_json(index_path, index)

        world_path = case/'state/world_context.json'
        effective = raw_read_json(world_path)
        effective['evidence_epoch'] = 1
        review = effective['world']['metadata']['lane_layout_review']
        review['observation']['ego_initial'].update(
            evidence_frame_ids=[FIRST, BEFORE], reason='The camera occupies the middle corridor.')
        source = copy.deepcopy(effective)
        receipt_path = case/'state/reconciliation.json'
        receipt = raw_read_json(receipt_path)
        if renumber:
            # The old IDs overlap the effective IDs. Applying this map a
            # second time would turn the confirmed left point into the middle.
            right = point_review.LANES[0]
            lane_map = {right: LEFT, MIDDLE: right, LEFT: MIDDLE}
            old_id = {new: old for old, new in lane_map.items()}
            review['adjudication']['lane_id_map'] = lane_map
            road = source['world']['road']
            for lane in road['lanes']:
                lane['lane_id'] = old_id[lane['lane_id']]
            for arm in road['roads']:
                arm['lane_ids'] = [old_id[lane] for lane in arm['lane_ids']]
                for anchor in arm['lane_knowledge']['anchors']:
                    anchor['lane_id'] = old_id[anchor['lane_id']]
            source['actor_hypotheses'][0]['hypothesized_lane'] = old_id[MIDDLE]
            for rows in source['field_evidence'].values():
                for evidence in rows:
                    if evidence.get('actor_id') == 'ego' and evidence.get('lane_id') is not None:
                        evidence['lane_id'] = old_id[evidence['lane_id']]
            batch_path = case/'batches/batch_007.json'
            batch = raw_read_json(batch_path)
            batch['registry_after'][0]['hypothesized_lane'] = old_id[MIDDLE]
            atomic_json(batch_path, batch)
            receipt['batch_sha256'][batch_path.name] = sha256(batch_path)
        original = case/point_review.ORIGINAL_WORLD
        atomic_json(original, source)
        atomic_json(case/'state/lane_layout.commit.json', dict(old_world_sha256=sha256(original)))
        atomic_json(world_path, effective)
        receipt['world'] = effective
        atomic_json(receipt_path, receipt)

        establish_baseline(case)
        self.assertNotIn(point_review.POINT, current_facts(case).claims)
        runner = point_review.MockWorld()
        self.assertEqual(run(runner, case)['status'], 'committed')
        self.assertEqual(runner.calls, [('world_closure', 'global-video-understanding')])
        view = current_facts(case)
        self.assertTrue(verified_reconciliation_lane_points(case, view))
        self.assertEqual(view.claims[point_review.POINT]['value'], LEFT)

        write_evidence_index(case)
        request = input_binding.freeze(case)
        self.assertEqual(input_binding.verify(case), request)
        return case, request

    def behavior(self, request):
        ego_sources = {row['frame_id']: row['source_observation_id']
                       for row in request['evidence_timeline']['groups'] if row['actor_id'] == 'ego'}
        trajectory = []
        for frame, time, lane in ((FIRST, 0.0, MIDDLE), (BEFORE, 1.5, MIDDLE), (AFTER, 3.0, LEFT)):
            trajectory.append(dict(frame_id=frame, time_s=time, lane_id=lane,
                                   detail='Observed camera corridor.',
                                   source_observation_ids=[ego_sources[frame]] if frame in ego_sources else []))
        event_evidence = [dict(frame_id=frame, time_s=time, status='observed',
                               reason='Camera corridor relative to the road boundaries.',
                               source_observation_ids=[ego_sources[frame]] if frame in ego_sources else [])
                          for frame, time in ((BEFORE, 1.5), (AFTER, 3.0))]
        return dict(actors=[dict(actor_id='ego', trajectory=trajectory)], semantic_limitations=[],
                    events=[dict(event_id='event_001', event_type='lane_change',
                                 involved_actor_ids=['ego'], from_lane_id=MIDDLE, to_lane_id=LEFT,
                                 time_range_s=[1.5, 3.0], evidence=event_evidence)])

    def test_published_point_freezes_and_passes_core_and_normal_behavior(self):
        for renumber in (False, True):
            with self.subTest(renumber=renumber):
                case, request = self.make_case(renumber=renumber)
                group, = [row for row in request['evidence_timeline']['groups']
                          if (row['actor_id'], row['frame_id']) == ('ego', AFTER)]
                self.assertEqual(group['time_s'], 3.0)
                self.assertEqual([row['value'] for row in group['accepted_claims']], [LEFT])
                change, = [row for row in obligations(case)['obligations'] if row['actor_id'] == 'ego']
                self.assertEqual(change['observation_envelope_s'], [1.5, 3.0])
                self.assertEqual((change['source_frame_id'], change['target_frame_id']), (BEFORE, AFTER))
                self.assertEqual((change['source_lane_id_effective'], change['target_lane_id_effective']),
                                 (MIDDLE, LEFT))
                valid = self.behavior(request)
                _check_source_candidate(case, valid)
                input_binding.write_behavior(case, valid)
                self.assertEqual(raw_read_json(case/'state/behavior.json'), valid)

    def test_core_and_normal_behavior_reject_missing_or_changed_endpoints(self):
        case, request = self.make_case()
        valid = self.behavior(request)
        for variant in ('flat', 'omitted', 'null', 'wrong_time', 'before_omitted'):
            with self.subTest(variant=variant):
                candidate = copy.deepcopy(valid)
                points = candidate['actors'][0]['trajectory']
                if variant == 'flat':
                    points[-1]['lane_id'] = MIDDLE
                    candidate['events'] = []
                elif variant == 'omitted':
                    # Event citations alone still cover the source in the old gate.
                    points.pop()
                elif variant == 'null':
                    points[-1]['lane_id'] = None
                elif variant == 'wrong_time':
                    # Keep the valid event citation to isolate the time of the sample.
                    points[-1]['time_s'] = 3.5
                    points[-1]['source_observation_ids'] = []
                else:
                    points.pop(1)
                with self.assertRaises(ContractError):
                    _check_source_candidate(case, candidate)
                with self.assertRaises(ContractError):
                    input_binding.write_behavior(case, candidate)
                self.assertFalse((case/'state/behavior.json').exists())

    def test_legacy_ego_point_remains_verified_under_original_policy(self):
        case = point_review.ARCHIVED.parent/'case_20261026T100153468Z_6fb2ffbb'
        if not case.is_dir():
            self.skipTest('Archived Ego point Case is unavailable')
        self.assertNotIn('ego_lane_point_handoff', raw_read_json(case/'state/execution_policy.json'))
        view = current_facts(case)
        self.assertTrue(verified_reconciliation_lane_points(case, view))
        self.assertEqual(view.claims['point:ego:frame_0021:actor_lane']['value'], LEFT)
        self.assertFalse(any(row['actor_id'] == 'ego' for row in obligations(case)['obligations']))


if __name__ == '__main__':
    unittest.main()
