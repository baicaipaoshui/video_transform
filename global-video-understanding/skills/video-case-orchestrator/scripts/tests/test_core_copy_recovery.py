"""NO-LLM checks for portable Batch binding and interrupted core copies."""
import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior import input_binding
from _lib.delivery import synthetic_target
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.core_approximate_delivery import _incomplete_copy


class CoreCopyRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name)/'case_recovery'
        self.root = Path(temporary.name)/'projection'
        self.isolated = self.root/'cases'/self.source.name
        atomic_json(self.source/'state/progress.json', dict(sessions=[], attempts={}))
        atomic_json(self.source/'batches/batch_0001.json', {'observation': 'lane change'})
        self.isolated.parent.mkdir(parents=True)
        shutil.copytree(self.source, self.isolated)

    def test_portable_timeline_accepts_copy_but_rejects_source_changes(self):
        batch = self.source/'batches/batch_0001.json'
        frozen = dict(revision=input_binding.EVIDENCE_TIMELINE, fact_version_id='v1',
                      groups=[], batch_sha256={str(batch): sha256(batch)})
        current = copy.deepcopy(frozen)
        current['batch_sha256'] = {str(self.isolated/'batches'/batch.name): sha256(batch)}
        atomic_json(self.isolated/'state/execution_policy.json',
                    {'behavior_evidence_timeline': input_binding.EVIDENCE_TIMELINE})
        with (patch.object(input_binding, 'active_request', return_value={
                'evidence_timeline': frozen, 'fact_version_id': 'v1'}),
              patch.object(input_binding, 'evidence_timeline', return_value=current)):
            self.assertEqual(input_binding.current_evidence_timeline(self.isolated), frozen)
            current['batch_sha256'] = {}
            with self.assertRaises(input_binding.InputChanged):
                input_binding.current_evidence_timeline(self.isolated)
            current['batch_sha256'] = {str(self.isolated/'batches'/batch.name): sha256(batch),
                                       str(self.isolated/'batches/batch_0002.json'): '1'*64}
            with self.assertRaises(input_binding.InputChanged):
                input_binding.current_evidence_timeline(self.isolated)
            current['batch_sha256'] = {str(self.isolated/'batches'/batch.name): '0'*64}
            with self.assertRaises(input_binding.InputChanged):
                input_binding.current_evidence_timeline(self.isolated)
            current['batch_sha256'] = {str(self.isolated/'batches'/batch.name): sha256(batch)}
            current['fact_version_id'] = 'v2'
            with self.assertRaises(input_binding.InputChanged):
                input_binding.current_evidence_timeline(self.isolated)

    def test_colliding_or_invalid_batch_sources_are_rejected(self):
        path = self.source/'batches/batch_0001.json'
        timeline = {'batch_sha256': {str(path): sha256(path),
                  str(self.isolated/'batches'/path.name): sha256(path)}}
        with self.assertRaisesRegex(input_binding.InputChanged, 'duplicate'):
            input_binding._portable_timeline(timeline)
        timeline['batch_sha256'] = {'other/batch_0001.json': sha256(path)}
        with self.assertRaisesRegex(input_binding.InputChanged, 'invalid'):
            input_binding._portable_timeline(timeline)

    def test_first_copy_failure_fallback_and_repeated_recovery(self):
        # The copy exists before the failure handler writes its fallback.
        self.assertIsNone(_incomplete_copy(self.source, self.root, self.isolated))
        synthetic_target.ensure(self.source, 'first projection failure')
        self.assertIsNone(_incomplete_copy(self.source, self.root, self.isolated))
        self.assertIsNone(_incomplete_copy(self.source, self.root, self.isolated))

    def test_missing_or_modified_fallback_is_rejected(self):
        synthetic_target.ensure(self.source, 'first projection failure')
        target = self.source/'synthetic_delivery/target.json'
        receipt = self.source/'synthetic_delivery/manifest.json'
        original_target, original_receipt = target.read_bytes(), receipt.read_bytes()
        receipt.unlink()
        with self.assertRaisesRegex(Exception, 'incomplete synthetic fallback'):
            _incomplete_copy(self.source, self.root, self.isolated)
        receipt.write_bytes(original_receipt)
        target.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(Exception, 'invalid synthetic fallback'):
            _incomplete_copy(self.source, self.root, self.isolated)
        target.write_bytes(original_target)
        data = read_json(receipt)
        data['case_id'] = 'different_case'
        atomic_json(receipt, data)
        with self.assertRaisesRegex(Exception, 'invalid synthetic fallback'):
            _incomplete_copy(self.source, self.root, self.isolated)
        receipt.write_bytes(original_receipt)
        atomic_json(target, {'synthetic': 'tampered'})
        data = read_json(receipt)
        data['target_sha256'] = sha256(target)
        atomic_json(receipt, data)
        with self.assertRaisesRegex(Exception, 'invalid synthetic fallback'):
            _incomplete_copy(self.source, self.root, self.isolated)

    def test_batch_change_or_extra_source_file_is_rejected(self):
        synthetic_target.ensure(self.source, 'first projection failure')
        atomic_json(self.source/'batches/batch_0001.json', {'observation': 'changed'})
        with self.assertRaisesRegex(Exception, 'differs from original Case'):
            _incomplete_copy(self.source, self.root, self.isolated)
        atomic_json(self.source/'synthetic_delivery/other.json', {})
        with self.assertRaisesRegex(Exception, 'not an intact Case copy'):
            _incomplete_copy(self.source, self.root, self.isolated)


if __name__ == '__main__':
    unittest.main()
