"""Frozen-case regressions for the lost pickup maneuver."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _lib.contracts.core_lane_continuity import (_source_world, _source_world_path,
                                                  validate, witnesses)
from _lib.contracts import description_draft
from _lib.contracts.description_draft import minimum_scene, project
from _lib.contracts import semantic_motion
from _lib.contracts.motion_witnesses import narrated_owner_lanes
from _lib.runtime.case_core import read_json, sha256
from _lib.runtime import fact_versions
from _lib.runtime import description_fallback as fallback
from _lib.runtime import core_motion_recovery
from _lib.runtime.conversion_work import (RETRY_CODES, source_witness_guidance,
                                          witness_receipt_guidance)
from _lib.contracts.retained_motion import (bounded_actor_local_motion_limit,
                                             distant_description,
                                             same_direction_lane_ids)
from _lib.runtime.case_core import atomic_json
from _lib.runtime.case_core import retryable
from _lib.world.lane_layout import (SOURCE_CONSISTENCY_REVISION,
                                    CORRECTION_DECISION_REVISION,
                                    compile_corridor_positions, configure_decision,
                                    physical_decision_issues)


ROOT = Path(__file__).resolve().parents[5]
FAILED = ROOT/'video_extractor/cases/case_20261019T175555560Z_1f7ff9e6'
SUCCESS = ROOT/'video_extractor/cases/case_20261019T013937315Z_78d1326e'
FAILED_DRAFT = ROOT/'video_extractor/description_fallback_deliveries'/FAILED.name


class CoreLaneContinuityTest(unittest.TestCase):
    def test_retained_motion_accepts_source_bound_distant_mixed_unknowns(self):
        limitation = dict(actor_ids=['actor_007'],
            impact_domains=['actor_lane', 'actor_motion', 'appearance'],
            execution_effect='bounded_uncertainty')
        self.assertTrue(bounded_actor_local_motion_limit(limitation, 'actor_007'))
        self.assertTrue(distant_description('同向车道远处的小客车'))
        unsafe = copy.deepcopy(limitation); unsafe['affected_event_ids'] = ['event_1']
        self.assertFalse(bounded_actor_local_motion_limit(unsafe, 'actor_007'))

    def test_retained_motion_reuses_exact_legacy_lane_count(self):
        road = dict(ego_direction_lanes=2,
            roads=[dict(road_id='road_001', lane_ids=['lane_001', 'lane_002'])],
            lanes=[dict(lane_id='lane_001'), dict(lane_id='lane_002')])
        self.assertEqual(same_direction_lane_ids(road), {'lane_001', 'lane_002'})
        road['lanes'][1]['travel_direction'] = 'opposite_to_ego'
        self.assertEqual(same_direction_lane_ids(road), {'lane_001'})
        road['ego_direction_lanes'] = 1
        self.assertEqual(same_direction_lane_ids(road), set())

    def test_converter_receives_program_owned_witness_paths(self):
        guidance = witness_receipt_guidance({'/z': {}, '/a': {}})
        self.assertEqual(guidance['source_paths'], ['/a', '/z'])
        self.assertIn('Omit them from worker coverage', guidance['instruction'])

    def test_conversion_continuation_retries_stream_interruptions(self):
        self.assertIn('model_stream_interrupted', RETRY_CODES)

    def test_converter_receives_the_existing_overtake_completion_gate(self):
        request = dict(policy={'source_witness_revision': 'motion-witnesses.v1'},
                       events=[dict(event_id='lane_change_1', event_type='lane_change')])
        guidance = source_witness_guidance(request)
        self.assertEqual(guidance['overtake_event_ids'], [])
        self.assertIn('frozen numeric endpoint positions', guidance['instruction'])
        request['events'].append(dict(event_id='overtake_1', event_type='overtake'))
        self.assertEqual(source_witness_guidance(request)['overtake_event_ids'], ['overtake_1'])

    def test_ego_lane_parser_does_not_claim_another_vehicles_lane(self):
        text = '白色 cab 车头在自车右前、挂车侧面并行，仍在 lane_002 走廊。'
        self.assertEqual(narrated_owner_lanes(text, 'ego'), [])
        self.assertEqual(narrated_owner_lanes('自车仍在 lane_003', 'ego'), ['lane_003'])
        self.assertEqual(narrated_owner_lanes('本车仍在 lane_002', 'actor_003'), ['lane_002'])

    def test_semantic_motion_reads_exact_legacy_ego_lane_count(self):
        lane = lambda lane_id: dict(lane_id=lane_id, road_id='road_001')
        actor = lambda actor_id, lane_id: dict(actor_id=actor_id,
            actor_type='ego_vehicle' if actor_id == 'ego' else 'vehicle',
            is_static=False, simulation_role='participant',
            trajectory=[dict(frame_id='frame_0001', time_s=0, lane_id=lane_id)])
        analysis = dict(scene_type='highway', junctions=[],
            road=dict(ego_direction_lanes=2,
                roads=[dict(road_id='road_001', lane_ids=['lane_001', 'lane_002'])],
                lanes=[lane('lane_001'), lane('lane_002')]),
            ego=actor('ego', 'lane_001'), actors=[actor('actor_001', 'lane_002')],
            metadata=dict(duration_s=1), events=[], limitations=[], simulation_scenarios=[])
        self.assertEqual([row['actor_id'] for row in semantic_motion.eligible(analysis)],
                         ['ego', 'actor_001'])
        incomplete = copy.deepcopy(analysis)
        incomplete['road']['ego_direction_lanes'] = 1
        self.assertEqual(semantic_motion.eligible(incomplete), [])
        explicit = copy.deepcopy(analysis)
        explicit['road']['lanes'][1]['travel_direction'] = 'opposite_to_ego'
        self.assertEqual(semantic_motion.eligible(explicit), [])

    def test_versioned_commit_reads_its_exact_parent_world(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = Path(directory)/'versioned_parent'
            (case/'state').mkdir(parents=True)
            before = case/'diagnostics/world_census_adjudication/initial/before/world.json'
            before.parent.mkdir(parents=True)
            atomic_json(before, dict(marker='initial'))
            atomic_json(case/'state/world_context.json', dict(marker='parent'))
            atomic_json(case/'state/actor_registry.json', [])
            atomic_json(case/'state/reconciliation.json', dict(status='reconciled'))
            atomic_json(case/'state/execution_policy.json',
                        dict(fact_versions=fact_versions.REVISION))
            parent = fact_versions.establish_baseline(case)
            atomic_json(case/'state/lane_layout.commit.json', dict(
                old_world_sha256=sha256(parent.world_path),
                fact_parent_version=parent.version_id,
                fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json')))

            files = {name: read_json(parent.directory/name) for name in fact_versions.FILES}
            files['world_context.json'] = dict(marker='later')
            manifest = fact_versions._publish(case, 'v000001-test', files)
            atomic_json(case/'state/fact_versions/active.json', dict(
                revision=fact_versions.REVISION, version_id='v000001-test',
                manifest_sha256=manifest))

            self.assertEqual(_source_world_path(case), parent.world_path)
            self.assertEqual(_source_world(case), dict(marker='parent'))

    def test_versioned_commit_rejects_changed_parent_and_legacy_uses_initial(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = Path(directory)/'parent_integrity'
            (case/'state').mkdir(parents=True)
            before = case/'diagnostics/world_census_adjudication/initial/before/world.json'
            before.parent.mkdir(parents=True)
            atomic_json(before, dict(marker='initial'))
            atomic_json(case/'state/world_context.json', dict(marker='current'))
            atomic_json(case/'state/lane_layout.commit.json',
                        dict(old_world_sha256=sha256(before)))
            self.assertEqual(_source_world(case), dict(marker='initial'))

            atomic_json(case/'state/actor_registry.json', [])
            atomic_json(case/'state/reconciliation.json', dict(status='reconciled'))
            atomic_json(case/'state/execution_policy.json',
                        dict(fact_versions=fact_versions.REVISION))
            parent = fact_versions.establish_baseline(case)
            atomic_json(case/'state/lane_layout.commit.json', dict(
                old_world_sha256=sha256(parent.world_path),
                fact_parent_version=parent.version_id,
                fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json')))
            atomic_json(parent.world_path, dict(marker='tampered'))
            with self.assertRaisesRegex(ValueError, 'Frozen pre-layout World is missing or changed'):
                _source_world(case)

    def test_failed_case_has_bound_cross_lane_witness_and_rejects_empty_events(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        rows = witnesses(FAILED)
        self.assertEqual([(r['actor_id'], r['source_lane_id'], r['target_lane_id'],
                           r['source_frame_id'], r['target_frame_id']) for r in rows], [
        ('actor_001', 'lane_003', 'lane_002', 'frame_0016', 'frame_0022')])
        behavior = read_json(FAILED/'state/behavior.candidate.json')
        with self.assertRaisesRegex(Exception, 'Bound lane observations lack a supported motion explanation') as caught:
            validate(FAILED, behavior)
        self.assertEqual(caught.exception.rule_id, 'core_scene.motion_continuity')
        self.assertEqual(caught.exception.code, 'core_motion_semantic_recovery_required')
        self.assertFalse(retryable(caught.exception.code))
        self.assertTrue(fallback.eligible_failure(dict(status='failed', error=dict(
            code=caught.exception.code, rule_id=caught.exception.rule_id, stage='behavior'))))
        self.assertFalse(caught.exception.diagnostics['lane_change_event'])

        restored = copy.deepcopy(behavior)
        pickup = next(a for a in restored['actors'] if a['actor_id'] == 'actor_001')
        next(p for p in pickup['trajectory'] if p['frame_id'] == 'frame_0016')['lane_id'] = 'lane_003'
        restored['events'] = [dict(event_type='lane_change', involved_actor_ids=['actor_001'],
                                   from_lane_id='lane_003', to_lane_id='lane_002')]
        self.assertTrue(validate(FAILED, restored))

    def test_agreeing_world_and_batch_block_wrong_ego_shift_for_new_requests(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        request = read_json(FAILED/'diagnostics/world_census_adjudication/initial/layout.request.json')
        response = read_json(FAILED/'state/lane_layout.commit.json')['census_certificate']['response']['layout']
        before = read_json(FAILED/'diagnostics/world_census_adjudication/initial/before/world.json')
        request['lane_layout_contract']['source_consistency_revision'] = SOURCE_CONSISTENCY_REVISION
        decision = compile_corridor_positions(request, response['decision'])
        issues = physical_decision_issues(request, decision, before)
        self.assertTrue(any(i['rule_id'] == 'world.layout.correlated_source_disagreement'
                            and i['source_ordinal'] == 1 and i['decision_ordinal'] == 0 for i in issues))
        new_request = read_json(FAILED/'diagnostics/world_census_adjudication/initial/layout.request.json')
        configure_decision(new_request, CORRECTION_DECISION_REVISION)
        self.assertEqual(new_request['lane_layout_contract']['source_consistency_revision'],
                         SOURCE_CONSISTENCY_REVISION)

    def test_successful_case_does_not_invent_pickup_lane_change(self):
        if not SUCCESS.is_dir():
            self.skipTest('Archived successful Case unavailable')
        self.assertEqual(witnesses(SUCCESS), [])
        self.assertTrue(validate(SUCCESS, read_json(SUCCESS/'state/behavior.candidate.json')))

    def test_archived_blocked_conversion_discards_only_unmodeled_relation_risks(self):
        archived = (FAILED_DRAFT/'semantic_revision_retry_1/semantic_revision_v1')
        if not (archived/'inputs/full_analysis.json').is_file():
            self.skipTest('Archived blocked conversion unavailable')
        bundle = read_json(archived/'inputs/source_bundle.json')
        full, _ = fallback._assemble(FAILED, bundle, archived)
        old = read_json(archived/'cases'/FAILED.name/'state/conversion_semantic_gap.request.json')
        analysis, receipt = project(bundle, full)
        self.assertEqual(analysis['ego']['trajectory'][0]['lane_id'], 'lane_002')
        self.assertNotIn('rightmost same-direction lane', analysis['ego']['identity_description'])
        self.assertEqual([e['event_type'] for e in analysis['events']], ['lane_change'])
        self.assertFalse(any(x.get('limitation_id') == 'lim_unregistered_silver_sedan'
                             for x in analysis['limitations']))
        omitted = [x for x in receipt['limitation_dispositions']
                   if x['handling'] == 'unmodeled_observation_limit']
        self.assertEqual([x['source']['limitation_id'] for x in omitted],
                         ['lim_unregistered_silver_sedan'])
        self.assertEqual(omitted[0]['source']['census_issue_ids'],
            ['batch_0003:frame_0001:f0001_obj4', 'batch_0003:frame_0013:f0013_obj5'])
        request = semantic_motion.request(analysis, '0'*64)
        self.assertFalse(any(e['source'].startswith('/limitations/1/') for e in request['evidence']))
        self.assertFalse(any(e['text'].startswith('unresolved question_') for e in request['evidence']))
        self.assertNotEqual(request['request_sha256'], old['request_sha256'])
        self.assertTrue(validate(FAILED, analysis))

    def test_ego_lane_execution_uses_each_bound_lane_and_only_assumes_missing_samples(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            lanes = [dict(lane_id=f'road_lane_{index}', road_id='road_001', ordinal_from_right=index,
                          travel_direction='same_as_ego') for index in range(3)]
            for start in range(3):
                with self.subTest(start_ordinal=start):
                    case = Path(directory)/f'case_{start}'
                    (case/'batches').mkdir(parents=True)
                    world_path = (case/'diagnostics/world_census_adjudication/initial/before/world.json')
                    world_path.parent.mkdir(parents=True)
                    atomic_json(world_path, {})
                    initial = dict(evidence_frame_ids=['frame_a'], ordinal_from_right=start,
                                   reason='Independent initial ego corridor')
                    batch = dict(observations=[dict(status='observed',
                        layout_observation=dict(ego_initial=initial))])
                    atomic_json(case/'batches/batch_0001.json', batch)
                    frames = [dict(frame_id=f'frame_{key}', time_s=index)
                              for index, key in enumerate('abcd')]
                    bundle = dict(frames=frames, world=dict(world=dict(road=dict(lanes=lanes))))
                    old_lane = lanes[(start + 1) % 3]['lane_id']
                    behavior = dict(actors=[dict(actor_id='ego', trajectory=[dict(
                        frame_id=frame['frame_id'], time_s=frame['time_s'], road_id='road_001',
                        lane_id=old_lane, ordinal_from_right=(start + 1) % 3,
                        detail='Contested old Behavior says one lane for the whole video')
                        for frame in frames])], events=[])
                    raw_world = dict(layout_observation=dict(ego_initial=initial))
                    atomic_json(case/'state/world_context.json', raw_world)
                    with patch.object(core_motion_recovery, '_source_world', return_value=raw_world):
                        restored, record = core_motion_recovery.restore_ego_execution(
                            case, bundle, behavior)
                    track = restored['actors'][0]['trajectory']
                    self.assertEqual([p['lane_id'] for p in track], [lanes[start]['lane_id']] * 4)
                    self.assertEqual([p['status'] for p in record['timeline']],
                                     ['verified_observation'] + ['unverified_continuation_assumption'] * 3)
                    self.assertIn(f'ordinal {start + 1} of 3 from the right',
                                  description_draft._ego_execution_description(record))
                    self.assertTrue(all('Contested old Behavior' not in p['detail'] for p in track))
                    self.assertEqual(len(record['contested_behavior']), 4)
                    self.assertEqual(restored['events'], [])

    def test_later_bound_ego_cross_lane_is_preserved_without_fabricated_event(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = Path(directory)/'case_cross'
            (case/'batches').mkdir(parents=True)
            world_path = case/'diagnostics/world_census_adjudication/initial/before/world.json'
            world_path.parent.mkdir(parents=True)
            atomic_json(world_path, {})
            lanes = [dict(lane_id=f'road_lane_{index}', road_id='road_001', ordinal_from_right=index,
                          travel_direction='same_as_ego') for index in range(3)]
            initial = dict(evidence_frame_ids=['frame_a'], ordinal_from_right=0,
                           reason='Initial ego lane observed')
            later = dict(status='observed', actor_id='ego', frame_id='frame_c', time_s=2,
                         lane_id='road_lane_1', ordinal_from_right=1,
                         reason='Later ego lane independently observed')
            atomic_json(case/'batches/batch_0001.json', dict(observations=[
                dict(status='observed', layout_observation=dict(ego_initial=initial)),
                dict(status='observed', evidence=[later])]))
            frames = [dict(frame_id=f'frame_{key}', time_s=index)
                      for index, key in enumerate('abcd')]
            bundle = dict(frames=frames, world=dict(world=dict(road=dict(lanes=lanes))))
            behavior = dict(actors=[dict(actor_id='ego', trajectory=[dict(
                frame_id=frame['frame_id'], time_s=frame['time_s'], road_id='road_001',
                lane_id='road_lane_2',
                ordinal_from_right=2, detail='Contested fixed-lane Behavior')
                for frame in frames])], events=[])
            atomic_json(case/'state/world_context.json',
                        dict(layout_observation=dict(ego_initial=initial)))
            with patch.object(core_motion_recovery, '_source_world', return_value=dict(
                    layout_observation=dict(ego_initial=initial))):
                restored, record = core_motion_recovery.restore_ego_execution(
                    case, bundle, behavior)
            self.assertEqual([p['lane_id'] for p in restored['actors'][0]['trajectory']],
                             ['road_lane_0', 'road_lane_0', 'road_lane_1', 'road_lane_1'])
            self.assertEqual([p['status'] for p in record['timeline']],
                ['verified_observation', 'unverified_continuation_assumption',
                 'verified_observation', 'unverified_continuation_assumption'])
            self.assertEqual(record['timeline'][2]['observations'][0]['observation_id'],
                             'batch_0001:observation_1:evidence_0:frame_c')
            self.assertNotIn('simulation assumption', restored['actors'][0]['trajectory'][2]['detail'])
            self.assertIn('simulation assumption', restored['actors'][0]['trajectory'][3]['detail'])
            self.assertEqual(restored['events'], [])
            description = description_draft._ego_execution_description(record)
            self.assertIn('2 s in road_lane_1', description)
            self.assertIn('not exact lane-crossing times', description)
            self.assertIn('unverified simulation assumption', description)

    def test_unresolved_batch_identity_cannot_move_core_source_witness(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        original = read_json
        def without_binding(path):
            data = original(path)
            if Path(path).name == 'batch_0003.json':
                data = copy.deepcopy(data)
                for frame in data.get('vehicle_census', []):
                    if frame.get('frame_id') == 'frame_0016':
                        for vehicle in frame.get('vehicles', []):
                            if vehicle.get('actor_id') == 'actor_001':
                                vehicle['binding'] = 'unresolved'
                return data
            return data
        with patch('_lib.contracts.core_lane_continuity.read_json', side_effect=without_binding):
            rows = witnesses(FAILED)
        self.assertFalse(any(row['source_frame_id'] == 'frame_0016' for row in rows))

    def test_minimum_scene_retains_restored_registered_actor_action(self):
        if not FAILED_DRAFT.is_dir():
            self.skipTest('Archived regression draft unavailable')
        bundle = read_json(FAILED_DRAFT/'inputs/source_bundle.json')
        full = read_json(FAILED_DRAFT/'inputs/full_analysis.json')
        pickup = next(a for a in full['actors'] if a['actor_id'] == 'actor_001')
        for point in pickup['trajectory']:
            point['lane_id'] = ('lane_003' if point['frame_id'] in {'frame_0001', 'frame_0013', 'frame_0016'}
                                else 'lane_002' if point['frame_id'] == 'frame_0022' else None)
            point['ordinal_from_right'] = (2 if point['lane_id'] == 'lane_003' else
                                           1 if point['lane_id'] == 'lane_002' else None)
        for point in full['ego']['trajectory']:
            point['lane_id'] = 'lane_002'
            point['ordinal_from_right'] = 1
        full['events'] = [dict(event_id='event_001', event_type='lane_change',
            involved_actor_ids=['actor_001'], from_lane_id='lane_003', to_lane_id='lane_002',
            time_range_s=[7.5, 10.233333], evidence=[
                dict(status='observed', actor_id='actor_001', frame_id='frame_0016',
                     time_s=7.5, lane_id='lane_003', boundary_state='before'),
                dict(status='observed', actor_id='actor_001', frame_id='frame_0022',
                     time_s=10.233333, lane_id='lane_002', boundary_state='after')])]
        projected, _ = minimum_scene(bundle, full)
        self.assertEqual([a['actor_id'] for a in projected['actors']], ['actor_001'])
        self.assertEqual([e['event_type'] for e in projected['events']], ['lane_change'])

    def test_archived_motion_recovers_but_uncited_background_blocks_new_conversion(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)/'core_motion_revision'
            first = fallback.prepare(FAILED, root)
            self.assertEqual(first['status'], 'needs_core_motion')
            request = read_json(root/'inputs/core_motion.request.json')
            self.assertEqual(request['witness']['actor_id'], 'actor_001')
            self.assertEqual(request['fixed_source_frames'], ['frame_0001'])
            self.assertEqual({r['frame_id'] for r in request['intermediate']},
                             {'frame_0013', 'frame_0017'})
            answer = dict(request_sha256=request['request_sha256'], decisions=[
                dict(frame_id=row['frame_id'],
                     lane_id=(request['witness']['source_lane_id'] if row['frame_id'] == 'frame_0013'
                              else request['witness']['target_lane_id']),
                     quote=(request['witness']['source_quote'][:30] if row['frame_id'] == 'frame_0013'
                            else row['contested_behavior']['detail'][:10]))
                for row in request['intermediate']])
            atomic_json(root/'inputs/core_motion.accepted.json', answer)
            second = fallback.prepare(FAILED, root)
            self.assertEqual(second['status'], 'blocked')
            self.assertIn('complete frame-bound sources', second['error']['message'])
            self.assertFalse(second['conversion_work_prepared'])
            self.assertIsNone(second.get('core_action_omission'))
            isolated = root/'cases'/FAILED.name
            analysis = read_json(root/'inputs/full_analysis.json')
            self.assertEqual([e['event_type'] for e in analysis['events']], ['lane_change'])
            pickup = next(a for a in analysis['actors'] if a['actor_id'] == 'actor_001')
            self.assertEqual({p['frame_id']: p['lane_id'] for p in pickup['trajectory']}, {
                'frame_0001': 'lane_003', 'frame_0016': 'lane_003',
                'frame_0017': None, 'frame_0022': 'lane_002'})
            self.assertEqual(analysis['metadata']['core_motion_unknown_samples'][0]
                             ['frame_id'], 'frame_0013')
            self.assertEqual(analysis['metadata']['core_motion_unknown_samples'][0]
                             ['execution_placement'], 'audit_only')
            self.assertEqual(analysis['metadata']['core_motion_timing'][0]
                             ['exact_crossing_time'], 'unknown')
            self.assertTrue(all('lane_002' not in p['detail'] for p in pickup['trajectory']
                                if p['frame_id'] in {'frame_0013', 'frame_0017'}))
            bundle = read_json(root/'inputs/source_bundle.json')
            _, normalizations = fallback._assemble(FAILED, bundle, root)
            self.assertEqual([p['model_field_status'] for p in normalizations[-1]
                             ['field_dispositions']], ['rejected_unsupported'] * 2)
            self.assertEqual({p['lane_id'] for p in analysis['ego']['trajectory']}, {'lane_002'})
            self.assertEqual(analysis['metadata']['ego_lane_execution_correction'][
                'later_track_status'], 'unverified_continuation_assumption')
            self.assertFalse((isolated/'state/conversion_work/workflow.json').exists())
            self.assertEqual(fallback.check(FAILED, root)['status'], 'blocked')
            self.assertTrue(validate(FAILED, analysis))

    def test_only_frozen_anchor_samples_recover_without_semantic_task(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        bundle, _ = fallback._bundle(FAILED)
        behavior = copy.deepcopy(bundle['behavior'])
        pickup = next(a for a in behavior['actors'] if a['actor_id'] == 'actor_001')
        pickup['trajectory'] = [p for p in pickup['trajectory'] if p['frame_id'] in
                                {'frame_0001', 'frame_0016', 'frame_0022'}]
        request = core_motion_recovery.request(FAILED, bundle, behavior)
        self.assertEqual(request['intermediate'], [])
        restored = core_motion_recovery.recover(FAILED, bundle, behavior, request)
        self.assertTrue(validate(FAILED, restored))
        self.assertEqual(behavior['events'], [])

    def test_archived_rejected_answer_cannot_restore_legacy_lane_claims(self):
        failed = (FAILED_DRAFT/'semantic_revision_v1/cases'/FAILED.name/
                  'diagnostics/invalid_candidates')
        paths = list(failed.glob('conversion_core_motion_recovery.attempt_1.*.json'))
        if not paths:
            self.skipTest('Archived rejected json-coverter answer unavailable')
        bundle, _ = fallback._bundle(FAILED)
        from _lib.behavior.compile import prepare_behavior_candidate, evidence_frames
        behavior, _ = prepare_behavior_candidate(copy.deepcopy(bundle['behavior']),
            evidence_frames(FAILED), bundle['registry'], copy.deepcopy(bundle['world']), case=FAILED)
        request = core_motion_recovery.request(FAILED, bundle, behavior)
        archived = read_json(paths[0])
        with self.assertRaisesRegex(Exception, 'unbound'):
            core_motion_recovery.recover(FAILED, bundle, behavior, request, archived)
        replay = dict(archived, request_sha256=request['request_sha256'])
        restored = core_motion_recovery.recover(FAILED, bundle, behavior, request, replay)
        pickup = next(a for a in restored['actors'] if a['actor_id'] == 'actor_001')
        self.assertEqual({p['frame_id']: p['lane_id'] for p in pickup['trajectory']}, {
            'frame_0001': 'lane_003', 'frame_0016': 'lane_003',
            'frame_0017': None, 'frame_0022': 'lane_002'})
        self.assertTrue(validate(FAILED, restored))
        self.assertEqual([r['model_field_status'] for r in
            core_motion_recovery.decision_audit(request, replay)], ['rejected_unsupported'] * 2)

    def test_valid_bound_field_survives_other_invalid_model_field(self):
        bundle, _ = fallback._bundle(FAILED)
        from _lib.behavior.compile import prepare_behavior_candidate, evidence_frames
        behavior, _ = prepare_behavior_candidate(copy.deepcopy(bundle['behavior']),
            evidence_frames(FAILED), bundle['registry'], copy.deepcopy(bundle['world']), case=FAILED)
        original = core_motion_recovery._bound_observations
        def observed(case, bundle, actor_id, times):
            value = original(case, bundle, actor_id, times)
            value['frame_0013'].append(dict(observation_id='test:bound:frame_0013',
                source_path='synthetic-test', actor_id=actor_id, frame_id='frame_0013',
                time_s=6.0, attribute='lane_id', lane_id='lane_003',
                quote='actor_001 in lane_003', binding='existing'))
            return value
        with patch.object(core_motion_recovery, '_bound_observations', observed):
            request = core_motion_recovery.request(FAILED, bundle, behavior)
            answer = dict(request_sha256=request['request_sha256'], decisions=[
                dict(frame_id='frame_0013', lane_id='lane_003', quote='actor_001 in lane_003'),
                dict(frame_id='frame_0017', lane_id='lane_002',
                     quote=request['intermediate'][1]['contested_behavior']['detail'][:20])])
            restored = core_motion_recovery.recover(FAILED, bundle, behavior, request, answer)
            self.assertEqual([r['model_field_status'] for r in
                core_motion_recovery.decision_audit(request, answer)],
                ['accepted', 'rejected_unsupported'])
        pickup = next(a for a in restored['actors'] if a['actor_id'] == 'actor_001')
        self.assertEqual([p['lane_id'] for p in pickup['trajectory']],
                         ['lane_003', 'lane_003', 'lane_003', None, 'lane_002'])
        self.assertTrue(validate(FAILED, restored))

    def test_terminal_text_failure_reuses_confirmed_endpoints(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)/'semantic_revision_v1'
            self.assertEqual(fallback.prepare(FAILED, root)['status'], 'needs_core_motion')
            request = read_json(root/'inputs/core_motion.request.json')
            case = root/'cases'/FAILED.name
            self.assertTrue(fallback._motion_failure_fallback(
                case, root, request, RuntimeError('model_attempts_exhausted')))
            result = fallback.prepare(FAILED, root)
            self.assertEqual(result['status'], 'blocked')
            self.assertIn('complete frame-bound sources', result['error']['message'])
            analysis = read_json(root/'inputs/full_analysis.json')
            self.assertTrue(validate(FAILED, analysis))
            self.assertFalse((case/'state/conversion_work/workflow.json').exists())
            minimum = root/'minimum_scene'
            self.assertEqual(fallback.prepare(FAILED, minimum)['status'], 'blocked')
            minimal = read_json(minimum/'inputs/full_analysis.json')
            self.assertTrue(validate(FAILED, minimal))
            self.assertEqual([e['event_type'] for e in minimal['events']], ['lane_change'])

    def test_failed_core_recovery_keeps_candidate_without_preparing_conversion(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)/'minimum_scene'
            result = fallback.prepare(FAILED, root)
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['core_action_omission']['rule_id'],
                             'core_scene.motion_continuity')
            analysis = read_json(root/'inputs/full_analysis.json')
            self.assertFalse(any(e['event_type'] == 'lane_change' for e in analysis['events']))
            self.assertFalse(result['conversion_work_prepared'])
            self.assertFalse((root/'output/target.json').exists())

    def test_invalid_text_fields_cannot_block_confirmed_core_recovery(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)/'core_motion_revision'
            self.assertEqual(fallback.prepare(FAILED, root)['status'], 'needs_core_motion')
            request = read_json(root/'inputs/core_motion.request.json')
            atomic_json(root/'inputs/core_motion.accepted.json', dict(
                request_sha256=request['request_sha256'], decisions=[
                    dict(frame_id=row['frame_id'], lane_id=request['witness']['source_lane_id'],
                         quote='invented') for row in request['intermediate']]))
            result = fallback.prepare(FAILED, root)
            self.assertEqual(result['status'], 'blocked')
            self.assertIn('complete frame-bound sources', result['error']['message'])
            self.assertFalse(result['conversion_work_prepared'])
            _, normalizations = fallback._assemble(FAILED, read_json(root/'inputs/source_bundle.json'), root)
            self.assertEqual([row['model_field_status'] for row in
                normalizations[-1]
                    ['field_dispositions']], ['rejected_unsupported'] * 2)
            self.assertTrue(validate(FAILED, read_json(root/'inputs/full_analysis.json')))

    def test_prior_unverified_output_routes_to_separate_revision(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            primary = Path(directory)/FAILED.name
            (primary/'output').mkdir(parents=True)
            atomic_json(primary/'output/manifest.json', {})
            pending = core_motion_recovery.NeedsSemanticMotion({})
            with patch.object(fallback, 'ROOT', Path(directory)), \
                    patch.object(fallback, 'check', side_effect=pending), \
                    patch.object(fallback, 'prepare', return_value={'status':'needs_core_motion'}) as prepared:
                self.assertEqual(fallback.run('prepare', FAILED.name)['status'], 'needs_core_motion')
            self.assertEqual(prepared.call_args.args[1], primary/fallback.MOTION_ROOT)

    def test_terminal_text_failure_cannot_bypass_uncited_risk_in_minimum(self):
        if not FAILED.is_dir():
            self.skipTest('Archived regression Case unavailable')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)/'core_motion_revision'
            with patch.object(fallback, '_motion_task', side_effect=ValueError('invalid archived decision')), \
                    patch.object(fallback, 'convert_work', side_effect=ValueError('conversion unavailable')):
                result = fallback.resume(FAILED, root, backend=object())
            self.assertEqual(result['status'], 'blocked')
            self.assertNotIn('core_action_omission', result)
            self.assertFalse((root/'minimum_scene/inputs/projection_receipt.json').exists())
            minimal = read_json(root/'minimum_scene/inputs/full_analysis.json')
            self.assertTrue(validate(FAILED, minimal))
            self.assertFalse((root/'minimum_scene/output/target.json').exists())


if __name__ == '__main__':
    unittest.main()
