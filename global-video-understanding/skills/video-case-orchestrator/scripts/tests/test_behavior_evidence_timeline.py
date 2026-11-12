"""NO-LLM checks for the version-gated Behavior source evidence handoff."""
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior import input_binding, repair
from _lib.behavior.input_binding import evidence_timeline, write_behavior
from _lib.contracts.core_lane_continuity import validate, validate_source_coverage, witnesses
from _lib.contracts.v3 import ContractError
from _lib.prompts import behavior_request_text
from _lib.runtime.case_core import atomic_json, read_json, sha256

ROOT = SKILLS.parents[1]
FAILED = ROOT/'video_extractor/cases/case_20261019T175555560Z_1f7ff9e6'
SUCCESS = ROOT/'video_extractor/cases/case_20261011T231731758Z_8adeacad'
SUCCESS_MOTION = ROOT/'video_extractor/cases/case_20261019T013937315Z_78d1326e'


def timeline(*, duplicate_object=False, accepted_lane=None):
    sources = [dict(source_id='batch_0001#/vehicle_census/0/vehicles/0',
                    kind='vehicle_description', status='observed',
                    object_id='object_1', text='first original description'),
               dict(source_id='batch_0002#/observations/0/evidence/0',
                    kind='observation_source_reason', status='observed',
                    text='second original reason')]
    if duplicate_object:
        sources.append(dict(source_id='batch_0001#/vehicle_census/0/vehicles/1',
                            kind='vehicle_description', status='observed',
                            object_id='object_2', text='conflicting object'))
    return dict(revision='actor-time-evidence.v1', lane_id_map={}, groups=[dict(
        source_observation_id='actor-frame:actor_001:frame_a', actor_id='actor_001',
        frame_id='frame_a', time_s=0, sources=sources,
        accepted_claims=([dict(claim_id='claim_1', field='actor_lane', value=accepted_lane)]
                         if accepted_lane else []))], unresolved=[])


def behavior(*, cite='point', lane='lane_001'):
    source_id = 'actor-frame:actor_001:frame_a'
    point = dict(frame_id='frame_a', time_s=0, lane_id=lane, detail='Physical position remains uncertain.')
    result = dict(ego=dict(actor_id='ego', trajectory=[]),
                  actors=[dict(actor_id='actor_001', trajectory=[point])],
                  events=[], semantic_limitations=[])
    if cite == 'point':
        point['source_observation_ids'] = [source_id]
    elif cite == 'limitation':
        result['semantic_limitations'] = [dict(limitation_id='uncertain_motion',
            description='The two descriptions do not fix physical crossing or its time.',
            actor_ids=['actor_001'], impact_domains=['actor_lane', 'actor_motion'],
            execution_effect='blocks_execution', source_observation_ids=[source_id])]
    elif cite == 'event':
        result['events'] = [dict(event_id='event_1', event_type='road_transition',
            involved_actor_ids=['actor_001'], evidence=[dict(frame_id='frame_a', time_s=0)],
            source_observation_ids=[source_id])]
    return result


class BehaviorEvidenceTimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)/'case_timeline'
        (self.case/'state').mkdir(parents=True)
        atomic_json(self.case/'state/world_context.json', dict(world=dict(road=dict(one_way=True))))

    def test_one_point_can_carry_multiple_original_sources(self):
        self.assertTrue(validate_source_coverage(self.case, behavior(), timeline=timeline()))
        with self.assertRaisesRegex(Exception, 'not carried'):
            validate_source_coverage(self.case, behavior(cite=None), timeline=timeline())
        cited_only = behavior(); cited_only['actors'][0]['trajectory'][0].pop('detail')
        with self.assertRaisesRegex(Exception, 'lacks an explanation'):
            validate_source_coverage(self.case, cited_only, timeline=timeline())

    def test_event_and_precise_unresolved_can_carry_without_extra_point(self):
        for kind in ('event', 'limitation'):
            with self.subTest(kind=kind):
                data = behavior(cite=kind)
                data['actors'][0]['trajectory'] = []
                self.assertTrue(validate_source_coverage(self.case, data, timeline=timeline()))

    def test_wrong_reference_and_structured_conflict_are_rejected(self):
        wrong = behavior(); wrong['actors'][0]['trajectory'][0]['frame_id'] = 'frame_b'
        with self.assertRaisesRegex(Exception, 'actor/frame/time'):
            validate_source_coverage(self.case, wrong, timeline=timeline())
        wrong = behavior(); wrong['actors'][0]['trajectory'][0]['source_observation_ids'] = ['unknown']
        with self.assertRaisesRegex(Exception, 'unknown source'):
            validate_source_coverage(self.case, wrong, timeline=timeline())
        with self.assertRaisesRegex(Exception, 'accepted lane claim'):
            validate_source_coverage(self.case, behavior(lane='lane_002'),
                                     timeline=timeline(accepted_lane='lane_001'))
        with self.assertRaisesRegex(Exception, 'accepted lane claim'):
            validate_source_coverage(self.case, behavior(cite='limitation', lane='lane_002'),
                                     timeline=timeline(accepted_lane='lane_001'))
        with self.assertRaisesRegex(Exception, 'multiple distinct Batch objects'):
            validate_source_coverage(self.case, behavior(), timeline=timeline(duplicate_object=True))

    def test_empty_motion_obligations_still_require_source_coverage_at_writer(self):
        atomic_json(self.case/'state/execution_policy.json',
                    dict(behavior_evidence_timeline='actor-time-evidence.v1'))
        atomic_json(self.case/'state/global_frames.json', dict(frames=[dict(frame_id='frame_a', time_s=0)]))
        with patch('_lib.behavior.input_binding.current_evidence_timeline', return_value=timeline()):
            with self.assertRaisesRegex(Exception, 'not carried'):
                write_behavior(self.case, behavior(cite=None))
            self.assertFalse((self.case/'state/behavior.json').exists())
            write_behavior(self.case, behavior())
            self.assertEqual(read_json(self.case/'state/behavior.json'), behavior())
            child = Path(self.temp.name)/'derived_case'
            (child/'state').mkdir(parents=True)
            with self.assertRaisesRegex(Exception, 'not carried'):
                write_behavior(child, behavior(cite=None), source_case=self.case)
            self.assertFalse((child/'state/behavior.json').exists())

    def test_unrecognized_original_english_is_indexed_not_silent(self):
        (self.case/'batches').mkdir()
        atomic_json(self.case/'state/actor_registry.json', [dict(actor_id='actor_001')])
        atomic_json(self.case/'state/global_frames.json', dict(frames=[dict(frame_id='frame_a', time_s=0)]))
        atomic_json(self.case/'manifest.json', dict(batches=[dict(batch_id='batch_0001', frames=[])]))
        atomic_json(self.case/'batches/batch_0001.json', dict(vehicle_census=[dict(
            frame_id='frame_a', time_s=0, vehicles=[dict(binding='existing',
                actor_id='actor_001', object_id='object_1',
                description='Its wheel footprint arcs across a broken white stripe.')])]))
        packet = evidence_timeline(self.case)
        self.assertEqual(witnesses(self.case), [])
        self.assertEqual(packet['groups'][0]['sources'][0]['text'],
                         'Its wheel footprint arcs across a broken white stripe.')
        with self.assertRaisesRegex(Exception, 'not carried'):
            validate_source_coverage(self.case, behavior(cite=None), timeline=packet)

    def test_old_fact_version_cannot_reuse_frozen_timeline(self):
        atomic_json(self.case/'state/execution_policy.json',
                    dict(behavior_evidence_timeline='actor-time-evidence.v1'))
        pointer = self.case/'state/fact_versions/active.json'
        pointer.parent.mkdir(parents=True)
        atomic_json(pointer, dict(version_id='v2'))
        request = dict(evidence_timeline=timeline(), fact_version_id='v1')
        with (patch('_lib.behavior.input_binding.active_request', return_value=request),
              patch('_lib.runtime.fact_versions.current_facts',
                    return_value=SimpleNamespace(version_id='v2'))):
            with self.assertRaisesRegex(Exception, 'older fact version'):
                input_binding.current_evidence_timeline(self.case)

    def test_1f7ff9e6_index_preserves_cross_frame_original_text(self):
        if not FAILED.is_dir():
            self.skipTest('Archived Case 1f7ff9e6 unavailable')
        protected = [FAILED/'batches/batch_0003.json', FAILED/'batches/batch_0004.json',
                     FAILED/'state/behavior.candidate.json']
        hashes = {str(path): sha256(path) for path in protected}
        packet = evidence_timeline(FAILED)
        groups = {(g['actor_id'], g['frame_id']): g for g in packet['groups']}
        before = groups['actor_001', 'frame_0016']['sources']
        after = groups['actor_001', 'frame_0022']['sources']
        self.assertTrue(any('actor_001 in lane_003' in item['text'] for item in before))
        self.assertTrue(any('between the dashed_white_1 and dashed_white_2' in item['text']
                            for item in after))
        self.assertTrue(any(row['source_binding'] == 'unresolved'
                            for row in packet['unresolved']))
        candidate = read_json(FAILED/'state/behavior.candidate.json')
        pickup = next(actor for actor in candidate['actors'] if actor['actor_id'] == 'actor_001')
        for point in pickup['trajectory']:
            key = ('actor_001', point.get('frame_id'))
            if key in groups:
                point['source_observation_ids'] = [groups[key]['source_observation_id']]
        with self.assertRaisesRegex(Exception, 'supported motion explanation'):
            validate(FAILED, candidate)
        self.assertEqual({str(path): sha256(path) for path in protected}, hashes)

    def test_verified_opposing_scope_can_exclude_without_extra_point(self):
        if not SUCCESS.is_dir():
            self.skipTest('Archived successful Case unavailable')
        index = evidence_timeline(SUCCESS)
        selected = [row for row in index['groups'] if row['actor_id'] == 'actor_007']
        self.assertTrue(selected)
        index['groups'] = selected
        self.assertTrue(validate_source_coverage(SUCCESS,
            read_json(SUCCESS/'state/behavior.json'), timeline=index))

    def test_existing_behavior_schema_preserves_optional_source_ids(self):
        if not SUCCESS.is_dir():
            self.skipTest('Archived successful Case unavailable')
        from _lib.behavior.compile import (evidence_frames, prepare_behavior_candidate,
                                           validate_compiled_behavior)
        index = evidence_timeline(SUCCESS)
        group = next(row for row in index['groups'] if row['actor_id'] == 'actor_001')
        original = read_json(SUCCESS/'state/behavior.json')
        actor = next(row for row in original['actors'] if row['actor_id'] == group['actor_id'])
        point = next(row for row in actor['trajectory'] if row['frame_id'] == group['frame_id'])
        point['source_observation_ids'] = [group['source_observation_id']]
        world = read_json(SUCCESS/'state/world_context.json')
        registry = read_json(SUCCESS/'state/actor_registry.json')
        frames = evidence_frames(SUCCESS)
        prepared, _ = prepare_behavior_candidate(original, frames, registry, world, case=SUCCESS)
        preserved = next(row for row in prepared['actors'] if row['actor_id'] == group['actor_id'])
        self.assertEqual(next(row for row in preserved['trajectory']
                              if row['frame_id'] == group['frame_id'])['source_observation_ids'],
                         [group['source_observation_id']])
        validate_compiled_behavior(prepared, world, registry, frames, execution=False)

    def test_successful_cases_need_only_source_links_on_existing_points(self):
        for case in (SUCCESS, SUCCESS_MOTION):
            if not case.is_dir():
                continue
            with self.subTest(case=case.name):
                path = case/'state/behavior.json'
                if not path.is_file():
                    path = case/'state/behavior.candidate.json'
                candidate = read_json(path)
                index = evidence_timeline(case)
                points = {(actor.get('actor_id'), point.get('frame_id')): point
                          for actor in [candidate.get('ego'), *candidate.get('actors', [])]
                          if isinstance(actor, dict) for point in actor.get('trajectory', [])}
                for row in index['groups']:
                    point = points.get((row['actor_id'], row['frame_id']))
                    if point:
                        point['source_observation_ids'] = [row['source_observation_id']]
                self.assertTrue(validate_source_coverage(case, candidate, timeline=index))

    def test_isolated_freeze_prompt_and_hash_drift(self):
        if not FAILED.is_dir():
            self.skipTest('Archived Case 1f7ff9e6 unavailable')
        clone = Path(self.temp.name)/FAILED.name
        shutil.copytree(FAILED, clone, symlinks=True)
        (clone/'state/behavior.candidate.json').unlink(missing_ok=True)
        (clone/input_binding.REQUEST).unlink(missing_ok=True)
        policy_path = clone/'state/execution_policy.json'
        policy = read_json(policy_path)
        policy['behavior_evidence_timeline'] = input_binding.EVIDENCE_TIMELINE
        atomic_json(policy_path, policy)
        # The historical World request binds the old policy bytes. This patch
        # isolates the new Behavior binding path without altering that request.
        with patch('_lib.world.single_world.validate_inputs'):
            request = input_binding.freeze(clone)
            self.assertIn('evidence_timeline', request)
            prompt = behavior_request_text(clone)
            self.assertIn(str(clone/input_binding.REQUEST), prompt)
            self.assertIn('仅供按 source_id 定向核查', prompt)
            self.assertLess(len(prompt.encode()) - len(behavior_request_text(FAILED).encode()),
                            2_000)  # The index body is read from the frozen request, not inlined.
            original = sha256(clone/'batches/batch_0003.json')
            with (clone/'batches/batch_0003.json').open('ab') as stream:
                stream.write(b' ')
            self.assertNotEqual(sha256(clone/'batches/batch_0003.json'), original)
            with self.assertRaisesRegex(Exception, 'frozen input changed'):
                input_binding.current_evidence_timeline(clone)

    def test_isolated_existing_repair_protocol_limits_source_fix(self):
        if not FAILED.is_dir():
            self.skipTest('Archived Case 1f7ff9e6 unavailable')
        clone = Path(self.temp.name)/FAILED.name
        shutil.copytree(FAILED, clone, symlinks=True)
        archived = clone/'diagnostics/source_coverage_test_archive.json'
        atomic_json(archived, read_json(clone/'state/behavior.candidate.json'))
        (clone/'state/behavior.candidate.json').unlink()
        (clone/input_binding.REQUEST).unlink()
        policy_path = clone/'state/execution_policy.json'
        policy = read_json(policy_path)
        policy['behavior_evidence_timeline'] = input_binding.EVIDENCE_TIMELINE
        atomic_json(policy_path, policy)
        error = ContractError('source not carried', rule_id='core_scene.source_coverage',
                              actor_id='actor_001')
        with patch('_lib.world.single_world.validate_inputs'):
            input_binding.freeze(clone)
            prompt = repair.repair_prompt(clone, clone/'state/behavior.candidate.json',
                                          archived, error)
            self.assertIn('evidence_timeline_path', prompt)
            packet = read_json(clone/'state/behavior_repair.request.json')
            self.assertEqual(packet['repair_contract'], 'behavior-source-coverage.v1')
            source_id = 'actor-frame:actor_001:frame_0001'
            result = repair.materialize(clone, dict(base_sha256=packet['base_sha256'],
                replacements=[dict(path='/actors/1/trajectory/0/source_observation_ids',
                                   value=[source_id])]))
            self.assertEqual(result['actors'][1]['trajectory'][0]['source_observation_ids'],
                             [source_id])
            before = read_json(archived)
            new_limit = dict(limitation_id='source_coverage_test',
                description='The source text does not establish a crossing time.',
                actor_ids=['actor_001'], impact_domains=['actor_lane', 'actor_motion'],
                execution_effect='blocks_execution',
                source_observation_ids=['actor-frame:actor_001:frame_0016'])
            unresolved = repair.materialize(clone, dict(base_sha256=packet['base_sha256'],
                replacements=[dict(path='/semantic_limitations',
                                   value=[*before['semantic_limitations'], new_limit])]))
            self.assertEqual(len(unresolved['actors'][1]['trajectory']),
                             len(before['actors'][1]['trajectory']))
            self.assertEqual(unresolved['semantic_limitations'][-1], new_limit)
            with self.assertRaisesRegex(Exception, 'not assigned'):
                repair.materialize(clone, dict(base_sha256=packet['base_sha256'],
                    replacements=[dict(path='/actors/1/trajectory/0/lane_id', value='lane_003')]))


if __name__ == '__main__':
    unittest.main()
