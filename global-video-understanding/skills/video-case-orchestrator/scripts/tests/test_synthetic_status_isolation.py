"""NO-LLM regression for formal and synthetic delivery status separation."""
import io
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.delivery.report import sections
from _lib.delivery.synthetic_target import public_result
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.orchestrator import schema
from _lib.stages.delivery import CaseDelivery


CASE_ID = 'case_20261020T063425856Z_88554da8'
SOURCE = SKILLS.parents[1]/'video_extractor/cases'/CASE_ID
LIST_CASES = SKILLS.parents[1]/'video-coordinator/skills/video-pipeline/scripts/list_cases.py'
spec = importlib.util.spec_from_file_location('video_case_list_for_delivery_test', LIST_CASES)
case_list = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case_list)


class SyntheticStatusIsolationTests(unittest.TestCase):
    def test_delivery_classes_in_formal_report(self):
        analysis = {'scene_type': 'highway', 'events': [], 'actors': [], 'quality_report': {}}
        checks = {'references': 'passed', 'render_verification': 'not_executed'}
        for status, label in (('done', '正式成功'),
                              ('done_degraded', '来源约束降级成功')):
            lines = dict(sections(analysis, {'status': 'done'}, checks,
                                  delivery_status=status))['执行状态']
            self.assertIn(label, lines[0])
        self.assertNotIn('执行状态', dict(sections(analysis)))

    def test_public_result_keeps_formal_target_and_ignores_synthetic_for_success(self):
        formal = {'status': 'done_degraded', 'generation_status': 'done_degraded',
                  'source_contract_validation': 'passed', 'verification_level': 'source_contract_validated',
                  'target_path': '/formal/target.json', 'artifacts': {'target.json': {'path': '/formal/target.json'}}}
        synthetic = {'target_path': '/synthetic/target.json', 'delivery_level': 'synthetic_unverified'}
        result = public_result(formal, synthetic)
        self.assertEqual(result['target_path'], '/formal/target.json')
        self.assertEqual(result['status'], 'done_degraded')
        self.assertEqual(result['synthetic_target'], synthetic)
        self.assertNotIn('synthetic', result['artifacts'])

    def test_statistics_count_only_verified_formal_artifacts(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temporary:
            folder = Path(temporary)/'case_formal'
            artifacts = {}
            for name in ('analysis.json', 'report.md', 'report.docx', 'target.json'):
                path = folder/'final'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding='utf-8')
                artifacts[name] = {'path': str(path), 'sha256': sha256(path)}
            for status, expected in (('done', 'formal_success'),
                                     ('done_degraded', 'source_bound_degraded')):
                progress = {'status': status, 'phase': 'done', 'artifacts': artifacts}
                health, _ = case_list.case_health(folder, progress)
                self.assertEqual(health, 'healthy')
                self.assertEqual(case_list.delivery_class(folder, progress, health), expected)

    def test_88554da8_copy_replay_and_cli_never_promote_synthetic(self):
        if not SOURCE.is_dir():
            self.skipTest('Archived Case 88554da8 is unavailable')
        source_files = [SOURCE/'state/progress.json', SOURCE/'synthetic_delivery/manifest.json',
                        SOURCE/'synthetic_delivery/target.json']
        original_hashes = {str(path): sha256(path) for path in source_files}
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temporary:
            extractor = Path(temporary)/'video_extractor'
            clone = extractor/'cases'/CASE_ID
            clone.parent.mkdir(parents=True)
            shutil.copytree(SOURCE, clone, symlinks=True)
            progress_path = clone/'state/progress.json'
            progress = read_json(progress_path)
            self.assertEqual(progress['status'], 'failed')
            self.assertEqual(progress['generation_status'], 'done_degraded')  # historical contamination
            progress.update(generation_status='not_generated',
                            verification_level='not_verified', source_contract_validation='not_passed')
            before = {key: progress[key] for key in
                      ('status', 'generation_status', 'source_contract_validation', 'verification_level')}
            CaseDelivery().ensure_synthetic_target(clone, progress, 'offline replay')
            self.assertEqual({key: progress[key] for key in before}, before)
            self.assertEqual(progress['artifacts'], {})
            self.assertEqual(progress['diagnostics']['synthetic_target']['delivery_level'],
                             'synthetic_unverified')
            self.assertEqual(progress['diagnostics']['synthetic_target']['target_path'],
                             str(clone/'synthetic_delivery/target.json'))
            schema('progress.schema.json', progress)
            atomic_json(progress_path, progress)

            from _lib.runtime import orchestrator as cli
            from _lib.runtime import core_approximate_delivery as core
            output = io.StringIO()
            with (patch.object(cli, 'EXTRACTOR', extractor),
                  patch.object(cli, 'QwenPawBackend', return_value=object()),
                  patch.object(core, 'run', return_value=progress),
                  patch.object(sys, 'argv', ['orchestrator', '--video', '/unused',
                                            '--case-id', CASE_ID, '--description-fallback', 'off']),
                  redirect_stdout(output)):
                cli.main()
            emitted = json.loads(output.getvalue())
            self.assertEqual({key: emitted[key] for key in before}, before)
            self.assertNotIn('target_path', emitted)
            self.assertEqual(emitted['synthetic_target']['target_path'],
                             str(clone/'synthetic_delivery/target.json'))
            self.assertEqual(read_json(progress_path)['artifacts'], {})
            health, _ = case_list.case_health(clone, progress)
            self.assertEqual(health, 'terminal')
            self.assertEqual(case_list.delivery_class(clone, progress, health), 'synthetic_only')

            legacy = read_json(SOURCE/'state/progress.json')
            normalized = public_result(legacy)
            self.assertEqual(normalized['status'], 'failed')
            self.assertEqual(normalized['generation_status'], 'not_generated')
            self.assertEqual(normalized['verification_level'], 'not_verified')
            self.assertNotIn('target_path', normalized)
        self.assertEqual({str(path): sha256(path) for path in source_files}, original_hashes)


if __name__ == '__main__':
    unittest.main()
