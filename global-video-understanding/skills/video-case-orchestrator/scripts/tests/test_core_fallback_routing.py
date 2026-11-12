"""NO-LLM checks for source-bound Core-to-Description handoff."""
import sys
from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts.census_scope import ScopeReviewCoverageError
from _lib.runtime.core_approximate_delivery import _noncore_scope_coverage
from _lib.runtime import core_approximate_delivery, description_fallback, orchestrator


class CoreFallbackRoutingTests(unittest.TestCase):
    def setUp(self):
        self.error = ScopeReviewCoverageError('observation_1', 'evidence_frame_ids',
                                              {'frame_0001', 'frame_0002'}, {'frame_0001'})
        self.error.diagnostics['scope_validation_issues'] = [dict(
            issue_id='observation_1', details=dict(self.error.diagnostics))]
        self.pending = {'observation_1': dict(actor_id=None,
            candidate_actor_ids=['actor_background'])}
        self.review = dict(decisions=[dict(issue_id='observation_1', outcome='irrelevant',
            affected_actor_ids=[], affected_event_ids=[],
            scope_proof=dict(rule_id='isolated_opposing'))])

    def test_background_coverage_is_eligible_without_accepting_exclusion(self):
        self.assertTrue(_noncore_scope_coverage(self.error, self.pending, self.review,
                                                {'ego', 'actor_core'}))

    def test_core_actor_or_interaction_is_not_eligible(self):
        self.assertFalse(_noncore_scope_coverage(self.error, self.pending, self.review,
                                                 {'ego', 'actor_background'}))
        self.pending['observation_1']['candidate_actor_ids'] = []
        self.review['decisions'][0]['affected_event_ids'] = ['event_core']
        self.assertFalse(_noncore_scope_coverage(self.error, self.pending, self.review,
                                                 {'ego', 'actor_core'}))

    def test_other_scope_failures_are_not_eligible(self):
        self.error.diagnostics['scope_validation_issues'][0]['details'] = dict(
            rule_id='identity.conflict')
        self.assertFalse(_noncore_scope_coverage(self.error, self.pending, self.review,
                                                 {'ego', 'actor_core'}))

    def test_core_interaction_gap_is_not_eligible(self):
        self.error.diagnostics['execution_gap_issues'] = [dict(
            rule_id='execution.interaction_conflict', impact_domains=['interaction'],
            actor_ids=['actor_core'])]
        self.assertFalse(_noncore_scope_coverage(self.error, self.pending, self.review,
                                                 {'ego', 'actor_core'}))

    def test_new_and_saved_core_cases_use_the_same_narrow_auto_route(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)/'cases/case_test/state/core_approximate_mode.json'
            case.parent.mkdir(parents=True)
            case.write_text('{}')
            for mode in ('new', 'resume', 'retry'):
                for code, status, calls in (
                        ('core_noncore_scope_evidence_insufficient', 'unverified', 1),
                        ('identity_evidence_insufficient', 'unverified', 1),
                        ('identity_conflict', 'failed', 0),
                        ('ContractError', 'failed', 0),
                        (None, 'accepted', 0)):
                    original = dict(case_id='case_test', status='failed' if code else 'accepted',
                        delivery_level=('unverified' if code in {
                            'core_noncore_scope_evidence_insufficient',
                            'identity_evidence_insufficient'} else 'core_scene_approximate'),
                        **({'error': dict(code=code)} if code else {}))
                    argv = ['process.py', '--video', '/synthetic/no-model.mp4',
                            '--case-id', 'case_test'] if mode != 'new' else [
                            'process.py', '--video', '/synthetic/no-model.mp4', '--new']
                    if mode == 'retry':
                        argv.append('--retry-failed')
                    output = io.StringIO()
                    with patch.object(sys, 'argv', argv), patch.object(orchestrator, 'EXTRACTOR', Path(folder)), \
                            patch.object(orchestrator, 'resolve_workflow_parameters',
                                         return_value={'workflow_version': 'v3'}), \
                            patch.object(orchestrator, 'QwenPawBackend', return_value=None), \
                            patch.object(core_approximate_delivery, 'run', return_value=original), \
                            patch.object(description_fallback, 'run', return_value=dict(
                                case_id='case_test', status='unverified', delivery_level='unverified',
                                generation_status='generated', target_path='/synthetic/target.json')) as fallback_run, \
                            contextlib.redirect_stdout(output):
                        orchestrator.main()
                    result = json.loads(output.getvalue())
                    self.assertEqual(result['status'], status)
                    self.assertEqual(fallback_run.call_count, calls)
                    if calls:
                        self.assertEqual(result['path_a_result'], original)


if __name__ == '__main__':
    unittest.main()
