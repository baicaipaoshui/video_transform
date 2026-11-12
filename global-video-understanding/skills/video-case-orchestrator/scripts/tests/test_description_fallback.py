"""NO-LLM tests. Synthetic saves never represent a real Case/model result."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))
from _lib.runtime import description_fallback as fallback
from _lib.contracts import description_draft as draft, semantic_motion
from _lib.contracts.census_history import rounds
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import normalize_parameters
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.orchestrator import validate_analysis


def fixture():
    def actor(aid):
        return dict(actor_id=aid, actor_type='ego_vehicle' if aid == 'ego' else 'vehicle',
            identity_description='Synthetic vehicle appearance.', is_static=False,
            simulation_role='participant', vehicle_class='sedan', color='white',
            trajectory=[dict(frame_id='f'+str(t), time_s=t, road_id='road_001',
                lane_id='lane_001', detail='Synthetic vehicle appearance.') for t in (0, 4)])
    analysis = dict(scene_type='highway', road=dict(roads=[dict(road_id='road_001',
        lane_ids=['lane_001'], heading_rad=0, road_geometry=dict(shape_type='straight', visible_length_m=300))],
        lanes=[dict(lane_id='lane_001', road_id='road_001', lane_type='driving',
                    travel_direction='same_as_ego', width_m=3.5)], lane_markings=[], one_way=True),
        junctions=[], environment=dict(weather='clear'), ego=actor('ego'), actors=[actor('actor_001')],
        events=[], limitations=[], simulation_scenarios=[], metadata=dict(duration_s=4),
        quality_report=dict(execution_ready=False, execution_projection='analysis_values_then_hardcoded_fallbacks'))
    registry = [dict(actor_id='actor_001', actor_type='vehicle', identity_description='Synthetic vehicle appearance.')]
    bundle = dict(world={'world': {k: copy.deepcopy(analysis[k]) for k in ('scene_type','road','junctions','environment')},
                         'questions_to_verify': [], 'ambiguities': []},
        registry=registry, behavior=dict(actors=draft.actors(analysis), events=[], executor_primitives=[], semantic_limitations=[]),
        census=None, pending={}, separately_bound={}, review={}, frames=[dict(frame_id='f'+str(t), time_s=t) for t in (0,4)],
        parameters=normalize_parameters({}), probe=dict(duration_s=4))
    return analysis, bundle


class FixtureOwner:
    """Publish explicitly synthetic worker saves to the REAL ConversionWork."""
    calls = []
    gap = 50
    speed = 36

    def __init__(self, backend, root):
        self.root = root

    def progress(self, case):
        return read_json(case/'state/progress.json')

    def model_stage(self, case, key, agent, prompt, adopt, **kwargs):
        self.calls.append(key)
        assert key in fallback.STAGES and agent == 'json-coverter'
        folder = case/'state/conversion_work'/key/'run_0001'
        stage = read_json(folder/'request.json')
        request = read_json(case/'state/conversion_semantic_gap.request.json')
        value = dict(request_sha256=stage['request_sha256'], complete=True)
        if key == fallback.STAGES[0]:
            value.update(relations=[], coverage=[dict(source=p['source'], status='no_motion',
                reason='Synthetic fixture text contains appearance only.') for p in request['evidence']])
        else:
            value['plans'] = [dict(actor_id=a['actor_id'], initial_x_m=self.gap*i,
                speed_knots=[dict(time_s=t, speed_kmh=self.speed) for t in (0,4)],
                assumption='Synthetic test simulation, not a video measurement.') for i,a in enumerate(request['eligible'])]
        atomic_json(folder/'save_0001.json', value)
        progress = self.progress(case)
        progress.setdefault('sessions', []).append(dict(stage=key, status='completed', task_id='synthetic-'+key))
        atomic_json(case/'state/progress.json', progress)
        adopt()


class DescriptionFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base/'original'
        self.root = self.base/'fallback'
        self.analysis, self.bundle = fixture()
        atomic_json(self.source/'case.json', dict(case_id=self.source.name, parameters=self.bundle['parameters']))
        atomic_json(self.source/'state/execution_policy.json', dict(conversion_policy_revision='conversion-policy.v1',
            conversion_continuation='conversion-continuation.v1', conversion_semantic_gap=semantic_motion.REVISION))
        atomic_json(self.source/'state/progress.json', dict(sessions=[], attempts={}))
        self.receipt = dict(revision=fallback.REVISION, source_case=str(self.source), case_id=self.source.name,
            bundle_sha256=digest(self.bundle), source_hashes={str(p):sha256(p) for p in self.source.rglob('*.json')})
        self.receipt['receipt_sha256'] = digest(self.receipt)
        self.addCleanup(patch.stopall)
        patch.object(fallback, '_bundle', return_value=(self.bundle, self.receipt)).start()
        patch.object(fallback, '_assemble', return_value=(self.analysis, [])).start()
        FixtureOwner.calls = []

    def prepare(self):
        return fallback.prepare(self.source, self.root)

    def convert(self):
        with patch.object(fallback, 'FallbackOwner', FixtureOwner):
            return fallback.resume(self.source, self.root, backend=object())

    def diagnostic_fixture(self):
        from _lib.behavior import input_binding
        actor = copy.deepcopy(self.analysis['actors'][0])
        actor['actor_id'] = 'unknown_position_vehicle'
        for point in actor['trajectory']:
            point['lane_id'] = None
            point['detail'] = 'Visible vehicle with unresolved wheel-area lane boundaries.'
        self.analysis['actors'].append(actor)
        self.bundle['registry'].append(dict(actor_id=actor['actor_id'], actor_type=actor['actor_type'],
                                            identity_description=actor['identity_description']))
        groups = []
        for source_actor in draft.actors(self.analysis):
            for point in source_actor['trajectory']:
                source = 'actor-frame:'+source_actor['actor_id']+':'+point['frame_id']
                point['source_observation_ids'] = [source]
                groups.append(dict(source_observation_id=source, actor_id=source_actor['actor_id'],
                    frame_id=point['frame_id'], time_s=point['time_s'], accepted_claims=[],
                    sources=[dict(kind='vehicle_description', status='observed',
                        source_id='synthetic_batch#/'+source, object_id=source_actor['actor_id'], text=point['detail'])]))
        timeline = dict(revision=input_binding.EVIDENCE_TIMELINE, groups=groups)
        self.bundle['behavior']['actors'] = copy.deepcopy(draft.actors(self.analysis))
        atomic_json(self.source/'state/world_context.json', self.bundle['world'])
        self.receipt['bundle_sha256'] = digest(self.bundle)
        self.receipt['source_hashes'] = {str(p):sha256(p) for p in self.source.rglob('*.json')}
        self.receipt['receipt_sha256'] = digest({k:v for k,v in self.receipt.items() if k!='receipt_sha256'})
        patch.object(input_binding, 'timeline_enabled', return_value=True).start()
        patch.object(input_binding, 'current_evidence_timeline', return_value=timeline).start()

    def test_diagnostic_risk_runs_original_converter_and_replays(self):
        self.diagnostic_fixture()
        result = self.convert()
        self.assertEqual(result['status'], 'unverified', result)
        self.assertEqual(fallback.check(self.source, self.root), result)
        manifest = read_json(self.root/'output/manifest.json')
        risk, = manifest['unmodeled_observations']
        self.assertEqual(risk['source']['simulation_role'], 'participant')
        self.assertEqual(risk['handling'], 'unmodeled_observation')
        self.assertEqual(risk['collision_verification'], 'not_verified')
        self.assertEqual(manifest['collision_scope'], 'modeled_vehicles_only')
        self.assertEqual(manifest['core_approximate_acceptance'], 'not_claimed')
        target = read_json(self.root/'output/target.json')
        self.assertEqual(len(target['scenario']['vehicles']), 2)
        manifest['unmodeled_observations'][0]['collision_verification'] = 'passed'
        atomic_json(self.root/'output/manifest.json', manifest)
        with self.assertRaises(draft.SourceChanged):
            fallback.check(self.source, self.root)

    def test_diagnostic_risk_receipt_cannot_be_removed_or_reclassified(self):
        self.diagnostic_fixture()
        self.assertTrue(self.prepare()['conversion_work_prepared'])
        path = self.root/'inputs/projection_receipt.json'
        original = path.read_bytes()
        for change in ('remove', 'role', 'source', 'claim', 'revision'):
            receipt = json.loads(original)
            if change == 'remove': receipt['unmodeled_observations'] = []
            elif change == 'role': receipt['unmodeled_observations'][0]['source']['simulation_role'] = 'background'
            elif change == 'source': receipt['unmodeled_observations'][0]['source_observations'][0]['frame_id'] = 'invented'
            elif change == 'claim': receipt['unmodeled_observations'][0]['handling'] = 'observation_excluded'
            else: receipt.pop('source_coverage_revision')
            atomic_json(path, receipt)
            with self.subTest(change=change), self.assertRaises(ValueError):
                fallback.check(self.source, self.root)
            path.write_bytes(original)
        self.assertEqual(self.prepare()['status'], 'conversion_prepared')

    def test_missing_diagnostic_source_never_prepares_conversion(self):
        self.diagnostic_fixture()
        self.analysis['actors'][-1]['trajectory'][0].pop('source_observation_ids')
        result = self.prepare()
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['conversion_work_prepared'])
        self.assertIn('not carried into Behavior', result['error']['message'])

    def test_numeric_failure_cannot_trigger_actor_removal(self):
        with patch.object(FixtureOwner, 'speed', 400):
            result = self.convert()
        self.assertEqual(result['status'], 'blocked', result)
        self.assertFalse((self.root/fallback.MINIMUM_ROOT).exists())
        self.assertFalse((self.root/'output/target.json').exists())

    def test_original_converter_and_motion_complete_unverified(self):
        before = {p:sha256(p) for p in self.source.rglob('*.json')}
        result = self.convert()
        self.assertEqual(result['status'], 'unverified', result)
        self.assertEqual(FixtureOwner.calls, list(fallback.STAGES))
        manifest = read_json(self.root/'output/manifest.json')
        self.assertEqual(manifest['checks']['target_schema'], 'passed')
        self.assertEqual(manifest['checks']['continuity'], 'passed')
        self.assertEqual(manifest['checks']['lane_change_collision'], 'not_applicable')
        self.assertEqual(manifest['strict_validate_completed'], 'not_claimed')
        self.assertEqual({p:sha256(p) for p in before}, before)
        self.assertEqual(fallback.check(self.source, self.root), result)
        self.assertEqual(self.convert(), result)
        self.assertEqual(len(FixtureOwner.calls), 2)

    def test_semantic_revision_reports_generation_and_core_fidelity_separately(self):
        self.root = self.base/'semantic_revision_v1'
        self.assertFalse(self.prepare()['target_generated'])
        result = self.convert()
        self.assertTrue(result['target_generated'])
        self.assertEqual(result['core_event_fidelity'], 'not_applicable')
        manifest = read_json(self.root/'output/manifest.json')
        self.assertTrue(manifest['target_generated'])
        self.assertEqual(manifest['core_event_fidelity'], 'not_applicable')
        case = self.root/'cases'/self.source.name
        analysis_sha = sha256(case/'final/analysis.json')
        request = read_json(case/'state/conversion_semantic_gap.request.json')
        workflow = read_json(case/'state/conversion_work/workflow.json')
        self.assertEqual(request['analysis_sha256'], analysis_sha)
        self.assertEqual(workflow['request_sha256'], request['request_sha256'])
        self.assertEqual([workflow['stages'][stage]['runs'][0]['request']['root_request_sha256']
                          for stage in fallback.STAGES], [request['request_sha256']] * 2)
        self.assertEqual(read_json(self.root/'output/conversion_result.json')['input_sha256'], analysis_sha)
        self.assertEqual(fallback.check(self.source, self.root), result)

    def test_prepare_and_replay_never_call_models(self):
        with patch.object(fallback.FallbackOwner, 'model_stage', side_effect=AssertionError('NO LLM')):
            result = self.prepare()
            self.assertTrue(result['conversion_work_prepared'])
            self.assertEqual(result, fallback.check(self.source, self.root))
            self.assertEqual(result, self.prepare())
            self.assertFalse((self.root/'output/target.json').exists())

    def test_lane_change_runs_continuous_collision_check(self):
        lane = dict(self.analysis['road']['lanes'][0], lane_id='lane_002')
        self.analysis['road']['lanes'].append(lane)
        self.analysis['road']['roads'][0]['lane_ids'].append('lane_002')
        self.analysis['ego']['trajectory'] = [dict(frame_id='f'+str(t),time_s=t,
            lane_id='lane_001' if t<3 else 'lane_002', road_id='road_001',
            detail='Synthetic vehicle appearance.') for t in (0,1,3,4)]
        self.analysis['events'] = [dict(event_id='change',event_type='lane_change',
            involved_actor_ids=['ego'],time_range_s=[1,3],evidence=[dict(actor_id='ego',status='observed',
                frame_id='f'+str(t),time_s=t,lane_id=lid) for t,lid in ((1,'lane_001'),(3,'lane_002'))])]
        self.bundle['world']['world']['road'] = copy.deepcopy(self.analysis['road'])
        self.bundle['behavior']['events'] = self.analysis['events']
        self.receipt['bundle_sha256'] = digest(self.bundle)
        self.receipt['receipt_sha256'] = digest({k:v for k,v in self.receipt.items() if k!='receipt_sha256'})
        result = self.convert()
        self.assertEqual(result['status'], 'unverified', result)
        self.assertEqual(read_json(self.root/'output/manifest.json')['checks']['lane_change_collision'], 'continuous_passed')

    def test_modeled_collision_cannot_trigger_actor_removal(self):
        with patch.object(FixtureOwner, 'gap', 0):
            result = self.convert()
        self.assertEqual(result['status'], 'blocked', result)
        self.assertFalse((self.root/'output/target.json').exists())
        self.assertIn('same-lane collision', result['error']['message'])
        self.assertFalse((self.root/fallback.MINIMUM_ROOT).exists())

    def test_mutated_source_bundle_analysis_request_and_receipt_rejected(self):
        self.prepare()
        paths = [self.source/'case.json', self.root/'inputs/source_bundle.json', self.root/'inputs/source_receipt.json',
            self.root/'cases/original/final/analysis.json', self.root/'cases/original/state/conversion_semantic_gap.request.json']
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                value = read_json(path); value['tampered'] = True; atomic_json(path,value)
                with self.assertRaises((draft.SourceChanged, ValueError)):
                    fallback.check(self.source, self.root)
                path.write_bytes(original)

    def test_existing_target_is_revalidated_on_prepare(self):
        result = self.convert(); self.assertEqual(result['status'], 'unverified', result)
        target = read_json(self.root/'output/target.json'); target['scenario']['vehicles'] = []
        atomic_json(self.root/'output/target.json',target)
        with self.assertRaises(draft.SourceChanged): self.prepare()

    def test_legacy_valid_receipt_keeps_its_manifest_format(self):
        result = self.convert()
        self.assertEqual(result['status'], 'unverified')
        path = self.root/'inputs/projection_receipt.json'
        receipt = read_json(path)
        receipt.pop('source_coverage_revision')
        receipt.pop('source_timeline_sha256')
        atomic_json(path, receipt)
        manifest_path = self.root/'output/manifest.json'
        manifest = read_json(manifest_path)
        manifest['projection_sha256'] = sha256(path)
        manifest.pop('collision_scope')
        manifest['use_restrictions'] = manifest['use_restrictions'][:2]
        atomic_json(manifest_path, manifest)
        self.assertEqual(fallback.check(self.source, self.root), result)

    def test_failed_final_check_keeps_draft_only(self):
        with patch.object(fallback.contract, 'require_motion', side_effect=draft.DraftBlocked('audit incomplete')):
            result = self.convert()
        self.assertEqual(result['generation_status'], 'not_generated')
        self.assertFalse((self.root/'output/target.json').exists())
        self.assertFalse((self.root/'staging/target.json').exists())
        self.assertEqual(read_json(self.root/'draft.json')['failure']['message'], 'audit incomplete')

    def test_incomplete_conversion_cannot_be_target(self):
        with patch.object(fallback, 'convert_work', side_effect=ValueError('incomplete conversion checkpoint')):
            result = fallback.resume(self.source, self.root, backend=object())
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(result['target_path'])

    def test_missing_and_skipped_motion_checks_reject(self):
        for status in (None, 'not_executed', 'skipped'):
            with self.assertRaises(draft.DraftBlocked):
                draft.require_motion(self.analysis, {}, {'semantic_motion': {'status':status}}, {})

    def test_text_inventory_stable_after_json_roundtrip(self):
        self.assertEqual(draft.text_inventory(self.bundle),
            draft.text_inventory(json.loads(json.dumps(self.bundle, sort_keys=True))))

    def test_text_task_cannot_replace_facts_or_invent_nested_measurements(self):
        source = '/behavior/actors/0/identity_description'
        for path, value in (('/ego/identity_description','Another vehicle'),
                            ('/ego/new_data',{'speed_mps':10}),
                            ('/ego/new_data',{'nested':{'evidence':{'status':'observed'}}})):
            candidate = dict(request_sha256='bound', additions=[dict(path=path,value=value,source=source,
                quote='Synthetic vehicle appearance.')])
            with self.assertRaises(draft.DraftBlocked):
                draft.validate_text_candidate(self.bundle,self.analysis,candidate,'bound')

    def test_text_task_can_add_source_cited_missing_representation(self):
        base = copy.deepcopy(self.analysis); base['ego'].pop('identity_description')
        value = dict(request_sha256='bound', additions=[dict(path='/ego/identity_description',
            value='Synthetic vehicle appearance.',source='/behavior/actors/0/identity_description',
            quote='Synthetic vehicle appearance.')])
        full, rows = draft.validate_text_candidate(self.bundle,base,value,'bound')
        self.assertEqual(full,self.analysis); self.assertEqual(len(rows),1)

    def test_same_frame_distinct_objects_cannot_select_one_execution_identity(self):
        for i in (1,2):
            self.bundle['pending'][str(i)] = dict(batch_id='batch',frame_id='f0',time_s=0,object_id=str(i),
                candidate_actor_ids=['actor_001'],description='Synthetic observation')
        _, receipt = draft.project(self.bundle,self.analysis)
        self.assertEqual([c['handling'] for c in receipt['identity_choices']],
            ['assumed_existing','unmodeled_observation'])
        self.assertEqual(receipt['unmodeled_observations'][0]['reason'],
                         'No supported identity/track connection; interaction remains unknown')

    def test_census_pair_obligation_requires_existing_validated_disposition(self):
        iid = 'distinction:f0:actor_001:ego'
        self.bundle['pending'][iid] = dict(issue_id=iid, frame_id='f0', time_s=0,
            actor_id='actor_001', compared_actor_id='ego', distinction=True,
            candidate_actor_ids=['ego'], field_path='/vehicles/0/sightings/0/distinct_from_actor_ids')
        with self.assertRaisesRegex(draft.DraftBlocked, 'validated disposition'):
            draft.project(self.bundle, self.analysis)
        self.bundle['validated_execution_dispositions'] = dict(choices=[
            dict(issue_id=iid, handling='verified_distinct')])
        _, receipt = draft.project(self.bundle, self.analysis)
        self.assertFalse(any(row['issue_id'] == iid for row in receipt['identity_choices']))
        self.assertFalse(any(row.get('issue_id') == iid for row in receipt['unmodeled_observations']))

    def test_missing_census_index_recovers_only_unique_frozen_observation(self):
        self.bundle['pending']['batch:f0:one'] = dict(batch_id='batch', frame_id='f0',
            time_s=0, object_id='one', observation_id='source-one',
            candidate_actor_ids=[], description='Background observation')
        item = dict(limitation_id='background', impact_domains=['actor_identity'],
                    execution_effect='bounded_uncertainty', source_frame_ids=['f0'])
        self.analysis['limitations'] = [item]
        self.bundle['validated_execution_dispositions'] = dict(choices=[
            dict(issue_id='batch:f0:one', handling='observation_excluded')])
        _, receipt = draft.project(self.bundle, self.analysis)
        disposition = receipt['limitation_dispositions'][0]
        self.assertEqual(disposition['handling'], 'unmodeled_observation_limit')
        self.assertEqual(disposition['index_repair']['census_issue_ids'], ['batch:f0:one'])
        self.assertEqual(item, self.analysis['limitations'][0])
        self.bundle['pending']['batch:f0:two'] = dict(self.bundle['pending']['batch:f0:one'],
            object_id='two', observation_id='source-two')
        with self.assertRaisesRegex(draft.DraftBlocked, 'auditable execution choice'):
            draft.project(self.bundle, self.analysis)
        item.pop('source_frame_ids')
        item['source_observation_ids'] = ['source-one']
        _, receipt = draft.project(self.bundle, self.analysis)
        self.assertEqual(receipt['limitation_dispositions'][0]['issue_ids'], ['batch:f0:one'])
        self.bundle.pop('validated_execution_dispositions')
        with self.assertRaisesRegex(draft.DraftBlocked, 'auditable execution choice'):
            draft.project(self.bundle, self.analysis)

    def test_registered_identity_cannot_be_changed(self):
        self.analysis['actors'][0]['identity_description'] = 'Different identity'
        with self.assertRaises(draft.DraftBlocked): draft.project(self.bundle,self.analysis)

    def test_unknown_actor_event_reference_blocks(self):
        self.analysis['events'] = [dict(event_id='e',event_type='context_observation',
            involved_actor_ids=['missing'], evidence=[dict(status='observed',frame_id='f0',time_s=0)])]
        with self.assertRaises(ValueError): draft.project(self.bundle,self.analysis)

    def test_route_content_only(self):
        for code in ('identity_evidence_insufficient','behavior_observation_blocked'):
            self.assertTrue(fallback.eligible_failure(dict(status='failed',error=dict(code=code))))
        for code in ('timeout','release_snapshot_mismatch','behavior_input_changed','invalid_json','internal_pipeline_error'):
            self.assertFalse(fallback.eligible_failure(dict(status='failed',error=dict(code=code))))
        self.assertFalse(fallback.eligible_failure(dict(status='accepted',error=dict(code='identity_evidence_insufficient'))))
        self.assertFalse(fallback.eligible_failure(dict(status='failed',error=dict(
            code='ContractError', message='Previously unseen content contract failure'))))
        self.assertTrue(fallback.eligible_failure(dict(status='failed',
            delivery_level='core_scene_approximate', error=dict(
                code='core_noncore_scope_evidence_insufficient'))))
        self.assertTrue(fallback.eligible_failure(dict(status='failed',
            delivery_level='core_scene_approximate', error=dict(
                code='identity_evidence_insufficient'))))
        for code in ('ContractError', 'census_scope_review_required',
                     'core_motion_semantic_recovery_required', 'identity_conflict'):
            self.assertFalse(fallback.eligible_failure(dict(status='failed',
                delivery_level='core_scene_approximate', error=dict(code=code))))

    def minimum_fixture(self):
        lane = dict(self.analysis['road']['lanes'][0], lane_id='lane_002')
        self.analysis['road']['lanes'].append(lane)
        self.analysis['road']['roads'][0]['lane_ids'].append('lane_002')
        self.analysis['ego']['trajectory'] = [dict(frame_id='f'+str(t), time_s=t,
            road_id='road_001', lane_id='lane_001' if t < 3 else 'lane_002',
            detail='Synthetic vehicle appearance.') for t in (0, 1, 3, 4)]
        self.analysis['actors'].append(dict(self.analysis['actors'][0], actor_id='actor_002',
            simulation_role='background', execution_scope_evidence=dict(kind='edge_fragment',
                checked_frame_ids=['f0', 'f1', 'f3', 'f4'], visible_frame_ids=['f0'],
                no_interaction=True, reason='Synthetic reviewed isolated edge fragment.'),
            trajectory=[copy.deepcopy(self.analysis['actors'][0]['trajectory'][0])]))
        self.bundle['registry'].append(dict(actor_id='actor_002', actor_type='vehicle',
            identity_description='Synthetic vehicle appearance.'))
        self.analysis['events'] = [dict(event_id='change', event_type='lane_change',
            involved_actor_ids=['ego'], time_range_s=[1, 3], from_lane_id='lane_001',
            to_lane_id='lane_002', evidence=[dict(actor_id='ego', status='observed',
                frame_id='f'+str(t), time_s=t, lane_id=lane_id) for t, lane_id in
                ((1, 'lane_001'), (3, 'lane_002'))])]
        self.analysis['limitations'] = [dict(limitation_id='execution_gap_0',
            description='unresolved question_1', impact_domains=['executor_primitive'],
            execution_effect='bounded_uncertainty')]
        self.bundle['world']['world']['road'] = copy.deepcopy(self.analysis['road'])
        self.bundle['frames'] = [dict(frame_id='f'+str(t), time_s=t) for t in (0, 1, 3, 4)]
        self.bundle['behavior']['actors'] = copy.deepcopy(draft.actors(self.analysis))
        self.bundle['world']['questions_to_verify'] = [dict(question_id='question_1',
            actor_ids=['actor_002'], impact_domains=['actor_lane'], resolution='unresolved')]
        self.receipt['bundle_sha256'] = digest(self.bundle)
        self.receipt['receipt_sha256'] = digest({k:v for k,v in self.receipt.items() if k!='receipt_sha256'})

    def test_minimum_scene_omits_sparse_peripheral_actor_and_bound_question(self):
        self.minimum_fixture()
        result, receipt = draft.minimum_scene(self.bundle, self.analysis)
        self.assertEqual([a['actor_id'] for a in result['actors']], ['actor_001'])
        self.assertEqual(result['limitations'], [])
        self.assertEqual([x['actor_id'] for x in receipt['simplification']['omitted_actors']],
                         ['actor_002'])
        self.assertEqual(receipt['simplification']['omitted_limitations'][0]['source_question']
                         ['question_id'], 'question_1')
        self.assertEqual({a['actor_id'] for a in semantic_motion.request(result, '0'*64)['eligible']},
                         {'ego', 'actor_001'})

    def test_minimum_projection_reports_missing_accepted_core_event(self):
        minimum = self.root/fallback.MINIMUM_ROOT
        event = dict(event_id='event_001', event_type='overtaking',
                     involved_actor_ids=['ego', 'actor_001'], time_range_s=[0, 4],
                     detail='Synthetic accepted interaction.', evidence=[])
        self.bundle['behavior']['events'] = [event]
        self.receipt['bundle_sha256'] = digest(self.bundle)
        self.receipt['receipt_sha256'] = digest({k:v for k,v in self.receipt.items() if k!='receipt_sha256'})
        atomic_json(minimum/'inputs/source_bundle.json', self.bundle)
        atomic_json(minimum/'inputs/source_receipt.json', self.receipt)
        _, projection = fallback._project(minimum, self.bundle, self.analysis)
        omission = projection['core_action_omission']
        self.assertEqual(omission['rule_id'], 'core_scene.motion_retention')
        self.assertEqual(omission['missing_motion'][0]['event_id'], 'event_001')
        prepared = fallback.prepare(self.source, minimum)
        self.assertEqual(prepared['status'], 'blocked')
        self.assertFalse(prepared['conversion_work_prepared'])
        self.assertEqual(prepared['core_action_omission']['rule_id'], 'core_scene.motion_retention')
        self.assertFalse((minimum/'output/target.json').exists())

    def test_minimum_reuses_relations_and_only_runs_missing_numeric_stage(self):
        self.minimum_fixture()
        self.assertEqual(self.convert()['status'], 'unverified')
        minimum = self.root/fallback.MINIMUM_ROOT
        prepared = fallback.prepare(self.source, minimum)
        self.assertTrue(prepared['conversion_work_prepared'])
        self.assertTrue((minimum/'inputs/reused_relations.json').is_file())
        before = list(FixtureOwner.calls)
        with patch.object(fallback, 'FallbackOwner', FixtureOwner):
            result = fallback.resume(self.source, minimum, backend=object())
        self.assertEqual(result['status'], 'unverified', result)
        self.assertEqual(FixtureOwner.calls, [*before, fallback.STAGES[1]])
        self.assertEqual(fallback.check(self.source, minimum), result)

    def test_transport_failure_does_not_authorize_minimum_task(self):
        from _lib.runtime.orchestrator import BackendError
        self.assertFalse(fallback._minimum_allowed(BackendError('timeout', 'timeout')))
        self.assertFalse(fallback._minimum_allowed(draft.SourceChanged('changed')))
        self.assertTrue(fallback._minimum_allowed(draft.DraftBlocked('unsupported peripheral motion')))

    def test_minimum_does_not_repeat_failed_representation_model_work(self):
        minimum = self.root/fallback.MINIMUM_ROOT
        with patch.object(fallback, '_assemble', side_effect=ValueError('missing source trajectory')):
            result = fallback.prepare(self.source, minimum)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('missing source trajectory', result['error']['message'])
        self.assertFalse((minimum/'inputs/text_draft.request.json').exists())

    def test_unusable_source_still_saves_truthful_draft_without_model(self):
        with patch.object(fallback, '_bundle', side_effect=draft.DraftBlocked('No archived ego trajectory')):
            result = fallback.resume(self.source, self.root, backend=object())
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['error']['message'], 'No archived ego trajectory')
        self.assertTrue((self.root/'draft.json').is_file())
        self.assertFalse((self.root/'output/target.json').exists())


class HistoricalCensusHandoffTests(unittest.TestCase):
    CASES = SKILLS.parents[1]/'video_extractor/cases'

    def case(self, suffix):
        matches = list(self.CASES.glob('*'+suffix))
        if len(matches) != 1:
            self.skipTest('Frozen Case unavailable: '+suffix)
        return matches[0]

    def test_census_distinctions_use_certificate_without_batch_slot(self):
        case = self.case('b56627fb')
        bundle, _ = fallback._bundle(case)
        distinctions = {iid for iid, row in bundle['pending'].items()
                        if row.get('distinction')}
        self.assertEqual(len(distinctions), 2)
        self.assertTrue(all('batch_id' not in bundle['pending'][iid]
                            and bundle['pending'][iid].get('field_path')
                            for iid in distinctions))
        self.assertFalse(distinctions & set(bundle['separately_bound']))
        for iid in distinctions:
            row = bundle['pending'][iid]
            self.assertTrue(any(issue['issue_id'] == iid
                and row['compared_actor_id'] not in draft.pointer(
                    round['response'], row['field_path'])
                for round in rounds(bundle['census'])
                for issue in round['result'].get('pending_distinctions', [])))
        dispositions = {row['issue_id']: row['handling'] for row in
                        bundle['validated_execution_dispositions']['choices']}
        self.assertEqual({dispositions[iid] for iid in distinctions}, {'verified_distinct'})
        full, _ = fallback._assemble(case, bundle)
        _, receipt = draft.project(bundle, full)
        unresolved = {row['issue_id']: row for row in receipt['identity_choices']
                      if row['issue_id'] in {
                          'batch_0002:frame_0008:obj_f0008_5',
                          'batch_0003:frame_0008:f0008_obj6'}}
        self.assertEqual(len(unresolved), 2)
        self.assertEqual({row['handling'] for row in unresolved.values()},
                         {'unmodeled_observation'})
        self.assertEqual({row['archived_review']['outcome'] for row in unresolved.values()},
                         {'needs_identity'})
        timeline = read_json(case/'state/behavior_input.request.json')['evidence_timeline']
        for iid in unresolved:
            observation = bundle['pending'][iid]
            matches = [row for row in timeline['unresolved']
                       if row.get('object_id') == observation['object_id']
                       and row['source_id'].startswith(observation['batch_id']+'#')]
            self.assertEqual(len(matches), 1)
            source_id = matches[0]['source_id']
            original = draft.pointer(read_json(case/'batches'/(
                observation['batch_id']+'.json')), source_id.split('#', 1)[1])
            self.assertEqual(original['binding'], 'unresolved')
            self.assertEqual(original['candidate_actor_ids'], ['actor_005'])

    def test_other_case_batch_observations_keep_original_slots(self):
        for suffix in ('065638b5', '7916ef73'):
            with self.subTest(case=suffix):
                bundle, _ = fallback._bundle(self.case(suffix))
                self.assertTrue(bundle['pending'])
                self.assertEqual(set(bundle['pending']), set(bundle['separately_bound']))
                self.assertTrue(all(row.get('batch_id') for row in bundle['pending'].values()))

    def test_current_case_prepares_source_bound_diagnostic_conversion_without_models(self):
        case = self.case('b56627fb')
        original = sha256(case/'state/behavior.candidate.json')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()/'description_b'
            with patch.object(fallback.FallbackOwner, 'model_stage', side_effect=AssertionError('NO LLM')):
                result = fallback.prepare(case, root=root)
                self.assertEqual(result['status'], 'conversion_prepared', result)
                self.assertEqual(fallback.check(case, root), result)
            self.assertTrue(result['conversion_work_prepared'])
            self.assertIsNone(result['target_path'])
            self.assertEqual(result['core_approximate_acceptance'], 'not_claimed')
            projection = read_json(root/'inputs/projection_receipt.json')
            row = next(r for r in projection['unmodeled_observations'] if r.get('actor_id') == 'actor_007')
            self.assertEqual(row['handling'], 'unmodeled_observation')
            self.assertEqual(row['source']['simulation_role'], 'participant')
            self.assertEqual(len(row['source_observations']), 3)
            self.assertEqual(row['collision_verification'], 'not_verified')
            analysis = read_json(root/'cases'/case.name/'final/analysis.json')
            self.assertEqual({a['actor_id'] for a in draft.actors(analysis)},
                             {'ego', 'actor_001', 'actor_002', 'actor_003'})
            self.assertEqual(analysis['events'][0]['time_range_s'], [1.5, 3.5])
            self.assertFalse((root/'output/target.json').exists())
        self.assertEqual(sha256(case/'state/behavior.candidate.json'), original)


if __name__ == '__main__':
    unittest.main()
