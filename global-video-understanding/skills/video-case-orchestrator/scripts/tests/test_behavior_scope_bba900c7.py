"""NO-LLM regression for the archived bba900c7 Behavior scope answer."""
import copy
import sys
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.bounded_repair import normalize_index
from _lib.behavior.compile import evidence_frames, prepare_behavior_candidate
from _lib.contracts import census_scope
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json
from _lib.world.gaps import IdentityEvidenceGap

CASE = SKILLS.parents[1]/'video_extractor/cases/case_20261021T014843022Z_bba900c7'
ARCHIVE = CASE/'diagnostics/candidate_snapshots/behavior.170a4cc412baef1050c9244400fa93664d6742c7676e6672e358418bfba2c787.json'
OPPOSING = {
    'batch_0002:frame_0001:f0001_v4',
    'batch_0002:frame_0001:f0001_v5',
    'batch_0002:frame_0001:f0001_v6',
    'batch_0002:frame_0008:f0008_v6',
}
EARLY_DARK = 'batch_0002:frame_0001:f0001_v3'


@unittest.skipUnless(ARCHIVE.is_file(), 'Archived current Case is unavailable')
class CurrentCaseScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = read_json(ARCHIVE)
        cls.frames = evidence_frames(CASE)
        behavior, cls.original_review = census_scope.split(cls.raw)
        cls.behavior, _ = prepare_behavior_candidate(
            behavior, cls.frames, read_json(CASE/'state/actor_registry.json'),
            read_json(CASE/'state/world_context.json'), case=CASE)

    def test_background_opposing_index_is_completed_without_identity_binding(self):
        original = copy.deepcopy(self.original_review)
        review, repairs = normalize_index(CASE, self.behavior, original, self.frames)
        self.assertEqual({r['issue_id'] for r in repairs}, OPPOSING)
        self.assertEqual(original, self.original_review)
        receipt = census_scope.validate_review(CASE, self.behavior, review,
                                               allow_pending=True)['execution_dispositions']
        choices = {r['issue_id']:r for r in receipt['choices']}
        for issue_id in OPPOSING:
            self.assertEqual(choices[issue_id]['handling'], 'observation_excluded')
            self.assertEqual(choices[issue_id]['identity_resolution'], 'unresolved')
        self.assertEqual(choices[EARLY_DARK]['handling'], 'needs_evidence')
        with self.assertRaises(IdentityEvidenceGap) as blocked:
            census_scope.validate_review(CASE, self.behavior, review)
        self.assertEqual(blocked.exception.diagnostics['issue_ids'], [EARLY_DARK])

    def test_incomplete_proof_cannot_be_indexed_or_excluded(self):
        review = copy.deepcopy(self.original_review)
        row = next(r for r in review['decisions'] if r['issue_id'] in OPPOSING)
        missing = row['scope_proof']['sightings'].pop()['frame_id']
        normalized, repairs = normalize_index(CASE, self.behavior, review, self.frames)
        self.assertNotIn(row['issue_id'], {r['issue_id'] for r in repairs})
        with self.assertRaises(census_scope.ScopeReviewCoverageError) as blocked:
            census_scope.validate_review(CASE, self.behavior, normalized, allow_pending=True)
        self.assertEqual(blocked.exception.rule_id, 'identity.census.scope_proof_frames')
        self.assertIn(missing, blocked.exception.diagnostics['missing_frame_ids'])

    def test_early_dark_cannot_be_marked_irrelevant_without_proof(self):
        review, _ = normalize_index(CASE, self.behavior, self.original_review, self.frames)
        row = next(r for r in review['decisions'] if r['issue_id'] == EARLY_DARK)
        row['outcome'] = 'irrelevant'
        row.pop('identity_assessment')
        with self.assertRaises(ContractError) as blocked:
            census_scope.validate_review(CASE, self.behavior, review, allow_pending=True)
        self.assertEqual(blocked.exception.rule_id, 'identity.census.relevance')


if __name__ == '__main__':
    unittest.main()
