"""NO-LLM routing regressions; generated test images are synthetic evidence."""
import copy
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.stages import batch
from _lib.runtime.case_core import Cases, atomic_json, read_json, sha256
from _lib.contracts.v3 import ContractError, normalize_parameters, validate_batch, validate_behavior
from _lib.contracts import batch_capacity, vehicle_census
from _lib.prompts import batch_response_schema
from _lib.world.single_world import LAYOUT_REVISION


def synthetic_world():
    actors = [dict(actor_id=f'actor_{i:03d}', actor_type='vehicle',
        first_seen_frame_id=f'frame_{i:04d}', identity_description=f'Synthetic vehicle {i}',
        evidence=[dict(status='observed', frame_id=f'frame_{i:04d}', time_s=(i-1)/2,
                       reason='Synthetic fixture only')]) for i in range(1, 5)]
    scene = dict(scene_type='highway', environment={}, junctions=[],
        road=dict(roads=[dict(road_id='road_001', lane_ids=['lane_001'])],
                  lanes=[dict(lane_id='lane_001', road_id='road_001', travel_direction='same_as_ego')],
                  lane_markings=[]),
        metadata=dict(lane_layout_review=dict(revision=LAYOUT_REVISION, question_id='question_001')))
    evidence = [dict(status='observed', frame_id=f'frame_{i:04d}', time_s=(i-1)/2,
                     reason='Synthetic road fixture') for i in (1, 2, 4, 5)]
    return dict(contract_version='video-case-world-behavior', evidence_epoch=1, world=scene,
        actor_hypotheses=actors, questions_to_verify=[dict(question_id='question_001',
            question='Independently verify lane layout', resolution='unresolved',
            impact_domains=['lane_count'], world_paths=['world.road.lanes'],
            evidence=[evidence[0], evidence[-1]])],
        ambiguities=[dict(ambiguity_id=f'ambiguity_{i:03d}', owner='behavior',
            subject_field='actor_motion', resolution='unresolved', impact_domains=['executor_primitive'],
            actor_ids=[f'actor_{i:03d}'], evidence_frame_ids=[f'frame_{i:04d}'],
            description='Synthetic unobserved continuation') for i in range(1, 5)],
        critical_intervals=[], field_evidence={
            key:copy.deepcopy(evidence) for key in ('world.scene_type', 'world.road.roads', 'world.road.lanes')})


class BatchOwnerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)/'case_20261019T000000000Z_aabbccdd'
        self.world = synthetic_world()
        frames = []
        for i in range(1, 6):
            path = self.case/'frames/probe'/f'frame_{i:04d}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (96, 64), (i*30, 80, 120)).save(path)
            frames.append(dict(frame_id=path.stem, time_s=(i-1)/2, path=str(path)))
        atomic_json(self.case/'state/world_context.json', self.world)
        atomic_json(self.case/'state/probe.json', dict(probe=dict(duration_s=2.002), decoded_frames=frames))
        atomic_json(self.case/'state/global_frames.json', dict(frames=frames))
        atomic_json(self.case/'state/binding.json', dict(source_sha256='0'*64))
        atomic_json(self.case/'state/progress.json', dict(sessions=[], attempts={}))
        atomic_json(self.case/'state/execution_policy.json', dict(
            batch_evidence_coverage=batch.COVERAGE_REVISION,
            batch_evidence_capacity=batch_capacity.REVISION, vehicle_census=vehicle_census.REVISION,
            batch_behavior_owner_routing=batch.BEHAVIOR_ROUTING_REVISION))
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('NO-LLM: network forbidden')).start()
        patch('_lib.runtime.orchestrator.QwenPawBackend.submit', side_effect=AssertionError('NO-LLM')).start()
        patch('_lib.runtime.orchestrator.QwenPawBackend.poll', side_effect=AssertionError('NO-LLM')).start()

    def freeze(self):
        return batch.freeze_v3(self.case)

    def test_retired_batch_expansion_option_rejected_by_direct_entry(self):
        from _lib.runtime.orchestrator import main
        stderr = io.StringIO()
        with (patch.object(sys, 'argv', ['process.py', '--enable-batch-evidence-expansion']),
              contextlib.redirect_stderr(stderr),
              self.assertRaises(SystemExit) as raised):
            main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('unrecognized arguments: --enable-batch-evidence-expansion',
                      stderr.getvalue())

    def test_new_plan_keeps_both_batches_and_all_five_census_frames(self):
        original = copy.deepcopy(self.world)
        before = sha256(self.case/'state/world_context.json')
        manifest = self.freeze()
        self.assertEqual(len(manifest['batches']), 2)
        layout, census = manifest['batches']
        self.assertEqual(layout['assigned_question_ids'], ['question_001'])
        self.assertNotIn('behavior_owner_routing', layout['packing']['evidence_selection'])
        self.assertEqual(census['assigned_question_ids'], [])
        self.assertEqual(census['assigned_ambiguity_ids'], [])
        expected = [f'frame_{i:04d}' for i in range(1, 6)]
        self.assertEqual([f['frame_id'] for f in census['frames']], expected)
        selection = census['packing']['evidence_selection']
        self.assertEqual(selection['capacity']['limit'], 6)
        self.assertEqual(selection['protected_frame_ids'], expected)
        self.assertEqual([d['issue_id'] for d in selection['behavior_owner_routing']['decisions']],
                         [f'ambiguity_{i:03d}' for i in range(1, 5)])
        self.assertEqual(read_json(self.case/'state/world_context.json'), original)
        self.assertEqual(sha256(self.case/'state/world_context.json'), before)
        self.assertIn('vehicle_census.evidence', manifest['n_zero_blockers'])
        self.assertFalse(any(i.startswith('ambiguity_') for i in manifest['n_zero_blockers']))
        batch_capacity.validate_manifest(self.case, manifest)

    def test_expanded_batch_rejects_unprotected_evidence(self):
        manifest = self.freeze()
        census = manifest['batches'][1]
        census['packing']['evidence_selection']['protected_frame_ids'].pop()
        with self.assertRaisesRegex(ContractError, 'protected evidence'):
            batch_capacity.validate_manifest(self.case, manifest)

    def test_layout_review_cannot_use_expanded_capacity(self):
        manifest = self.freeze()
        census = manifest['batches'][1]
        census['packing']['layout_review_revision'] = LAYOUT_REVISION
        with self.assertRaisesRegex(ContractError, 'ordinary Batches'):
            batch_capacity.validate_manifest(self.case, manifest)

    def test_evidence_bytes_windows_and_capacity_identical_to_unrouted_plan(self):
        with patch.object(batch, 'route_behavior_answers', return_value=set()):
            original = self.freeze()
        (self.case/'manifest.json').unlink()  # This is only the synthetic temporary fixture.
        routed = self.freeze()
        for old, new in zip(original['batches'], routed['batches']):
            self.assertEqual(old['frames'], new['frames'])
            self.assertEqual((old['start_s'], old['end_s']), (new['start_s'], new['end_s']))
            new_packing = copy.deepcopy(new['packing'])
            new_packing['evidence_selection'].pop('behavior_owner_routing', None)
            self.assertEqual(old['packing'], new_packing)

    def test_frozen_legacy_manifest_returns_without_routing_or_writes(self):
        with patch.object(batch, 'route_behavior_answers', return_value=set()):
            old = self.freeze()
        path = self.case/'manifest.json'
        before = sha256(path)
        with (patch.object(batch, 'route_behavior_answers', side_effect=AssertionError('Frozen Case')),
              patch.object(batch, 'atomic_json', side_effect=AssertionError('Frozen Case write'))):
            self.assertEqual(self.freeze(), old)
        self.assertEqual(sha256(path), before)

    def test_mixed_dependencies_and_unknown_owner_still_need_batch_answers(self):
        changes = [dict(owner='world'), dict(subject_field='actor_identity'),
            dict(actor_ids=[]), dict(actor_ids=['missing']), dict(owner=None),
            dict(resolution='resolved'), dict(impact_domains=[]),
            *[dict(impact_domains=['executor_primitive', domain]) for domain in
              ('actor_identity', 'actor_lane', 'road_topology', 'event_type', 'event_order')],
            *[{key:['bound_dependency']} for key in
              ('world_paths', 'related_world_paths', 'related_question_ids', 'affected_event_ids',
               'event_ids', 'census_issue_ids', 'related_actor_ids')],
            dict(identity_binding={'kind':'unknown'}), dict(scope_binding={'event_id':'event_001'})]
        for change in changes:
            with self.subTest(change=change):
                world = copy.deepcopy(self.world)
                world['ambiguities'][0].update(change)
                self.assertNotIn('ambiguity_001', batch.behavior_motion_deferrals(world))

    def test_declared_main_event_dependency_does_not_defer(self):
        self.world['critical_intervals'] = [dict(start_s=0, end_s=1,
            ambiguity_ids=['ambiguity_001'], event_ids=['event_001'])]
        self.assertNotIn('ambiguity_001', batch.behavior_motion_deferrals(self.world))

    def test_separate_identity_question_for_same_actor_blocks_deferral(self):
        self.world['questions_to_verify'].append(dict(question_id='question_002',
            actor_ids=['actor_001'], resolution='unresolved', impact_domains=['actor_identity']))
        self.assertNotIn('ambiguity_001', batch.behavior_motion_deferrals(self.world))
        self.assertIn('ambiguity_002', batch.behavior_motion_deferrals(self.world))

    def test_only_mixed_question_stays_assigned_in_the_same_census_batch(self):
        self.world['ambiguities'][0]['impact_domains'].append('actor_lane')
        atomic_json(self.case/'state/world_context.json', self.world)
        census = self.freeze()['batches'][1]
        self.assertEqual(census['assigned_ambiguity_ids'], ['ambiguity_001'])
        self.assertEqual(len(census['frames']), 5)

    def test_census_disabled_keeps_original_assignment_contract(self):
        policy = read_json(self.case/'state/execution_policy.json'); policy.pop('vehicle_census')
        atomic_json(self.case/'state/execution_policy.json', policy)
        census = self.freeze()['batches'][1]
        self.assertEqual(len(census['assigned_ambiguity_ids']), 4)
        self.assertNotIn('behavior_owner_routing', census['packing']['evidence_selection'])

    def test_existing_unfrozen_case_without_new_policy_keeps_original_plan(self):
        policy = read_json(self.case/'state/execution_policy.json')
        policy.pop('batch_behavior_owner_routing')
        atomic_json(self.case/'state/execution_policy.json', policy)
        with patch.object(batch, 'route_behavior_answers', side_effect=AssertionError('Old Case')):
            census = self.freeze()['batches'][1]
        self.assertEqual(len(census['assigned_ambiguity_ids']), 4)
        self.assertEqual(len(census['frames']), 5)

    def test_case_creation_sets_policy_but_reuse_does_not_migrate_old_case(self):
        source = Path(self.temp.name)/'synthetic-video.mp4'
        source.write_bytes(b'NO-LLM synthetic binding fixture, not a decoded video')
        cases = Cases(Path(self.temp.name)/'bindings')
        case, created = cases.bind(source, normalize_parameters({}), force_new=True)
        self.assertTrue(created)
        path = case/'state/execution_policy.json'
        policy = read_json(path)
        self.assertEqual(policy.pop('batch_behavior_owner_routing'), batch.BEHAVIOR_ROUTING_REVISION)
        atomic_json(path, policy)
        before = sha256(path)
        reused, created = cases.bind(source, normalize_parameters({}))
        self.assertEqual(reused, case)
        self.assertFalse(created)
        self.assertEqual(sha256(path), before)

    def test_request_remains_census_required_and_source_bound(self):
        spec = self.freeze()['batches'][1]
        payload = batch.behavior_routing_payload(spec, self.world)
        self.assertIn('EVERY supplied frame', payload['task'])
        schema = batch_response_schema(vehicle_census=True)
        self.assertIn('vehicle_census', schema['required'])
        world = copy.deepcopy(self.world); world['ambiguities'][0]['owner'] = 'world'
        with self.assertRaises(ContractError): batch.behavior_routing_payload(spec, world)
        spec['frames'].pop()
        with self.assertRaises(ContractError): batch.behavior_routing_payload(spec, self.world)

    def test_census_cannot_drop_a_frame_and_batch_cannot_answer_deferred_issue(self):
        spec = self.freeze()['batches'][1]
        census = [dict(frame_id=f['frame_id'], time_s=f['time_s'], vehicles=[]) for f in spec['frames']]
        vehicle_census.validate(census, spec['frames'], self.world['actor_hypotheses'], self.world['actor_hypotheses'])
        with self.assertRaises(vehicle_census.CensusError):
            vehicle_census.validate(census[:-1], spec['frames'], self.world['actor_hypotheses'], self.world['actor_hypotheses'])
        candidate = dict(contract_version='video-case-world-behavior', case_id=self.case.name,
            batch_id=spec['batch_id'], session_id='synthetic', world_evidence_epoch=1,
            answered_question_ids=[], answered_ambiguity_ids=[], observations=[],
            semantic_conflicts=[], registry_after=self.world['actor_hypotheses'], vehicle_census=census)
        validate_batch(candidate, self.world, [], [])
        candidate['observations'] = [dict(ambiguity_id='ambiguity_001', status='unknown', reason='Still unknown')]
        with self.assertRaisesRegex(ContractError, 'unassigned ambiguity'):
            validate_batch(candidate, self.world, [], [])

    def test_behavior_still_rejects_missing_or_unknown_owned_challenges(self):
        self.freeze()
        behavior = dict(contract_version='video-case-world-behavior', world_evidence_epoch=1,
            actors=[dict(actor_id='ego', actor_type='ego_vehicle', identity_description='Synthetic ego',
                simulation_role='participant', is_static=False, trajectory=[dict(frame_id='frame_0001',
                    time_s=0, road_id='road_001', lane_id='lane_001')])],
            events=[], executor_primitives=[], semantic_limitations=[], challenge_resolutions=[])
        for resolutions in ([], [dict(challenge_id=a['ambiguity_id'], resolution='unknown')
                                  for a in self.world['ambiguities']]):
            behavior['challenge_resolutions'] = resolutions
            with self.assertRaisesRegex(ContractError, 'Behavior did not resolve owned ambiguities'):
                validate_behavior(behavior, world=self.world, execution=False)

    def request_at_submission_boundary(self, manifest):
        # Restrict this synthetic request fixture to its ordinary Census spec.
        # The full two-Batch plan is independently checked by the freeze tests.
        manifest = copy.deepcopy(manifest)
        manifest['batches'] = [manifest['batches'][1]]
        atomic_json(self.case/'manifest.json', manifest)
        class Boundary(BaseException):
            pass
        class Owner(batch.BatchLifecycle):
            def progress(self, case):
                return read_json(case/'state/progress.json')
            def save_progress(self, case, progress):
                atomic_json(case/'state/progress.json', progress)
            def model_stage(self, case, key, agent, *args, **kwargs):
                self.payload = read_json(case/'state/requests'/f'{key}.json')
                raise Boundary()
        owner = Owner()
        with self.assertRaises(Boundary): owner.v3_batches(self.case)
        return owner.payload

    def test_real_request_builder_preserves_census_and_reuses_frozen_request(self):
        manifest = self.freeze()
        payload = self.request_at_submission_boundary(manifest)
        self.assertIn('vehicle_census', payload['response_schema']['required'])
        self.assertEqual(payload['assigned_ambiguity_ids'], [])
        self.assertEqual(len(payload['input_paths']['images']), 5)
        self.assertIn('EVERY supplied frame', payload['task'])
        path = self.case/'state/requests/batch_0002.json'
        before = sha256(path)
        self.assertEqual(self.request_at_submission_boundary(manifest), payload)
        self.assertEqual(sha256(path), before)

    def test_legacy_request_builder_adds_no_routing_fields(self):
        with patch.object(batch, 'route_behavior_answers', return_value=set()):
            manifest = self.freeze()
        payload = self.request_at_submission_boundary(manifest)
        self.assertEqual(len(payload['assigned_ambiguity_ids']), 4)
        self.assertNotIn('task', payload)
        self.assertNotIn('behavior_owner_routing', payload)

    def test_ordinary_batch_adopts_only_frame_bound_ego_lane_evidence(self):
        from _lib.world.actor_facts import EGO_LANE_OBSERVATIONS, EGO_LANE_OBSERVATIONS_KEY

        policy_path = self.case/'state/execution_policy.json'
        policy = read_json(policy_path)
        policy[EGO_LANE_OBSERVATIONS_KEY] = EGO_LANE_OBSERVATIONS
        atomic_json(policy_path, policy)
        manifest = self.freeze()
        spec = manifest['batches'][1]
        payload = self.request_at_submission_boundary(manifest)
        self.assertEqual(payload[EGO_LANE_OBSERVATIONS_KEY], EGO_LANE_OBSERVATIONS)
        frame = spec['frames'][-1]
        row = dict(status='observed', actor_id='ego', frame_id=frame['frame_id'],
                   time_s=frame['time_s'], ordinal_from_right=0,
                   reason='The camera corridor lies between the visible road edges.')
        progress_path = self.case/'state/progress.json'
        atomic_json(progress_path, dict(read_json(progress_path), completed_batches=[]))
        registry = self.world['actor_hypotheses']
        response = dict(answered_question_ids=[], answered_ambiguity_ids=[], semantic_conflicts=[],
                        registry_after=registry,
                        vehicle_census=[dict(frame_id=f['frame_id'], time_s=f['time_s'], vehicles=[])
                                        for f in spec['frames']],
                        observations=[dict(status='observed', evidence=[copy.deepcopy(row)])])

        class MockOwner(batch.BatchLifecycle):
            def progress(self, case):
                return read_json(case/'state/progress.json')

            def save_progress(self, case, progress):
                atomic_json(case/'state/progress.json', progress)

            def model_stage(self, case, key, agent, prompt, adopt, precheck, *, candidate_path):
                if 'observations[].evidence[]' not in prompt:
                    raise AssertionError('Frozen request omitted Ego lane producer guidance')
                atomic_json(candidate_path, response)
                adopt('mock-ego-lane-evidence')

        owner = MockOwner()
        target = self.case/'batches'/f"{spec['batch_id']}.json"
        response['observations'][0]['evidence'][0].pop('time_s')
        with self.assertRaisesRegex(ContractError, 'Invalid Ego lane observation'):
            owner.v3_batches(self.case)
        self.assertFalse(target.exists())

        response['observations'][0]['evidence'][0] = row
        owner.v3_batches(self.case)
        accepted = read_json(target)
        self.assertEqual(accepted['observations'][0]['evidence'], [row])
        self.assertEqual(accepted['registry_after'], registry)
        self.assertEqual(read_json(self.case/'state/requests'/f"{spec['batch_id']}.json"), payload)


if __name__ == '__main__':
    unittest.main()
