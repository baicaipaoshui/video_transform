"""NO-LLM checks for independent Target acceptance of a v3 core receipt."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.runtime.case_core import sha256

SCRIPT = Path(__file__).resolve().parents[1]/'accept_core_approximate_target.py'
spec = importlib.util.spec_from_file_location('accept_core_approximate_target', SCRIPT)
target_acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target_acceptance)


class TargetV3ProjectionSourcesTest(unittest.TestCase):
    def test_v3_replays_full_sources_and_core_events(self):
        replay = ({'events': [{'event_id': 'event_other'}]}, [], {}, {'decisions': []})
        full = {'actors': [{'actor_id': 'actor_other'}]}
        core = {'events': [{'event_id': 'event_core'}]}
        receipt = {'revision': 'core-approximate-probe.v3',
                   'source_projection_disposition': {'sha256': 'mock-bound-proof'},
                   'representative_actors': [{'actor_id': 'representative'}]}
        with patch.object(target_acceptance, 'validate_analysis_motion') as replay_motion:
            target_acceptance.validate_v3_projection_sources(
                Path('/mock/case'), receipt, replay, full, core)
        replay_motion.assert_called_once_with(Path('/mock/case'), replay[0], core,
            projection_source={'full_analysis': full,
                'disposition': receipt['source_projection_disposition'],
                'review': replay[3],
                'representatives': receipt['representative_actors']})

    def test_missing_or_rejected_source_receipt_blocks_target(self):
        receipt = {'revision': 'core-approximate-probe.v3'}
        with self.assertRaisesRegex(ValueError, 'lacks its source disposition'):
            target_acceptance.validate_v3_projection_sources('case', receipt, None, {}, {})
        receipt['source_projection_disposition'] = {'sha256': 'forged'}
        with patch.object(target_acceptance, 'validate_analysis_motion',
                          side_effect=ValueError('expired World scope receipt')):
            with self.assertRaisesRegex(ValueError, 'expired World scope receipt'):
                target_acceptance.validate_v3_projection_sources(
                    'case', receipt, ({}, [], {}, {}), {}, {})
        with patch.object(target_acceptance, 'validate_analysis_motion') as replay_motion:
            target_acceptance.validate_v3_projection_sources(
                'case', {'revision': 'core-approximate-probe.v2'}, None, None, {})
        replay_motion.assert_not_called()

    def test_prepare_accepts_v3_version_but_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = root/'cases/mock_case'
            case.mkdir(parents=True)
            output = root/'approximate_output'
            output.mkdir()
            (output/'target.json').write_text('{}')
            (output/'conversion_result.json').write_text('{}')
            inputs = root/'inputs'
            inputs.mkdir()
            source = case/'source.json'
            source.write_text('{}')
            receipt = {'revision': 'core-approximate-probe.v3',
                       'protected_inputs_unchanged': True,
                       'approximate_target_generated': False,
                       'approximate_acceptance': 'not_run',
                       'strict_completion': 'not_attempted_or_modified',
                       'source_hashes': {str(source): sha256(source)},
                       'fact_version': 'v000000'}
            (inputs/'projection_receipt.json').write_text(json.dumps(receipt))
            with patch.object(target_acceptance, 'current_facts',
                              side_effect=RuntimeError('v3 passed receipt gate')):
                with self.assertRaisesRegex(RuntimeError, 'v3 passed receipt gate'):
                    target_acceptance.prepare(root, case)
            source.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'Frozen projection source changed'):
                target_acceptance.prepare(root, case)


if __name__ == '__main__':
    unittest.main()
