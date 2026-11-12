"""Regression for a real closure's converging field-evidence paths."""
import copy
import json
from pathlib import Path
import sys
import unittest

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts.census_identity import remap
from _lib.runtime.world_adjudication import compile_response

CASE = SKILLS.parents[1] / 'video_extractor/cases/case_20261027T034536473Z_7916ef73'


class CensusIdentityEvidenceHandoffTests(unittest.TestCase):
    def setUp(self):
        request = CASE / 'diagnostics/world_census_adjudication/initial/request.json'
        answer = CASE / 'diagnostics/invalid_candidates/world_closure.attempt_2.27ddfb2a8d07acea.json'
        self.request = json.loads(request.read_text())
        self.answer = json.loads(answer.read_text())

    def test_original_answer_preserves_all_field_witnesses(self):
        before = copy.deepcopy((self.request, self.answer))
        world_path = Path(self.request['world_path'])
        original_bytes = world_path.read_bytes()
        world = json.loads(original_bytes)
        result = compile_response(self.request, self.answer)
        self.assertEqual(result['canonical_actor_ids'], {'actor_002': 'actor_003'})
        expected = []
        for actor in ('actor_002', 'actor_003'):
            for witness in world['field_evidence'][f'world.actor_hypotheses.{actor}.hypothesized_lane']:
                updated = remap(witness, result['canonical_actor_ids'])
                if updated not in expected:
                    expected.append(updated)
        self.assertEqual(len(expected), 4)
        self.assertEqual(result['world']['field_evidence'][
            'world.actor_hypotheses.actor_003.hypothesized_lane'], expected)
        self.assertNotIn('world.actor_hypotheses.actor_002.hypothesized_lane',
                         result['world']['field_evidence'])
        self.assertEqual((self.request, self.answer), before)
        self.assertEqual(world_path.read_bytes(), original_bytes)

    def test_conflicting_keyed_observation_still_rejected(self):
        with self.assertRaisesRegex(Exception, 'collide in a keyed observation'):
            remap({'actor_002': {'speed': 1}, 'actor_003': {'speed': 2}},
                  {'actor_002': 'actor_003'})

    def test_changed_identity_anchor_still_rejected(self):
        self.answer['identity_groups'][0]['anchor_matches'][0]['time_s'] += 0.5
        with self.assertRaisesRegex(Exception, 'original frame/time/lane anchor'):
            compile_response(self.request, self.answer)

    def test_covisible_identity_conflict_still_rejected(self):
        vehicle = next(v for v in self.answer['vehicles'] if v['key'] == 'truck_white_1017')
        vehicle['sightings'][0]['distinct_from_actor_ids'].append('actor_002')
        with self.assertRaisesRegex(Exception, 'co-visible distinct identities'):
            compile_response(self.request, self.answer)


if __name__ == '__main__':
    unittest.main()
