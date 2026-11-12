"""NO-LLM motion obligation and Behavior checkpoint regressions."""
import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.input_binding import write_behavior
from _lib.contracts.core_lane_continuity import obligations, validate
from _lib.contracts.core_lane_continuity import validate_analysis_motion
from _lib.behavior.compile import (evidence_frames, prepare_behavior_candidate,
                                   validate_compiled_behavior)
from _lib.stages.delivery import CaseDelivery
from _lib.runtime.v3 import V3Orchestrator
from _lib.runtime import core_approximate_delivery as core, description_fallback as fallback
from _lib.candidate_validation import validate_behavior_candidate
from _lib.runtime.case_core import atomic_json, read_json, sha256


ROOT = SKILLS.parents[1]
FAILED = ROOT/'video_extractor/cases/case_20261019T175555560Z_1f7ff9e6'
SUCCESS = ROOT/'video_extractor/cases/case_20261019T013937315Z_78d1326e'
COMPLETED = ('8adeacad', 'b274b884', 'c2042c33')


def fixture(root, *, road_transition=False, lane_remap=False, identity_dispute=False):
    case = root/'case_motion'
    (case/'state').mkdir(parents=True)
    (case/'batches').mkdir()
    lanes = [('lane_001', 0, 'road_001'),
             ('lane_002', 1, 'road_002' if road_transition else 'road_001')]
    roads = []
    for road_id in sorted({row[2] for row in lanes}):
        roads.append(dict(road_id=road_id, lane_knowledge=dict(anchors=[
            dict(lane_id=lane_id, ordinal_from_right=ordinal)
            for lane_id, ordinal, owner in lanes if owner == road_id])))
    world = dict(layout_observation=dict(boundaries_right_to_left=['curb', 'B1', 'B2']),
                 world=dict(road=dict(roads=roads), metadata=dict(lane_layout_review=dict(
                     adjudication=dict(lane_id_map=(
                         {'lane_001': 'lane_x', 'lane_002': 'lane_x'} if lane_remap else {}))))),
                 actor_hypotheses=[dict(actor_id='actor_001', hypothesized_lane='lane_001')],
                 field_evidence={'world.actors.actor_001.hypothesized_lane': [
                     dict(status='observed', frame_id='frame_a', time_s=0, reason='first lane')]},
                 questions_to_verify=([dict(resolution='unresolved',
                     impact_domains=['actor_identity'], actor_ids=['actor_001'],
                     evidence=[dict(frame_id='frame_b')])] if identity_dispute else []))
    atomic_json(case/'state/world_context.json', world)
    atomic_json(case/'state/actor_registry.json', [dict(actor_id='actor_001')])
    atomic_json(case/'state/global_frames.json', dict(frames=[
        dict(frame_id='frame_a', time_s=0), dict(frame_id='frame_b', time_s=1)]))
    atomic_json(case/'batches/batch_0001.json', dict(vehicle_census=[dict(
        frame_id='frame_b', time_s=1, vehicles=[dict(binding='existing',
            actor_id='actor_001', object_id='object_b',
            description='lane_id=lane_002 ordinal_from_right=1')])]))
    atomic_json(case/'state/reconciliation.json', dict(status='reconciled',
        batch_sha256={'batch_0001.json': sha256(case/'batches/batch_0001.json')}))
    return case


def behavior(case):
    row = obligations(case)['obligations'][0]
    return dict(ego=dict(actor_id='ego', trajectory=[]), actors=[dict(actor_id='actor_001',
        trajectory=[dict(frame_id='frame_a', lane_id=row['source_lane_id_effective']),
                    dict(frame_id='frame_b', lane_id=row['target_lane_id_effective'])])],
        events=[], semantic_limitations=[])


def limitation(case, kind, effect='blocks_execution'):
    row = obligations(case)['obligations'][0]
    return dict(limitation_id='motion_gap', description='Source anchors do not establish the mechanism.',
                actor_ids=['actor_001'], impact_domains=['actor_lane', 'actor_motion'],
                execution_effect=effect, motion_explanation=kind,
                motion_obligation_id=row['obligation_id'],
                source_observation_id=row['source_observation_id'],
                target_observation_id=row['target_observation_id'])


class MotionObligationsTests(unittest.TestCase):
    def test_physical_crossing_requires_anchors_and_event(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            data = behavior(case)
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate(case, data)
            data['events'] = [dict(event_type='lane_change', involved_actor_ids=['actor_001'],
                                   from_lane_id='lane_001', to_lane_id='lane_002')]
            self.assertTrue(validate(case, data))
            self.assertEqual(validate(case, data, return_dispositions=True)[0]['resolution'],
                             'physical_crossing')
            data['actors'][0]['trajectory'][0]['lane_id'] = 'lane_002'
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate(case, data)

    def test_road_transition_requires_distinct_source_roads_and_event(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory), road_transition=True)
            data = behavior(case)
            data['semantic_limitations'] = [limitation(case, 'road_transition')]
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate(case, data)
            data['actors'][0]['trajectory'][0]['road_id'] = 'road_001'
            data['actors'][0]['trajectory'][1]['road_id'] = 'road_002'
            data['events'] = [dict(event_type='road_transition', involved_actor_ids=['actor_001'],
                                   from_road_id='road_001', to_road_id='road_002')]
            self.assertTrue(validate(case, data))
            self.assertEqual(validate(case, data, return_dispositions=True)[0]['resolution'],
                             'road_transition')

    def test_lane_remap_needs_effective_mapping_and_explicit_disposition(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory), lane_remap=True)
            data = behavior(case)
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate(case, data)
            data['semantic_limitations'] = [limitation(case, 'lane_remap')]
            self.assertTrue(validate(case, data))
            self.assertEqual(validate(case, data, return_dispositions=True)[0]['resolution'],
                             'lane_remap')

    def test_identity_dispute_needs_existing_unresolved_identity_question(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory), identity_dispute=True)
            data = behavior(case)
            data['actors'][0]['trajectory'] = data['actors'][0]['trajectory'][1:]
            data['semantic_limitations'] = [limitation(case, 'identity_dispute')]
            self.assertTrue(validate(case, data))
            self.assertEqual(validate(case, data, return_dispositions=True)[0]['resolution'],
                             'identity_dispute')
            world_path = case/'state/world_context.json'
            world = read_json(world_path); world['questions_to_verify'] = []
            atomic_json(world_path, world)
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate(case, data)

    def test_insufficient_evidence_keeps_unknown_but_cannot_erase_anchors(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            data = behavior(case)
            data['semantic_limitations'] = [limitation(case, 'insufficient_evidence',
                                                       'bounded_uncertainty')]
            self.assertTrue(validate(case, data))
            self.assertEqual(validate(case, data, return_dispositions=True)[0]['resolution'],
                             'insufficient_evidence')
            data['actors'][0]['trajectory'][0]['lane_id'] = 'lane_002'
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate(case, data)

    def test_source_and_mapping_changes_invalidate_derived_packet_and_old_explanation(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            data = behavior(case)
            data['semantic_limitations'] = [limitation(case, 'insufficient_evidence')]
            old = obligations(case)
            world_path = case/'state/world_context.json'
            world = read_json(world_path)
            world['world']['metadata']['lane_layout_review']['adjudication']['lane_id_map'] = {
                'lane_001': 'lane_x', 'lane_002': 'lane_x'}
            atomic_json(world_path, world)
            self.assertNotEqual(obligations(case)['sha256'], old['sha256'])
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate(case, data)
            registry = case/'state/actor_registry.json'
            prior = obligations(case)['sha256']
            atomic_json(registry, [dict(actor_id='actor_001', identity_revision='new')])
            self.assertNotEqual(obligations(case)['sha256'], prior)
            batch = case/'batches/batch_0001.json'
            value = read_json(batch); value['provenance'] = 'new source'
            atomic_json(batch, value)
            with self.assertRaisesRegex(Exception, 'differ from accepted reconciliation'):
                obligations(case)

    def test_fact_version_change_during_validation_is_rejected(self):
        from _lib.runtime.fact_versions import FactVersionError
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            data = behavior(case)
            data['semantic_limitations'] = [limitation(case, 'insufficient_evidence')]
            packet = obligations(case)
            packet['fact_version_id'] = 'v000001'
            pointer = case/'state/fact_versions/active.json'
            atomic_json(pointer, dict(version_id='v000001'))
            with (patch('_lib.contracts.core_lane_continuity.obligations', return_value=packet),
                  patch('_lib.runtime.fact_versions.current_facts',
                        side_effect=FactVersionError('Fact version changed during stage execution'))):
                with self.assertRaisesRegex(FactVersionError, 'Fact version changed'):
                    validate(case, data)

    def test_formal_writer_rejects_before_overwrite_and_accepts_explained_unknown(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            target = case/'state/behavior.json'
            original = dict(marker='prior accepted bytes')
            atomic_json(target, original)
            data = behavior(case)
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                write_behavior(case, data)
            self.assertEqual(read_json(target), original)
            data['semantic_limitations'] = [limitation(case, 'insufficient_evidence')]
            write_behavior(case, data)
            self.assertEqual(read_json(target), data)

    def test_derived_copy_validates_completed_parent_before_child_write(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)
            parent = fixture(root)
            child = root/'derived_case'
            (child/'state').mkdir(parents=True)
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                write_behavior(child, behavior(parent), source_case=parent)
            self.assertFalse((child/'state/behavior.json').exists())
            data = behavior(parent)
            data['semantic_limitations'] = [limitation(parent, 'insufficient_evidence')]
            write_behavior(child, data, source_case=parent)
            self.assertEqual(read_json(child/'state/behavior.json'), data)

    def test_preview_world_and_behavior_partial_commit_can_revalidate(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            proposed = read_json(case/'state/world_context.json')
            proposed['world']['metadata']['preview_reviewed'] = True
            data = behavior(case)
            row = obligations(case, world_override=proposed)['obligations'][0]
            explanation = limitation(case, 'insufficient_evidence')
            explanation['motion_obligation_id'] = row['obligation_id']
            data['semantic_limitations'] = [explanation]
            write_behavior(case, data, world_override=proposed)
            atomic_json(case/'state/world_context.json', proposed)
            write_behavior(case, data, world_override=proposed)
            self.assertEqual(read_json(case/'state/behavior.json'), data)

    def test_1f7ff9e6_isolated_copy_rejects_original_omission(self):
        if not FAILED.is_dir():
            self.skipTest('Archived Case 1f7ff9e6 unavailable')
        protected = [FAILED/'state/progress.json', FAILED/'state/behavior.candidate.json',
                     FAILED/'batches/batch_0003.json', FAILED/'batches/batch_0004.json']
        hashes = {str(path): sha256(path) for path in protected}
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            clone = Path(directory)/FAILED.name
            shutil.copytree(FAILED, clone, symlinks=True)
            packet = obligations(clone)
            self.assertEqual([(row['source_frame_id'], row['target_frame_id'])
                              for row in packet['obligations']], [('frame_0016', 'frame_0022')])
            candidate = read_json(clone/'state/behavior.candidate.json')
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                write_behavior(clone, candidate)
            from _lib.candidate_validation import validate_behavior_candidate
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                validate_behavior_candidate(clone/'state/behavior.candidate.json', clone,
                                            observation_only=True)
            self.assertFalse((clone/'state/behavior.json').exists())
        self.assertEqual({str(path): sha256(path) for path in protected}, hashes)

    def test_existing_success_case_retains_legacy_behavior(self):
        if not SUCCESS.is_dir():
            self.skipTest('Archived successful Case unavailable')
        self.assertEqual(obligations(SUCCESS)['obligations'], [])
        self.assertTrue(validate(SUCCESS, read_json(SUCCESS/'state/behavior.candidate.json')))
        for suffix in COMPLETED:
            with self.subTest(suffix=suffix):
                case = next((ROOT/'video_extractor/cases').glob('case_*'+suffix), None)
                if case is None:
                    self.skipTest('Completed archived Case '+suffix+' unavailable')
                self.assertIn(read_json(case/'state/progress.json')['status'], {'done', 'done_degraded'})
                self.assertTrue(validate(case, read_json(case/'state/behavior.json')))

    def test_archived_lossless_shape_recovery_and_conflicts(self):
        for suffix in ('b274b884', 'c2042c33'):
            with self.subTest(suffix=suffix):
                case = next(ROOT.glob('video_extractor/cases/case_*'+suffix))
                original = read_json(case/'state/behavior.json')
                world = read_json(case/'state/world_context.json')
                registry = read_json(case/'state/actor_registry.json')
                frames = evidence_frames(case)
                wrapped = copy.deepcopy(original)
                actor = next(row for row in wrapped['actors'] if row['actor_id'] == 'actor_001')
                actor['trajectory'] = {'samples': actor['trajectory']}
                prepared, receipts = prepare_behavior_candidate(wrapped, frames, registry, world,
                                                                 case=case)
                self.assertEqual(prepared['actors'], original['actors'])
                self.assertEqual(prepared['events'], original['events'])
                self.assertEqual(actor['trajectory'], {'samples': next(row for row in original['actors']
                    if row['actor_id'] == 'actor_001')['trajectory']})
                repair = next(row for row in receipts if row.get('rule_id') == 'behavior.lossless_shape.v1')
                self.assertIn('/actors/1/trajectory', repair['changed_fields'])
                self.assertNotEqual(repair['before_sha256'], repair['after_sha256'])
                validate_compiled_behavior(prepared, world, registry, frames, execution=False,
                                           allow_pending_identity=True)
                ambiguous_wrapper = copy.deepcopy(wrapped)
                next(row for row in ambiguous_wrapper['actors'] if row['actor_id'] == 'actor_001')[
                    'trajectory']['metadata'] = 'unsupported sibling'
                with self.assertRaises(Exception):
                    prepare_behavior_candidate(ambiguous_wrapper, frames, registry, world,
                                               case=case)
                missing = copy.deepcopy(original); missing.pop('challenge_resolutions')
                blocked, receipts = prepare_behavior_candidate(missing, frames, registry, world,
                                                               case=case)
                self.assertNotIn('challenge_resolutions', blocked)
                self.assertFalse(any('/challenge_resolutions' in r.get('changed_fields', [])
                                     for r in receipts))
                with self.assertRaises(Exception):
                    validate_compiled_behavior(blocked, world, registry, frames, execution=False)
                no_challenge_world = copy.deepcopy(world)
                no_challenge_world['ambiguities'] = []
                defaulted, receipts = prepare_behavior_candidate(missing, frames, registry,
                                                                   no_challenge_world, case=case)
                self.assertEqual(defaulted['challenge_resolutions'], [])
                self.assertEqual(defaulted['actors'], original['actors'])
                self.assertEqual(defaulted['events'], original['events'])
                self.assertTrue(any('/challenge_resolutions' in r.get('changed_fields', [])
                                    for r in receipts))
                for kind in ('identity', 'lane'):
                    bad = copy.deepcopy(original)
                    item = next(row for row in bad['actors'] if row['actor_id'] == 'actor_001')
                    if kind == 'identity':
                        item['identity_description'] = 'Different vehicle.'
                    else:
                        item['trajectory'][0]['lane_id'] = 'lane_003'
                    with self.assertRaises(Exception):
                        prepared, _ = prepare_behavior_candidate(bad, frames, registry, world,
                                                                 case=case)
                        validate_compiled_behavior(prepared, world, registry, frames,
                                                   execution=False, allow_pending_identity=True)
                bad_frame = copy.deepcopy(original)
                bad_frame['actors'][0]['trajectory'][0]['frame_id'] = 'nonexistent_frame'
                with self.assertRaises(Exception):
                    prepare_behavior_candidate(bad_frame, frames, registry, world, case=case)

    def test_accepted_events_survive_analysis_and_completed_replay(self):
        for suffix in ('b274b884', 'c2042c33'):
            with self.subTest(suffix=suffix):
                case = next(ROOT.glob('video_extractor/cases/case_*'+suffix))
                source = read_json(case/'state/behavior.json')
                analysis = read_json(case/'final/analysis.json')
                self.assertEqual(validate_analysis_motion(case, source, analysis), [])
                self.assertTrue(CaseDelivery.validate_completed(
                    case, read_json(case/'state/progress.json')))
                damaged = copy.deepcopy(analysis)
                damaged['events'] = [row for row in damaged['events']
                                     if row.get('event_type') != 'overtaking']
                with self.assertRaisesRegex(Exception, 'Accepted core motion'):
                    validate_analysis_motion(case, source, damaged)

    def test_completed_degraded_case_keeps_its_accepted_motion(self):
        case = next(ROOT.glob('video_extractor/cases/case_*b17213eb'), None)
        if case is None:
            self.skipTest('Archived degraded Case unavailable')
        source = read_json(case/'state/behavior.json')
        analysis = read_json(case/'final/analysis.json')
        self.assertEqual(validate_analysis_motion(case, source, analysis), [])
        self.assertTrue(CaseDelivery.validate_completed(
            case, read_json(case/'state/progress.json')))
        damaged = copy.deepcopy(analysis)
        damaged['events'] = []
        with self.assertRaisesRegex(Exception, 'Accepted core motion'):
            validate_analysis_motion(case, source, damaged)

    def test_strict_conversion_entry_checks_motion_before_converter(self):
        case = next(ROOT.glob('video_extractor/cases/case_*b274b884'))
        original = sha256(case/'state/progress.json')
        runner = V3Orchestrator(object())
        class GateSeen(Exception):
            pass
        with (patch.object(runner, 'save_progress'),
              patch('_lib.contracts.core_lane_continuity.validate_analysis_motion',
                    side_effect=GateSeen),
              patch('_lib.stages.delivery.subprocess.run',
                    side_effect=AssertionError('Converter must not run'))):
            with self.assertRaises(GateSeen):
                runner.strict_convert(case)
        self.assertEqual(sha256(case/'state/progress.json'), original)

    def test_current_isolated_core_and_description_entries_use_shape_recovery(self):
        source = next(ROOT.glob('audit_reports/behavior_recovery_20261022/'
                                'current_variants/nested_trajectory/case_*'), None)
        if source is None:
            self.skipTest('Isolated 1f7ff9e6 shape variant unavailable')
        original = sha256(source/'state/progress.json')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)
            with self.assertRaises(Exception) as caught:
                core._project(source, root/'core')
            self.assertEqual(getattr(caught.exception, 'rule_id', None),
                             'core_scene.motion_continuity')
            result = fallback.prepare(source, root/'description')
            self.assertEqual(result['status'], 'needs_core_motion')
            self.assertFalse((root/'description/output/target.json').exists())
        self.assertEqual(sha256(source/'state/progress.json'), original)

    def test_strict_candidate_validation_entry_uses_shape_recovery(self):
        for suffix in ('b274b884', 'c2042c33'):
            with self.subTest(suffix=suffix):
                case = next(ROOT.glob('video_extractor/cases/case_*'+suffix))
                candidate = ROOT/'audit_reports/behavior_recovery_20261022/mutations'/suffix/'nested_trajectory.json'
                original = sha256(case/'state/behavior.json')
                repairs = []
                def capture(*args, **kwargs):
                    result = prepare_behavior_candidate(*args, **kwargs)
                    repairs.extend(result[1])
                    return result
                with patch('_lib.candidate_validation.prepare_behavior_candidate', side_effect=capture):
                    with self.assertRaisesRegex(Exception, 'Scope review omits required'):
                        validate_behavior_candidate(candidate, case, observation_only=True)
                self.assertTrue(any(row.get('rule_id') == 'behavior.lossless_shape.v1'
                                    for row in repairs))
                self.assertEqual(sha256(case/'state/behavior.json'), original)

    def test_cached_core_projection_rechecks_analysis_motion(self):
        source = next(ROOT.glob('video_extractor/cases/case_*a2219ae2'), None)
        if source is None:
            self.skipTest('Archived core projection unavailable')
        root = ROOT/'video_extractor/core_approximate_deliveries'/source.name
        receipt = root/'inputs/projection_receipt.json'
        if not receipt.is_file():
            self.skipTest('Archived core projection receipt unavailable')
        original = sha256(receipt)
        class GateSeen(Exception):
            pass
        with patch('_lib.contracts.core_lane_continuity.validate_analysis_motion',
                   side_effect=GateSeen):
            with self.assertRaises(GateSeen):
                core._project(source, root)
        self.assertEqual(sha256(receipt), original)

    def test_formal_commit_paths_use_the_shared_motion_writer(self):
        stage = (SKILLS/'_lib/stages/behavior.py').read_text(encoding='utf-8')
        review = (SKILLS/'_lib/behavior/actor_motion_review.py').read_text(encoding='utf-8')
        self.assertNotIn('atomic_json(target,', stage)
        self.assertNotIn("atomic_json(case/'state/behavior.json'", stage)
        self.assertNotIn('atomic_json(target,', review)
        self.assertEqual(stage.count('write_behavior(case,'), 6)  # preview, candidate, supplements, replay, lane review
        self.assertEqual(review.count('write_behavior(case,'), 1)
        for relative in ('_lib/runtime/scene_revision.py', '_lib/runtime/identity_revision.py'):
            self.assertIn('write_behavior(', (SKILLS/relative).read_text(encoding='utf-8'))

    def test_actor_review_checks_motion_before_its_commit(self):
        from _lib.behavior import actor_motion_review
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            with patch.object(actor_motion_review, 'validate_compiled_behavior'):
                with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                    actor_motion_review._validate_case(case, behavior(case), [])

    def test_completed_review_resumes_recheck_motion(self):
        from _lib.behavior import actor_motion_review
        from _lib.stages.behavior import BehaviorStage
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = fixture(Path(directory))
            target = case/'state/behavior.json'
            atomic_json(target, behavior(case))
            progress = dict(diagnostics={
                'preview_review': dict(status='completed'),
                'lane_review': dict(status='completed', actor_id='actor_001',
                                    behavior_sha256=sha256(target))})
            stage = BehaviorStage()
            stage.progress = lambda _case: progress
            stage.save_progress = lambda _case, _progress: None
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                stage.review_preview_relations(case)
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                stage.review_actor_lane(case, 'actor_001')
            atomic_json(case/'case.json', dict(parameters=dict(workflow_version='v3')))
            accepted = case/'diagnostics/actor_motion_review_actor_001/accepted.json'
            atomic_json(accepted, dict(result_sha256=sha256(target)))
            with self.assertRaisesRegex(Exception, 'supported motion explanation'):
                actor_motion_review.run(stage, case, 'actor_001')


if __name__ == '__main__':
    unittest.main()
