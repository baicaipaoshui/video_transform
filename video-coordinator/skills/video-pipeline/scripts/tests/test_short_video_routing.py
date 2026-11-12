"""NO-LLM entrypoint routing tests; no Case or model task is created."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'process.py'
spec = importlib.util.spec_from_file_location('pipeline_entry', SCRIPT)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class ShortVideoRoutingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.video = Path(folder.name) / 'clip with spaces.mp4'
        self.video.write_bytes(b'Synthetic path; duration probe is mocked.')
        self.addCleanup(patch.stopall)
        self.duration = patch.object(entry, 'video_duration', return_value=2.002).start()
        patch.object(entry.subprocess, 'run', side_effect=AssertionError('NO-LLM: no subprocess')).start()
        patch.object(entry.os, 'execv', side_effect=AssertionError('NO-LLM: no execution')).start()

    def command(self, *args, parameters=None):
        argv = ['--video', str(self.video), *args]
        if parameters is not None:
            argv += ['--parameters-json', json.dumps(parameters)]
        self.stderr = io.StringIO()
        with contextlib.redirect_stderr(self.stderr):
            result = entry.command(argv)
        self.parameters = json.loads(result[result.index('--parameters-json') + 1])
        return result

    def test_two_second_new_case_uses_v2_without_core_or_preview_defaults(self):
        result = self.command('--new')
        self.assertEqual(self.parameters, {'workflow_version': 'v2'})
        self.assertNotIn('--core-scene-approximate', result)
        self.assertIn('selected workflow=v2', self.stderr.getvalue())
        self.assertIn('--new', result)
        self.assertEqual(result[result.index('--video') + 1], str(self.video.resolve()))

    def test_default_lookup_also_selects_v2(self):
        result = self.command()
        self.assertEqual(self.parameters['workflow_version'], 'v2')
        self.assertNotIn('--new', result)

    def test_duration_boundary_includes_encoding_tolerance(self):
        for duration in (0.1, 1.0, 2.0, 2.002, 2.1):
            with self.subTest(duration=duration):
                self.duration.return_value = duration
                self.command('--new')
                self.assertEqual(self.parameters['workflow_version'], 'v2')

    def test_longer_videos_keep_v3_core_default(self):
        for duration in (2.1001, 2.5, 5, 10):
            with self.subTest(duration=duration):
                self.duration.return_value = duration
                result = self.command('--new')
                self.assertEqual(self.parameters['workflow_version'], 'v3')
                self.assertIn('--core-scene-approximate', result)
                self.assertTrue(self.parameters['fallback_policy']['straight_preview']['enabled'])

    def test_explicit_version_wins_without_probing(self):
        for version in ('v2', 'v3'):
            with self.subTest(version=version):
                result = self.command('--new', parameters={'workflow_version': version})
                self.assertEqual(self.parameters['workflow_version'], version)
                self.assertEqual('--core-scene-approximate' in result, version == 'v3')
        self.duration.assert_not_called()

    def test_saved_case_keeps_empty_parameters_and_does_not_probe(self):
        result = self.command('--case-id', 'synthetic_saved_case', '--retry-failed')
        self.assertEqual(self.parameters, {})
        self.assertNotIn('--core-scene-approximate', result)
        self.duration.assert_not_called()

    def test_retired_batch_expansion_option_rejected_before_probe(self):
        with self.assertRaises(SystemExit) as raised:
            self.command('--case-id', 'synthetic_saved_case', '--retry-failed',
                         '--enable-batch-evidence-expansion')
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('unrecognized arguments: --enable-batch-evidence-expansion',
                      self.stderr.getvalue())
        self.duration.assert_not_called()

    def test_retired_bounded_recovery_option_rejected_before_probe(self):
        with self.assertRaises(SystemExit) as raised:
            self.command('--case-id', 'synthetic_saved_case', '--retry-failed',
                         '--enable-bounded-recovery')
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('unrecognized arguments: --enable-bounded-recovery',
                      self.stderr.getvalue())
        self.duration.assert_not_called()

    def test_retired_short_facts_option_rejected_before_probe(self):
        with self.assertRaises(SystemExit) as raised:
            self.command('--case-id', 'synthetic_saved_case', '--retry-failed',
                         '--recompile-short-facts')
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('unrecognized arguments: --recompile-short-facts',
                      self.stderr.getvalue())
        self.duration.assert_not_called()

    def test_explicit_v3_modes_win_without_probing(self):
        for options in (
                ('--strict-delivery',), ('--core-scene-approximate',),
                ('--strict-delivery', '--execution-scope', 'ego_vehicle_scene.v4'),
                ('--strict-delivery', '--execution-planning', 'ego_scene_approximation.v1')):
            with self.subTest(options=options):
                self.command('--new', *options)
                self.assertNotEqual(self.parameters.get('workflow_version'), 'v2')
        self.duration.assert_not_called()

    def test_fallback_switch_preserves_automatic_v2_selection(self):
        result = self.command('--new', '--description-fallback', 'auto')
        self.assertEqual(self.parameters['workflow_version'], 'v2')
        self.assertNotIn('--core-scene-approximate', result)

    def test_existing_incompatible_v3_options_still_reject(self):
        with self.assertRaises(SystemExit):
            self.command('--new', '--execution-scope', 'ego_vehicle_scene.v4')
        self.duration.assert_not_called()

    def test_v3_parameter_contracts_win_without_probing(self):
        for name in ('video_fidelity_policy', 'conversion_defaults',
                     'parked_scenery_per_side', 'confirmed_lane_assignments'):
            with self.subTest(name=name):
                self.command('--new', parameters={name: {}})
                self.assertNotEqual(self.parameters.get('workflow_version'), 'v2')
        self.duration.assert_not_called()

    def test_retired_policy_is_still_rejected(self):
        with self.assertRaises(SystemExit):
            self.command('--new', parameters={'execution_policy_version': 'efficient_v1'})
        self.duration.assert_not_called()

    def test_other_parameters_are_preserved(self):
        parameters = {'road_source': 'preview_json', 'allowed_degradations': [],
                      'fallback_policy': {'mode': 'controlled'}}
        self.command('--new', parameters=parameters)
        self.assertEqual(self.parameters, dict(parameters, workflow_version='v2'))

    def test_unavailable_probe_keeps_v3_with_diagnostic(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('ffprobe', 10),
                      ValueError('Invalid metadata'), KeyError('duration')):
            with self.subTest(error=type(error).__name__):
                self.duration.side_effect = error
                result = self.command('--new')
                self.assertNotEqual(self.parameters.get('workflow_version'), 'v2')
                self.assertIn('--core-scene-approximate', result)
                self.assertIn('Duration unavailable', self.stderr.getvalue())


class VideoDurationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.video = Path(folder.name) / 'clip with spaces.mp4'
        self.video.write_bytes(b'Synthetic fixture')

    def test_ffprobe_is_metadata_only_bounded_and_preserves_path(self):
        response = subprocess.CompletedProcess([], 0, stdout=json.dumps(
            {'streams': [{'width': 1920, 'height': 1080}], 'format': {'duration': '2.002'}}))
        with patch.object(entry.subprocess, 'run', return_value=response) as run:
            self.assertEqual(entry.video_duration(self.video), 2.002)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], 'ffprobe')
        self.assertEqual(argv[-1], str(self.video.resolve()))
        self.assertNotIn('-show_frames', argv)
        self.assertEqual(run.call_args.kwargs['timeout'], 10)
        self.assertTrue(run.call_args.kwargs['check'])

    def test_invalid_durations_and_missing_video_stream_reject(self):
        for duration in ('0', '-1', 'NaN', 'Infinity', 'N/A', None):
            with self.subTest(duration=duration):
                response = subprocess.CompletedProcess([], 0, stdout=json.dumps(
                    {'streams': [{}], 'format': {'duration': duration}}))
                with patch.object(entry.subprocess, 'run', return_value=response):
                    with self.assertRaises((ValueError, TypeError)):
                        entry.video_duration(self.video)
        response = subprocess.CompletedProcess([], 0, stdout='{"streams": [], "format": {"duration": "2"}}')
        with patch.object(entry.subprocess, 'run', return_value=response):
            with self.assertRaises(ValueError):
                entry.video_duration(self.video)


if __name__ == '__main__':
    unittest.main()
