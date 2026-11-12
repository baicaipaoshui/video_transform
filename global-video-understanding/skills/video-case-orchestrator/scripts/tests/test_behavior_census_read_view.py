"""NO-LLM, isolated checks for the frozen Behavior Census reading view."""
import copy
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.behavior import census_read_view, input_binding
from _lib.prompts import behavior_request_text
from _lib.runtime.case_core import atomic_json, read_json, sha256

ROOT = SKILLS.parents[1]
SOURCE = ROOT/'video_extractor/cases/case_20261021T122844498Z_dd0ed586'


class BehaviorCensusReadViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_dir():
            raise unittest.SkipTest('Frozen 2026-10-21 Case is unavailable')
        cls.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.case = Path(cls.temp.name)/SOURCE.name
        cls.case.mkdir()
        for directory in ('state', 'batches', 'diagnostics'):
            shutil.copytree(SOURCE/directory, cls.case/directory)
        for name in ('case.json', 'manifest.json'):
            shutil.copy2(SOURCE/name, cls.case/name)
        (cls.case/input_binding.REQUEST).unlink()
        (cls.case/input_binding.SESSIONS).unlink(missing_ok=True)
        # The frozen frame paths in this historical Case are absolute paths to
        # the original Case; its source validation already passes there.
        cls.validation_patch = patch('_lib.world.single_world.validate_inputs', return_value=None)
        cls.validation_patch.start()
        cls.addClassCleanup(cls.validation_patch.stop)
        cls.request = input_binding.freeze(cls.case)
        cls.view_path = Path(cls.request['census_read_view']['path'])

    def test_complete_facts_and_unresolved_mapping(self):
        value = read_json(self.view_path)
        source_request = read_json(SOURCE/input_binding.REQUEST)
        census = read_json(SOURCE/'state/fact_versions/v000000/census_adjudication.accepted.json')
        registry = read_json(SOURCE/'state/fact_versions/v000000/actor_registry.json')
        self.assertEqual(value['actor_ids'], [actor['actor_id'] for actor in registry])
        self.assertEqual(value['sources']['registry_sha256'], sha256(
            SOURCE/'state/fact_versions/v000000/actor_registry.json'))
        self.assertEqual(value['sources']['claims_sha256'], sha256(
            SOURCE/'state/fact_versions/v000000/claims.json'))
        self.assertEqual(len(read_json(SOURCE/'state/fact_versions/v000000/claims.json')), 44)
        self.assertEqual(value['evidence_timeline_sha256'],
                         input_binding.digest(self.request['evidence_timeline']))
        self.assertEqual(len(self.request['evidence_timeline']['groups']), 9)
        current = value['rounds'][-1]
        self.assertEqual(current['request']['records'], census['request']['records'])
        self.assertEqual(current['response']['bindings'], census['response']['bindings'])
        self.assertEqual(current['response']['vehicles'], census['response']['vehicles'])
        self.assertEqual(current['response']['relevance'], census['response']['relevance'])
        self.assertEqual(sum(len(v['sightings']) for v in current['response']['vehicles']), 33)
        self.assertEqual(set(value['pending_issues']), set(census['result']['remaining_issue_ids']))
        unresolved = source_request['evidence_timeline']['unresolved']
        self.assertEqual({r['source_id'] for r in value['unresolved_sources']},
                         {r['source_id'] for r in unresolved})
        self.assertEqual(Counter(r['census_issue_status'] for r in value['unresolved_sources']),
                         Counter({None: 26, 'resolved': 7, 'excluded': 2, 'remaining': 6}))
        self.assertEqual(input_binding.verify(self.case)['request_sha256'],
                         self.request['request_sha256'])

    def test_every_reference_returns_to_frozen_source(self):
        value = read_json(self.view_path)
        timeline = self.request['evidence_timeline']
        rows = {row['source_id']: row for row in timeline['unresolved']}
        records = {r['issue_id']: r for r in value['rounds'][-1]['request']['records']}
        for round_view in value['rounds']:
            certificate_path = Path(round_view['certificate_path'])
            self.assertEqual(sha256(certificate_path), round_view['certificate_sha256'])
            certificate = read_json(certificate_path)
            for ref in round_view['issue_references']:
                record_index = int(ref['record_pointer'].rsplit('/', 1)[1])
                binding_index = int(ref['binding_pointer'].rsplit('/', 1)[1])
                self.assertEqual(certificate['request']['records'][record_index]['issue_id'],
                                 ref['issue_id'])
                self.assertEqual(certificate['response']['bindings'][binding_index]['issue_id'],
                                 ref['issue_id'])
        for link in value['unresolved_sources']:
            row = rows[link['source_id']]
            path, source_hash, pointer = census_read_view._source(
                self.case, row, timeline['batch_sha256'])
            self.assertEqual(source_hash, value['sources']['batch_sha256'][path])
            self.assertEqual(pointer, link['source_pointer'])
            issue_id = link['census_issue_id']
            if issue_id:
                record = records[issue_id]
                self.assertEqual((record['batch_id'], record['frame_id'], record['object_id']),
                                 (Path(path).stem, row['frame_id'], row['object_id']))
            else:
                self.assertEqual(row['source_binding'], 'observation_actor_unbound')

    def test_stale_missing_and_corrupt_inputs_block(self):
        timeline = copy.deepcopy(self.request['evidence_timeline'])
        timeline['fact_version_id'] = 'v999999'
        with self.assertRaisesRegex(Exception, 'fact version differ'):
            census_read_view.build(self.case, timeline)
        pointer = self.case/'state/fact_versions/active.json'
        original_pointer = pointer.read_bytes()
        try:
            value = read_json(pointer)
            value['version_id'] = 'v999999'
            atomic_json(pointer, value)
            with self.assertRaises(Exception):
                input_binding.current_census_read_view(self.case)
        finally:
            pointer.write_bytes(original_pointer)
        timeline = copy.deepcopy(self.request['evidence_timeline'])
        row = next(r for r in timeline['unresolved'] if r['source_binding'] == 'unresolved')
        row['object_id'] = 'missing_object'
        with self.assertRaisesRegex(Exception, 'no exact issue mapping'):
            census_read_view.build(self.case, timeline)

        original_view = self.view_path.read_bytes()
        try:
            value = read_json(self.view_path)
            value['unresolved_sources'].pop()
            atomic_json(self.view_path, value)
            with self.assertRaisesRegex(Exception, 'reading view|frozen input changed'):
                input_binding.verify(self.case)
        finally:
            self.view_path.write_bytes(original_view)

        batch = self.case/'batches/batch_0002.json'
        original_batch = batch.read_bytes()
        try:
            batch.write_bytes(original_batch + b' ')
            with self.assertRaises(Exception):
                census_read_view.build(self.case, self.request['evidence_timeline'])
        finally:
            batch.write_bytes(original_batch)

        certificate = self.case/'state/fact_versions/v000000/census_adjudication.accepted.json'
        original_certificate = certificate.read_bytes()
        try:
            certificate.write_text('{}\n')
            with self.assertRaises(Exception):
                census_read_view.build(self.case, self.request['evidence_timeline'])
        finally:
            certificate.write_bytes(original_certificate)
        self.assertEqual(input_binding.verify(self.case)['request_sha256'],
                         self.request['request_sha256'])

    def test_new_prompt_and_frozen_legacy_prompt(self):
        compact = behavior_request_text(self.case)
        legacy = behavior_request_text(SOURCE)
        self.assertIn('读取已冻结的 Census 精简阅读视图', compact)
        self.assertNotIn('读取证书 request/response/result', compact)
        self.assertIn('读取证书 request/response/result', legacy)
        self.assertNotIn('census_read_view', read_json(SOURCE/input_binding.REQUEST))

    def test_copied_frozen_view_preserves_bytes_and_rejects_source_drift(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            case = Path(directory)/self.case.name
            shutil.copytree(self.case, case)
            metadata = self.request['census_read_view']
            timeline = self.request['evidence_timeline']
            path = census_read_view.verify(case, metadata, timeline)
            self.assertEqual(path, case/census_read_view.DIRECTORY/self.view_path.name)
            self.assertEqual(sha256(path), metadata['sha256'])
            self.assertEqual(read_json(path), read_json(self.view_path))
            batch = case/'batches/batch_0002.json'
            before = batch.read_bytes()
            batch.write_bytes(before + b' ')
            with self.assertRaisesRegex(Exception, 'copied.*source changed'):
                census_read_view.verify(case, metadata, timeline)
            batch.write_bytes(before)
            pointer = case/'state/fact_versions/active.json'
            original_pointer = pointer.read_bytes()
            value = read_json(pointer)
            value['version_id'] = 'v999999'
            atomic_json(pointer, value)
            with self.assertRaises(Exception):
                census_read_view.verify(case, metadata, timeline)
            pointer.write_bytes(original_pointer)
            changed = dict(metadata, path=str(path.parent/'wrong.json'))
            with self.assertRaisesRegex(Exception, 'binding changed'):
                census_read_view.verify(case, changed, timeline)
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(Exception, 'binding changed'):
                census_read_view.verify(case, metadata, timeline)


if __name__ == '__main__':
    unittest.main()
