"""NO-LLM checks for active source alignment and opaque-text reporting."""
import copy
from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior import input_binding
from _lib.contracts.reconciliation_alignment import inspect
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.fact_versions import current_facts

ROOT = SKILLS.parents[1]/'video_extractor/cases'
FAILED = ROOT/'case_20261019T175555560Z_1f7ff9e6'
SUCCESS = ROOT/'case_20261011T231731758Z_8adeacad'
SUCCESS_MOTION = ROOT/'case_20261019T013937315Z_78d1326e'


class ReconciliationAlignmentTests(unittest.TestCase):
    def synthetic(self, root, *, disagree=True, spatial=True, wording='车轮贴近右侧标线，边界不清楚。'):
        case = Path(root)/'unseen_video'
        (case/'state').mkdir(parents=True)
        (case/'batches').mkdir()
        before = case/'diagnostics/world_census_adjudication/initial/before/world.json'
        before.parent.mkdir(parents=True)
        def world(lane, ordinal):
            return dict(world=dict(road=dict(roads=[dict(lane_knowledge=dict(anchors=[
                dict(lane_id='lane_a', ordinal_from_right=0),
                dict(lane_id='lane_b', ordinal_from_right=1)]))]),
                metadata=dict(lane_layout_review=dict(observation=dict(ego_initial=dict(
                    evidence_frame_ids=['image_start'], ordinal_from_right=ordinal))))),
                actor_hypotheses=[dict(actor_id='vehicle_Z', first_seen_frame_id='image_start',
                                       hypothesized_lane=lane)],
                questions_to_verify=[dict(question_id='question_x',
                    actor_ids=['vehicle_Z'],
                    impact_domains=['actor_lane' if spatial else 'actor_motion'])])
        old = world('lane_b', 1)
        new = world('lane_a' if disagree else 'lane_b', 0 if disagree else 1)
        atomic_json(before, old)
        atomic_json(case/'state/lane_layout.commit.json', dict(old_world_sha256=sha256(before)))
        atomic_json(case/'state/world_context.json', new)
        atomic_json(case/'state/actor_registry.json', [dict(actor_id='vehicle_Z')])
        atomic_json(case/'state/global_frames.json', dict(frames=[
            dict(frame_id='image_start', time_s=0)]))
        batch_path = case/'batches/segment_one.json'
        atomic_json(batch_path, dict(registry_after=[dict(actor_id='vehicle_Z',
                    first_seen_frame_id='image_start', hypothesized_lane='lane_b')],
                    vehicle_census=[dict(frame_id='image_start', time_s=0,
                        vehicles=[dict(binding='existing', actor_id='vehicle_Z',
                                       object_id='vehicle_object', description='同向浅色车。')])],
                    observations=[dict(question_id='question_x', frame_id='image_start',
                                       reason=wording)]))
        timeline = dict(fact_version_id=None, batch_sha256={str(batch_path):sha256(batch_path)},
                        groups=[dict(actor_id='vehicle_Z', frame_id='image_start',
                            sources=[dict(source_id='segment_one#/observations/0/reason',
                                          kind='observation_source_reason', text=wording)])],
                        unresolved=[])
        return case, timeline

    def test_varied_ids_and_chinese_text_are_not_silently_aligned(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case, timeline = self.synthetic(root)
            result = inspect(case, timeline=timeline)
            blocked = [result['rows'][i] for i in result['blocking']]
            self.assertTrue(any(r['actor_id'] == 'vehicle_Z' and r['frame_id'] == 'image_start'
                                and r['field'] == 'actor_lane' for r in blocked))
            self.assertTrue(any(r['field'] == 'source_text' and r['fact_status'] == 'unverified'
                                for r in blocked))
            self.assertFalse(result['motion_obligation_ids'])

    def test_mapping_preserves_physical_lane_and_cross_time_is_not_conflict(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case, timeline = self.synthetic(root, disagree=False, spatial=False,
                                             wording='位置在下一时刻改变。')
            world_path = case/'state/world_context.json'
            world = read_json(world_path)
            anchors = world['world']['road']['roads'][0]['lane_knowledge']['anchors']
            anchors[0]['lane_id'], anchors[1]['lane_id'] = 'corridor_x', 'corridor_y'
            world['actor_hypotheses'][0]['hypothesized_lane'] = 'corridor_y'
            atomic_json(world_path, world)
            later = copy.deepcopy(timeline['groups'][0])
            later['frame_id'] = 'image_later'
            timeline['groups'].append(later)
            result = inspect(case, timeline=timeline)
            self.assertFalse(result['blocking'])
            self.assertTrue(result['unverified_text'])
            self.assertFalse(any(r['fact_status'] == 'conflict' for r in result['rows']))

    def test_english_text_has_same_unverified_result_without_phrase_parser(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case, timeline = self.synthetic(root, disagree=False,
                wording='The wheel patch overlaps a fading painted boundary.')
            packet = inspect(case, timeline=timeline)
            self.assertTrue(any(packet['rows'][i]['field'] == 'source_text'
                                for i in packet['blocking']))

    def test_new_policy_indexes_question_bound_text_without_actor_spelling(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case, _ = self.synthetic(root, wording='轮迹靠近边界，单帧无法确定。')
            atomic_json(case/'manifest.json', dict(batches=[dict(batch_id='segment_one', frames=[])]))
            atomic_json(case/'state/execution_policy.json', dict(
                reconciliation_alignment='reconciliation-alignment.v1',
                behavior_evidence_timeline='actor-time-evidence.v1',
                behavior_input_binding='behavior-input.v1'))
            timeline = input_binding.evidence_timeline(case)
            group = next(g for g in timeline['groups'] if g['actor_id'] == 'vehicle_Z')
            self.assertTrue(any(s['kind'] == 'observation_source_reason'
                                and s['text'] == '轮迹靠近边界，单帧无法确定。'
                                for s in group['sources']))
            batch_path = case/'batches/segment_one.json'
            batch = read_json(batch_path)
            batch['observations'].append(dict(question_id='question_no_actor',
                frame_id='image_start', reason='未标识车辆靠近另一道边界。'))
            atomic_json(batch_path, batch)
            timeline = input_binding.evidence_timeline(case)
            self.assertTrue(any(s['source_binding'] == 'observation_actor_unbound'
                                and s['text'] == '未标识车辆靠近另一道边界。'
                                for s in timeline['unresolved']))
            self.assertTrue(any(r['next_action'] == 'source_binding_review'
                                for r in inspect(case, timeline=timeline)['rows']))
            world_path = case/'state/world_context.json'
            world = read_json(world_path)
            world['questions_to_verify'].append(dict(question_id='question_road',
                actor_ids=[], impact_domains=['lane_count']))
            atomic_json(world_path, world)
            batch['observations'].append(dict(question_id='question_road',
                frame_id='image_start', reason='远端边界线条无法逐一追到路面。'))
            atomic_json(batch_path, batch)
            packet = inspect(case)
            self.assertTrue(any(packet['rows'][i]['field'] == 'source_text'
                                and packet['rows'][i]['actor_id'] is None
                                for i in packet['blocking']))
            with self.assertRaisesRegex(Exception, 'unresolved execution-relevant facts'):
                input_binding.freeze(case)
            self.assertFalse((case/'state/behavior_input.request.json').exists())

    def test_mapping_that_changes_physical_lane_is_blocked(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case, timeline = self.synthetic(root, disagree=False, spatial=False)
            world_path = case/'state/world_context.json'
            world = read_json(world_path)
            world['world']['metadata']['lane_layout_review']['adjudication'] = dict(
                lane_id_map={'lane_b': 'lane_a'})
            atomic_json(world_path, world)
            packet = inspect(case, timeline=timeline)
            self.assertTrue(any(packet['rows'][i]['field'] == 'lane_id_map'
                                for i in packet['blocking']))

    def test_timeline_version_and_batch_hash_must_match_effective_inputs(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case, timeline = self.synthetic(root)
            stale = copy.deepcopy(timeline)
            stale['fact_version_id'] = 'older_version'
            with self.assertRaisesRegex(ValueError, 'another fact version'):
                inspect(case, timeline=stale)
            stale = copy.deepcopy(timeline)
            path = next(iter(stale['batch_sha256']))
            stale['batch_sha256'][path] = '0'*64
            with self.assertRaisesRegex(ValueError, 'Batch source changed'):
                inspect(case, timeline=stale)

    def test_archived_case_finds_world_dispute_and_pickup_text_gap(self):
        if not FAILED.is_dir():
            self.skipTest('Archived Case unavailable')
        packet = inspect(FAILED)
        blocked = [packet['rows'][i] for i in packet['blocking']]
        self.assertTrue(any(r['actor_id'] == 'ego' and r['frame_id'] == 'frame_0001'
                            and r['field'] == 'actor_lane' for r in blocked))
        self.assertTrue(any(r['actor_id'] == 'actor_001' and r['frame_id'] == 'frame_0001'
                            and r['field'] == 'actor_lane' for r in blocked))
        self.assertTrue(any(r['actor_id'] == 'actor_001' and r['frame_id'] == 'frame_0016'
                            and r['field'] == 'source_text' for r in blocked))
        self.assertTrue(any(r['actor_id'] == 'actor_001' and r['frame_id'] == 'frame_0022'
                            and r['field'] == 'source_text' for r in blocked))
        self.assertTrue(any(r['actor_id'] == 'actor_001' and r['frame_id'] == 'frame_0013'
                            and r['field'] == 'source_text' for r in packet['rows']))
        with patch('_lib.contracts.reconciliation_alignment.enabled', return_value=True), \
             patch('_lib.behavior.input_binding.timeline_enabled', return_value=True):
            with self.assertRaisesRegex(Exception, 'unresolved execution-relevant facts'):
                input_binding._require_reconciliation_alignment(FAILED)

    def test_same_frame_claim_disagreement_is_found_without_new_fact_store(self):
        if not FAILED.is_dir():
            self.skipTest('Archived Case unavailable')
        view = current_facts(FAILED)
        claims = dict(view.claims)
        for number, lane in enumerate(('lane_001', 'lane_002')):
            claims[f'probe_{number}'] = dict(actor_id='actor_001',
                frame_id='frame_0001', field='actor_lane', value=lane,
                claim_id=f'probe_{number}')
        with patch('_lib.contracts.reconciliation_alignment.current_facts',
                   return_value=replace(view, claims=claims)):
            packet = inspect(FAILED)
        self.assertTrue(any(packet['rows'][i]['reason'] ==
                            'Accepted claims disagree on one physical lane'
                            for i in packet['blocking']))

    def test_archived_success_has_no_blocking_alignment(self):
        for case in (SUCCESS, SUCCESS_MOTION):
            if not case.is_dir():
                continue
            with self.subTest(case=case.name):
                packet = inspect(case)
                self.assertEqual(packet['blocking'], [])
                self.assertGreater(len(packet['unverified_text']), 0)


if __name__ == '__main__':
    unittest.main()
