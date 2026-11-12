"""Source retractions preserve facts and cannot manufacture an executable relation."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_core_source_repair as fixtures
from _lib.behavior import repair, source_revision
from _lib.contracts import semantic_motion as motion, core_scene_scope
from _lib.contracts.core_lane_continuity import validate_source_coverage
from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.contracts import motion_witnesses, bounded_execution
from _lib.runtime.conversion_work import ConversionWork, STAGES, validate_save


class SourceWithdrawalTests(unittest.TestCase):
    def packets(self, suffix='803e13de', change=lambda value: None):
        return fixtures.CoreSourceRepairTests()._repair_packet(suffix, 'ego', change)

    def response(self, packet):
        return dict(base_sha256=packet['base_sha256'], replacements=[], source_dispositions=[
            dict(point_index=p['point_index'], action='withdraw',
                 reason='No same-frame Ego source exists; retain other Actor observations independently.')
            for p in packet['source_revision_authorization']['eligible_points']])

    def test_withdrawal_retains_actions_other_actors_and_qualitative_sources(self):
        for case, original, timeline, packet in self.packets():
            raw = self.response(packet)
            revised = repair.materialize(case, raw)
            self.assertTrue(validate_source_coverage(case, revised, timeline=timeline))
            self.assertEqual(original['actors'][1:], revised['actors'][1:])
            self.assertEqual(original['events'], revised['events'])
            self.assertEqual(original['executor_primitives'], revised['executor_primitives'])
            removed = {d['point_index'] for d in raw['source_dispositions']}
            self.assertEqual(revised['actors'][0]['trajectory'],
                [p for i,p in enumerate(original['actors'][0]['trajectory']) if i not in removed])
            self.assertEqual(revised['actors'][0]['identity_description'], source_revision.SUMMARY)
            qualitative = [l for l in revised['semantic_limitations'] if source_revision.QUALITATIVE in l]
            self.assertEqual(len(qualitative), 2)
            for limit in qualitative:
                record = limit[source_revision.QUALITATIVE]
                self.assertNotEqual(record['actor_id'], 'ego')
                self.assertIn(record['actor_id'], core_scene_scope.required_actors({}, revised, ['ego']))
                group = next(g for g in timeline['groups'] if g['source_observation_id'] == record['source_observation_id'])
                self.assertTrue(all(s in group['sources'] for s in record['sources']))
            before = sha256(packet['base_path'])
            receipt = Path(source_revision.archive(case, raw, revised))
            self.assertEqual(sha256(packet['base_path']), before)
            source_revision.verify_archive(case, receipt.relative_to(case), sha256(receipt))
            changed = read_json(receipt.parent/'behavior.json'); changed['events'] = []
            atomic_json(receipt.parent/'behavior.json', changed)
            with self.assertRaisesRegex(ValueError, 'revision changed'):
                source_revision.verify_archive(case, receipt.relative_to(case), sha256(receipt))

    def test_authority_is_required_and_cannot_remove_an_event_anchor(self):
        for case, original, timeline, packet in self.packets():
            raw = self.response(packet)
            for protected in packet['source_revision_authorization']['protected_point_indices']:
                bad = copy.deepcopy(raw)
                bad['source_dispositions'][0]['point_index'] = protected
                with self.assertRaisesRegex(ValueError, 'authorized points'):
                    repair.materialize(case, bad)
            bad = copy.deepcopy(raw)
            bad['replacements'] = [dict(path='/events/0', value={})]
            with self.assertRaisesRegex(ValueError, 'combine'):
                repair.materialize(case, bad)
            packet.pop('source_revision_authorization'); packet.pop('request_sha256')
            atomic_json(case/'state/behavior_repair.request.json', packet)
            with self.assertRaisesRegex(ValueError, 'explicit authorization'):
                repair.materialize(case, raw)

    def test_signature_forgery_and_full_answer_injection_rejected(self):
        for case, original, timeline, packet in self.packets():
            revised = repair.materialize(case, self.response(packet))
            with self.assertRaisesRegex(ValueError, 'assigned patch'):
                repair.materialize(case, revised)
            forged = dict(base_sha256=packet['base_sha256'], replacements=[dict(
                path='/semantic_limitations', value=[*original['semantic_limitations'],
                    next(l for l in revised['semantic_limitations'] if source_revision.KEY in l)])])
            with self.assertRaisesRegex(ValueError, 'assigned patch'):
                repair.materialize(case, forged)
            packet['source_revision_authorization']['eligible_points'][0]['frame_id'] = 'forged'
            atomic_json(case/'state/behavior_repair.request.json', packet)
            with self.assertRaisesRegex(Exception, 'request changed'):
                repair.materialize(case, self.response(packet))

    def test_other_frozen_cases_accept_only_unbound_non_action_points(self):
        for suffix in ('7916ef73', '0794326f', '065638b5'):
            _, original, timeline = fixtures.frozen(suffix)
            def break_unbound(value):
                ego = next(a for a in value['actors'] if a['actor_id'] == 'ego')
                for point in ego['trajectory']:
                    if not any(g['actor_id']=='ego' and g['frame_id']==point['frame_id'] for g in timeline['groups']):
                        point['source_observation_ids'] = ['missing-source']
            for case, _, index, packet in self.packets(suffix, break_unbound):
                # Only the explicitly authorized subset is withdrawn. Any corrupt
                # action endpoint stays blocked and cannot be "repaired" by removal.
                authority = packet.get('source_revision_authorization')
                self.assertIsNotNone(authority)
                revised = repair.materialize(case, self.response(packet))
                self.assertEqual(original['events'], revised['events'])
                self.assertEqual(original['executor_primitives'], revised['executor_primitives'])
                self.assertFalse(set(p['point_index'] for p in authority['eligible_points'])
                                 & set(authority['protected_point_indices']))
                self.assertEqual(original['actors'][1:], revised['actors'][1:])

    def test_other_actor_source_cannot_be_relabelled_as_ego(self):
        for case, original, timeline, packet in self.packets():
            point = packet['source_revision_authorization']['eligible_points'][0]
            foreign = next(g['source_observation_id'] for g in timeline['groups']
                           if g['frame_id']==point['frame_id'] and g['actor_id']!='ego')
            patched = repair.materialize(case, dict(base_sha256=packet['base_sha256'],
                replacements=[dict(path=f"/actors/0/trajectory/{point['point_index']}/source_observation_ids",
                                   value=[foreign])]))
            with self.assertRaises(fixtures.ContractError):
                validate_source_coverage(case, patched, timeline=timeline)

    def test_qualitative_witness_receipts_and_conversion_work_keep_metric_assumptions(self):
        for case, original, timeline, packet in self.packets():
            revised = repair.materialize(case, self.response(packet))
            analysis = dict(ego=revised['actors'][0], actors=revised['actors'][1:],
                road={'lanes':[]}, events=revised['events'], limitations=revised['semantic_limitations'])
            # Component fixture contains only the real, legally retained qualitative
            # sources. It grants no identity/scope approval to the complete Case.
            evidence = [e for e in motion.evidence(analysis, revised['actors'])
                        if e.get('usage')=='qualitative_only']
            atomic_json(case/'final/analysis.json', analysis)
            request = dict(eligible=revised['actors'], evidence=evidence, duration_s=10,
                events=revised['events'], policy={motion_witnesses.POLICY_KEY:motion_witnesses.REVISION},
                analysis_sha256=sha256(case/'final/analysis.json'),
                bounded_execution=dict(source_record={'registry':[]}, lane_change=None, sources={},relations=[]))
            request[motion_witnesses.KEY] = motion_witnesses.build(analysis, request)
            coverage, relations = motion_witnesses.complete(request, [], [])
            self.assertEqual(relations, [])
            self.assertTrue(all(c['type']=='body_progress' and
                                'simulation assumptions' in c['assumption']
                                for c in request[motion_witnesses.KEY]['constraints']))
            self.assertTrue(all(r['owner'] in {'qualitative_only','body_progress_assumption'}
                               for r in request[motion_witnesses.KEY]['receipts'].values()))
            self.assertTrue(all(row['status']=='qualitative_only' for row in coverage))
            motion.validate_relations(request, coverage, relations)
            request['request_sha256'] = digest(request)
            atomic_json(case/'state/conversion_semantic_gap.request.json', request)
            owner = SimpleNamespace(progress=lambda _case: {'sessions':[]})
            work = ConversionWork(owner, case, request)
            run = work.new_run(STAGES[0], {'runs':[]})
            self.assertEqual(set(run['request']['qualitative_source_constraints']['source_paths']),
                             {e['source'] for e in evidence})
            self.assertEqual(run['request']['remaining_sources'], [])
            validate_save(request, STAGES[0], dict(request_sha256=run['request']['request_sha256'],
                complete=True, coverage=coverage, relations=[]), run['request']['request_sha256'])

    def test_registry_cab_text_cannot_bypass_qualitative_constraint(self):
        # A different identifier/time and an existing English grammar branch:
        # the Registry must not reintroduce the forbidden centre relation.
        record = {'registry':[dict(actor_id='truck_other', evidence=[dict(
            status='observed',time_s=7.25,reason='tractor cab beside ego')])]}
        request = dict(eligible=[dict(actor_id='ego',trajectory=[]),
                                dict(actor_id='truck_other',trajectory=[])], duration_s=12,
            evidence=[dict(source='/limitations/0/body/text',text='tractor cab beside ego',
                actor_ids=['truck_other','ego'], time_s=7.25,usage='qualitative_only')])
        analysis = {'metadata':{bounded_execution.KEY:record}}
        with patch('_lib.contracts.census_scope.validate_analysis',return_value={}), \
                patch('_lib.behavior.lane_change.from_analysis',return_value=None):
            fields = bounded_execution.request_fields(analysis,request)
        self.assertEqual(fields[bounded_execution.KEY]['sources'], {})
        self.assertEqual(fields[bounded_execution.KEY]['relations'], [])
        self.assertTrue(all(e['usage']=='qualitative_only' for e in request['evidence']))

    def test_qualitative_handoff_cannot_become_whole_vehicle_parallel_or_overtake(self):
        for case, original, timeline, packet in self.packets():
            revised = repair.materialize(case, self.response(packet))
            analysis = dict(ego=revised['actors'][0], actors=revised['actors'][1:],
                            events=revised['events'], limitations=revised['semantic_limitations'])
            evidence = motion.evidence(analysis, revised['actors'])
            body = next(e for e in evidence if e.get('usage')=='qualitative_only' and e['source'].startswith('/actors/'))
            expected = dict(eligible=revised['actors'], evidence=evidence, policy={}, events=revised['events'], duration_s=10)
            coverage = [dict(source=body['source'],status='qualitative_only',reason='Retained with body-part limitation.')]
            motion.validate_relations(expected, coverage, [], partial=True)
            with self.assertRaisesRegex(ValueError, 'qualitative constraint'):
                motion.validate_relations(expected, [dict(coverage[0], status='program_verified')], [], partial=True)
            for kind in ('alongside','overtakes'):
                rel = dict(type=kind, actor_id=body['actor_ids'][0],reference_actor_id='ego',
                    time_interval_s=[0,body['time_s']] if kind=='overtakes' else [body['time_s']]*2,
                    evidence=[dict(source=body['source'],quote=body['text'])])
                with self.assertRaisesRegex(ValueError, 'whole-vehicle'):
                    motion.validate_relations(expected, coverage, [rel], partial=True)
            bad = copy.deepcopy(analysis)
            owner = next(a for a in bad['actors'] if a['actor_id']==body['actor_ids'][0])
            owner['trajectory'] = []
            with self.assertRaisesRegex(ValueError, 'omitted'):
                motion.evidence(bad, [bad['ego'], *bad['actors']])
            expected['policy'][motion_witnesses.POLICY_KEY] = motion_witnesses.REVISION
            ordinary = next(e for e in evidence if e.get('usage') not in {'audit_only','qualitative_only'}
                            and e['actor_ids'] and e['actor_ids'][0]!='ego')
            with self.assertRaisesRegex(ValueError, 'completion lacks frozen source'):
                motion.validate_relations(expected,
                    [dict(source=ordinary['source'],status='used',reason='Incomplete passing description')],
                    [dict(type='overtakes', actor_id='ego', reference_actor_id=ordinary['actor_ids'][0],
                          time_interval_s=[0,10], evidence=[dict(source=ordinary['source'],quote=ordinary['text'])])],
                    partial=True)


if __name__ == '__main__':
    unittest.main()
