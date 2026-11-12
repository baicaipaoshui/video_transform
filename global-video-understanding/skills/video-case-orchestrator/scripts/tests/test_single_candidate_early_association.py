"""NO-LLM checks for bounded single-candidate early associations."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts import census_execution, early_association


class SingleCandidateEarlyAssociationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)
        (self.case/'state').mkdir()
        (self.case/'case.json').write_text(json.dumps({'parameters': {}}))
        (self.case/'state/execution_policy.json').write_text(json.dumps({
            early_association.POLICY_KEY: early_association.AUTOMATIC_REVISION,
            'conversion_semantic_gap': 'conversion-semantic-motion.v1',
            'conversion_parameters_sha256': 'parameters',
        }))
        self.frames = {
            'frame_0': {'frame_id': 'frame_0', 'time_s': 0.0},
            'frame_1': {'frame_id': 'frame_1', 'time_s': 1.5},
            'frame_2': {'frame_id': 'frame_2', 'time_s': 3.0},
            'frame_late': {'frame_id': 'frame_late', 'time_s': 3.5},
        }
        self.world = {'world': {'scene_type': 'highway', 'junctions': [], 'road': {'lanes': [
            {'lane_id': 'lane_ego', 'travel_direction': 'same_as_ego'},
            {'lane_id': 'lane_target', 'travel_direction': 'same_as_ego'},
            {'lane_id': 'lane_right', 'travel_direction': 'same_as_ego'},
        ]}}, 'questions_to_verify': [], 'ambiguities': []}
        self.early_issue = 'early_observation'
        self.late_issue = 'later_observation'
        self.early_actor = {'actor_id': 'vehicle_target', 'actor_type': 'vehicle',
            'is_static': False, 'trajectory': [
                {'frame_id': 'frame_1', 'time_s': 1.5, 'lane_id': 'lane_target'},
                {'frame_id': 'frame_2', 'time_s': 3.0, 'lane_id': 'lane_target'},
            ]}
        self.late_actor = {'actor_id': 'vehicle_right', 'actor_type': 'vehicle',
            'is_static': False, 'trajectory': [
                {'frame_id': 'frame_0', 'time_s': 0.0, 'lane_id': 'lane_right'},
                {'frame_id': 'frame_1', 'time_s': 1.5, 'lane_id': 'lane_right'},
            ]}
        self.early_limit = {'limitation_id': 'early_identity',
            'census_issue_ids': [self.early_issue], 'actor_ids': ['vehicle_target'],
            'impact_domains': ['actor_identity'], 'execution_effect': 'bounded_uncertainty'}
        self.late_limit = {'limitation_id': 'later_identity',
            'census_issue_ids': [self.late_issue], 'actor_ids': ['vehicle_right'],
            'impact_domains': ['actor_identity'], 'execution_effect': 'bounded_uncertainty'}
        self.behavior = {'actors': [
            {'actor_id': 'ego', 'actor_type': 'vehicle', 'trajectory': [
                {'frame_id': 'frame_0', 'time_s': 0.0, 'lane_id': 'lane_ego'},
                {'frame_id': 'frame_1', 'time_s': 1.5, 'lane_id': 'lane_ego'},
            ]}, self.early_actor, self.late_actor], 'events': [],
            'executor_primitives': [],
            'semantic_limitations': [self.early_limit, self.late_limit]}
        self.pending = {
            self.early_issue: {'issue_id': self.early_issue, 'frame_id': 'frame_0',
                'candidate_actor_ids': ['vehicle_target']},
            self.late_issue: {'issue_id': self.late_issue, 'frame_id': 'frame_late',
                'candidate_actor_ids': ['vehicle_right']},
        }
        self.review = {'decisions': [
            self.decision(self.early_issue, 'vehicle_target'),
            self.decision(self.late_issue, 'vehicle_right'),
        ]}
        self.registry = [
            {'actor_id': 'vehicle_target', 'actor_type': 'vehicle',
             'first_seen_frame_id': 'frame_1'},
            {'actor_id': 'vehicle_right', 'actor_type': 'vehicle',
             'first_seen_frame_id': 'frame_0'},
        ]
        self.saved = {'sha256': 'certificate',
            'result': {'pending_distinctions': [], 'registry': self.registry}}
        self.conversion = {'parameters_sha256': 'parameters', 'allowed_degradations': [
            'vehicle_position_policy_default', 'vehicle_speed_policy_default',
            'trajectory_continuation_policy_default']}

    @staticmethod
    def decision(issue_id, actor_id):
        return {'issue_id': issue_id, 'outcome': 'needs_identity',
            'affected_actor_ids': [actor_id], 'affected_event_ids': [],
            'identity_assessment': {'candidate_status': 'compatible',
                'alternative_actor_ids': [], 'counterevidence': []}}

    def derive(self, pending=None, behavior=None):
        with patch('_lib.contracts.conversion_policy.build', return_value=self.conversion), \
             patch('_lib.contracts.census_relevance.validate', return_value=None):
            return early_association.group_candidate_independence(
                self.case, behavior or self.behavior,
                pending or {self.early_issue: self.pending[self.early_issue]},
                self.review, self.frames, self.world, self.saved,
                registry=self.registry)

    def test_single_candidate_early_prefix_is_kept_unknown(self):
        group = self.derive()
        self.assertEqual(group['actor_ids'], ['vehicle_target'])
        self.assertEqual(group['identity_resolution'], 'unresolved')
        self.assertEqual(group['actors'][0]['observed_from_s'], 1.5)
        self.assertEqual(group['description_usage'], 'audit_only')

    def test_other_later_identity_issue_does_not_hide_valid_early_group(self):
        snapshot = copy.deepcopy(self.saved)
        with patch('_lib.contracts.conversion_policy.build', return_value=self.conversion), \
             patch('_lib.contracts.census_relevance.validate', return_value=None):
            proof = early_association.automatic(self.case, self.behavior, self.pending,
                self.review, self.frames, self.world, snapshot=snapshot)
        self.assertEqual([row['issue_ids'] for row in proof['groups']],
                         [[self.early_issue]])
        self.assertNotIn(self.late_issue,
                         {issue for row in proof['groups'] for issue in row['issue_ids']})

    def test_motion_reconstruction_metadata_does_not_suppress_early_proof(self):
        self.assertFalse(census_execution._identity_assumptions_active({
            'observations': {}, 'lanes': {}, 'bounded_execution': {'revision': 'v1'}}))
        for active in ({'observations': {'issue': {}}}, {'lanes': {'vehicle': {}}},
                       {'inherited_opposing': ['vehicle']}):
            with self.subTest(active=active):
                self.assertTrue(census_execution._identity_assumptions_active(active))

    def test_early_prefix_inside_ego_corridor_is_rejected(self):
        behavior = copy.deepcopy(self.behavior)
        next(actor for actor in behavior['actors'] if actor['actor_id'] == 'vehicle_target')[
            'trajectory'][0]['lane_id'] = 'lane_ego'
        next(actor for actor in behavior['actors'] if actor['actor_id'] == 'vehicle_target')[
            'trajectory'][1]['lane_id'] = 'lane_ego'
        with self.assertRaises(early_association.IndependenceNotEstablished):
            self.derive(behavior=behavior)

    def test_maneuver_before_confirmed_first_seen_is_rejected(self):
        behavior = copy.deepcopy(self.behavior)
        behavior['events'] = [{'event_id': 'lane_change', 'event_type': 'lane_change',
            'involved_actor_ids': ['ego'], 'time_range_s': [0.5, 1.5]}]
        with self.assertRaises(early_association.IndependenceNotEstablished):
            self.derive(behavior=behavior)

    def test_later_observation_cannot_use_early_prefix_contract(self):
        with self.assertRaises(early_association.IndependenceNotEstablished):
            self.derive(pending={self.late_issue: self.pending[self.late_issue]})


if __name__ == '__main__':
    unittest.main()
