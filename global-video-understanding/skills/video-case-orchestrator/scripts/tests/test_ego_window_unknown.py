"""NO-LLM scope and partial-source regressions; all inputs are synthetic."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from _lib.world.readiness import execution_dependencies, world_execution_issues, world_uncertainties
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.reconciliation_review import prepare, commit, run
from _lib.contracts.reconciliation_alignment import inspect
from test_reconciliation_review import fixture, road_candidate

PARAMETERS = dict(road_source='preview_json', fallback_policy=dict(straight_preview=dict(enabled=True)))


def scene():
    evidence = [dict(frame_id=f, time_s=t, status='observed', reason='Observed current corridor.')
                for f, t in [('start_A', 0), ('end_Z', 6)]]
    layout = dict(boundaries_right_to_left=['edge', 'stripe', 'median'], same_direction_count=dict(kind='exact', count=2, evidence_frame_ids=['start_A', 'end_Z']),
                  right_outer_boundary=dict(confirmed=True, evidence_frame_ids=['start_A']),
                  ego_initial=dict(ordinal_from_right=1, evidence_frame_ids=['start_A']))
    q = dict(question_id='future_branch', resolution='unresolved', impact_domains=['road_topology'],
             question='Unseen later branch remains unknown.', world_paths=['world.road.roads[0].lane_ids'],
             evidence=evidence, localization=dict(road_id='route_north', attribute='future_use',
                temporal_scope='after_observed_window', evidence_frame_ids=['start_A', 'end_Z'],
                observed_window=dict(time_range_s=[0, 6], lane_count_stable=True, boundary_continuity=True,
                                     no_merge_or_diverge=True, no_junction_entry=True)))
    world = dict(world=dict(scene_type='highway', junctions=[],
        metadata=dict(lane_layout_review=dict(observation=layout)), road=dict(roads=[dict(
            road_id='route_north', lane_ids=['outer', 'inner'], heading_rad=0,
            lane_knowledge=dict(anchors=[dict(lane_id='outer', ordinal_from_right=0),
                                        dict(lane_id='inner', ordinal_from_right=1)]),
            road_geometry=dict(shape_type='straight'))], lanes=[])),
        questions_to_verify=[q], ambiguities=[], field_evidence={}, actor_hypotheses=[])
    behavior = dict(actors=[dict(actor_id='ego', simulation_role='participant', is_static=False,
        trajectory=[dict(frame_id=e['frame_id'], time_s=e['time_s'], lane_id='inner') for e in evidence])],
        events=[], executor_primitives=[])
    return world, behavior


class EgoWindowUnknownTests(unittest.TestCase):
    def decision(self, world, behavior):
        return execution_dependencies(world, behavior, PARAMETERS)['decisions'][0]['decision']

    def test_unused_future_branch_is_deferred_then_excluded_without_changing_facts(self):
        w, b = scene(); original = copy.deepcopy(w)
        self.assertEqual(self.decision(w, None), 'deferred')
        self.assertEqual(self.decision(w, b), 'excluded')
        self.assertFalse(world_execution_issues(world_uncertainties(w), w, b, PARAMETERS))
        self.assertEqual(w, original)
        self.assertEqual(w['questions_to_verify'][0]['resolution'], 'unresolved')

    def test_ego_enters_branch_or_road_connection_blocks(self):
        for change in ['lane', 'road', 'connection', 'event', 'roadside']:
            with self.subTest(change=change):
                w, b = scene(); last = b['actors'][0]['trajectory'][-1]
                if change == 'lane': last['lane_id'] = 'branch_lane'
                if change == 'road': last['road_id'] = 'exit_road'
                if change == 'connection': last['connection_id'] = 'unknown_link'
                if change == 'roadside': last['roadside_area_id'] = 'exit_gore'
                if change == 'event': b['events'] = [dict(event_type='turn', involved_actor_ids=['ego'])]
                self.assertNotEqual(self.decision(w, b), 'excluded')

    def test_unlocalized_topology_can_reach_behavior_but_cannot_pass_final_gate(self):
        w, b = scene(); w['questions_to_verify'][0].pop('localization')
        self.assertEqual(self.decision(w, None), 'deferred')
        self.assertEqual(self.decision(w, b), 'required')
        self.assertTrue(world_execution_issues(world_uncertainties(w), w, b, PARAMETERS))
        w['questions_to_verify'][0]['impact_domains'] = ['lane_count']
        self.assertEqual(self.decision(w, None), 'required')

    def test_core_boundary_or_connection_unknown_still_blocks(self):
        for path in ['world.road.lanes[0].left_boundary', 'world.junctions[0].lane_connections']:
            w, b = scene(); q = w['questions_to_verify'][0]
            q['world_paths'] = [path]
            q.pop('localization')
            self.assertEqual(self.decision(w, None), 'required')
            self.assertEqual(self.decision(w, b), 'required')

    def test_missing_scope_bad_boundary_and_window_extension_block(self):
        for change in ['boundary', 'ego', 'junction']:
            w, b = scene(); q = w['questions_to_verify'][0]
            if change == 'boundary':
                q['localization']['observed_window']['boundary_continuity'] = False
                w['world']['metadata']['lane_layout_review']['observation']['right_outer_boundary']['confirmed'] = False
            if change == 'ego': w['world']['metadata']['lane_layout_review']['observation']['ego_initial'] = {}
            if change == 'junction': w['world']['junctions'] = [dict(junction_id='actual_split')]
            self.assertNotEqual(self.decision(w, None), 'deferred', change)
        w, b = scene(); b['actors'][0]['trajectory'][-1]['time_s'] = 7
        self.assertNotEqual(self.decision(w, b), 'excluded')

    def test_background_roadside_deferred_then_rechecked_for_use(self):
        w, b = scene(); q = w['questions_to_verify'][0]
        q['world_paths'] = ['world.road.roadside_areas']
        q['localization'] = dict(area_id='grass_strip', road_id='route_north')
        w['world']['road']['roadside_areas'] = [dict(area_id='grass_strip', road_id='route_north', type='grass')]
        self.assertEqual(self.decision(w, None), 'deferred')
        self.assertEqual(self.decision(w, b), 'excluded')
        b['actors'][0]['trajectory'][-1]['roadside_area_id'] = 'grass_strip'
        self.assertNotEqual(self.decision(w, b), 'excluded')
        b['actors'][0]['trajectory'][-1].pop('roadside_area_id')
        w['world']['road']['roadside_areas'][0]['type'] = 'curbside_parking'
        self.assertNotEqual(self.decision(w, b), 'excluded')

    def test_preliminary_handoff_uses_original_inventory_and_no_model(self):
        import test_reconciliation_review as helpers
        from _lib.runtime.fact_versions import establish_baseline
        from _lib.behavior.input_binding import _require_reconciliation_alignment
        for conflict in ('absent', 'unresolved', 'settled'):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as root:
                with patch.object(helpers, 'establish_baseline'):
                    case = fixture(root, actor='car_local', first='start_A', later='end_Z',
                        lanes=('outer', 'inner'), bounds=('edge', 'stripe', 'median'),
                        road_only=True, road_id='route_north', road_text='Unknown later branch.')
                w, _ = scene()
                q = w['questions_to_verify'][0]; q['question_id'] = 'question_road'
                q['evidence'][-1]['time_s'] = 2
                q['localization']['observed_window']['time_range_s'] = [0, 2]
                for path in [case/'state/world_context.json',
                             case/'diagnostics/world_census_adjudication/initial/before/world.json']:
                    value = read_json(path)
                    value['world'].update(scene_type='highway', junctions=[])
                    road = value['world']['road']['roads'][0]
                    road.update(heading_rad=0, road_geometry=dict(shape_type='straight'))
                    value['questions_to_verify'] = [copy.deepcopy(q)]
                    atomic_json(path, value)
                reconciliation = read_json(case/'state/reconciliation.json')
                reconciliation['world'] = read_json(case/'state/world_context.json')
                atomic_json(case/'state/reconciliation.json', reconciliation)
                atomic_json(case/'case.json', dict(parameters=PARAMETERS))
                if conflict != 'absent':
                    batch = read_json(case/'batches/batch_007.json')
                    batch['semantic_conflicts'] = [dict(conflict_id='conflict_link',
                        world_path='world.road.roads[0].lane_ids', proposed_value=['unknown_branch'],
                        impact_domains=['road_topology'], evidence=q['evidence'])]
                    reconciliation['unresolved_conflict_ids'] = ['conflict_link'] if conflict == 'unresolved' else []
                    atomic_json(case/'batches/batch_007.json', batch)
                atomic_json(case/'state/lane_layout.commit.json', dict(old_world_sha256=sha256(
                    case/'diagnostics/world_census_adjudication/initial/before/world.json')))
                reconciliation['batch_sha256'] = {'batch_007.json': sha256(case/'batches/batch_007.json')}
                atomic_json(case/'state/reconciliation.json', reconciliation)
                establish_baseline(case)
                class NoModel:
                    def model_stage(self, *args, **kwargs):
                        raise AssertionError('No model authorized')
                report = inspect(case)
                if conflict == 'unresolved':
                    self.assertTrue(report['blocking'])
                    with self.assertRaises(Exception): _require_reconciliation_alignment(case)
                else:
                    self.assertFalse(report['blocking'])
                    self.assertIsNone(run(NoModel(), case))
                    self.assertTrue(_require_reconciliation_alignment(case))
                    sources = [r for r in report['rows'] if r['field'] == 'road_source_text']
                    self.assertEqual(len(sources), 4)
                    self.assertTrue(all(r['fact_status'] == 'unverified' and
                        r['next_action'] == 'behavior_dependency_review' for r in sources))

    def test_direct_roadside_event_and_action_dependencies_block(self):
        for field in ('events', 'executor_primitives'):
            w, b = scene(); q = w['questions_to_verify'][0]
            q['world_paths'] = ['world.road.roadside_areas']
            q['localization'] = dict(area_id='grass_strip', road_id='route_north')
            w['world']['road']['roadside_areas'] = [dict(area_id='grass_strip', road_id='route_north', type='grass')]
            b[field] = [dict(roadside_area_id='grass_strip')]
            self.assertNotEqual(self.decision(w, b), 'excluded')

    def partial_case(self, root):
        text = 'Two observed travel lanes. Visible EXIT sign. Marking meaning unknown. Later branch unknown.'
        case = fixture(root, actor='car_local', first='frame_start', later='frame_end',
                       lanes=('outer', 'inner'), bounds=('edge', 'stripe', 'median'),
                       road_only=True, road_text=text)
        path = prepare(case); req = read_json(path); answer = road_candidate(req)
        decision = answer['road_decisions'][0]; decision['outcome'] = 'partially_explained'
        for row in decision['sources']:
            row['assessment'] = 'partial'
            row['evidence_frame_ids'] = list(dict.fromkeys([row['frame_id'], 'frame_start']))
            row['components'] = [dict(text=t, fact=f, assessment=a,
                evidence_frame_ids=row['evidence_frame_ids'], reason='Synthetic source-bound component.')
                for t, f, a in [('Two observed travel lanes. ', 'current_layout', 'covered'),
                    ('Visible EXIT sign. ', 'visible_sign', 'covered'),
                    ('Marking meaning unknown. ', 'marking_interpretation', 'unresolved'),
                    ('Later branch unknown.', 'future_topology', 'unresolved')]]
        output = path.with_name(path.name.replace('.request.json', '.candidate.json'))
        return case, path, output, answer

    def test_partial_source_keeps_components_and_never_covers_whole_source(self):
        with tempfile.TemporaryDirectory() as root:
            case, path, output, answer = self.partial_case(root)
            before = read_json(case/'state/world_context.json')
            atomic_json(output, answer); result = commit(case, path, output)
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(read_json(case/'state/world_context.json'), before)
            report = inspect(case)
            rows = [r for r in report['rows'] if r['field'] == 'road_source_text']
            self.assertTrue(all(r['fact_status'] == 'partially_confirmed' for r in rows))
            self.assertTrue(report['blocking'])  # No temporal scope was supplied.
            self.assertTrue(all(len(r['sources'][0]['components']) == 4 for r in rows))
            class NoModel:
                def model_stage(self, *args, **kwargs):
                    raise AssertionError('A partial answer must not cause another model task')
            with self.assertRaisesRegex(Exception, 'new evidence is required'):
                run(NoModel(), case)

    def test_partial_source_omission_forgery_and_unseen_confirmation_rejected(self):
        for error in ['omission', 'future', 'frame', 'layout', 'contradiction']:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as root:
                case, path, output, answer = self.partial_case(root)
                row = answer['road_decisions'][0]['sources'][0]
                if error == 'omission': row['components'].pop()
                if error == 'future': row['components'][-1]['assessment'] = 'covered'
                if error == 'frame': row['components'][1]['evidence_frame_ids'] = []
                if error == 'layout': row['components'][0]['evidence_frame_ids'] = ['frame_end']
                if error == 'contradiction': row['components'][-1]['assessment'] = 'contradictory'
                atomic_json(output, answer)
                with self.assertRaises(Exception): commit(case, path, output)


if __name__ == '__main__':
    unittest.main()
