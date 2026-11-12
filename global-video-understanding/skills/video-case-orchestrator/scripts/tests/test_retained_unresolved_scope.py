"""NO-LLM checks for source-bound unresolved observation retention."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts import behavior_census_scope, census_execution, census_relevance
from _lib.contracts.actor_identity_revision import issues as identity_issues
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.execution_scope import EGO_POLICY, project
from _lib.delivery.report import sections
from _lib.world.gaps import IdentityEvidenceGap


class RetainedUnresolvedScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)
        (self.case/'state').mkdir()
        (self.case/'state/execution_policy.json').write_text(json.dumps({
            'world_census_adjudication': census_relevance.REVISION,
            'behavior_census_scope': census_relevance.RETAIN_BEHAVIOR,
            'execution_scope': EGO_POLICY,
        }))
        self.frames = {fid: dict(frame_id=fid, time_s=time) for fid, time in
                       [('frame_a', 0.0), ('frame_b', 1.0)]}
        self.world = dict(world=dict(road=dict(one_way=True, lanes=[
            dict(lane_id='through', road_id='main', travel_direction='same_as_ego')])),
            actor_hypotheses=[dict(actor_id='vehicle_a', first_seen_frame_id='frame_a')],
            questions_to_verify=[], ambiguities=[], critical_intervals=[])
        self.registry = [dict(actor_id='vehicle_a', actor_type='vehicle',
            first_seen_frame_id='frame_a', evidence=[dict(frame_id='frame_a',
                status='observed', reason='White lead car with square rear lights')])]
        self.actor = dict(actor_id='vehicle_a', actor_type='vehicle', simulation_role='participant',
            trajectory=[dict(frame_id=fid, time_s=self.frames[fid]['time_s'],
                             lane_id='through', road_id='main') for fid in self.frames])
        self.behavior = dict(actors=[dict(actor_id='ego', actor_type='vehicle',
            simulation_role='ego', trajectory=[]), self.actor], events=[],
            executor_primitives=[], semantic_limitations=[])
        self.observation = dict(issue_id='observation_a', frame_id='frame_a',
            candidate_actor_ids=['vehicle_a'], window_s=[0.0, 1.0],
            description='White car observed ahead of ego in source frame')
        self.proof = dict(rule_id='registered_observation.v1', actor_id='vehicle_a',
            frame_id='frame_a', source_description=self.observation['description'],
            registered_anchor_reason='White lead car with square rear lights',
            source_features='White lead car with square rear lights in source image',
            actor_features='Square rear lights visible in registered anchor',
            co_visible_distinctions=[], trajectory_frame_ids=['frame_a', 'frame_b'],
            relation_to_ego_path='ego_lane',
            interaction_evidence='Lead car remains in ego travel corridor at both frames')
        self.row = dict(issue_id='observation_a', outcome='retain', reason='Keep lead relation',
            evidence_frame_ids=['frame_a'], affected_actor_ids=['vehicle_a'],
            affected_event_ids=[], scope_proof=self.proof)

    def checked(self):
        return census_relevance.validate(self.case, self.behavior, self.row,
            self.observation, self.frames, self.world, registry=self.registry)

    def test_retention_keeps_existing_object_and_its_observed_points(self):
        checked = self.checked()
        self.assertEqual(checked['handling'], 'retained_unresolved')
        receipt = dict(revision=census_relevance.EXECUTION, choices=[dict(checked,
            candidate_actor_ids=['vehicle_a'], observation_frame_id='frame_a', outcome='retain')])
        receipt['sha256'] = digest(receipt)
        _, projected, audit = project(self.world, self.behavior, policy_id=EGO_POLICY,
            execution_dispositions=receipt)
        self.assertEqual(projected['actors'], self.behavior['actors'])
        self.assertEqual(audit['representative_actors'], [])
        self.assertEqual(audit['execution_dispositions']['choices'][0]['identity_resolution'], 'unresolved')

    def test_delivery_report_explicitly_marks_identity_uncertainty(self):
        scope = dict(excluded_actors=[], execution_dispositions=dict(choices=[dict(
            issue_id='observation_a', handling='retained_unresolved',
            actor_id='vehicle_a', observation_frame_id='frame_a',
            identity_resolution='unresolved')]))
        report = sections(dict(scene_type='highway', actors=[], events=[],
            quality_report=dict(execution_actor_scope=scope)))
        self.assertIn('身份未决但保留的车辆观察', str(report))
        self.assertIn('身份 unresolved', str(report))

    def test_unique_candidate_or_no_candidate_is_not_identity_proof(self):
        for candidates in ([], ['vehicle_a', 'vehicle_b']):
            with self.subTest(candidates=candidates):
                self.observation['candidate_actor_ids'] = candidates
                with self.assertRaises(ContractError): self.checked()

    def test_retention_does_not_discharge_an_actor_identity_dispute(self):
        self.behavior['semantic_limitations'] = [dict(limitation_id='identity_gap',
            actor_ids=['vehicle_a'], impact_domains=['actor_identity'],
            execution_effect='blocks_execution', description='Physical continuity is uncertain')]
        checked = self.checked()
        receipt = dict(revision=census_relevance.EXECUTION, choices=[dict(checked,
            candidate_actor_ids=['vehicle_a'], observation_frame_id='frame_a', outcome='retain')])
        receipt['sha256'] = digest(receipt)
        _, projected, _ = project(self.world, self.behavior, policy_id=EGO_POLICY,
            execution_dispositions=receipt)
        self.assertEqual(projected['semantic_limitations'], self.behavior['semantic_limitations'])
        self.assertEqual(len(identity_issues(self.world, projected)), 1)

    def test_same_evidence_contract_covers_lane_change_following_and_turning(self):
        for scene, relation, event_type in (
                ('highway', 'adjacent_lane', 'lane_change'),
                ('highway', 'ego_lane', 'car_following'),
                ('intersection', 'crossing_path', 'turn')):
            with self.subTest(scene=scene, event=event_type):
                self.world['world']['scene_type'] = scene
                self.proof['relation_to_ego_path'] = relation
                self.behavior['events'] = [dict(event_id='event_a', event_type=event_type,
                    involved_actor_ids=['ego', 'vehicle_a'])]
                self.row['affected_event_ids'] = ['event_a']
                self.assertEqual(self.checked()['handling'], 'retained_unresolved')
        self.behavior['events'][0]['involved_actor_ids'] = ['ego']
        with self.assertRaises(ContractError): self.checked()

    def test_physically_separated_opposing_vehicle_cannot_be_retained_as_ego_interaction(self):
        self.world['world']['road']['lanes'][0]['travel_direction'] = 'opposite_to_ego'
        with self.assertRaises(ContractError): self.checked()

    def test_covisible_vehicle_requires_an_archived_distinction_anchor(self):
        second = dict(actor_id='vehicle_b', actor_type='vehicle', simulation_role='participant',
            trajectory=[dict(frame_id='frame_a', time_s=0.0, lane_id='through', road_id='main')])
        self.behavior['actors'].append(second)
        with self.assertRaises(ContractError): self.checked()
        self.registry.append(dict(actor_id='vehicle_b', actor_type='vehicle',
            first_seen_frame_id='frame_a', evidence=[dict(frame_id='frame_a',
                status='observed', reason='Dark car in adjacent image region')]))
        self.proof['co_visible_distinctions'] = [dict(actor_id='vehicle_b',
            registered_anchor_reason='Dark car in adjacent image region',
            features='Dark car is to the right of the white lead car')]
        self.assertEqual(self.checked()['handling'], 'retained_unresolved')

    def test_physical_anchor_trajectory_and_road_are_required(self):
        mutations = (
            lambda: self.registry[0]['evidence'].clear(),
            lambda: self.actor['trajectory'].pop(),
            lambda: self.actor['trajectory'][0].update(lane_id=None),
            lambda: self.proof.update(registered_anchor_reason='model says same car'),
            lambda: self.proof.update(source_description='model invented source description'),
            lambda: self.proof.update(co_visible_distinctions=[dict(actor_id='unregistered',
                registered_anchor_reason='invented', features='other car')]),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                saved = copy.deepcopy((self.registry, self.behavior, self.proof))
                mutation()
                with self.assertRaises(ContractError): self.checked()
                self.registry, self.behavior, self.proof = saved
                self.actor = self.behavior['actors'][1]
                self.row['scope_proof'] = self.proof

    def test_duplicate_execution_binding_is_rejected(self):
        other = dict(self.observation, issue_id='observation_b')
        row = dict(self.row, issue_id='observation_b')
        review = dict(decisions=[self.row, row])
        saved = dict(result=dict(world=self.world, registry=self.registry))
        with patch.object(census_execution, '_certificate_hash', return_value='frozen'):
            with self.assertRaises(ContractError) as blocked:
                census_execution.decisions(self.case, self.behavior,
                    {'observation_a':self.observation, 'observation_b':other},
                    review, snapshot=saved, frames=self.frames)
        self.assertEqual(blocked.exception.rule_id, 'identity.census.duplicate_execution_binding')

    def test_old_frozen_policy_does_not_accept_retain(self):
        (self.case/'state/execution_policy.json').write_text(json.dumps({
            'world_census_adjudication': census_relevance.REVISION,
            'behavior_census_scope': census_relevance.BEHAVIOR,
            'execution_scope': EGO_POLICY,
        }))
        with self.assertRaises(ContractError): self.checked()

    def test_unclear_relation_routes_to_existing_world_point_review(self):
        unknown = dict(self.row, outcome='unknown', scope_proof=None)
        receipt = dict(revision=census_relevance.EXECUTION,
            choices=[dict(issue_id='observation_a', handling='needs_evidence',
                          candidate_actor_ids=['vehicle_a'])])
        with patch.object(census_execution, 'decisions', return_value=receipt):
            with self.assertRaises(IdentityEvidenceGap) as blocked:
                behavior_census_scope.blocked({'observation_a': self.observation},
                    {'observation_a': unknown}, self.behavior, dict(decisions=[unknown]),
                    case=self.case)
        self.assertTrue(blocked.exception.pending_census)
        self.assertEqual(blocked.exception.diagnostics['issue_ids'], ['observation_a'])


if __name__ == '__main__':
    unittest.main()
