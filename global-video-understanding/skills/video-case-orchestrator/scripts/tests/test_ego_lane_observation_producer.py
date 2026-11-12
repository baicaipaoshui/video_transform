"""NO-LLM World producer replay against the archived Ego observation frame."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.compile import preview_digest
from _lib.contracts.v3 import ContractError
from _lib.prompts import world_request_text
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.world import actor_facts, single_world


CASE = SKILLS.parents[1]/'video_extractor/cases/case_20261110T152017408Z_8e67615b'
FIELD = 'world.actor_hypotheses.actor_001.hypothesized_lane'


class EgoLaneObservationProducerTests(unittest.TestCase):
    def test_world_request_freezes_opt_in_producer_rule(self):
        key = actor_facts.EGO_LANE_OBSERVATIONS_KEY
        rule = actor_facts.ego_lane_observation_rule()
        for opted_in in (False, True):
            with self.subTest(opted_in=opted_in), tempfile.TemporaryDirectory() as root:
                case = Path(root)/'case_world_producer'
                image = case/'frames/frame_0001.jpg'
                image.parent.mkdir(parents=True)
                image.write_bytes(b'NO-LLM frozen image binding fixture')
                atomic_json(case/'case.json', dict(parameters={}))
                atomic_json(case/'state/global_frames.json', dict(frames=[dict(
                    frame_id='frame_0001', time_s=0.0, path=str(image))]))
                policy = dict(semantic_compilation=single_world.REVISION)
                if opted_in:
                    policy[key] = actor_facts.EGO_LANE_OBSERVATIONS
                atomic_json(case/'state/execution_policy.json', policy)

                request = single_world.packet(case, layout_review=False)
                self.assertEqual(key in request, opted_in)
                self.assertEqual(rule in request['response_schema']['$defs']['evidence']['allOf'], opted_in)
                fields = request['response_schema']['properties']['field_evidence']
                self.assertEqual(key in fields.get('properties', {}), opted_in)
                self.assertEqual('field_evidence.ego_lane_observations[]' in world_request_text(case), opted_in)
                before = sha256(case/single_world.REQUEST)
                self.assertEqual(single_world.packet(case, layout_review=False), request)
                self.assertEqual(sha256(case/single_world.REQUEST), before)

    def test_world_compiles_explicit_frame_candidate_using_frozen_fragment(self):
        if not CASE.is_dir():
            self.skipTest('Archived Ego regression Case is unavailable')
        old_request = read_json(CASE/single_world.REQUEST)
        raw = read_json(CASE/'diagnostics/candidate_snapshots'/
                        'world.d6071f36618f2749aaa21f2c68f9ad6050a687cd796fb130fac3a0547bdd718b.json')
        request = copy.deepcopy(old_request)
        key = actor_facts.EGO_LANE_OBSERVATIONS_KEY
        request[key] = actor_facts.EGO_LANE_OBSERVATIONS
        actor_facts.extend_ego_lane_observation_schema(request['response_schema'], world=True)
        request.pop('request_sha256')
        request['request_sha256'] = preview_digest(request)
        raw['request_sha256'] = request['request_sha256']
        original = copy.deepcopy(raw['field_evidence'][FIELD])
        observation = next(item for item in original if item['frame_id'] == 'frame_0007')
        point = dict(observation, actor_id='ego', lane_id='lane_003', ordinal_from_right=2)
        raw['field_evidence'][key] = [point]
        compiled = single_world.compile_world(raw, request)
        self.assertEqual(compiled['field_evidence'][key], [point])
        self.assertEqual(compiled['field_evidence'][FIELD], original)
        self.assertNotIn('ego', {actor['actor_id'] for actor in compiled['actor_hypotheses']})

        raw['field_evidence'][FIELD].append(point)
        with self.assertRaisesRegex(ContractError, 'reserved evidence array'):
            single_world.compile_world(raw, request)
        raw['field_evidence'][FIELD].pop()
        point.pop('time_s')
        with self.assertRaisesRegex(ContractError, 'Invalid Ego lane observation'):
            single_world.compile_world(raw, request)
        # The original private request retains the former evidence grammar.
        raw['request_sha256'] = old_request['request_sha256']
        compiled = single_world.compile_world(raw, old_request)
        self.assertEqual(compiled['field_evidence'][key][0]['time_s'], 3.0)


if __name__ == '__main__':
    unittest.main()
