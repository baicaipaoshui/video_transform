"""NO-LLM source and scope checks across the full-to-core boundary."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts.core_lane_continuity import validate_source_coverage
from _lib.contracts.core_projection_sources import (
    make, validate, execution_required_actors, required_background_actors,
    _unresolved_review_dependency)
from _lib.contracts import core_projection_sources
from _lib.contracts import core_identity_hypothesis
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json
from _lib.runtime.fact_versions import current_facts

ROOT = SKILLS.parents[1]
BACKGROUND = ROOT/'video_extractor/cases/case_20261021T014843022Z_bba900c7'
UNRESOLVED = ROOT/'video_extractor/cases/case_20261022T035340201Z_065638b5'
DISTINCT = ROOT/'video_extractor/cases/case_20261029T102100747Z_b56627fb'


def inputs(case):
    raw = read_json(case/'state/behavior.candidate.json')
    full = dict(raw, ego=next(a for a in raw['actors'] if a['actor_id'] == 'ego'),
                actors=[a for a in raw['actors'] if a['actor_id'] != 'ego'],
                limitations=raw['semantic_limitations'])
    return (current_facts(case).world, raw, full,
            read_json(case/'state/behavior_frames.json')['frames'],
            read_json(case/'state/behavior_input.request.json')['evidence_timeline'],
            raw['census_scope_review'])


class CoreProjectionSourceTest(unittest.TestCase):
    def setUp(self):
        if not BACKGROUND.is_dir() or not UNRESOLVED.is_dir():
            self.skipTest('Frozen source Cases unavailable')
        self.world, self.behavior, self.full, self.frames, self.timeline, self.review = inputs(BACKGROUND)
        self.core = {a['actor_id'] for a in [self.full['ego'], *self.full['actors']]} - {'actor_003'}

    def proof(self):
        return make(BACKGROUND, self.world, self.behavior, self.full, self.core,
                    self.frames, self.timeline, self.review)

    def projected(self):
        return dict(self.full, actors=[a for a in self.full['actors']
                                       if a['actor_id'] in self.core],
                    limitations=[row for row in self.full['limitations']
                                 if row.get('actor_ids') != ['actor_003']])

    def test_historical_distinct_and_unresolved_review_dependencies(self):
        if not DISTINCT.is_dir():
            self.skipTest('Frozen distinct Case unavailable')
        current_review = inputs(DISTINCT)[-1]['decisions']
        distinct = next(row for row in current_review
                        if row['outcome'] == 'distinct'
                        and row['scope_proof']['actor_id'] == 'actor_003')
        self.assertFalse(_unresolved_review_dependency(distinct, 'actor_003'))
        self.assertFalse(_unresolved_review_dependency(distinct, 'actor_001'))
        current_unresolved = next(row for row in current_review
                                  if row['outcome'] == 'needs_identity')
        self.assertTrue(_unresolved_review_dependency(current_unresolved, 'actor_005'))

        needs_identity = next(row for row in self.review['decisions']
                              if row['outcome'] == 'needs_identity')
        self.assertTrue(_unresolved_review_dependency(
            needs_identity, needs_identity['affected_actor_ids'][0]))
        other_review = inputs(UNRESOLVED)[-1]['decisions']
        pending = next(row for row in other_review if row['outcome'] == 'needs_identity')
        self.assertTrue(_unresolved_review_dependency(
            pending, pending['affected_actor_ids'][0]))

        # Mixed reviews must not let one confirmed distinction discharge a
        # separate unresolved identity or event dependency for the same Actor.
        mixed = copy.deepcopy(needs_identity)
        mixed['affected_actor_ids'] = ['actor_003']
        self.assertTrue(any(_unresolved_review_dependency(row, 'actor_003')
                            for row in (distinct, mixed)))
        for outcome in ('unknown', 'pending'):
            unresolved = copy.deepcopy(distinct)
            unresolved['outcome'] = outcome
            self.assertTrue(_unresolved_review_dependency(unresolved, 'actor_003'))
        event = copy.deepcopy(distinct)
        event['affected_event_ids'] = ['event_001']
        self.assertTrue(_unresolved_review_dependency(event, 'actor_003'))
        unproved = copy.deepcopy(distinct)
        unproved['scope_proof']['distinct'] = False
        self.assertTrue(_unresolved_review_dependency(unproved, 'actor_003'))

    def test_real_background_source_requires_bound_projection_disposition(self):
        proof = self.proof()
        self.assertEqual([(r['actor_id'], len(r['source_rows'])) for r in proof['excluded']],
                         [('actor_003', 1)])
        projected = self.projected()
        with self.assertRaisesRegex(ContractError, 'not carried'):
            validate_source_coverage(BACKGROUND, projected, timeline=self.timeline)
        context = dict(source_behavior=self.behavior, full_analysis=self.full,
                       frames=self.frames, review=self.review, disposition=proof)
        self.assertTrue(validate_source_coverage(BACKGROUND, projected,
                                                 timeline=self.timeline,
                                                 projection_context=context))

    def test_missing_scope_core_omission_and_occlusion_still_block(self):
        missing = copy.deepcopy(self.behavior)
        next(a for a in missing['actors'] if a['actor_id'] == 'actor_003').pop(
            'execution_scope_evidence')
        with self.assertRaises(ContractError):
            make(BACKGROUND, self.world, missing, self.full, self.core,
                 self.frames, self.timeline, self.review)
        with self.assertRaises(ContractError):
            make(BACKGROUND, self.world, self.behavior, self.full,
                 self.core - {'actor_001'}, self.frames, self.timeline, self.review)
        occluded = copy.deepcopy(self.behavior)
        next(a for a in occluded['actors'] if a['actor_id'] == 'actor_003')[
            'execution_scope_evidence']['reason'] += ' Later occluded behind another vehicle.'
        with self.assertRaisesRegex(ContractError, 'Occlusion'):
            make(BACKGROUND, self.world, occluded, self.full, self.core,
                 self.frames, self.timeline, self.review)
        interacting = copy.deepcopy(self.behavior)
        interacting['events'][0]['involved_actor_ids'].append('actor_003')
        with self.assertRaisesRegex(ContractError, 'relation or action'):
            make(BACKGROUND, self.world, interacting, self.full, self.core,
                 self.frames, self.timeline, self.review)

    def test_behavior_owned_resolution_crosses_the_core_omission_boundary(self):
        world, behavior, full = copy.deepcopy((self.world, self.behavior, self.full))
        ambiguity = dict(ambiguity_id='ambiguity_background_state',
            actor_ids=['actor_003'], owner='behavior', resolution='unresolved',
            impact_domains=['executor_primitive'], subject_field='actor_state',
            related_world_paths=['world.actor_hypotheses.actor_003.hypothesized_lane'])
        resolution = dict(challenge_id=ambiguity['ambiguity_id'], resolution='resolved',
            execution_effect='retain_actor_state', decision='State remains unknown.',
            rationale='Only one observed source frame is available.',
            consistency='The source Actor remains in the full Analysis.',
            evidence_frame_ids=[self.frames[0]['frame_id']])
        world.setdefault('ambiguities', []).append(ambiguity)
        behavior.setdefault('challenge_resolutions', []).append(resolution)
        full['challenge_resolutions'] = copy.deepcopy(behavior['challenge_resolutions'])
        proof = make(BACKGROUND, world, behavior, full, self.core,
                     self.frames, self.timeline, self.review)
        row = next(item for item in proof['excluded'] if item['actor_id'] == 'actor_003')
        self.assertEqual(row['behavior_resolved_challenges'][0]['issue_id'],
                         ambiguity['ambiguity_id'])

        missing = copy.deepcopy(behavior)
        missing['challenge_resolutions'].pop()
        with self.assertRaisesRegex(ContractError, 'Unresolved World issue'):
            make(BACKGROUND, world, missing, full, self.core,
                 self.frames, self.timeline, self.review)
        with self.assertRaisesRegex(ContractError, 'Unresolved World issue'):
            make(BACKGROUND, world, behavior, full, self.core,
                 self.frames, self.timeline, self.review,
                 revision=core_projection_sources.V2_REVISION)

    def test_resolved_world_issue_crosses_the_core_omission_boundary(self):
        world = copy.deepcopy(self.world)
        question = dict(question_id='question_background_lane',
            actor_ids=['actor_003'], resolution='resolved', impact_domains=['actor_lane'],
            world_paths=['world.actor_hypotheses.actor_003.hypothesized_lane'])
        world.setdefault('questions_to_verify', []).append(question)
        proof = make(BACKGROUND, world, self.behavior, self.full, self.core,
                     self.frames, self.timeline, self.review)
        row = next(item for item in proof['excluded'] if item['actor_id'] == 'actor_003')
        self.assertEqual(row['resolved_world_issues'][0]['issue_id'],
                         question['question_id'])
        with self.assertRaisesRegex(ContractError, 'Unresolved World issue'):
            make(BACKGROUND, world, self.behavior, self.full, self.core,
                 self.frames, self.timeline, self.review,
                 revision=core_projection_sources.V2_REVISION)

    def test_source_tamper_and_expired_receipt_block(self):
        proof = self.proof()
        projected = self.projected()
        altered = copy.deepcopy(self.timeline)
        next(g for g in altered['groups'] if g['actor_id'] == 'actor_003')[
            'sources'][0]['text'] += ' changed'
        with self.assertRaises(ContractError):
            validate(BACKGROUND, self.world, self.behavior, self.full, projected,
                     self.frames, altered, self.review, proof)
        newer = copy.deepcopy(self.behavior)
        next(a for a in newer['actors'] if a['actor_id'] == 'actor_003')[
            'execution_scope_evidence']['reason'] += ' revised'
        with self.assertRaises(ContractError):
            validate(BACKGROUND, self.world, newer, self.full, projected,
                     self.frames, self.timeline, self.review, proof)
        changed_core = copy.deepcopy(projected)
        changed_core['actors'] = [a for a in changed_core['actors']
                                  if a['actor_id'] != 'actor_001']
        with self.assertRaises(ContractError):
            validate(BACKGROUND, self.world, self.behavior, self.full, changed_core,
                     self.frames, self.timeline, self.review, proof)
        uncited = copy.deepcopy(self.full)
        next(a for a in uncited['actors'] if a['actor_id'] == 'actor_003')[
            'trajectory'][0]['source_observation_ids'] = []
        with self.assertRaisesRegex(ContractError, 'not carried'):
            make(BACKGROUND, self.world, self.behavior, uncited, self.core,
                 self.frames, self.timeline, self.review)

    def test_other_case_unknown_lane_does_not_gain_exclusion(self):
        world, behavior, full, frames, timeline, review = inputs(UNRESOLVED)
        core = {a['actor_id'] for a in [full['ego'], *full['actors']]} - {'actor_003'}
        with self.assertRaisesRegex(ContractError, 'World has not proved'):
            make(UNRESOLVED, world, behavior, full, core, frames, timeline, review)

    def test_execution_relevant_track_is_retained_from_independent_sources(self):
        actor = next(a for a in self.behavior['actors']
                     if (a.get('execution_scope_evidence') or {}).get('kind') == 'edge_fragment')
        aid = actor['actor_id']
        actor['simulation_role'] = 'participant'
        actor['execution_scope_evidence']['kind'] = 'execution_relevant'
        actor['execution_scope_evidence']['no_interaction'] = False
        self.assertEqual(execution_required_actors(self.world, self.behavior,
            self.full, self.timeline), {aid})
        missing = copy.deepcopy(self.full)
        next(a for a in missing['actors'] if a['actor_id'] == aid)[
            'trajectory'][0]['source_observation_ids'] = []
        with self.assertRaisesRegex(ContractError, 'World/Batch sources'):
            execution_required_actors(self.world, self.behavior, missing, self.timeline)

    def test_background_without_an_existing_scope_proof_is_retained(self):
        behavior = copy.deepcopy(self.behavior)
        actor = next(a for a in behavior['actors'] if a['actor_id'] == 'actor_003')
        actor.pop('execution_scope_evidence')
        self.assertIn('actor_003', required_background_actors(
            self.world, behavior, self.frames))
        self.assertNotIn('actor_003', required_background_actors(
            self.world, self.behavior, self.frames))
        missing_source = copy.deepcopy(self.timeline)
        missing_source['groups'] = [group for group in missing_source['groups']
                                    if group['actor_id'] != 'actor_003']
        self.assertIn('actor_003', required_background_actors(
            self.world, self.behavior, self.frames, missing_source))

    def test_existing_opposing_proof_does_not_require_a_batch_timeline_row(self):
        frame_id = self.frames[0]['frame_id']
        ego = dict(actor_id='ego', actor_type='ego_vehicle', is_static=False,
            simulation_role='participant', trajectory=[])
        actor = dict(actor_id='actor_099', actor_type='vehicle', is_static=False,
            simulation_role='background', trajectory=[dict(frame_id=frame_id,
                time_s=self.frames[0]['time_s'], lane_id=None, source_observation_ids=[])],
            direction_evidence=dict(status='observed', value='opposite_to_ego',
                frame_ids=[frame_id], reason='The median physically separates this vehicle.'))
        limitation = dict(limitation_id='lim_opposing_identity', actor_ids=['actor_099'],
            impact_domains=['actor_identity', 'actor_lane', 'actor_motion'],
            execution_effect='bounded_uncertainty',
            description='The exact opposing vehicle identity, lane, and motion remain unknown.')
        behavior = dict(actors=[ego, actor], events=[], executor_primitives=[],
            video_fidelity=[], challenge_resolutions=[], semantic_limitations=[limitation])
        world = dict(world=dict(road=dict(one_way=False)), questions_to_verify=[],
            ambiguities=[], actor_hypotheses=[dict(actor_id='actor_099',
                actor_type='vehicle', hypothesized_lane=None)])
        full = dict(ego=copy.deepcopy(ego), actors=[copy.deepcopy(actor)],
                    events=[], limitations=[copy.deepcopy(limitation)])
        timeline = dict(revision='actor-time-evidence.v1', groups=[])
        self.assertNotIn('actor_099', required_background_actors(
            world, behavior, self.frames, timeline))
        proof = make(BACKGROUND, world, behavior, full, {'ego'},
                     self.frames, timeline, None)
        row = next(item for item in proof['excluded'] if item['actor_id'] == 'actor_099')
        self.assertEqual(row['background_scope_kind'], 'opposing_preservation')
        self.assertEqual(row['source_rows'], [])

    def test_unknown_lane_needs_a_bound_world_verdict_for_each_ego_event(self):
        world, behavior, full, frames, timeline, review = inputs(UNRESOLVED)
        core = {a['actor_id'] for a in [full['ego'], *full['actors']]} - {'actor_003'}
        verdict = dict(revision='world-question-review.v1', request_sha256='mock-request',
            decision_sha256='mock-answer', result_sha256='mock-result',
            source_frames=[dict(frame_id='frame_0001', time_s=0.0, sha256='mock-frame')],
            scope_relation=dict(actor_id='actor_003', event_id='event_001',
                                outcome='path_separated', occupancy_frame_ids=['frame_0001']))
        with patch('_lib.runtime.question_review.bound_scope_relation', return_value=verdict):
            proof = make(UNRESOLVED, world, behavior, full, core,
                         frames, timeline, review)
            self.assertIsNone(proof['excluded'][0]['lane_id'])
            self.assertEqual(proof['excluded'][0]['uncertainties_retained'],
                             ['exact_lane_unknown', 'later_motion_unknown'])
            projected = dict(full, actors=[a for a in full['actors']
                                           if a['actor_id'] in core])
            self.assertEqual(len(validate(UNRESOLVED, world, behavior, full,
                projected, frames, timeline, review, proof)), 1)
            denied = copy.deepcopy(verdict)
            denied['scope_relation']['outcome'] = 'potential_interaction'
            with patch('_lib.runtime.question_review.bound_scope_relation', return_value=denied):
                with self.assertRaisesRegex(ContractError, 'World has not proved'):
                    make(UNRESOLVED, world, behavior, full, core,
                         frames, timeline, review)
        with self.assertRaises(ContractError):
            validate(UNRESOLVED, world, behavior, full, projected,
                     frames, timeline, review, proof)

    def test_source_free_non_vehicle_uses_existing_scope(self):
        behavior = copy.deepcopy(self.behavior)
        full = copy.deepcopy(self.full)
        scenery = dict(actor_id='scenery_001', actor_type='sign', trajectory=[])
        behavior['actors'].append(scenery)
        full['actors'].append(copy.deepcopy(scenery))
        proof = make(BACKGROUND, self.world, behavior, full, self.core,
                     self.frames, self.timeline, self.review)
        self.assertEqual([r['actor_id'] for r in proof['excluded']], ['actor_003'])

    def test_different_actor_id_and_two_source_frames(self):
        # A second same-lane observed point changes the source set and receipt;
        # omission is still gated by the complete range and disjoint corridor.
        world, behavior, full, timeline = copy.deepcopy(
            (self.world, self.behavior, self.full, self.timeline))
        blob = json.dumps([world, behavior, full, timeline], ensure_ascii=False)
        world, behavior, full, timeline = json.loads(blob.replace('actor_003', 'actor_099'))
        actor = next(a for a in behavior['actors'] if a['actor_id'] == 'actor_099')
        point = copy.deepcopy(actor['trajectory'][0])
        point['frame_id'] = 'frame_0004'
        point['source_observation_ids'] = ['actor-frame:actor_099:frame_0004']
        actor['trajectory'].append(point)
        actor['execution_scope_evidence']['kind'] = 'distant_transient'
        actor['execution_scope_evidence']['visible_frame_ids'].append('frame_0004')
        full_actor = next(a for a in full['actors'] if a['actor_id'] == 'actor_099')
        full_actor['trajectory'].append(copy.deepcopy(point))
        group = copy.deepcopy(next(g for g in timeline['groups'] if g['actor_id'] == 'actor_099'))
        group.update(frame_id='frame_0004', time_s=1.5,
                     source_observation_id='actor-frame:actor_099:frame_0004')
        group['sources'][0]['source_id'] = 'batch_0002#/vehicle_census/1/vehicles/1'
        timeline['groups'].append(group)
        core = {a['actor_id'] for a in [full['ego'], *full['actors']]} - {'actor_099'}
        proof = make(BACKGROUND, world, behavior, full, core,
                     self.frames, timeline, self.review)
        self.assertEqual(len(proof['excluded'][0]['source_rows']), 2)
        projected = dict(full, actors=[a for a in full['actors']
                                       if a['actor_id'] != 'actor_099'])
        self.assertEqual(len(validate(BACKGROUND, world, behavior, full, projected,
                                      self.frames, timeline, self.review, proof)), 2)

    def test_nontrajectory_absence_source_is_retained_without_a_fake_point(self):
        full, timeline = copy.deepcopy((self.full, self.timeline))
        positive = next(group for group in timeline['groups']
                        if group['actor_id'] == 'actor_003')
        visible = {point['frame_id'] for point in next(actor for actor in
                   self.behavior['actors'] if actor['actor_id'] == 'actor_003')['trajectory']}
        frame = next(frame for frame in self.frames if frame['frame_id'] not in visible)
        source_id = f"actor-frame:actor_003:{frame['frame_id']}"
        negative = copy.deepcopy(positive)
        negative.update(frame_id=frame['frame_id'], time_s=frame['time_s'],
                        source_observation_id=source_id, accepted_claims=[])
        negative['sources'] = [dict(positive['sources'][0],
            kind='observation_source_reason', status='observed',
            text='Actor is not visible in this checked frame.', object_id=None)]
        timeline['groups'].append(negative)
        full['limitations'].append(dict(limitation_id='lim_absence',
            actor_ids=['actor_003'], impact_domains=['actor_motion'],
            execution_effect='bounded_uncertainty',
            description='Later visibility remains unknown.',
            source_observation_ids=[source_id]))
        proof = make(BACKGROUND, self.world, self.behavior, full, self.core,
                     self.frames, timeline, self.review)
        row = next(item for item in proof['excluded'] if item['actor_id'] == 'actor_003')
        self.assertEqual(row['nontrajectory_source_rows'][0]['source_observation_id'],
                         source_id)
        projected = self.projected()
        self.assertEqual(validate(BACKGROUND, self.world, self.behavior, full,
            projected, self.frames, timeline, self.review, proof), {
                positive['source_observation_id'], source_id})

    def test_rejected_unregistered_observation_stays_unknown_without_actor_link(self):
        full = copy.deepcopy(self.full)
        full['actors'].append(dict(actor_id='actor_099', actor_type='vehicle',
            trajectory=[dict(frame_id='frame_0004', time_s=1.5)]))
        issue = 'batch_0004:frame_0001:unknown_vehicle'
        limitation = dict(limitation_id='lim_unassigned', census_issue_ids=[issue],
            impact_domains=['actor_identity', 'actor_lane', 'actor_motion'],
            execution_effect='bounded_uncertainty',
            description='The physical vehicle remains unassigned.')
        full['limitations'].append(limitation)
        risks = [dict(issue_id=issue, handling='needs_evidence',
            observation=dict(issue_id=issue, frame_id='frame_0001', time_s=0,
                object_id='unknown_vehicle', candidate_actor_ids=['actor_099']),
            review=dict(outcome='needs_identity', affected_actor_ids=['actor_099'],
                affected_event_ids=[], identity_assessment=dict(candidate_status='rejected',
                    alternative_actor_ids=[], evidence_frame_ids=['frame_0001'],
                    reason='The registered Actor is visible separately.')))]
        rows = core_identity_hypothesis.observation_only_deferrals(full, risks)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['handling'], 'unassigned_observation_unknown')
        self.assertEqual(rows[0]['candidate_actor_ids'], [])
        self.assertEqual(rows[0]['unassigned_observations'][0][
            'rejected_candidate_actor_ids'], ['actor_099'])

        adopted = copy.deepcopy(full)
        next(actor for actor in adopted['actors'] if actor['actor_id'] == 'actor_099')[
            'trajectory'].append(dict(frame_id='frame_0001', time_s=0))
        self.assertEqual(core_identity_hypothesis.observation_only_deferrals(
            adopted, risks), [])


if __name__ == '__main__':
    unittest.main()
