"""Synthetic NO-LLM checks; these are not real video or delivery results."""
import contextlib
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.runtime import orchestrator as runtime
from _lib.runtime.case_core import Cases, atomic_json, read_json, sha256


class NoBackend:
    def submit(self, *args, **kwargs):
        raise AssertionError('NO-LLM: no submission')

    def poll(self, *args, **kwargs):
        raise AssertionError('NO-LLM: no polling')


class SimulatedCrash(BaseException):
    pass


class NormalShortVideoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root/'synthetic.mp4'
        source.write_bytes(b'Synthetic source, never decoded')
        self.case, created = Cases(root/'cases').bind(
            source, {'workflow_version': 'v2'}, force_new=True)
        self.assertTrue(created)
        self.context = dict(
            scene_type='intersection',
            scene_facts=dict(travel_lane_count=2, ego_lane_from_right=1,
                left_parking_strip=False, right_parking_strip=False,
                stop_line_x_m=dict(value=60, status='estimated', confidence=0.5),
                ego_speed_mps=dict(value=8, status='estimated', confidence=0.5),
                crosswalk_count=1, traffic_light_count=1),
            event_hypothesis=dict(event_type='straight_driving'), event_phases=[],
            actor_hypotheses=[dict(actor_id='actor_001', actor_type='vehicle',
                first_seen_frame_id='frame_0001', identity_description='Synthetic sedan',
                is_static=False, simulation_role='participant', lane_from_right=1,
                speed_mps=dict(value=10, status='estimated', confidence=0.5),
                initial_longitudinal_m=dict(value=20, status='estimated', confidence=0.5))],
            critical_intervals=[], questions_to_verify=[])
        atomic_json(self.case/'state/probe.json', dict(probe=dict(duration_s=2.0)))
        atomic_json(self.case/'state/global_frames.json', dict(frames=[
            dict(frame_id='frame_0001', time_s=0.0),
            dict(frame_id='frame_0002', time_s=2.0)]))
        atomic_json(self.case/'state/global_context.json', self.context)
        atomic_json(self.case/'manifest.json', dict(batches=[]))
        self.owner = runtime.Orchestrator(NoBackend())
        runtime.validate_global_context(self.context, 2.0)

    def assert_final_analysis(self):
        self.owner.aggregate(self.case)
        path = self.case/'final/analysis.json'
        analysis = read_json(path)
        runtime.validate_analysis(analysis, read_json(self.case/'state/actor_registry.json'))
        self.assertEqual(analysis['scene_type'], 'intersection')
        self.assertEqual([a['actor_id'] for a in analysis['actors']], ['actor_001'])
        self.assertEqual(analysis['ego']['trajectory'][-1]['time_s'], 2.0)
        self.assertFalse((self.case/'final/analysis.candidate.json').exists())
        before = sha256(path)
        self.owner.aggregate(self.case)
        self.assertEqual(sha256(path), before)

    def test_new_short_video_compiles_and_aggregates_normally(self):
        original = copy.deepcopy(self.context)
        expected = runtime.compile_short_intersection_analysis(self.case, self.context)
        self.assertEqual(self.context, original)
        self.assertEqual(self.owner.adopt_short_video_analysis(self.case),
                         runtime.FastPathOutcome.COMPILED)
        self.assertEqual(read_json(self.case/'final/analysis.candidate.json'), expected)
        self.assert_final_analysis()

    def test_interruption_after_candidate_write_reuses_checkpoint(self):
        candidate = self.case/'final/analysis.candidate.json'
        def write_then_crash(path, value):
            atomic_json(path, value)
            if Path(path) == candidate:
                raise SimulatedCrash('Synthetic process stop after candidate save')
        with patch.object(runtime, 'atomic_json', side_effect=write_then_crash):
            with self.assertRaises(SimulatedCrash):
                self.owner.adopt_short_video_analysis(self.case)
        self.assertTrue(candidate.is_file())
        saved = sha256(candidate)
        self.owner = runtime.Orchestrator(NoBackend())
        with patch.object(runtime, 'compile_short_intersection_analysis',
                          side_effect=AssertionError('Reuse the saved candidate')):
            self.assertEqual(self.owner.adopt_short_video_analysis(self.case),
                             runtime.FastPathOutcome.COMPILED)
        self.assertEqual(sha256(candidate), saved)
        self.assert_final_analysis()


class RetiredShortFactsEntryTests(unittest.TestCase):
    def test_direct_entry_rejects_retired_option(self):
        stderr = io.StringIO()
        with (patch.object(sys, 'argv', ['process.py', '--recompile-short-facts']),
              contextlib.redirect_stderr(stderr),
              self.assertRaises(SystemExit) as raised):
            runtime.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('unrecognized arguments: --recompile-short-facts', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
