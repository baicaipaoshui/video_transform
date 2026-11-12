"""NO-LLM World point review through the existing fact and Behavior handoff."""
import sys
import tempfile
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.input_binding import evidence_timeline
from _lib.contracts.core_lane_continuity import obligations
from _lib.contracts.reconciliation_alignment import inspect
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.fact_versions import (commit_reconciliation_lane_points,
                                         current_facts, establish_baseline)
from _lib.runtime.reconciliation_review import commit, prepare, run


def fixture(root, *, actor, first, later, lanes, bounds, batch_only=False,
            road_only=False, road_fact='lane_count_boundary', road_text='Road edge is unclear.',
            road_id='road_main'):
    case = Path(root)/f'case_{actor}'
    (case/'state').mkdir(parents=True)
    (case/'batches').mkdir()
    (case/'frames').mkdir()
    before = case/'diagnostics/world_census_adjudication/initial/before/world.json'
    before.parent.mkdir(parents=True)
    frames = []
    for frame_id, time_s in ((first, 0.0), (later, 2.0)):
        path = case/'frames'/f'{frame_id}.jpg'
        path.write_bytes(f'original image {frame_id}'.encode())
        frames.append(dict(frame_id=frame_id, time_s=time_s, path=str(path),
                           source_path=str(path), sha256=sha256(path)))
    anchors = [dict(lane_id=lane, ordinal_from_right=index)
               for index, lane in enumerate(lanes)]

    def world(actor_lane):
        road_data = dict(roads=[dict(road_id=road_id, lane_ids=list(lanes),
                                    lane_knowledge=dict(anchors=anchors))])
        if road_only and road_fact == 'road_boundary':
            road_data['lane_markings'] = [dict(marking_id=bounds[0], road_id=road_id)]
        if road_only and road_fact == 'shoulder_presence':
            road_data['roadside_areas'] = [dict(area_id='area_right', road_id=road_id,
                                                side='right', type='shoulder')]
        layout = dict(boundaries_right_to_left=bounds,
            same_direction_count=dict(kind='exact', count=len(lanes), evidence_frame_ids=[first]),
            right_outer_boundary=dict(confirmed=True, evidence_frame_ids=[first]),
            right_roadside=dict(type='shoulder', evidence_frame_ids=[first]),
            ego_initial=dict(evidence_frame_ids=[first], ordinal_from_right=0,
                             right_boundary_id=bounds[0], left_boundary_id=bounds[1]))
        questions = [dict(question_id='question_lane', resolution='unresolved',
                          actor_ids=[actor], impact_domains=['actor_lane'])]
        field_evidence = {f'world.actors.{actor}.hypothesized_lane': [dict(
            frame_id=first, time_s=0.0, status='observed', reason='Original lane boundary pair.')]}
        if road_only:
            path = ('world.road.roadside_areas' if road_fact == 'shoulder_presence'
                    else 'world.road.lane_markings' if road_fact == 'road_boundary'
                    else 'world.road.roads[0].lane_ids')
            questions.append(dict(question_id='question_road', resolution='unresolved',
                                  impact_domains=['road_topology' if road_fact == 'shoulder_presence'
                                                  else 'lane_count'], world_paths=[path], evidence=[]))
            field_evidence[path] = [dict(frame_id=first, status='observed',
                                         reason='Visible physical road evidence.')]
        return dict(world=dict(road=road_data, metadata=dict(lane_layout_review=dict(
            observation=layout,
            adjudication=dict(lane_id_map={})))),
            actor_hypotheses=[dict(actor_id=actor, first_seen_frame_id=first,
                                   hypothesized_lane=actor_lane)],
            field_evidence=field_evidence, questions_to_verify=questions)

    original = world(lanes[1] if road_only else lanes[2]); effective = world(lanes[1])
    atomic_json(before, original)
    atomic_json(case/'state/lane_layout.commit.json', dict(old_world_sha256=sha256(before)))
    atomic_json(case/'state/world_context.json', effective)
    atomic_json(case/'state/actor_registry.json', [dict(actor_id=actor,
        first_seen_frame_id=first, hypothesized_lane=lanes[1])])
    atomic_json(case/'state/global_frames.json', dict(frames=[] if batch_only else frames))
    batch = dict(batch_id='batch_007', semantic_conflicts=[],
        registry_after=[dict(actor_id=actor, first_seen_frame_id=first,
                             hypothesized_lane=lanes[1] if road_only else lanes[2])],
        vehicle_census=[dict(frame_id=frame_id, time_s=time_s, vehicles=[dict(
            binding='existing', actor_id=actor, object_id='same_vehicle',
            description=('Vehicle at original boundary pair.' if frame_id == first else
                         f'wheel area between {bounds[1]} and {bounds[2]} rows'))])
            for frame_id, time_s in ((first, 0.0), (later, 2.0))],
        observations=([dict(question_id='question_road', frame_id=frame_id,
                            reason=road_text, evidence=[dict(frame_id=frame_id,
                                time_s=time_s, status='unresolved', reason=road_text)])
                       for frame_id, time_s in ((first, 0.0), (later, 2.0))]
                      if road_only else [dict(question_id='question_lane', frame_id=first,
                                              reason='车轮位于原始边界之间，需要单帧复核。')]))
    batch_path = case/'batches/batch_007.json'
    atomic_json(batch_path, batch)
    atomic_json(case/'manifest.json', dict(batches=[dict(
        batch_id='batch_007', frames=frames if batch_only else [])]))
    atomic_json(case/'state/reconciliation.json', dict(status='reconciled', world=effective,
        batch_sha256={batch_path.name: sha256(batch_path)}, unresolved_conflict_ids=[]))
    atomic_json(case/'state/execution_policy.json', dict(
        reconciliation_alignment='reconciliation-alignment.v1',
        behavior_evidence_timeline='actor-time-evidence.v1',
        fact_versions='case-facts.v1'))
    establish_baseline(case)
    return case


def candidate(request, *, outcome='revised', ordinal=2):
    decisions = []
    for group in request['groups']:
        if outcome == 'evidence_insufficient':
            decisions.append(dict(dispute_id=group['dispute_id'], outcome=outcome,
                                  reason='Original frame does not show a decisive boundary.'))
            continue
        lane = request['lane_by_ordinal'][str(ordinal)]
        decisions.append(dict(dispute_id=group['dispute_id'], outcome=outcome,
            actor_id=group['actor_id'], frame_id=group['frame_id'], lane_id=lane,
            ordinal_from_right=ordinal,
            right_boundary_id=request['boundaries_right_to_left'][ordinal],
            left_boundary_id=request['boundaries_right_to_left'][ordinal + 1],
            reviewed_source_ids=group['source_ids'],
            evidence_frame_ids=[group['frame_id']],
            reason='Mock original-frame observation identifies the physical boundary pair.'))
    return dict(request_sha256=request['request_sha256'], decisions=decisions)


def road_candidate(request, *, outcome='explained'):
    decisions = []
    for group in request['road_groups']:
        visible = group['layout_evidence_frame_ids'][0]
        decisions.append(dict(dispute_id=group['dispute_id'], outcome=outcome,
            road_id=group['road_id'], road_fact=group['road_fact'], same_segment=True,
            reason='The accepted layout evidence covers the same road segment.',
            sources=[dict(source_id=source['source_id'], frame_id=source['frame_id'],
                assessment=('covered' if source['frame_id'] == visible else 'visibility_limited')
                           if outcome == 'explained' else
                           'contradictory' if outcome == 'contradiction' else 'unresolved',
                evidence_frame_ids=[visible] if outcome == 'explained' else [source['frame_id']],
                reason='Source frame is retained; visible layout frame is cited.')
                     for source in group['sources']]))
    return dict(request_sha256=request['request_sha256'], decisions=[], road_decisions=decisions)


class MockWorld:
    def __init__(self, outcome='revised'):
        self.calls = []
        self.outcome = outcome

    def model_stage(self, case, key, agent, prompt, adopt, precheck, candidate_path):
        self.calls.append((key, agent))
        request = read_json(candidate_path.with_name(
            candidate_path.name.replace('.candidate.json', '.request.json')))
        atomic_json(candidate_path, candidate(request, outcome=self.outcome))
        adopt(None)


class MockRoadWorld(MockWorld):
    def model_stage(self, case, key, agent, prompt, adopt, precheck, candidate_path):
        self.calls.append((key, agent))
        request = read_json(candidate_path.with_name(
            candidate_path.name.replace('.candidate.json', '.request.json')))
        atomic_json(candidate_path, road_candidate(request))
        adopt(None)


class ReconciliationReviewTests(unittest.TestCase):
    def test_road_sources_grouped_explained_and_rechecked_without_model(self):
        variants = (
            ('lane_count_boundary', ('lane_R', 'lane_L'), ('edge_R', 'stripe', 'edge_L'),
             '远处右侧车道边界被遮挡，无法确认是否还有车道。', 'image_03', 'image_19', 'route_A'),
            ('shoulder_presence', ('outside', 'center', 'inside', 'passing'),
             ('r0', 'r1', 'r2', 'r3', 'r4'),
             'The roadside shoulder is obscured in this view.', 'scene_A', 'scene_Z', 'route_B'),
            ('road_boundary', ('outer', 'middle', 'inner'),
             ('line_outer', 'mark_a', 'mark_b', 'line_inner'),
             'The right solid edge line is partly outside the image.', 'capture_2', 'capture_31', 'route_C'),
        )
        for kind, lanes, bounds, wording, first, later, road_id in variants:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir='/private/tmp') as root:
                case = fixture(root, actor='car_sample', first=first, later=later,
                               lanes=lanes, bounds=bounds, road_only=True,
                               road_fact=kind, road_text=wording, road_id=road_id)
                before = current_facts(case)
                blocked = inspect(case)
                self.assertEqual(len([blocked['rows'][i] for i in blocked['blocking']
                                      if blocked['rows'][i]['field'] == 'road_source_text']), 4)
                path = prepare(case); request = read_json(path)
                self.assertEqual(request['revision'], 'reconciliation-world-point-review.v2')
                self.assertEqual(request['groups'], [])
                self.assertEqual(len(request['road_groups']), 1)
                group = request['road_groups'][0]
                self.assertEqual(group['road_id'], road_id)
                self.assertEqual(len(group['sources']), 4)
                self.assertEqual({source['frame_id'] for source in group['sources']}, {first, later})
                self.assertTrue(all(source['batch_sha256'] for source in group['sources']))
                runner = MockRoadWorld()
                result = run(runner, case)
                self.assertEqual(result['status'], 'committed')
                self.assertEqual(runner.calls, [('world_closure', 'global-video-understanding')])
                output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
                self.assertEqual(commit(case, path, output), result)
                self.assertIsNone(run(runner, case))
                self.assertEqual(current_facts(case).version_id, before.version_id)
                after = inspect(case)
                self.assertFalse(after['blocking'])
                self.assertEqual(len([row for row in after['rows']
                                      if row['field'] == 'road_source_text' and row['fact_status'] == 'explained']), 4)
                self.assertEqual(read_json(case/'state/world_context.json')['world']['road']['roads'][0]['lane_ids'],
                                 list(lanes))

    def test_road_conflict_insufficient_and_stale_source_stay_blocked(self):
        for outcome in ('contradiction', 'evidence_insufficient'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory(dir='/private/tmp') as root:
                case = fixture(root, actor='car_local', first='frame_8', later='frame_42',
                               lanes=('right', 'left'), bounds=('b0', 'b1', 'b2'),
                               road_only=True, road_text='Right edge not fully visible.')
                path = prepare(case); request = read_json(path)
                output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
                atomic_json(output, road_candidate(request, outcome=outcome))
                result = commit(case, path, output)
                self.assertEqual(result['status'], 'needs_review')
                self.assertTrue(inspect(case)['blocking'])
                self.assertEqual(current_facts(case).version_id, request['parent_version'])
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='car_local', first='frame_8', later='frame_42',
                           lanes=('right', 'left'), bounds=('b0', 'b1', 'b2'), road_only=True)
            path = prepare(case); request = read_json(path)
            output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
            atomic_json(output, road_candidate(request))
            source = case/'batches/batch_007.json'
            source.write_bytes(source.read_bytes() + b' ')
            with self.assertRaisesRegex(Exception, 'source changed'):
                commit(case, path, output)

    def test_unproved_shoulder_is_retained_for_behavior_dependency(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='car_local', first='frame_12', later='frame_75',
                           lanes=('outer', 'inner'), bounds=('edge', 'split', 'median'),
                           road_only=True, road_fact='shoulder_presence',
                           road_text='The outer roadside is obscured.')
            path = prepare(case); request = read_json(path)
            output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
            atomic_json(output, road_candidate(request, outcome='evidence_insufficient'))
            result = commit(case, path, output)
            self.assertEqual(result['status'], 'needs_review')
            self.assertTrue(result['deferred_dependency'])
            report = inspect(case)
            self.assertFalse(report['blocking'])
            retained = [row for row in report['rows'] if row['field'] == 'road_source_text']
            self.assertEqual(len(retained), 4)
            self.assertTrue(all(row['fact_status'] == 'unverified'
                                and row['next_action'] == 'behavior_dependency_review'
                                for row in retained))
            self.assertEqual(current_facts(case).world['questions_to_verify'][-1]['resolution'],
                             'unresolved')
            from _lib.world.readiness import execution_dependencies
            world = current_facts(case).world
            independent = dict(actors=[], events=[], executor_primitives=[])
            decisions = execution_dependencies(world, independent)['decisions']
            self.assertEqual(next(row['decision'] for row in decisions
                                  if row['issue_id'] == 'question_road'), 'excluded')
            dependent = dict(actors=[dict(actor_id='car_local', roadside_area_id='area_right')],
                             events=[], executor_primitives=[])
            decisions = execution_dependencies(world, dependent)['decisions']
            self.assertEqual(next(row['decision'] for row in decisions
                                  if row['issue_id'] == 'question_road'), 'undetermined')

    def test_road_explanation_cannot_promote_an_occluded_frame(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='car_local', first='visible_5', later='occluded_88',
                           lanes=('outer', 'inner'), bounds=('edge', 'split', 'median'),
                           road_only=True, road_fact='shoulder_presence')
            path = prepare(case); request = read_json(path)
            output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
            answer = road_candidate(request)
            later = next(row for row in answer['road_decisions'][0]['sources']
                         if row['frame_id'] == 'occluded_88')
            later['assessment'] = 'covered'
            atomic_json(output, answer)
            with self.assertRaisesRegex(Exception, 'own visible frame'):
                commit(case, path, output)
            self.assertTrue(inspect(case)['blocking'])

    def test_automatic_request_acceptance_recheck_and_motion(self):
        variants = (
            ('vehicle_Z', 'image_open', 'image_tail', ('roadside', 'centre', 'passing'),
             ('edgeR', 'markA', 'markB', 'edgeL')),
            ('car_87', 'sample_alpha', 'sample_omega', ('R', 'M', 'L'),
             ('right_edge', 'dash_a', 'dash_b', 'left_edge')),
        )
        for actor, first, later, lanes, bounds in variants:
            with self.subTest(actor=actor), tempfile.TemporaryDirectory(dir='/private/tmp') as root:
                case = fixture(root, actor=actor, first=first, later=later,
                               lanes=lanes, bounds=bounds)
                original_batch = sha256(case/'batches/batch_007.json')
                original_world = sha256(case/'diagnostics/world_census_adjudication/initial/before/world.json')
                before = current_facts(case)
                self.assertTrue(inspect(case)['blocking'])
                request = read_json(prepare(case))
                self.assertEqual(request['groups'][0]['actor_id'], actor)
                self.assertEqual(request['groups'][0]['frame']['frame_id'], first)
                self.assertTrue(request['groups'][0]['batch_sources'])
                self.assertTrue(request['groups'][0]['original_actor'])
                self.assertEqual(request['lane_by_ordinal']['2'], lanes[2])
                runner = MockWorld()
                result = run(runner, case)
                after = current_facts(case)
                self.assertEqual(runner.calls, [('world_closure', 'global-video-understanding')])
                self.assertEqual(result['status'], 'committed')
                self.assertNotEqual(before.version_id, after.version_id)
                self.assertFalse(inspect(case)['blocking'])
                self.assertEqual(after.world['actor_hypotheses'][0]['hypothesized_lane'], lanes[2])
                self.assertEqual(after.registry[0]['hypothesized_lane'], lanes[2])
                self.assertEqual(sha256(case/'batches/batch_007.json'), original_batch)
                self.assertEqual(sha256(case/'diagnostics/world_census_adjudication/initial/before/world.json'),
                                 original_world)
                timeline, motion = evidence_timeline(case), obligations(case)
                self.assertEqual(timeline['fact_version_id'], after.version_id)
                self.assertEqual(motion['fact_version_id'], after.version_id)
                self.assertEqual([(row['source_frame_id'], row['target_frame_id'])
                                  for row in motion['obligations']], [(first, later)])
                self.assertIsNone(run(runner, case))
                self.assertEqual(len(runner.calls), 1)
                request_path = next((case/'diagnostics/question_review').glob('alignment-*.request.json'))
                output = request_path.with_name(request_path.name.replace('.request.json', '.candidate.json'))
                result_path = request_path.with_name(request_path.name.replace('.request.json', '.result.json'))
                result_path.unlink()  # Simulate a crash after the active pointer switch.
                self.assertEqual(commit(case, request_path, output)['effective_version'], after.version_id)
                self.assertTrue(result_path.is_file())

    def test_insufficient_conflict_stale_source_and_no_duplicate_review(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='other_vehicle', first='at_start', later='at_end',
                           lanes=('right', 'middle', 'left'), bounds=('b0', 'b1', 'b2', 'b3'))
            old = current_facts(case)
            request_path = prepare(case)
            request = read_json(request_path)
            output = request_path.with_name(request_path.name.replace('.request.json', '.candidate.json'))
            atomic_json(output, candidate(request, outcome='evidence_insufficient'))
            result = commit(case, request_path, output)
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(current_facts(case).version_id, old.version_id)
            with self.assertRaisesRegex(Exception, 'new evidence is required'):
                run(MockWorld(), case)
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='cab_Y', first='start_view', later='end_view',
                           lanes=('right_0', 'center_1', 'left_2'),
                           bounds=('line_0', 'line_1', 'line_2', 'line_3'))
            runner = MockWorld(outcome='evidence_insufficient')
            with self.assertRaisesRegex(Exception, 'not closed'):
                run(runner, case)
            self.assertEqual(len(runner.calls), 1)
            with self.assertRaisesRegex(Exception, 'new evidence is required'):
                run(runner, case)
            self.assertEqual(len(runner.calls), 1)
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='vehicle_123', first='first_seen', later='last_seen',
                           lanes=('outer', 'center', 'inner'), bounds=('c0', 'c1', 'c2', 'c3'))
            old = current_facts(case)
            path = prepare(case); request = read_json(path)
            output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
            wrong = candidate(request, ordinal=2)
            wrong['decisions'][0]['right_boundary_id'] = 'foreign_boundary'
            atomic_json(output, wrong)
            with self.assertRaisesRegex(Exception, 'physical boundaries'):
                commit(case, path, output)
            self.assertEqual(current_facts(case).version_id, old.version_id)
            self.assertEqual(read_json(prepare(case))['request_sha256'], request['request_sha256'])
            source = case/'batches/batch_007.json'
            source.write_bytes(source.read_bytes() + b' ')
            with self.assertRaisesRegex(Exception, 'source changed'):
                commit(case, path, output)
            self.assertEqual(current_facts(case).version_id, old.version_id)

    def test_confirmed_and_stale_parent(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='bus_Q', first='pic_11', later='pic_72',
                           lanes=('lane_R', 'lane_M', 'lane_L'),
                           bounds=('edge_0', 'dash_1', 'dash_2', 'edge_3'))
            path = prepare(case); request = read_json(path)
            output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
            atomic_json(output, candidate(request, outcome='confirmed', ordinal=1))
            result = commit(case, path, output)
            self.assertEqual(result['status'], 'committed')
            self.assertFalse(inspect(case)['blocking'])
            self.assertEqual(current_facts(case).world['actor_hypotheses'][0][
                'hypothesized_lane'], 'lane_M')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='truck_W', first='f_initial', later='f_later',
                           lanes=('lane_0', 'lane_1', 'lane_2'),
                           bounds=('b_0', 'b_1', 'b_2', 'b_3'))
            path = prepare(case); request = read_json(path)
            output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
            atomic_json(output, candidate(request))
            parent = current_facts(case)
            lane_point = dict(actor_id='truck_W', frame_id='f_later', lane_id='lane_1',
                              ordinal_from_right=1, right_boundary_id='b_1',
                              left_boundary_id='b_2', source_path='batches/batch_007.json',
                              source_sha256=sha256(case/'batches/batch_007.json'),
                              reason='Separate later original frame.')
            commit_reconciliation_lane_points(case, parent.version_id, [lane_point])
            with self.assertRaisesRegex(Exception, 'old fact version'):
                commit(case, path, output)

    def test_batch_only_original_frame_is_reviewable(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='van_K', first='local_frame_A', later='local_frame_B',
                           lanes=('shoulder', 'drive', 'pass'),
                           bounds=('r_edge', 'line_1', 'line_2', 'l_edge'),
                           batch_only=True)
            runner = MockWorld()
            result = run(runner, case)
            self.assertEqual(result['status'], 'committed')
            self.assertFalse(inspect(case)['blocking'])
            self.assertEqual(runner.calls, [('world_closure', 'global-video-understanding')])

    def test_point_task_does_not_bind_an_old_census_closure(self):
        from _lib.contracts.census_adjudication import INFORMATION_REVISIONS
        from _lib.runtime.census_task_accounting import binding
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            case = fixture(root, actor='wagon_T', first='frame_A', later='frame_B',
                           lanes=('corridor_0', 'corridor_1', 'corridor_2'),
                           bounds=('mark_0', 'mark_1', 'mark_2', 'mark_3'))
            old_request = case/'diagnostics/world_census_adjudication/initial/request.json'
            old_request.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(old_request, dict(revision=sorted(INFORMATION_REVISIONS)[0],
                                          request_sha256='0'*64))
            class Backend:
                def submit(self):
                    pass
                def poll(self):
                    pass
            class Runner:
                backend = Backend()
            runner = Runner()
            self.assertIsNotNone(binding(runner, case, 'world_closure', dict(attempts={})))
            runner._reconciliation_point_task = True
            self.assertIsNone(binding(runner, case, 'world_closure', dict(attempts={})))


if __name__ == '__main__':
    unittest.main()
