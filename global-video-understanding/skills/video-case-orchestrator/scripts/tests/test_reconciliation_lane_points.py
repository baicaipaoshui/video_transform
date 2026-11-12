"""NO-LLM regression for first physical lane-point admission."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.input_binding import evidence_timeline
from _lib.contracts.census_adjudication import proof
from _lib.contracts.core_lane_continuity import obligations
from _lib.runtime.case_core import sha256
from _lib.runtime.fact_versions import (commit_reconciliation_lane_points,
                                         current_facts, verified_reconciliation_lane_points)

SOURCE = SKILLS.parents[1]/'video_extractor/cases/case_20261019T175555560Z_1f7ff9e6'


class ReconciliationLanePointsTest(unittest.TestCase):
    def test_first_admission_keeps_frozen_sources_and_later_consumers_aligned(self):
        if not SOURCE.is_dir():
            self.skipTest('Archived Case 1f7ff9e6 unavailable')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = Path(root)/'isolated_case'
            shutil.copytree(SOURCE, case)
            old = current_facts(case)
            boundaries = old.world['world']['metadata']['lane_layout_review'][
                'observation']['boundaries_right_to_left']
            lane_map = old.world['world']['metadata']['lane_layout_review'][
                'adjudication']['lane_id_map']
            frozen = {p.name: sha256(p) for p in (case/'batches').glob('batch_*.json')}

            def point(actor, frame, ordinal, source, reason):
                return dict(actor_id=actor, frame_id=frame,
                    lane_id=f'lane_{ordinal + 1:03d}', ordinal_from_right=ordinal,
                    right_boundary_id=boundaries[ordinal],
                    left_boundary_id=boundaries[ordinal + 1], source_path=source,
                    source_sha256=sha256(case/source), reason=reason)

            before = 'diagnostics/world_census_adjudication/initial/before/world.json'
            rows = [point('ego', 'frame_0001', 1, before, 'Ego between B1 and B2.'),
                    point('actor_001', 'frame_0001', 2, before, 'Pickup between B2 and B3.'),
                    point('actor_001', 'frame_0022', 1, 'batches/batch_0004.json',
                          'Pickup wheel area between the two dashed rows.')]
            bad = dict(rows[0], source_sha256='0'*64)
            with self.assertRaisesRegex(Exception, 'cited source changed'):
                commit_reconciliation_lane_points(case, old.version_id, [bad])
            self.assertEqual(current_facts(case).version_id, old.version_id)

            new = commit_reconciliation_lane_points(case, old.version_id, rows)
            self.assertTrue(verified_reconciliation_lane_points(case, new))
            self.assertTrue(proof(case))
            self.assertEqual(new.world['world']['metadata']['lane_layout_review'][
                'adjudication']['lane_id_map'], lane_map)
            self.assertEqual(new.world['world']['metadata']['lane_layout_review'][
                'observation']['ego_initial']['ordinal_from_right'], 1)
            self.assertEqual(new.registry[0]['hypothesized_lane'], 'lane_003')
            self.assertEqual(new.reconciliation['world'], new.world)
            self.assertEqual(frozen, {p.name: sha256(p)
                                      for p in (case/'batches').glob('batch_*.json')})
            motion = obligations(case)
            self.assertEqual(motion['fact_version_id'], new.version_id)
            self.assertEqual([(r['source_frame_id'], r['source_lane_id'],
                               r['target_frame_id'], r['target_lane_id'])
                              for r in motion['obligations']],
                             [('frame_0001', 'lane_003', 'frame_0022', 'lane_002')])
            timeline = evidence_timeline(case)
            self.assertEqual(timeline['fact_version_id'], new.version_id)
            accepted = {g['frame_id']: g['accepted_claims'] for g in timeline['groups']
                        if g['actor_id'] == 'actor_001'}
            self.assertEqual(accepted['frame_0001'][0]['value'], 'lane_003')
            self.assertEqual(accepted['frame_0022'][0]['value'], 'lane_002')


if __name__ == '__main__':
    unittest.main()
