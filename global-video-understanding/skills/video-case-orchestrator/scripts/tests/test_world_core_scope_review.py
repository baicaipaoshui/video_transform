"""NO-LLM World question review for an unknown-lane core path relation."""
import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime import question_review
from _lib.runtime.case_core import read_json, sha256
from _lib.runtime.fact_versions import current_facts

ROOT = SKILLS.parents[1]
SOURCE = ROOT/'video_extractor/cases/case_20261022T035340201Z_065638b5'
FRAMES = [('frame_0001', 0.0), ('frame_0004', 1.5),
          ('frame_0007', 3.0), ('frame_0008', 3.5)]


@unittest.skipUnless(SOURCE.is_dir(), 'Archived source Case unavailable')
class WorldCoreScopeReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)/'case_mock'
        self.fact = self.case/'state/fact_versions/v000000'
        self.fact.mkdir(parents=True)
        self.review_root = self.case/question_review.ROOT
        self.review_root.mkdir(parents=True)
        self.world = current_facts(SOURCE).world
        self.behavior = read_json(SOURCE/'state/behavior.candidate.json')
        self.world_path = self.fact/'world_context.json'
        self.world_path.write_text(json.dumps(self.world, ensure_ascii=False))
        self.behavior_path = self.case/'state/behavior.candidate.json'
        self.behavior_path.write_text(json.dumps(self.behavior, ensure_ascii=False))
        self.manifest = self.fact/'manifest.json'
        self.manifest.write_text('{}')
        self.question = next(q for q in self.world['questions_to_verify']
                             if q['question_id'] == 'question_002')
        event = next(e for e in self.behavior['events'] if e['event_id'] == 'event_001')
        ego = next(a for a in self.behavior['actors'] if a['actor_id'] == 'ego')
        source_frames = []
        for fid, time_s in FRAMES:
            path = self.case/'frames'/f'{fid}.jpg'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fid.encode())  # Hash-bound Mock frame; no model or video decode.
            source_frames.append(dict(frame_id=fid, time_s=time_s,
                                      path=str(path), sha256=sha256(path)))
        body = dict(revision=question_review.REVISION, review_id='mockscope',
            case_id=self.case.name, issue_id='question_002', parent_version='v000000',
            parent_manifest_sha256=sha256(self.manifest), world_path=str(self.world_path),
            world_sha256=sha256(self.world_path), question=self.question,
            issue_sha256=digest(self.question),
            input_hashes={str(p): sha256(p) for p in (self.world_path, self.behavior_path)},
            source_frames=source_frames, target_frame={'frame_id': 'frame_0001', 'time_s': 0.0},
            permitted_actor_ids=['ego', 'actor_003'],
            scope_context=dict(actor_id='actor_003', event=event, ego=ego,
                primitives=[p for p in self.behavior['executor_primitives']
                            if p.get('event_id') == 'event_001'],
                behavior_path=str(self.behavior_path),
                behavior_sha256=sha256(self.behavior_path)),
            scope_instruction='Mock World review: decide from original frames.')
        self.request = dict(body, request_sha256=digest(body))
        self.request_path = self.review_root/'mockscope.request.json'
        self.request_path.write_text(json.dumps(self.request, ensure_ascii=False))

    def decision(self, outcome='path_separated'):
        relation = dict(actor_id='actor_003', event_id='event_001', outcome=outcome,
            road_id='road_001', boundary_id='marking_003', occupancy_side='right',
            occupancy_description='Mock frame shows occupied space beyond the boundary.',
            occupancy_frame_ids=['frame_0001'], occlusion='clear',
            time_range_s=[0.0, 3.5], evidence_frame_ids=[fid for fid, _ in FRAMES],
            interaction_reason='Mock visual review of occupancy and Ego event path.')
        body = dict(issue_id='question_002', request_sha256=self.request['request_sha256'],
            outcome='unresolved', review_reason='Exact lane remains unknown.',
            evidence=[dict(frame_id=fid, time_s=time_s, status='observed',
                           reason='Mock frame reviewed.') for fid, time_s in FRAMES],
            scope_relation=relation)
        return dict(body, decision_sha256=digest(body))

    def test_positive_commits_only_supplemental_review_and_replays(self):
        decision = self.decision()
        decision_path = self.review_root/'mockscope.decision.json'
        decision_path.write_text(json.dumps(decision, ensure_ascii=False))
        view = SimpleNamespace(version_id='v000000', directory=self.fact,
                               world=self.world, world_path=self.world_path)
        with patch.object(question_review.fact_versions, 'current_facts', return_value=view), \
             patch.object(question_review.fact_versions, '_lock', return_value=contextlib.nullcontext()):
            result = question_review.commit(self.case, self.request_path, decision_path)
            proof = question_review.bound_scope_relation(
                self.case, 'actor_003', 'event_001', self.behavior)
        self.assertEqual(result['status'], 'scope_reviewed')
        self.assertEqual(result['effective_version'], 'v000000')
        self.assertEqual(proof['scope_relation']['outcome'], 'path_separated')
        self.assertIsNone(next(a for a in read_json(self.world_path)['actor_hypotheses']
                               if a['actor_id'] == 'actor_003')['hypothesized_lane'])
        self.assertFalse((self.case/'state/fact_versions/active.json').exists())
        frame = Path(self.request['source_frames'][0]['path'])
        frame.write_bytes(b'changed')
        with self.assertRaises(ContractError):
            question_review._verified_request(self.case, self.request_path)

    def test_negative_unknown_occlusion_and_short_window(self):
        for outcome in ('potential_interaction', 'evidence_insufficient'):
            with self.subTest(outcome=outcome):
                self.assertEqual(question_review._decision(self.request, self.decision(outcome))[0]
                                 ['scope_relation']['outcome'], outcome)
        occluded = self.decision()
        occluded['scope_relation']['occlusion'] = 'potential'
        occluded['decision_sha256'] = digest({k: v for k, v in occluded.items()
                                              if k != 'decision_sha256'})
        with self.assertRaises(ContractError):
            question_review._decision(self.request, occluded)
        short = self.decision()
        short['scope_relation']['time_range_s'] = [0.0, 3.0]
        short['decision_sha256'] = digest({k: v for k, v in short.items()
                                           if k != 'decision_sha256'})
        with self.assertRaises(ContractError):
            question_review._decision(self.request, short)
        missing = self.decision()
        missing['scope_relation']['occupancy_frame_ids'] = ['frame_0008']
        missing['decision_sha256'] = digest({k: v for k, v in missing.items()
                                             if k != 'decision_sha256'})
        with self.assertRaises(ContractError):
            question_review._decision(self.request, missing)
        stale = self.decision()
        stale['scope_relation']['boundary_id'] = 'marking_002'
        stale['decision_sha256'] = digest({k: v for k, v in stale.items()
                                           if k != 'decision_sha256'})
        with self.assertRaises(ContractError):
            question_review._decision(self.request, stale)

    def test_forged_result_or_expired_version_is_not_a_scope_credential(self):
        decision_path = self.review_root/'mockscope.decision.json'
        decision_path.write_text(json.dumps(self.decision(), ensure_ascii=False))
        view = SimpleNamespace(version_id='v000000', directory=self.fact,
                               world=self.world, world_path=self.world_path)
        with patch.object(question_review.fact_versions, 'current_facts', return_value=view), \
             patch.object(question_review.fact_versions, '_lock', return_value=contextlib.nullcontext()):
            question_review.commit(self.case, self.request_path, decision_path)
        result_path = self.review_root/'mockscope.result.json'
        result = read_json(result_path)
        result['effective_version'] = 'v999999'
        result_path.write_text(json.dumps(result, ensure_ascii=False))
        with patch.object(question_review.fact_versions, 'current_facts', return_value=view):
            with self.assertRaises(ContractError):
                question_review.bound_scope_relation(
                    self.case, 'actor_003', 'event_001', self.behavior)
        result['effective_version'] = 'v000000'
        result_path.write_text(json.dumps(result, ensure_ascii=False))
        self.manifest.write_text('{"changed":true}')
        with patch.object(question_review.fact_versions, 'current_facts', return_value=view):
            with self.assertRaises(ContractError):
                question_review.bound_scope_relation(
                    self.case, 'actor_003', 'event_001', self.behavior)

    def test_original_frame_path_rebinds_only_to_identical_isolated_copy(self):
        original = question_review._frames(SOURCE, ['frame_0001'])[0]
        isolated = Path(self.temp.name)/'cases'/SOURCE.name
        copied = isolated/Path(original['path']).relative_to(SOURCE)
        copied.parent.mkdir(parents=True)
        copied.write_bytes(Path(original['path']).read_bytes())
        row = dict(frame_id='frame_0001', time_s=0.0,
                   path=original['path'], sha256=original['sha256'])
        with patch('_lib.behavior.compile.evidence_frames', return_value=[row]):
            self.assertEqual(question_review._frames(isolated, ['frame_0001'])[0]['path'],
                             str(copied.resolve()))
            copied.write_bytes(b'changed')
            with self.assertRaises(ContractError):
                question_review._frames(isolated, ['frame_0001'])


if __name__ == '__main__':
    unittest.main()
