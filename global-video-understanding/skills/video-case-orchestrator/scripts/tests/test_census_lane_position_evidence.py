"""Offline admission checks for new Census lane observations."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts import census_adjudication as census
from _lib.contracts.census_history import replay
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import raw_read_json, sha256
from _lib.runtime.world_adjudication import compile_response


CASES = SKILLS.parents[1] / 'video_extractor/cases'
TRAILER_CASE = CASES / 'case_20261110T152017408Z_8e67615b'
PICKUP_CASE = CASES / 'case_20261019T175555560Z_1f7ff9e6'


def bind(packet):
    packet.pop('request_sha256', None)
    packet['request_sha256'] = digest(packet)


def sighting(response, actor_id, frame_id):
    vehicle = next(row for row in response['vehicles'] if row['actor_id'] == actor_id)
    return next(row for row in vehicle['sightings'] if row['frame_id'] == frame_id)


class CensusLanePositionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archived = raw_read_json(TRAILER_CASE/'state/census_adjudication.accepted.json')
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        isolated = Path(temporary.name).resolve()/TRAILER_CASE.name
        isolated.mkdir()
        packet = copy.deepcopy(cls.archived['request'])
        packet['case_dir'] = str(isolated)
        # Keep the actual frozen images and inner layout packet as read-only
        # evidence. Rebind the isolated Case metadata and Batch files by hash.
        files = ['case.json', 'manifest.json', 'state/execution_policy.json',
                 'state/probe.json', 'state/global_frames.json']
        files.extend(str(path.relative_to(TRAILER_CASE)) for path in
                     sorted((TRAILER_CASE/'batches').glob('batch_*.json')))
        for name in files:
            source, copied = TRAILER_CASE/name, isolated/name
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copied)
            if name == 'state/execution_policy.json':
                policy = raw_read_json(copied)
                policy[census.LANE_POSITION_KEY] = census.LANE_POSITION_REVISION
                copied.write_text(json.dumps(policy), encoding='utf-8')
            packet['input_hashes'].pop(str(source))
            packet['input_hashes'][str(copied)] = sha256(copied)
        packet[census.LANE_POSITION_KEY] = census.LANE_POSITION_REVISION
        packet['response_schema'] = census.response_schema(
            packet['revision'], layout=packet['layout_request'],
            lane_position_evidence=census.LANE_POSITION_REVISION)
        bind(packet)
        census.verify_packet(packet)
        cls.packet = packet

    def answer(self):
        result = copy.deepcopy(self.archived['response'])
        result['request_sha256'] = self.packet['request_sha256']
        return result

    def same_corridor_trailer(self):
        result = self.answer()
        trailer = next(row for row in result['vehicles'] if row['actor_id'] == 'actor_001')
        for row in trailer['sightings']:
            if row['lane_id'] != 'lane_002':
                row.update(lane_id='lane_002', features='Trailer wheel track remains in the middle corridor.')
        # The other binding vehicle appears in this answer one frame earlier
        # than its Registry anchor. This is a new first known corridor point.
        first = sighting(result, 'actor_002', 'frame_0001')
        first['lane_position_evidence'] = dict(right_boundary_id='marking_003', left_boundary_id='marking_004')
        return result

    def test_archived_receipt_replays_under_original_policy(self):
        path = TRAILER_CASE/'state/census_adjudication.accepted.json'
        original_hash = sha256(path)
        self.assertNotIn(census.LANE_POSITION_KEY, self.archived['request'])
        result = replay(self.archived)
        self.assertEqual(digest(result['result']), self.archived['result_sha256'])
        self.assertEqual(sha256(path), original_hash)

    def test_removing_policy_marker_cannot_restore_legacy_acceptance(self):
        packet = copy.deepcopy(self.packet)
        del packet[census.LANE_POSITION_KEY]
        bind(packet)
        with self.assertRaisesRegex(ContractError, 'differs from the frozen policy or parent'):
            census.verify_packet(packet)

    def test_archived_trailer_change_needs_evidence_at_first_changed_point(self):
        answer = self.answer()
        # A later claim cannot supply the missing evidence for frame_0010.
        later = sighting(answer, 'actor_001', 'frame_0012')
        later['lane_position_evidence'] = dict(right_boundary_id='marking_001', left_boundary_id='marking_002')
        with self.assertRaisesRegex(ContractError, 'evidence is required for v_actor_001 at frame_0010'):
            compile_response(self.packet, answer)

    def test_shape_valid_but_conflicting_corridor_is_rejected(self):
        answer = self.answer()
        changed = sighting(answer, 'actor_001', 'frame_0010')
        changed['lane_position_evidence'] = dict(right_boundary_id='marking_002', left_boundary_id='marking_003')
        census.validate_response(self.packet, answer)
        with self.assertRaisesRegex(ContractError, 'contradicts the physical corridor for v_actor_001 at frame_0010'):
            compile_response(self.packet, answer)

    def test_same_corridor_and_unknown_preserve_original_admission(self):
        answer = self.same_corridor_trailer()
        sighting(answer, 'actor_001', 'frame_0010').update(lane_id=None, features='Lane obscured in this view.')
        original = copy.deepcopy(answer)
        result = compile_response(self.packet, answer)
        self.assertEqual(result['vehicle_ids']['v_actor_001'], 'actor_001')
        self.assertEqual(answer, original)

    def test_unknown_does_not_reset_the_last_known_corridor(self):
        answer = self.same_corridor_trailer()
        sighting(answer, 'actor_001', 'frame_0010').update(lane_id=None, features='Lane obscured in this view.')
        sighting(answer, 'actor_001', 'frame_0012').update(lane_id='lane_001', features='Trailer appears to the right.')
        with self.assertRaisesRegex(ContractError, 'evidence is required for v_actor_001 at frame_0012'):
            compile_response(self.packet, answer)

    def test_independent_pickup_change_with_observed_boundaries(self):
        world = raw_read_json(PICKUP_CASE/'state/world_context.json')
        lanes = {row['lane_id']: row for row in world['world']['road']['lanes']}
        probe = raw_read_json(PICKUP_CASE/'state/probe.json')
        frames = {row['frame_id']: row for row in probe['decoded_frames']}
        # Original frames show the pickup on opposite sides of B2 at the
        # two endpoints. Its historical Census reply reviews other vehicles.
        old = {'actor_001': dict(first_seen_frame_id='frame_0001', hypothesized_lane='lane_003')}
        descriptor = {'pickup': dict(actor_id='actor_001', role='binding')}
        before, after, continued = 'frame_0007', 'frame_0016', 'frame_0022'
        points = {fid: dict(lane_id=lane_id, time_s=frames[fid]['time_s']) for fid, lane_id in
                  ((before, 'lane_003'), (after, 'lane_002'), (continued, 'lane_002'))}
        with self.assertRaisesRegex(ContractError, 'evidence is required for pickup at frame_0016'):
            census._validate_lane_positions(descriptor, {'pickup': points}, old, frames, lanes, None, {})
        points[after]['lane_position_evidence'] = dict(
            right_boundary_id='B1_dashed_white_1', left_boundary_id='B2_dashed_white_2')
        census._validate_lane_positions(descriptor, {'pickup': points}, old, frames, lanes, None, {})


if __name__ == '__main__':
    unittest.main()
