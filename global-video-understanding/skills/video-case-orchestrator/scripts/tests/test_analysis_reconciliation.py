"""NO-LLM contract and synthetic-delivery checks."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.contracts import analysis_reconciliation as contract
from _lib.contracts import description_draft
from _lib.delivery.synthetic_target import build as synthetic_build, validate as synthetic_validate
from _lib.runtime.case_core import atomic_json, read_json
from _lib.stages.delivery import CaseDelivery


def fixture():
    analysis = dict(scene_type='highway',
        road=dict(lanes=[dict(lane_id='lane_001'),dict(lane_id='lane_002')]),
        ego=dict(actor_id='ego', trajectory=[]), actors=[
            dict(actor_id='actor_002', trajectory=[dict(frame_id='frame_0004',time_s=1.5,
                                                        lane_id='lane_002')])],
        events=[], limitations=[], metadata={})
    sentence = ('两条未决观察是同一物理对象；但与 actor_002 同帧共存，'
                '正式身份保持未知。')
    behavior = dict(semantic_limitations=[dict(description=sentence,
        impact_domains=['actor_identity'], actor_ids=['actor_002'],
        census_issue_ids=['batch_a:obj_1','batch_b:obj_2'])])
    census = dict(response=dict(identity_groups=[], bindings=[]))
    return analysis, behavior, census


def candidate(packet):
    return dict(request_sha256=packet['request_sha256'], complete=True,
        decisions=[dict(issue_id=packet['issues'][0]['issue_id'], decision='keep_unknown',
            reason='No formal identity receipt.',
            citations=[dict(ref='behavior_limit', quote='同一物理对象')],
            alternatives=[dict(interpretation='two vehicles', reason='not confirmed')],
            additions=[], execution_hint=dict(kind='provisional_link',
                census_issue_ids=['batch_a:obj_1','batch_b:obj_2'],
                reason='Execution only; formal identity remains unresolved.'))])


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.case = Path(temporary.name)/'case_test'
        atomic_json(self.case/'state/execution_policy.json',
                    {'analysis_reconciliation':contract.REVISION,
                     'synthetic_target':'synthetic-target.v1'})
        self.analysis,self.behavior,self.census = fixture()

    def test_compact_packet_and_locked_identity(self):
        packet = contract.request(self.analysis,self.behavior,self.census)
        self.assertEqual(len(packet['issues']),1)
        self.assertLess(len(json.dumps(packet,ensure_ascii=False)),5000)
        answer = contract.validate(packet,candidate(packet))
        self.assertEqual(contract.apply(self.analysis,packet,answer),self.analysis)
        self.assertEqual(answer['decisions'][0]['execution_hint']['kind'],'provisional_link')

    def test_fake_quote_and_unauthorized_change_rejected(self):
        packet = contract.request(self.analysis,self.behavior,self.census)
        answer = candidate(packet)
        answer['decisions'][0]['citations'][0]['quote']='imaginary witness'
        with self.assertRaisesRegex(ValueError,'quote'):
            contract.validate(packet,answer)
        answer = candidate(packet)
        answer['decisions'][0].update(decision='add_derived',
            additions=[dict(path='/actors/0/trajectory/0/lane_id',value='lane_001')])
        with self.assertRaisesRegex(ValueError,'allowance'):
            contract.validate(packet,answer)

    def test_missing_issue_rejected(self):
        packet = contract.request(self.analysis,self.behavior,self.census)
        answer = dict(request_sha256=packet['request_sha256'],complete=True,decisions=[])
        with self.assertRaisesRegex(ValueError,'omits'):
            contract.validate(packet,answer)

    def test_derived_lane_requires_exact_quoted_lane(self):
        analysis = copy.deepcopy(self.analysis)
        analysis['events']=[dict(event_id='event_001',event_type='lane_change',
            detail='从 lane_001 向左进入 lane_002', involved_actor_ids=['ego'],
            evidence=[dict(actor_id='ego',frame_id='frame_0001',time_s=0.0,status='observed')])]
        packet=contract.request(analysis,dict(semantic_limitations=[]))
        issue=next(row for row in packet['issues'] if row['kind']=='event_text_lane')
        answer=dict(request_sha256=packet['request_sha256'],complete=True,decisions=[dict(
            issue_id=issue['issue_id'],decision='add_derived',reason='The source text names the destination.',
            citations=[dict(ref='event_text',quote='lane_002')], alternatives=[],
            additions=[dict(path='/events/0/to_lane_id',value='lane_002')],execution_hint=None)])
        self.assertEqual(contract.apply(analysis,packet,answer)['events'][0]['to_lane_id'],'lane_002')

    def test_model_timeout_keeps_unknown_and_replays_receipt(self):
        owner=CaseDelivery()
        with (patch('_lib.contracts.census_adjudication.proof',return_value=self.census),
              patch.object(owner,'model_stage',side_effect=TimeoutError('model unavailable'),create=True)):
            result=owner.reconcile_analysis(self.case,self.analysis,self.behavior)
        self.assertEqual(result,self.analysis)
        receipt=read_json(self.case/'state/analysis_reconciliation.json')
        self.assertEqual(receipt['status'],'kept_unknown')
        self.assertEqual(receipt['response']['decisions'][0]['decision'],'keep_unknown')
        with (patch('_lib.contracts.census_adjudication.proof',return_value=self.census),
              patch.object(owner,'model_stage',side_effect=AssertionError('must reuse receipt'),create=True)):
            self.assertEqual(owner.reconcile_analysis(self.case,self.analysis,self.behavior),self.analysis)

    def test_valid_model_candidate_is_accepted(self):
        owner=CaseDelivery()
        def model_stage(case,key,agent,prompt,adopt,**kwargs):
            packet=read_json(case/'state/analysis_reconciliation.request.json')
            atomic_json(case/'state/analysis_reconciliation.candidate.json',candidate(packet))
            adopt()
        with (patch('_lib.contracts.census_adjudication.proof',return_value=self.census),
              patch.object(owner,'model_stage',side_effect=model_stage,create=True)):
            self.assertEqual(owner.reconcile_analysis(self.case,self.analysis,self.behavior),self.analysis)
        receipt=read_json(self.case/'state/analysis_reconciliation.json')
        self.assertEqual(receipt['status'],'accepted')
        self.assertEqual(receipt['response']['decisions'][0]['execution_hint']['kind'],'provisional_link')

    def test_synthetic_target_is_schema_valid_and_idempotent(self):
        from _lib.delivery.synthetic_target import ensure
        self.assertTrue(synthetic_validate(synthetic_build(self.case.name)))
        first=ensure(self.case,'forced normal conversion failure')
        second=ensure(self.case,'later failure')
        self.assertEqual(first['target_sha256'],second['target_sha256'])
        self.assertEqual(first['delivery_level'],'synthetic_unverified')
        self.assertEqual(len(read_json(Path(first['target_path']))['scenario']['vehicles']),1)

    def test_synthetic_target_survives_invalid_analysis_json(self):
        from _lib.delivery.synthetic_target import ensure
        analysis=self.case/'final/analysis.json'
        analysis.parent.mkdir(parents=True,exist_ok=True)
        analysis.write_text('{broken',encoding='utf-8')
        result=ensure(self.case,'invalid Analysis')
        self.assertEqual(result['omitted_actor_ids'],'unknown')
        self.assertEqual(result['analysis_sha256'],__import__('hashlib').sha256(
            analysis.read_bytes()).hexdigest())
        self.assertTrue(synthetic_validate(read_json(result['target_path'])))

    def test_interrupted_core_reconciliation_copy_can_resume(self):
        from _lib.runtime.core_approximate_delivery import _incomplete_copy
        import shutil
        source=self.case
        atomic_json(source/'state/progress.json',dict(case_id=source.name,
            phase='aggregating',status='running',attempts={'behavior':1},
            sessions=[dict(stage='behavior',status='completed')]))
        root=source.parent/'isolated_work'
        isolated=root/'cases'/source.name
        isolated.parent.mkdir(parents=True)
        shutil.copytree(source,isolated)
        progress=read_json(isolated/'state/progress.json')
        progress['attempts']['analysis_reconciliation']=1
        progress['sessions'].append(dict(stage='analysis_reconciliation',status='completed'))
        progress['diagnostics']={'model_task_reservations':[]}
        atomic_json(isolated/'state/progress.json',progress)
        atomic_json(isolated/'state/analysis_reconciliation.request.json',{'issues':[]})
        self.assertIsNone(_incomplete_copy(source,root,isolated))

    def test_failed_strict_case_reports_separate_synthetic_delivery(self):
        progress=dict(status='failed',generation_status='not_generated',
                      source_contract_validation='not_passed',
                      verification_level='not_verified',artifacts={},diagnostics={})
        result=CaseDelivery().ensure_synthetic_target(self.case,progress,'forced conversion failure')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['generation_status'],'not_generated')
        self.assertEqual(result['source_contract_validation'],'not_passed')
        self.assertEqual(result['verification_level'],'not_verified')
        self.assertEqual(result['artifacts'],{})
        self.assertTrue(Path(result['diagnostics']['synthetic_target']['target_path']).is_file())

    def test_core_approximate_terminal_failure_gets_synthetic_target(self):
        from _lib.runtime import core_approximate_delivery as core
        with (patch.object(core,'select_case',return_value=self.case),
              patch.object(core,'_run_selected',return_value=dict(case_id=self.case.name,
                  status='failed',phase='failed',generation_status='not_generated',
                  error={'message':'forced conversion failure'}))):
            result=core.run(object(),'unused-video',{},new=True)
        self.assertEqual(result['generation_status'],'not_generated')
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('target_path',result)
        self.assertEqual(result['synthetic_target']['delivery_level'],'synthetic_unverified')
        self.assertTrue(Path(result['synthetic_target']['target_path']).is_file())

    def test_core_approximate_exception_keeps_failed_formal_status(self):
        from _lib.runtime import core_approximate_delivery as core
        with (patch.object(core,'select_case',return_value=self.case),
              patch.object(core,'_run_selected',side_effect=RuntimeError('offline failure'))):
            result=core.run(object(),'unused-video',{},new=True)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['generation_status'],'not_generated')
        self.assertEqual(result['delivery_level'],'unverified')
        self.assertNotIn('target_path',result)
        self.assertTrue(Path(result['synthetic_target']['target_path']).is_file())

    def test_archived_lane_change_uses_boundary_witnesses(self):
        root=SKILLS.parents[1]/'video_extractor/description_fallback_deliveries'
        inputs=root/'case_20261020T015118907Z_ffb377e3/inputs'
        if not (inputs/'source_bundle.json').is_file():
            self.skipTest('Archived independent Case fixture unavailable')
        bundle=read_json(inputs/'source_bundle.json')
        full=read_json(inputs/'full_analysis.json')
        projected,receipt=description_draft.minimum_scene(bundle,full)
        self.assertEqual(projected['events'][0]['event_type'],'lane_change')
        self.assertEqual({a['actor_id'] for a in projected['actors']},{'actor_001','actor_002'})
        self.assertEqual(receipt['simplification']['modeled_actor_ids'],
                         ['actor_001','actor_002','ego'])

    def test_archived_pickup_hint_only_merges_execution_proxies(self):
        root=SKILLS.parents[1]/'video_extractor/description_fallback_deliveries'
        inputs=root/'case_20261020T015118907Z_ffb377e3/inputs'
        if not (inputs/'source_bundle.json').is_file():
            self.skipTest('Archived independent Case fixture unavailable')
        bundle=read_json(inputs/'source_bundle.json')
        full=read_json(inputs/'full_analysis.json')
        packet=contract.request(full,bundle['behavior'],bundle['census'])
        issue=next(row for row in packet['issues'] if row['kind']=='unresolved_identity_text')
        answer=dict(request_sha256=packet['request_sha256'],complete=True,decisions=[dict(
            issue_id=issue['issue_id'],decision='keep_unknown',
            reason='Formal identity remains unresolved.',
            citations=[dict(ref='behavior_limit',quote='同一物理对象')],
            alternatives=[],additions=[],execution_hint=dict(kind='provisional_link',
                census_issue_ids=issue['census_issue_ids'],reason='Execution representative only.'))])
        bundle['analysis_reconciliation']=dict(request=packet,response=answer)
        projected,receipt=description_draft.project(bundle,full,defer_motion=True)
        proxies=[a for a in projected['actors'] if a['actor_id'].startswith('unverified_vehicle_')]
        self.assertEqual(len(proxies),1)
        self.assertEqual(len([row for row in receipt['identity_choices']
            if row.get('provisional_group_issue_ids')]),2)
        self.assertEqual(full['limitations'][0]['census_issue_ids'],issue['census_issue_ids'])

    def test_three_completed_v3_cases_need_no_new_text_task(self):
        root=SKILLS.parents[1]/'video_extractor/cases'
        for case_id in ('case_20261011T231731758Z_8adeacad',
                        'case_20261012T121038605Z_b274b884',
                        'case_20261013T172904544Z_c2042c33'):
            case=root/case_id
            if not (case/'final/analysis.json').is_file():
                self.skipTest('Archived completed Case fixture unavailable')
            with self.subTest(case_id=case_id):
                analysis=read_json(case/'final/analysis.json')
                behavior=read_json(case/'state/behavior.json')
                census=read_json(case/'state/census_adjudication.accepted.json')
                self.assertEqual(contract.request(analysis,behavior,census)['issues'],[])
                with (patch('_lib.contracts.census_adjudication.proof',return_value=census),
                      patch.object(CaseDelivery,'model_stage',side_effect=AssertionError(
                          'successful replay must skip the model'),create=True)):
                    self.assertEqual(CaseDelivery().reconcile_analysis(
                        self.case,analysis,behavior),analysis)


if __name__=='__main__':
    unittest.main()
