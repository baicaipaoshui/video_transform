"""NO-LLM integration: synthetic stopped v2 inputs, real original Converter."""
import contextlib
import copy
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))
from _lib.legacy.v2 import minimum_fallback as minimum
from _lib.legacy.v2.batch_contract import RepairStopped
from _lib.runtime import description_fallback, orchestrator
from _lib.runtime.case_core import Cases, atomic_json, read_json, sha256
from _lib.delivery.converter import mela_valid


class V2MinimumFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.video = self.base/'synthetic.mp4'
        self.video.write_bytes(b'NO-LLM synthetic source; no real video understanding is claimed')
        self.extractor = self.base/'extractor'
        self.case, _ = Cases(self.extractor/'cases').bind(self.video, {'workflow_version':'v2'}, force_new=True)
        frames = []
        for index, time_s in enumerate((0.0, 2.0), 1):
            path = self.case/'frames'/f'{index}.png'
            path.write_bytes(b'Synthetic evidence bytes '+str(index).encode())
            frames.append(dict(frame_id=f'frame_{index:04d}',time_s=time_s,path=str(path),sha256=sha256(path)))
        atomic_json(self.case/'state/probe.json', dict(probe=dict(duration_s=2.002),decoded_frames=frames))
        atomic_json(self.case/'state/global_frames.json', dict(frames=frames))
        atomic_json(self.case/'state/global_context.json', dict(scene_type='highway',
            scene_facts=dict(travel_lane_count=2,ego_lane_from_right=2,
                ego_speed_mps=dict(value=20,status='estimated',confidence=0.4)),
            event_hypothesis=dict(event_type='none_discrete_steady_following'),
            actor_hypotheses=[dict(actor_id='actor_1',actor_type='vehicle',
                identity_description='Synthetic peripheral vehicle')],
            questions_to_verify=['Absolute scales and peripheral motion are uncertain']))
        progress = read_json(self.case/'state/progress.json')
        progress.update(status='failed',phase='failed',error=dict(code='v2_batch_repeated_error'),sessions=[])
        atomic_json(self.case/'state/progress.json',progress)
        self.root = self.base/'delivery'
        self.addCleanup(patch.stopall)
        patch.object(socket.socket,'connect',side_effect=AssertionError('NO-LLM network forbidden')).start()
        patch.object(orchestrator.QwenPawBackend,'submit',side_effect=AssertionError('NO-LLM')).start()
        patch.object(orchestrator.QwenPawBackend,'poll',side_effect=AssertionError('NO-LLM')).start()

    def change(self, name, update):
        value = read_json(self.case/name)
        update(value)
        atomic_json(self.case/name,value)

    def run_fallback(self, action='resume'):
        return minimum.run(action,self.case,self.root)

    def assert_blocked(self, result):
        self.assertEqual(result['status'],'blocked',result)
        self.assertIsNone(result['target_path'])
        self.assertTrue((self.root/'draft.json').is_file())
        self.assertFalse((self.root/'output/target.json').exists())

    def test_real_converter_publishes_only_checked_unverified_minimum(self):
        before = {str(p):sha256(p) for p in self.case.rglob('*') if p.is_file()}
        result = self.run_fallback()
        self.assertEqual(result['status'],'unverified',result)
        self.assertTrue(mela_valid(Path(result['target_path'])))
        manifest = read_json(Path(result['manifest_path']))
        self.assertEqual(manifest['checks']['continuity'],'passed')
        self.assertEqual(manifest['checks']['numeric_plan'],'passed')
        self.assertEqual(manifest['checks']['target_schema'],'passed')
        self.assertEqual(manifest['strict_validate_completed'],'not_claimed')
        self.assertEqual(manifest['simplification']['modeled_actor_ids'],['ego'])
        self.assertTrue(manifest['simplification']['unmodeled_observations'])
        self.assertTrue(manifest['simplification']['unverified_evidence'])
        self.assertEqual(before,{str(p):sha256(p) for p in self.case.rglob('*') if p.is_file()})

    def test_prepare_and_check_do_not_run_converter_or_model(self):
        with patch.object(minimum.subprocess,'run',side_effect=AssertionError('prepare/check must be offline')):
            self.assertEqual(self.run_fallback('prepare')['status'],'prepared')
            self.assertEqual(self.run_fallback('check')['status'],'prepared')
        self.assertFalse((self.root/'attempt.json').exists())

    def test_running_case_is_not_copied_or_modified(self):
        self.change('state/progress.json',lambda p:p.update(status='batching',sessions=[dict(status='running')]))
        before = sha256(self.case/'state/progress.json')
        self.assert_blocked(self.run_fallback())
        self.assertFalse((self.root/'source.json').exists())
        self.assertEqual(sha256(self.case/'state/progress.json'),before)

    def test_unresolved_remote_submission_stays_draft(self):
        self.change('state/progress.json',lambda p:p.update(diagnostics=dict(unresolved_submissions={'batch_0001':{}})))
        self.assert_blocked(self.run_fallback())

    def test_unknown_lane_or_confirmed_action_cannot_be_invented(self):
        self.change('state/global_context.json',lambda c:c['scene_facts'].update(ego_lane_from_right=None))
        self.assert_blocked(self.run_fallback())

    def test_main_lane_change_without_structured_track_stays_draft(self):
        self.change('state/global_context.json',lambda c:c['event_hypothesis'].update(event_type='lane_change'))
        self.assert_blocked(self.run_fallback())

    def test_existing_analysis_preserves_main_lane_change_and_continuous_audit(self):
        frames = []
        for index,time_s in enumerate((0,1,3,4),1):
            path=self.case/'frames'/f'change{index}.png';path.write_bytes(b'Synthetic change '+str(index).encode())
            frames.append(dict(frame_id=f'change_{index}',time_s=time_s,path=str(path),sha256=sha256(path)))
        atomic_json(self.case/'state/probe.json',dict(probe=dict(duration_s=4),decoded_frames=frames))
        atomic_json(self.case/'state/global_frames.json',dict(frames=frames))
        full=minimum._raw_analysis(minimum._snapshot(self.case))
        full['ego']['trajectory']=[dict(frame_id=f['frame_id'],time_s=f['time_s'],road_id='road_001',
            lane_id='lane_002' if f['time_s']<3 else 'lane_001',
            speed_mps=dict(value=20,status='estimated',confidence=0.4)) for f in frames]
        full['events']=[dict(event_id='change',event_type='lane_change',involved_actor_ids=['ego'],
            time_range_s=[1,3],evidence=[dict(actor_id='ego',status='observed',frame_id=frames[i]['frame_id'],
                time_s=frames[i]['time_s'],lane_id='lane_002' if i==1 else 'lane_001') for i in (1,2)])]
        atomic_json(self.case/'final/analysis.json',full)
        result=self.run_fallback()
        self.assertEqual(result['status'],'unverified',result)
        self.assertEqual(read_json(Path(result['manifest_path']))['checks']['lane_change_collision'],'continuous_passed')
        self.assertEqual(read_json(self.root/'analysis.json')['events'],full['events'])

    def test_existing_analysis_cannot_invent_an_evidence_frame(self):
        full=minimum._raw_analysis(minimum._snapshot(self.case))
        full['ego']['trajectory'][0]['frame_id']='unbound-frame'
        atomic_json(self.case/'final/analysis.json',full)
        self.assert_blocked(self.run_fallback())

    def test_known_curvature_is_not_replaced_with_straight_road(self):
        self.change('state/global_context.json',lambda c:c['scene_facts'].update(road_shape='curved'))
        self.assert_blocked(self.run_fallback())

    def test_missing_metrics_use_original_policy_without_claiming_measurement(self):
        self.change('state/global_context.json',lambda c:c['scene_facts'].pop('ego_speed_mps'))
        result = self.run_fallback()
        self.assertEqual(result['status'],'unverified',result)
        manifest = read_json(Path(result['manifest_path']))
        self.assertTrue(manifest['simplification']['original_converter_fallbacks'])
        self.assertEqual(manifest['numeric_assumptions']['status'],'simulated_not_video_measured')

    def test_explicit_restricted_defaults_are_not_bypassed(self):
        self.change('case.json',lambda c:c['parameters'].update(allowed_degradations=[]))
        self.assert_blocked(self.run_fallback())

    def test_excessive_source_speed_is_not_silently_clamped(self):
        self.change('state/global_context.json',lambda c:c['scene_facts']['ego_speed_mps'].update(value=100))
        self.assert_blocked(self.run_fallback())

    def test_frame_hash_change_stays_draft(self):
        frames = read_json(self.case/'state/global_frames.json')['frames']
        Path(frames[0]['path']).write_bytes(b'changed')
        self.assert_blocked(self.run_fallback())

    def test_snapshot_cannot_change_facts_or_omit_counterevidence(self):
        self.assertEqual(self.run_fallback('prepare')['status'],'prepared')
        snapshot = read_json(self.root/'source.json')
        snapshot['files']['state/global_context.json']['scene_facts']['travel_lane_count']=3
        atomic_json(self.root/'source.json',snapshot)
        self.assert_blocked(self.run_fallback())

    def test_source_update_invalidates_prepared_draft(self):
        self.run_fallback('prepare')
        self.change('state/global_context.json',lambda c:c['scene_facts'].update(travel_lane_count=3))
        self.assert_blocked(self.run_fallback())

    def test_failed_motion_check_quarantines_candidate_and_does_not_retry(self):
        with patch.object(minimum,'_audit',side_effect=minimum.DraftBlocked('Synthetic failed motion check')):
            self.assert_blocked(self.run_fallback())
        self.assertFalse((self.root/'staging/target.json').exists())
        self.assertTrue((self.root/'staging/rejected_candidate.json').exists())
        with patch.object(minimum.subprocess,'run',side_effect=AssertionError('No second conversion attempt')):
            self.assert_blocked(self.run_fallback())

    def test_published_target_replays_without_new_converter_call(self):
        first = self.run_fallback()
        self.assertEqual(first['status'],'unverified',first)
        before = sha256(Path(first['target_path']))
        original_run = minimum.subprocess.run
        def replay_only(command, **kwargs):
            self.assertNotIn('--input', command, 'No repeated delivery generation; source replay remains required')
            return original_run(command, **kwargs)
        with patch.object(minimum.subprocess,'run',side_effect=replay_only), \
                patch.object(minimum.bounded_execution,'plan',side_effect=AssertionError('Reuse frozen numeric plan')):
            result = self.run_fallback()
        self.assertEqual(result['status'],'unverified',result)
        self.assertEqual(sha256(Path(result['target_path'])),before)

    def test_tampered_published_manifest_cannot_report_success(self):
        result = self.run_fallback()
        self.assertEqual(result['status'],'unverified',result)
        path = Path(result['manifest_path'])
        value = read_json(path); value['status']='accepted'; atomic_json(path,value)
        result = self.run_fallback('check')
        self.assertEqual(result['status'],'blocked',result)
        self.assertIsNone(result['target_path'])

    def test_global_and_archived_counterevidence_cannot_be_overwritten(self):
        path=self.case/'diagnostics/invalid_candidates/bad.json'
        atomic_json(path,dict(challenges=[dict(actor_ids=['ego'],field='ego lane')]))
        self.assert_blocked(self.run_fallback())

    def test_public_entry_automatically_routes_content_stop_with_actual_case_id(self):
        error = RepairStopped('v2_batch_repeated_error')
        error.case_id = self.case.name
        output = io.StringIO()
        with patch.object(orchestrator.Orchestrator,'run',side_effect=error), \
                patch.object(description_fallback,'source_case',return_value=self.case), \
                patch.object(description_fallback,'ROOT',self.base/'deliveries'), \
                patch.object(sys,'argv',['process.py','--video',str(self.video),'--parameters-json','{"workflow_version":"v2"}']), \
                contextlib.redirect_stdout(output):
            orchestrator.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'],'unverified',result)
        self.assertEqual(result['case_id'],self.case.name)
        self.assertEqual(result['path_a_result']['error']['code'],'v2_batch_repeated_error')

    def test_real_v2_stop_exception_carries_case_id_for_dispatch(self):
        self.change('state/progress.json',lambda p:p.update(status='batching',phase='batching',error=None))
        owner = orchestrator.Orchestrator(None)
        with patch.object(orchestrator,'EXTRACTOR',self.extractor), \
                patch.object(owner,'validate_manifest'), \
                patch.object(owner,'batches',side_effect=RepairStopped('v2_batch_repeated_error')):
            with self.assertRaises(RepairStopped) as stopped:
                owner.run(self.video,case_id=self.case.name,parameters={'workflow_version':'v2'})
        self.assertEqual(stopped.exception.case_id,self.case.name)
        self.assertEqual(owner.progress(self.case)['status'],'failed')


if __name__ == '__main__':
    unittest.main()
