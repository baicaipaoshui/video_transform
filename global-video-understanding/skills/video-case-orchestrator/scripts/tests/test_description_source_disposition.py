"""NO-LLM source-disposition tests; synthetic inputs are not video evidence."""
import copy
from pathlib import Path
import tempfile
import unittest

from test_description_fallback import fixture, draft
from _lib.contracts.v3 import ContractError


class DiagnosticSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name).resolve()
        self.full, self.bundle = fixture()

    def sources(self):
        groups = []
        for actor in draft.actors(self.full):
            for point in actor['trajectory']:
                source = 'actor-frame:'+actor['actor_id']+':'+point['frame_id']
                point['source_observation_ids'] = [source]
                groups.append(dict(source_observation_id=source,
                    actor_id=actor['actor_id'], frame_id=point['frame_id'], time_s=point['time_s'],
                    sources=[dict(kind='vehicle_description', status='observed',
                        source_id='batch_source#/'+source, object_id=actor['actor_id'],
                        text=point['detail'])], accepted_claims=[]))
        self.bundle['behavior']['actors'] = copy.deepcopy(draft.actors(self.full))
        return dict(revision='actor-time-evidence.v1', groups=groups)

    def unknown(self, actor_id='vehicle_far'):
        actor = copy.deepcopy(self.full['actors'][0])
        actor['actor_id'] = actor_id
        for point in actor['trajectory']:
            point['lane_id'] = None
            point['detail'] = 'Visible vehicle; wheel-area lane boundaries cannot be resolved.'
        self.full['actors'].append(actor)
        self.bundle['registry'].append(dict(actor_id=actor_id, actor_type=actor['actor_type'],
                                            identity_description=actor['identity_description']))
        return actor

    def audit(self, timeline):
        execution, receipt = draft.project(self.bundle, self.full)
        view = draft.diagnostic_source_view(self.case, self.bundle, self.full,
                                             execution, receipt, timeline)
        return execution, receipt, view

    def test_legal_vehicle_remains_executable(self):
        timeline = self.sources()
        execution, receipt, _ = self.audit(timeline)
        self.assertEqual([a['actor_id'] for a in execution['actors']], ['actor_001'])
        self.assertEqual(receipt['unmodeled_observations'], [])

    def test_retained_single_frame_vehicle_does_not_need_an_exclusion_proof(self):
        self.full['actors'][0]['trajectory'] = self.full['actors'][0]['trajectory'][:1]
        execution, receipt, _ = self.audit(self.sources())
        self.assertEqual([a['actor_id'] for a in execution['actors']], ['actor_001'])
        self.assertEqual(receipt['unmodeled_observations'], [])

    def test_unknown_position_preserves_every_source_and_participant(self):
        for aid in ('vehicle_far', 'different_identity_19'):
            with self.subTest(actor_id=aid):
                self.full, self.bundle = fixture()
                actor = self.unknown(aid)
                timeline = self.sources()
                before = copy.deepcopy(self.full)
                execution, receipt, view = self.audit(timeline)
                row, = receipt['unmodeled_observations']
                self.assertEqual(row['handling'], 'unmodeled_observation')
                self.assertEqual(row['source'], actor)
                self.assertEqual(row['source']['simulation_role'], 'participant')
                self.assertEqual(len(row['source_observations']), 2)
                self.assertEqual(row['collision_verification'], 'not_verified')
                self.assertEqual(row['interaction_verification'], 'not_verified')
                self.assertIn('Ego interaction is possible', row['risk'])
                self.assertNotIn(aid, [a['actor_id'] for a in execution['actors']])
                self.assertIn(aid, [a['actor_id'] for a in view['actors']])
                self.assertEqual(self.full, before)

    def test_actor_count_lane_ids_and_vehicle_class_do_not_change_source_rules(self):
        for count, vehicle_class, lane_index, extra_road in ((1, 'sedan', 2, False),
                                                             (4, 'bus', 3, True)):
            with self.subTest(count=count, vehicle_class=vehicle_class):
                self.full, self.bundle = fixture()
                lane = f'lane_{lane_index:03d}'
                self.full['road']['lanes'] = [dict(self.full['road']['lanes'][0], lane_id=f'lane_{i:03d}')
                                              for i in range(1, lane_index+1)]
                self.full['road']['roads'][0]['lane_ids'] = [r['lane_id'] for r in self.full['road']['lanes']]
                for actor in draft.actors(self.full):
                    actor['vehicle_class'] = vehicle_class
                    for point in actor['trajectory']: point['lane_id'] = lane
                for index in range(1, count):
                    actor = copy.deepcopy(self.full['actors'][0])
                    actor['actor_id'] = 'retained_'+str(index)
                    self.full['actors'].append(actor)
                    self.bundle['registry'].append(dict(actor_id=actor['actor_id'],
                        actor_type=actor['actor_type'], identity_description=actor['identity_description']))
                if extra_road:
                    road = copy.deepcopy(self.full['road']['roads'][0])
                    next_lane = f'lane_{lane_index+1:03d}'
                    road.update(road_id='road_004', lane_ids=[next_lane])
                    self.full['road']['roads'].append(road)
                    self.full['road']['lanes'].append(dict(self.full['road']['lanes'][0],
                        lane_id=next_lane, road_id='road_004'))
                self.bundle['world']['world']['road'] = copy.deepcopy(self.full['road'])
                self.unknown('unlocalized_'+str(count))
                timeline = self.sources()
                execution, receipt, _ = self.audit(timeline)
                self.assertEqual(len(execution['actors']), count)
                self.assertEqual(receipt['unmodeled_observations'][0]['handling'], 'unmodeled_observation')

    def test_unsupported_road_does_not_gain_motion_planner_admission(self):
        self.full['scene_type'] = 'intersection'
        self.bundle['world']['world']['scene_type'] = 'intersection'
        with self.assertRaisesRegex(draft.DraftBlocked, 'motion planner cannot cover'):
            draft.project(self.bundle, self.full)

    def test_real_source_loss_is_not_a_risk_disposition(self):
        self.unknown(); timeline = self.sources()
        self.full['actors'][-1]['trajectory'][0].pop('source_observation_ids')
        with self.assertRaisesRegex(ContractError, 'not carried into Behavior'):
            self.audit(timeline)

    def test_fabricated_source_reference_is_rejected(self):
        self.unknown(); timeline = self.sources()
        self.full['actors'][-1]['trajectory'][0]['source_observation_ids'] = ['invented']
        with self.assertRaisesRegex(ContractError, 'unknown source observation'):
            self.audit(timeline)

    def test_missing_timeline_does_not_license_unmodeled_vehicle(self):
        self.unknown(); self.sources()
        execution, receipt = draft.project(self.bundle, self.full)
        with self.assertRaisesRegex(draft.DraftBlocked, 'complete frame-bound sources'):
            draft.diagnostic_source_view(self.case, self.bundle, self.full, execution, receipt, None)

    def test_time_mismatch_is_rejected(self):
        self.unknown(); timeline = self.sources()
        self.full['actors'][-1]['trajectory'][0]['time_s'] = 1
        with self.assertRaisesRegex(ContractError, 'actor/frame/time differs'):
            self.audit(timeline)

    def test_missing_or_forged_risk_row_is_rejected(self):
        self.unknown(); timeline = self.sources()
        execution, receipt = draft.project(self.bundle, self.full)
        receipt['unmodeled_observations'] = []
        with self.assertRaisesRegex(draft.DraftBlocked, 'one risk row'):
            draft.diagnostic_source_view(self.case, self.bundle, self.full, execution, receipt, timeline)
        execution, receipt = draft.project(self.bundle, self.full)
        receipt['unmodeled_observations'][0]['source']['simulation_role'] = 'background'
        with self.assertRaisesRegex(draft.DraftBlocked, 'differs from frozen source'):
            draft.diagnostic_source_view(self.case, self.bundle, self.full, execution, receipt, timeline)

    def test_known_lane_claim_cannot_be_hidden_as_unknown(self):
        actor = self.unknown(); timeline = self.sources()
        timeline['groups'][-1]['accepted_claims'] = [dict(field='actor_lane', value='lane_001')]
        with self.assertRaisesRegex(draft.DraftBlocked, 'unknown executable position'):
            self.audit(timeline)
        timeline['groups'][-1]['accepted_claims'] = []
        actor['trajectory'][0]['position_ego_m'] = dict(x=50)
        with self.assertRaisesRegex(draft.DraftBlocked, 'unknown executable position'):
            self.audit(timeline)

    def test_core_event_or_relation_dependency_cannot_be_omitted(self):
        actor = self.unknown(); timeline = self.sources()
        execution, receipt = draft.project(self.bundle, self.full)
        self.full['events'] = [dict(involved_actor_ids=['ego', actor['actor_id']])]
        with self.assertRaisesRegex(draft.DraftBlocked, 'required action/interaction'):
            draft.diagnostic_source_view(self.case, self.bundle, self.full, execution, receipt, timeline)
        self.full['events'] = []
        self.bundle['world']['relations'] = [dict(actor_id=actor['actor_id'], reference_actor_id='ego')]
        with self.assertRaisesRegex(draft.DraftBlocked, 'required action/interaction'):
            draft.diagnostic_source_view(self.case, self.bundle, self.full, execution, receipt, timeline)

    def test_retained_ego_or_vehicle_motion_cannot_change(self):
        timeline = self.sources()
        for aid in ('ego', 'actor_001'):
            execution, receipt = draft.project(self.bundle, self.full)
            next(a for a in draft.actors(execution) if a['actor_id'] == aid)['trajectory'][0]['lane_id'] = None
            with self.subTest(actor_id=aid), self.assertRaisesRegex(draft.DraftBlocked, 'changes a retained Actor'):
                draft.diagnostic_source_view(self.case, self.bundle, self.full, execution, receipt, timeline)

    def test_source_bound_relative_motion_cannot_disappear_in_minimum_scene(self):
        from _lib.behavior.source_revision import QUALITATIVE
        execution, receipt = draft.project(self.bundle, self.full)
        self.full['limitations'] = [dict(description='Source-bound sparse progression.',
            **{QUALITATIVE: dict(actor_id='actor_001', reference_actor_id='ego', completion='unconfirmed')})]
        with self.assertRaisesRegex(draft.DraftBlocked, 'source-bound relative motion constraint'):
            draft.require_core_scene(self.bundle, self.full, execution, receipt)

    def test_pending_core_interaction_does_not_become_unmodeled_risk(self):
        self.unknown(); timeline = self.sources()
        execution, receipt = draft.project(self.bundle, self.full)
        self.bundle['pending'] = {'pending': dict(candidate_actor_ids=['vehicle_far'])}
        self.bundle['review'] = dict(decisions=[dict(issue_id='pending', affected_event_ids=['main_event'])])
        with self.assertRaisesRegex(draft.DraftBlocked, 'retained Census interaction'):
            draft.diagnostic_source_view(self.case, self.bundle, self.full, execution, receipt, timeline)

    def test_existing_exclusion_proof_is_distinct_from_unmodeled_risk(self):
        actor = self.unknown()
        actor['simulation_role'] = 'background'
        self.bundle['frames'].append(dict(frame_id='f2', time_s=2))
        actor['execution_scope_evidence'] = dict(kind='distant_transient',
            checked_frame_ids=['f0', 'f2', 'f4'], visible_frame_ids=['f0', 'f4'],
            no_interaction=True, reason='Synthetic complete reviewed isolation evidence.')
        timeline = self.sources()
        _, receipt, _ = self.audit(timeline)
        row, = receipt['unmodeled_observations']
        self.assertEqual(row['handling'], 'observation_excluded')
        self.assertNotIn('missing_information', row)


if __name__ == '__main__':
    unittest.main()
