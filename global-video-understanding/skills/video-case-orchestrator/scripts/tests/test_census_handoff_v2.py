"""NO-LLM checks for the new source-complete Census handoff."""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior import census_read_view, input_binding
from _lib.runtime.case_core import read_json, sha256

CASES = SKILLS.parents[1]/'video_extractor/cases'


class CensusHandoffV2Tests(unittest.TestCase):
    def view(self, suffix):
        case = next(CASES.glob('*'+suffix))
        timeline = input_binding.evidence_timeline(case)
        return case, timeline, census_read_view.build(
            case, timeline, census_read_view.COMPACT_REVISION)

    def test_9d54079b_formal_and_pending_sources(self):
        _, timeline, view = self.view('9d54079b')
        rows = view['unresolved_sources']
        self.assertEqual(len(rows), len(timeline['unresolved']))
        self.assertEqual(Counter(r['final_disposition'] for r in rows),
                         Counter(resolved=4, excluded=5, remaining=18))
        self.assertEqual(set(view['pending_issues']),
                         {r['census_issue_id'] for r in rows if r['final_disposition'] == 'remaining'})
        self.assertTrue(all(r['census_proof']['certificate_sha256'] in view['certificates'] and
                            r['census_proof']['binding_pointer']
                            for r in rows if r['final_disposition'] != 'unresolved_source'))
        # A preliminary opposing-background opinion cannot grant exclusion.
        self.assertTrue(any(r['preliminary_relevance'] == 'background_candidate'
                            and r['final_disposition'] == 'remaining' for r in rows))
        self.assertTrue(any(r['census_issue_id'] == 'batch_0004:frame_0008:frame_0008_obj6'
                            and r['final_disposition'] == 'remaining' for r in rows))
        self.assertTrue(any(r['census_issue_id'] == 'batch_0003:frame_0011:obj_003'
                            and r['final_disposition'] == 'remaining' for r in rows))
        self.assertTrue(any(r['census_issue_id'] == 'batch_0002:frame_0011:f11_obj2'
                            and r['final_disposition'] == 'remaining' for r in rows))
        self.assertTrue(any('遮挡' in next(x['text'] for x in timeline['unresolved']
                                         if x['source_id'] == r['source_id'])
                            and r['final_disposition'] == 'remaining' for r in rows))
        self.assertEqual(sum(len(p['applicable_issue_ids']) for p in view['shared_road_proofs']), 5)
        self.assertTrue(any(len(p['applicable_issue_ids']) > 1 for p in view['shared_road_proofs']))
        self.assertTrue(all(r['road_proof_id'] in {p['proof_id'] for p in view['shared_road_proofs']}
                            for r in rows if r['final_disposition'] == 'excluded'))
        self.assertTrue(all('sightings' not in p for p in view['shared_road_proofs']))

    def test_other_historical_case_and_frozen_revision(self):
        case, _, view = self.view('1f7ff9e6')
        self.assertEqual(Counter(r['final_disposition'] for r in view['unresolved_sources']),
                         Counter(resolved=5, remaining=14))
        self.assertEqual(census_read_view.revision_for(case), census_read_view.REVISION)
        self.assertEqual(view['shared_road_proofs'], [])

    def test_forged_or_stale_source_fails_closed(self):
        case, timeline, _ = self.view('9d54079b')
        old = census_read_view.build(case, timeline)
        forged = copy.deepcopy(old)
        row = next(r for r in forged['unresolved_sources'] if r['census_issue_status'] == 'remaining')
        row['census_issue_status'] = 'excluded'
        with self.assertRaisesRegex(Exception, 'final Census disposition conflicts'):
            census_read_view._compact(forged, timeline)
        missing = copy.deepcopy(old)
        missing['unresolved_sources'].pop()
        with self.assertRaisesRegex(Exception, 'cover every original unresolved source'):
            census_read_view._compact(missing, timeline)
        stale = copy.deepcopy(timeline)
        stale['fact_version_id'] = 'v-stale'
        with self.assertRaisesRegex(Exception, 'fact version differ'):
            census_read_view.build(case, stale, census_read_view.COMPACT_REVISION)

    def test_joint_coverage_rejects_missing_behavior_decision(self):
        case, _, view = self.view('9d54079b')
        pending = view['pending_issues']
        choices = [dict(issue_id=iid) for iid in pending]
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            path = Path(directory)/'view.json'
            path.write_text(json.dumps(view, ensure_ascii=False))
            with patch('_lib.behavior.input_binding.current_census_read_view', return_value=path):
                receipt = census_read_view.source_coverage(case, pending, dict(choices=choices))
                self.assertEqual(len(receipt['inherited_resolved_sources']), 4)
                self.assertEqual(len(receipt['inherited_excluded_sources']), 5)
                self.assertEqual(len(receipt['behavior_issue_ids']), 18)
                with self.assertRaisesRegex(Exception, 'do not cover every pending'):
                    census_read_view.source_coverage(case, pending, dict(choices=choices[:-1]))
                with self.assertRaisesRegex(Exception, 'pending issues differ'):
                    census_read_view.source_coverage(case, {}, dict(choices=choices))

    def test_one_source_text_can_apply_to_two_frames(self):
        case, timeline, view = self.view('b3984ed8')
        repeated = [r for r in view['unresolved_sources']
                    if r['source_id'] == 'batch_0002#/observations/3/reason']
        self.assertEqual({r['frame_id'] for r in repeated}, {'frame_0001', 'frame_0002'})
        self.assertEqual(len(view['unresolved_sources']), len(timeline['unresolved']))
        dispositions = Counter(r['final_disposition'] for r in view['unresolved_sources'])
        self.assertEqual(dispositions['excluded'], 3)
        self.assertEqual(dispositions['unresolved_source'], 6)
        self.assertEqual(dispositions['remaining'] + dispositions['resolved'], 5)
        self.assertTrue(all(r['census_proof']['binding_pointer']
                            for r in view['unresolved_sources']
                            if r['final_disposition'] == 'resolved'))

    def test_resolved_identity_uses_final_canonical_actor(self):
        rounds = [
            dict(request=dict(records=[dict(issue_id='issue-a')]),
                 result=dict(vehicle_ids={'vehicle-a': 'actor_001'})),
            dict(request=dict(records=[]),
                 result=dict(canonical_actor_ids={'actor_001': 'actor_004'})),
        ]
        self.assertEqual(census_read_view.turn_actor_id(
            rounds, 'issue-a', dict(vehicle_key='vehicle-a')), 'actor_004')

    def test_frozen_v2_view_survives_actual_core_case_copy(self):
        source = next(CASES.glob('*6fb2ffbb'))
        original = input_binding.current_census_read_view(source)
        self.assertEqual(read_json(original)['revision'], census_read_view.COMPACT_REVISION)
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            copied = Path(directory)/source.name
            shutil.copytree(source, copied)
            replayed = input_binding.current_census_read_view(copied)
            self.assertTrue(replayed.is_relative_to(copied))
            self.assertEqual(sha256(replayed), sha256(original))
            self.assertEqual(read_json(replayed), read_json(original))


if __name__ == '__main__':
    unittest.main()
