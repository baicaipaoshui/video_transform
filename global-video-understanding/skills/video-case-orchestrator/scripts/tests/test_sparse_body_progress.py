"""NO-LLM regression checks for source-bound sparse body progress."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.lane_change import actor_name, lane_names
from _lib.behavior.source_revision import QUALITATIVE, REVISION as SOURCE_REVISION
from _lib.contracts import motion_witnesses as witnesses, semantic_motion as motion
from _lib.contracts.conversion_policy import digest
from _lib.delivery.motion_check import decode
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.conversion_work import ConversionWork, STAGES


def source_fixture(vehicle_class='sedan', side='right'):
    aid = 'vehicle_other'
    details = [f'{vehicle_class} body side on ego\'s {side}',
               f'{vehicle_class} front side on ego\'s {side}; pass not complete']
    points = [dict(time_s=t, frame_id=f'frame_{i}', detail=detail,
                   source_observation_ids=[f'obs_{i}'])
              for i, (t, detail) in enumerate(zip((2., 4.), details))]
    limits = [dict(actor_ids=[aid, 'ego'], description='Body position has no measured distance.',
                   **{QUALITATIVE: dict(revision=SOURCE_REVISION,
                       representation='qualitative_only', completion='unconfirmed',
                       actor_id=aid, reference_actor_id='ego', frame_id=point['frame_id'],
                       time_s=point['time_s'], source_observation_id=f'obs_{i}',
                       behavior_detail_sha256=digest(point['detail']),
                       sources=[dict(text=point['detail'], status='observed',
                                     source_id=f'source_{i}')])})
              for i, point in enumerate(points)]
    actor = dict(actor_id=aid, vehicle_class=vehicle_class, trajectory=points,
                 identity_description=vehicle_class)
    ego = dict(actor_id='ego', vehicle_class='sedan', trajectory=[],
               identity_description='Ego')
    analysis = dict(ego=ego, actors=[actor],
                    road=dict(lanes=[], roads=[dict(lane_ids=['lane_001', 'lane_002'])]),
                    events=[], limitations=limits)
    request = dict(eligible=[ego, actor], evidence=motion.evidence(analysis, [ego, actor]),
                   duration_s=4.1, events=[], analysis_sha256=digest(analysis),
                   bounded_execution=dict(source_record={'registry': [],
                                                         'single_frame_motion': {}},
                                          lane_change=None, sources={}, relations=[]))
    return analysis, request


def target_for(analysis, side):
    names = lane_names(analysis)
    ego_lane, other_lane = ('lane_002', 'lane_001') if side == 'right' else ('lane_001', 'lane_002')
    def vehicle(aid, lane):
        return dict(name=actor_name(aid), timeline=[dict(type='drive', action=dict(
            start_position=dict(position=dict(reference_name=names[lane]))))])
    return dict(scene=dict(road_network=dict(roads=[dict(segments=[dict(lanes=[
        dict(name=name, width=3.5) for name in names.values()])])])),
        scenario=dict(vehicles=[vehicle('ego', ego_lane), vehicle('vehicle_other', other_lane)]))


class SparseBodyProgressTests(unittest.TestCase):
    def test_description_b_reuses_sparse_source_witness_without_core_certificate(self):
        analysis, request = source_fixture('bus', 'right')
        analysis['metadata'] = {'description_fallback': {'delivery_level': 'unverified'}}
        request.pop('bounded_execution')
        body = witnesses.description_body(analysis, request)
        self.assertEqual(body['constraints'], witnesses.build(analysis, dict(
            request, bounded_execution=dict(source_record={'registry': [], 'single_frame_motion': {}},
                                            lane_change=None, sources={}, relations=[])))['constraints'])
        self.assertEqual(body['constraints'][0]['completion'], 'in_progress')
        request[witnesses.KEY] = body
        request['policy'] = {witnesses.POLICY_KEY: witnesses.REVISION}
        coverage, relations = witnesses.complete(request, [], [])
        motion.validate_relations(request, coverage, relations, partial=True)
        self.assertEqual({row['status'] for row in coverage}, {'qualitative_only'})
        with self.assertRaisesRegex(ValueError, 'Body-side Target relation reversed'):
            witnesses.check(request, {'ego': [(0., 4.1, -5., 22.5, 0.)],
                                      'vehicle_other': [(0., 4.1, 0., 20., 0.)]},
                            target=target_for(analysis, 'left'), analysis=analysis)
        analysis['limitations'][1][QUALITATIVE]['sources'][0]['status'] = 'unknown'
        request['evidence'] = motion.evidence(analysis, request['eligible'])
        self.assertEqual(witnesses.qualitative_body_constraints(analysis, request)[0], [])

    def test_description_b_conversion_work_completes_reserved_witness_sources(self):
        analysis, request = source_fixture('bus', 'right')
        analysis['metadata'] = {'description_fallback': {'delivery_level': 'unverified'}}
        request.pop('bounded_execution')
        request[witnesses.KEY] = witnesses.description_body(analysis, request)
        request['policy'] = {witnesses.POLICY_KEY: witnesses.REVISION}
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = Path(directory)
            atomic_json(case/'final/analysis.json', analysis)
            request['analysis_sha256'] = sha256(case/'final/analysis.json')
            request[witnesses.KEY]['analysis_sha256'] = request['analysis_sha256']
            request['request_sha256'] = digest({k: v for k, v in request.items()
                                                if k != 'request_sha256'})
            atomic_json(case/'state/conversion_semantic_gap.request.json', request)
            owner = SimpleNamespace(progress=lambda _case: {'sessions': []})
            work = ConversionWork(owner, case, request)
            entry = work.state['stages'].setdefault(STAGES[0], {'runs': []})
            work.new_run(STAGES[0], entry)
            reserved = set(request[witnesses.KEY]['receipts'])
            worker = dict(complete=True, request_sha256=request['request_sha256'], relations=[],
                          coverage=[dict(source=e['source'],
                                         status='qualitative_only' if e.get('usage') == 'qualitative_only'
                                         else 'no_motion', reason='Synthetic source classification')
                                    for e in request['evidence'] if e['source'] not in reserved])
            accepted = work.root/'accepted'/f'{digest(worker)}.json'
            atomic_json(accepted, worker)
            entry.update(complete=True, checkpoint=dict(sha256=sha256(accepted),
                content_sha256=digest(worker), request_sha256=worker['request_sha256']))
            work.persist()
            completed = work.finish_stage(STAGES[0])
            self.assertEqual({row['source'] for row in completed['coverage']},
                             {row['source'] for row in request['evidence']})
            self.assertEqual({row['status'] for row in completed['coverage']
                              if row['source'] in reserved}, {'qualitative_only'})

    def test_sparse_source_preserves_parts_side_and_qualitative_coverage(self):
        for vehicle_class, side, length in [('sedan', 'right', 5.), ('bus', 'left', 12.)]:
            with self.subTest(vehicle_class=vehicle_class, side=side):
                analysis, request = source_fixture(vehicle_class, side)
                record = witnesses.build(analysis, request)
                self.assertEqual(len(record['constraints']), 1)
                body = record['constraints'][0]
                self.assertEqual((body['actor_id'], body['reference_actor_id']),
                                 ('vehicle_other', 'ego'))
                self.assertEqual([(w['time_s'], w['part'], w['side'])
                                  for w in body['witnesses']],
                                 [(2., 'side', side), (4., 'front', side)])
                self.assertEqual(body['assumed_length_m'], length)
                self.assertEqual(body['completion'], 'in_progress')
                self.assertIn('simulation assumptions', body['assumption'])
                request[witnesses.KEY] = record
                request['policy'] = {witnesses.POLICY_KEY: witnesses.REVISION}
                coverage, relations = witnesses.complete(request, [], [])
                self.assertEqual(relations, [])
                self.assertEqual({row['status'] for row in coverage}, {'qualitative_only'})
                motion.validate_relations(request, coverage, relations, partial=True)
                bad_relation = dict(type='alongside', actor_id='ego',
                                    reference_actor_id='vehicle_other',
                                    time_interval_s=[2., 2.],
                                    evidence=[body['witnesses'][0]['evidence']])
                with self.assertRaisesRegex(ValueError, 'whole-vehicle'):
                    motion.validate_relations(request, coverage, [bad_relation], partial=True)
                self.assertTrue(list(witnesses.candidates(request, 'vehicle_other', 80., [0.])))

    def test_one_point_ambiguous_or_unobserved_remains_qualitative(self):
        for kind in ('one_point', 'ambiguous_side', 'unobserved'):
            with self.subTest(kind=kind):
                analysis, _ = source_fixture()
                if kind == 'one_point':
                    analysis['limitations'].pop()
                elif kind == 'ambiguous_side':
                    point = analysis['actors'][0]['trajectory'][1]
                    point['detail'] = point['detail'].replace("ego's right", 'ego nearby')
                    analysis['limitations'][1][QUALITATIVE]['behavior_detail_sha256'] = digest(point['detail'])
                else:
                    analysis['limitations'][1][QUALITATIVE]['sources'][0]['status'] = 'unknown'
                _, request = source_fixture()
                request['eligible'] = [analysis['ego'], analysis['actors'][0]]
                request['evidence'] = motion.evidence(analysis, request['eligible'])
                self.assertEqual(witnesses.build(analysis, request)['constraints'], [])

    def test_conversion_work_discloses_assumptions_without_upgrading_source(self):
        analysis, request = source_fixture()
        request[witnesses.KEY] = witnesses.build(analysis, request)
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = Path(directory)
            atomic_json(case / 'final/analysis.json', analysis)
            request['analysis_sha256'] = sha256(case / 'final/analysis.json')
            request['request_sha256'] = digest(request)
            atomic_json(case / 'state/conversion_semantic_gap.request.json', request)
            owner = SimpleNamespace(progress=lambda _case: {'sessions': []})
            work = ConversionWork(owner, case, request)
            stage = work.new_run(STAGES[0], {'runs': []})['request']
            self.assertIn('body_progress', stage['qualitative_source_constraints']['instruction'])
            self.assertIn('coverage remains qualitative_only',
                          stage['qualitative_source_constraints']['instruction'])
            self.assertIn('qualitative_only',
                          stage['program_owned_witness_receipts']['instruction'])
            self.assertEqual(len(stage['qualitative_source_constraints']['source_paths']), 6)

    def test_target_side_and_unfinished_pass_are_checked(self):
        for vehicle_class, side, start, speed in [('sedan', 'right', -2., 21.),
                                                   ('bus', 'left', -5., 22.5)]:
            with self.subTest(vehicle_class=vehicle_class, side=side):
                analysis, request = source_fixture(vehicle_class, side)
                request[witnesses.KEY] = witnesses.build(analysis, request)
                profiles = {'ego': [(0., 4.1, start, speed, 0.)],
                            'vehicle_other': [(0., 4.1, 0., 20., 0.)]}
                result = witnesses.check(request, profiles,
                    target=target_for(analysis, side), analysis=analysis)
                self.assertTrue(result[0]['lateral_checked'])
                with self.assertRaisesRegex(ValueError, 'Body-side Target relation reversed'):
                    witnesses.check(request, profiles,
                        target=target_for(analysis, 'left' if side == 'right' else 'right'),
                        analysis=analysis)
                if vehicle_class == 'sedan':
                    extended = copy.deepcopy(request)
                    extended['duration_s'] = 10.
                    completed = {'ego': [(0., 10., start, speed, 0.)],
                                 'vehicle_other': [(0., 10., 0., 20., 0.)]}
                    with self.assertRaisesRegex(ValueError, 'Unfinished pass became completed'):
                        witnesses.check(extended, completed)

    def test_reversed_motion_and_wrong_body_region_are_rejected(self):
        analysis, request = source_fixture('bus')
        request[witnesses.KEY] = witnesses.build(analysis, request)
        profiles = {'ego': [(0., 4.1, -5., 22.5, 0.)],
                    'vehicle_other': [(0., 4.1, 0., 20., 0.)]}
        reversed_request = copy.deepcopy(request)
        for row in reversed_request[witnesses.KEY]['constraints'][0]['witnesses']:
            row['part'] = 'side'
        reversed_profiles = {'ego': [(0., 4.1, 6., 18.5, 0.)],
                             'vehicle_other': profiles['vehicle_other']}
        with self.assertRaisesRegex(ValueError, 'Passing progression reversed'):
            witnesses.check(reversed_request, reversed_profiles)
        request[witnesses.KEY]['constraints'][0]['witnesses'][0]['part'] = 'rear'
        with self.assertRaisesRegex(ValueError, 'Body-part witness lost'):
            witnesses.check(request, profiles)

    def test_conflicting_stage_order_does_not_create_numeric_constraint(self):
        analysis, request = source_fixture('bus')
        points = analysis['actors'][0]['trajectory']
        points[0]['detail'], points[1]['detail'] = points[1]['detail'], points[0]['detail']
        for index, point in enumerate(points):
            record = analysis['limitations'][index][QUALITATIVE]
            record['behavior_detail_sha256'] = digest(point['detail'])
            record['sources'][0]['text'] = point['detail']
        request['eligible'] = [analysis['ego'], analysis['actors'][0]]
        request['evidence'] = motion.evidence(analysis, request['eligible'])
        self.assertEqual(witnesses.build(analysis, request)['constraints'], [])

    def test_complete_four_phase_witness_still_plans_and_checks(self):
        body = dict(type='body_progress', actor_id='vehicle_other', reference_actor_id='ego',
                    witnesses=[dict(time_s=t, part=part) for t, part in
                               [(1., 'rear'), (2., 'middle_rear'),
                                (4., 'middle_front'), (6., 'cab')]],
                    assumed_length_m=12., tolerance_m=2., completion='unconfirmed',
                    assumption='Vehicle geometry is a simulation assumption.')
        request = dict(duration_s=7., eligible=[dict(actor_id='ego', vehicle_class='sedan'),
                                                dict(actor_id='vehicle_other', vehicle_class='bus')],
                       motion_witnesses=dict(constraints=[body]))
        self.assertTrue(list(witnesses.candidates(request, 'vehicle_other', 80., [0.])))
        result = witnesses.check(request, {'ego': [(0., 7., -6., 22., 0.)],
                                           'vehicle_other': [(0., 7., 0., 20., 0.)]})
        self.assertFalse(result[0]['lateral_checked'])
        self.assertEqual(result[0]['completion'], 'unconfirmed')

    def test_distinct_historical_video_keeps_gap_and_rejects_false_overtake(self):
        repo = SKILLS.parents[1]
        case = repo / 'video_extractor/cases/case_20261004T170101907Z_5ed9c0a3'
        analysis = read_json(case / 'final/analysis.json')
        target = read_json(case / 'final/target.json')
        duration = analysis['metadata']['duration_s']
        vehicles = {row['name']: row for row in target['scenario']['vehicles']}
        profiles = {aid: decode(vehicles[actor_name(aid)], duration)
                    for aid in ('ego', 'actor_001')}
        stable = dict(type='stable_gap', actor_id='actor_001', reference_actor_id='ego',
                      interval_s=[6., duration], tolerance_m=1.)
        self.assertLess(witnesses.check({witnesses.KEY: {'constraints': [stable]}},
                                       profiles)[0]['drift_m'], 1e-6)
        with self.assertRaisesRegex(ValueError, 'relation failed'):
            motion.check_relations(dict(eligible=[analysis['ego'], analysis['actors'][0]]),
                [dict(type='overtakes', actor_id='ego', reference_actor_id='actor_001',
                      time_interval_s=[6., duration])], profiles)


if __name__ == '__main__':
    unittest.main()
