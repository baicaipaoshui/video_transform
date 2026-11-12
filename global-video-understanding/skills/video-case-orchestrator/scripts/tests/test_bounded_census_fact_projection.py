"""NO-LLM regressions for accepted Census facts in bounded Behavior repair."""
import copy
import sys
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.bounded_repair import (materialize, prepare, preserve_rebuild,
                                           request, require_actionable)
from _lib.contracts import census_scope
from _lib.runtime.case_core import read_json, sha256
from _lib.runtime.bounded_recovery import RecoveryStopped
from _lib.runtime.census_scope_recovery import (_accepted_fact_projection,
    accepted_fact_projection, validate_patch)


CERT = 'certificate-case-z'
ISSUE = 'batch_0099:frame_0007:vehicle_z'


def fixture():
    base = dict(world_evidence_epoch=4, actors=[
        dict(actor_id='ego', first_seen_frame_id='frame_0001',
             trajectory=[dict(frame_id='frame_0001', time_s=0.0,
                              lane_id='lane_middle', detail='ego unchanged')]),
        dict(actor_id='actor_z', first_seen_frame_id='frame_0099',
             trajectory=[dict(frame_id='frame_0099', time_s=9.9,
                              lane_id='lane_left', detail='original observation')]),
    ], events=[dict(event_id='event_keep', involved_actor_ids=['ego'])],
        semantic_limitations=[
            dict(limitation_id='lim_identity_z', actor_ids=['actor_z'],
                 census_issue_ids=[ISSUE], impact_domains=['actor_identity'],
                 execution_effect='bounded_uncertainty', description='identity pending'),
            dict(limitation_id='lim_mixed_z', actor_ids=['actor_z'],
                 census_issue_ids=[ISSUE], impact_domains=['actor_identity', 'interaction'],
                 execution_effect='bounded_uncertainty', description='interaction pending'),
        ])
    world = {'evidence_epoch': 5, 'world': {'road': {'lanes': [
        dict(lane_id='lane_left', road_id='road_z', ordinal_from_right=2)]}}}
    common = dict(observation_anchor_id='anchor-z', actor_id='actor_z',
                  frame_id='frame_0007', time_s=0.7, features='dark vehicle fragment',
                  source_certificate_sha256=CERT, source_issue_ids=[ISSUE])
    claims = {
        'identity': dict(common, field='actor_identity', value='actor_z', claim_id='claim-i'),
        'lane': dict(common, field='actor_lane', value='lane_left', claim_id='claim-l'),
    }
    return base, world, claims, {ISSUE: {'actor_z'}}


class AcceptedCensusProjectionTests(unittest.TestCase):
    def test_earlier_sighting_and_pure_identity_retirement_are_local(self):
        base, world, claims, links = fixture(); frozen = copy.deepcopy(base)
        result, manifest = _accepted_fact_projection(base, world, claims, links, CERT)
        self.assertEqual(base, frozen)
        self.assertEqual(result['actors'][0], base['actors'][0])
        self.assertEqual(result['events'], base['events'])
        actor = result['actors'][1]
        self.assertEqual(actor['first_seen_frame_id'], 'frame_0007')
        self.assertEqual([p['frame_id'] for p in actor['trajectory']],
                         ['frame_0007', 'frame_0099'])
        self.assertNotIn('speed_mps', actor['trajectory'][0])
        self.assertNotIn('position_ego_m', actor['trajectory'][0])
        self.assertEqual(actor['trajectory'][1], base['actors'][1]['trajectory'][0])
        self.assertEqual([r['limitation_id'] for r in result['semantic_limitations']],
                         ['lim_mixed_z'])
        self.assertEqual(manifest['added_sightings'][0]['source_issue_ids'], [ISSUE])
        self.assertEqual(manifest['retired_limitations'][0]['limitation_id'], 'lim_identity_z')

    def test_unpaired_or_unbound_claim_cannot_add_a_sighting(self):
        base, world, claims, links = fixture()
        claims.pop('lane')
        result, manifest = _accepted_fact_projection(base, world, claims, links, CERT)
        self.assertEqual(result['actors'], base['actors'])
        self.assertEqual(manifest['added_sightings'], [])

    def test_multiple_accepted_early_sightings_keep_distinct_source_paths(self):
        base, world, claims, links = fixture()
        for field, value, suffix in (('actor_identity', 'actor_z', 'i'),
                                     ('actor_lane', 'lane_left', 'l')):
            row = copy.deepcopy(claims['identity' if field == 'actor_identity' else 'lane'])
            row.update(observation_anchor_id='anchor-z-8', frame_id='frame_0008',
                       time_s=0.8, field=field, value=value, claim_id='claim-8-'+suffix)
            claims['frame8-'+field] = row
        result, manifest = _accepted_fact_projection(base, world, claims, links, CERT)
        actor = result['actors'][1]
        self.assertEqual([p['frame_id'] for p in actor['trajectory']],
                         ['frame_0007', 'frame_0008', 'frame_0099'])
        self.assertEqual([r['path'] for r in manifest['added_sightings']],
                         ['/actors/1/trajectory/0', '/actors/1/trajectory/1'])

    def test_mixed_limitation_is_not_retired(self):
        base, world, claims, links = fixture()
        base['semantic_limitations'] = [base['semantic_limitations'][1]]
        result, manifest = _accepted_fact_projection(base, world, claims, links, CERT)
        self.assertEqual(result['semantic_limitations'], base['semantic_limitations'])
        self.assertEqual(manifest['retired_limitations'], [])

    def test_limitation_bookkeeping_path_is_not_actionable_resolution(self):
        base, _, _, _ = fixture()
        with self.assertRaises(RecoveryStopped) as stopped:
            require_actionable('.', data=base,
                allowed_paths=['/semantic_limitations/1/scope_binding'], issue_ids=[],
                blocked=['identity.temporal_binding:lim_mixed_z'],
                lane_obligations=[], failure={'message': 'handoff blocked'})
        self.assertEqual(stopped.exception.code, 'bounded_recovery_contract_blocked')
        self.assertEqual(stopped.exception.diagnostics['unactionable_issues'],
                         ['identity.temporal_binding:lim_mixed_z'])


CASE = SKILLS.parents[1]/'video_extractor/cases/case_20261026T045856427Z_b3984ed8'
ARCHIVE = CASE/'diagnostics/invalid_candidates/behavior.attempt_2.6db52ae552913577.json'


@unittest.skipUnless(ARCHIVE.is_file(), 'Archived bounded-recovery Case is unavailable')
class ArchivedCandidateReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = read_json(ARCHIVE)
        cls.invalid, cls.review = census_scope.split(raw)

    def test_unrelated_ego_rewrite_is_rejected(self):
        with self.assertRaises(Exception) as patch_error:
            validate_patch(CASE, self.invalid)
        self.assertIn('unrelated actors', str(patch_error.exception))
        with self.assertRaises(RecoveryStopped):
            preserve_rebuild(CASE, self.invalid, self.review)

    def test_empty_patch_is_no_progress(self):
        packet = request(CASE)
        if packet is None:
            self.skipTest('Archived Case has no frozen bounded request')
        patch = dict(base_sha256=packet['base_sha256'], replacements=[],
                     census_scope_review=dict(
                         certificate_sha256=packet['scope_context']['certificate_sha256'],
                         decisions=[]), lane_closures=[])
        with self.assertRaises(RecoveryStopped) as stopped:
            materialize(CASE, patch)
        self.assertEqual(stopped.exception.code, 'bounded_recovery_no_progress')

    def test_invalid_candidate_is_not_reused_as_the_trusted_base(self):
        projected, _ = accepted_fact_projection(CASE)
        self.assertNotEqual(projected, self.invalid)
        request_path = CASE/'state/behavior_repair.request.json'
        before = sha256(request_path)
        class Failure(Exception):
            pass
        failure = Failure('handoff blocked')
        failure.bounded_behavior = self.invalid
        failure.bounded_review = self.review
        failure.diagnostics = {'message': 'handoff blocked'}
        with self.assertRaises(RecoveryStopped) as stopped:
            prepare(CASE, Path('/private/tmp/no-model-output.json'), failure)
        self.assertEqual(stopped.exception.code, 'bounded_recovery_contract_blocked')
        self.assertEqual(stopped.exception.diagnostics['unactionable_issues'],
                         ['identity.temporal_binding:limitation_004'])
        self.assertEqual(sha256(request_path), before)


if __name__ == '__main__':
    unittest.main()
