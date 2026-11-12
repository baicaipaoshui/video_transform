"""NO-LLM replay of authentic Census certificates into isolated fact stores."""
import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from _lib.behavior.compile import preview_digest
from _lib.runtime import fact_versions
from _lib.runtime.core_approximate_delivery import _abandoned_first_behavior_submission
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime.world_adjudication import validate_journal
from _lib.stages import reconciliation


CASES = Path(__file__).resolve().parents[5]/'video_extractor/cases'


def source_case(mode, *, pending=None, index=0):
    matches = []
    for journal_path in sorted(CASES.glob('*/state/lane_layout.commit.json')):
        journal = read_json(journal_path)
        certificate = journal.get('census_certificate')
        if (certificate and certificate['request']['mode'] == mode and
                (journal_path.parent/'fact_versions/active.json').is_file()):
            journal = copy.deepcopy(journal)
            if pending:
                journal.pop('status', None)
                signed(journal)
            matches.append((journal_path.parent.parent, journal))
    if len(matches) <= index:
        raise AssertionError(f'Not enough archived {mode} Census journals')
    return matches[index]


def signed(journal):
    journal['sha256'] = preview_digest({k:v for k,v in journal.items() if k != 'sha256'})
    return journal


class FirstCensusCommit(unittest.TestCase):
    def setUp(self):
        self.original_root = fact_versions._root
        self.temporary = tempfile.TemporaryDirectory(prefix='census-commit-no-llm-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)/'fact_versions'
        self.root.mkdir()
        self.patch_root = patch.object(fact_versions, '_root', return_value=self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def baseline(self, case, *, joint=False):
        if joint:
            journal = read_json(case/'state/lane_layout.commit.json')
            request = journal['census_certificate']['request']
            original = case/'state/fact_versions/v000000'
            files = {name: read_json(original/name) for name in fact_versions.FILES}
            files['world_context.json'] = read_json(request['world_path'])
            files['actor_registry.json'] = read_json(request['registry_path'])
            files['census_adjudication.accepted.json'] = {}
            files['claims.json'] = {}
            files['revisions.json'] = []
            signature = fact_versions._publish(case, 'v000000', files)
            atomic_json(self.root/'active.json', dict(revision=fact_versions.REVISION,
                        version_id='v000000', manifest_sha256=signature))
        else:
            shutil.copytree(case/'state/fact_versions/v000000', self.root/'v000000')
            atomic_json(self.root/'active.json', dict(revision=fact_versions.REVISION,
                        version_id='v000000',
                        manifest_sha256=sha256(self.root/'v000000/manifest.json')))

    def test_first_after_layout_pending_and_interruptions(self):
        case, journal = source_case('census_after_layout', pending=True)
        validate_journal(journal)
        self.baseline(case)
        parent = fact_versions.current_facts(case)
        self.assertIsNone(parent.census)
        with self.assertRaises(fact_versions.FactVersionError):
            fact_versions.commit_world_closure(case, journal, fault_at='before_publish')
        self.assertEqual(fact_versions.current_facts(case).version_id, 'v000000')
        with self.assertRaises(fact_versions.FactVersionError):
            fact_versions.commit_world_closure(case, journal, fault_at='before_pointer')
        self.assertEqual(fact_versions.current_facts(case).version_id, 'v000000')
        accepted = fact_versions.commit_world_closure(case, journal)
        self.assertEqual(accepted.census['sha256'], journal['census_certificate']['sha256'])
        self.assertEqual(fact_versions.commit_world_closure(case, journal).version_id,
                         accepted.version_id)
        self.assertEqual(len([r for r in accepted.revisions
                              if r['kind'] == 'world-closure-commit.v1']), 1)

        changed = signed(copy.deepcopy(journal))
        changed['fact_parent_version'] = 'v999999'
        signed(changed)
        with self.assertRaises(fact_versions.FactVersionError):
            fact_versions.commit_world_closure(case, changed)
        changed['fact_parent_version'] = accepted.version_id
        changed['fact_parent_manifest_sha256'] = sha256(accepted.directory/'manifest.json')
        changed['old_world_sha256'] = sha256(accepted.world_path)
        changed['old_registry_sha256'] = sha256(accepted.registry_path)
        signed(changed)
        with self.assertRaisesRegex(fact_versions.FactVersionError, 'incompatible parent'):
            fact_versions.commit_world_closure(case, changed)

    def test_after_pointer_recovery_does_not_publish_twice(self):
        case, journal = source_case('census_after_layout', pending=True)
        self.baseline(case)
        with self.assertRaises(fact_versions.FactVersionError):
            fact_versions.commit_world_closure(case, journal, fault_at='after_pointer')
        once = fact_versions.current_facts(case)
        twice = fact_versions.commit_world_closure(case, journal)
        self.assertEqual(once.version_id, twice.version_id)
        self.assertEqual(len(once.revisions), len(twice.revisions))

    def test_pending_journal_recovers_repeatedly_without_source_writes(self):
        case, journal = source_case('census_after_layout', pending=True)
        self.baseline(case)
        isolated_journal = self.root.parent/'lane_layout.commit.json'
        atomic_json(isolated_journal, journal)
        source_journal = case/'state/lane_layout.commit.json'
        source_hash = sha256(source_journal)

        def local_read(path):
            return read_json(isolated_journal if Path(path) == source_journal else path)

        def local_write(path, value):
            if Path(path) != source_journal:
                raise AssertionError('Recovery attempted a source Case write')
            atomic_json(isolated_journal, value)

        hashes = {path.name:sha256(path) for path in (case/'batches').glob('batch_*.json')}
        with patch.object(reconciliation, 'read_json', side_effect=local_read), \
                patch.object(reconciliation, 'atomic_json', side_effect=local_write):
            runner = reconciliation.WorldReconciliation()
            self.assertTrue(runner.commit_lane_layout(case, hashes))
            first = fact_versions.current_facts(case)
            self.assertTrue(runner.commit_lane_layout(case, hashes))
        self.assertEqual(read_json(isolated_journal)['status'], 'committed')
        self.assertEqual(fact_versions.current_facts(case).version_id, first.version_id)
        self.assertEqual(sha256(source_journal), source_hash)

    def test_after_layout_rejects_unaccepted_predecessor(self):
        case, original = source_case('census_after_layout', pending=True)
        files = {name:read_json(case/'state/fact_versions/v000000'/name)
                 for name in fact_versions.FILES}
        files['reconciliation.json']['status'] = 'needs_review'
        manifest = fact_versions._publish(case, 'v000000', files)
        atomic_json(self.root/'active.json', dict(revision=fact_versions.REVISION,
                    version_id='v000000', manifest_sha256=manifest))
        parent = fact_versions.current_facts(case)
        journal = copy.deepcopy(original)
        journal.update(fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json'),
                       old_world_sha256=sha256(parent.world_path),
                       old_registry_sha256=sha256(parent.registry_path))
        signed(journal)
        with self.assertRaisesRegex(fact_versions.FactVersionError, 'accepted layout'):
            fact_versions.commit_world_closure(case, journal)

    def test_after_layout_rejects_changed_fact_source(self):
        case, original = source_case('census_after_layout', pending=True)
        files = {name:read_json(case/'state/fact_versions/v000000'/name)
                 for name in fact_versions.FILES}
        files['world_context.json']['evidence_epoch'] += 1
        files['reconciliation.json']['world'] = files['world_context.json']
        manifest = fact_versions._publish(case, 'v000000', files)
        atomic_json(self.root/'active.json', dict(revision=fact_versions.REVISION,
                    version_id='v000000', manifest_sha256=manifest))
        parent = fact_versions.current_facts(case)
        journal = copy.deepcopy(original)
        journal.update(fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json'),
                       old_world_sha256=sha256(parent.world_path),
                       old_registry_sha256=sha256(parent.registry_path))
        signed(journal)
        with self.assertRaisesRegex(fact_versions.FactVersionError, 'source differs'):
            fact_versions.commit_world_closure(case, journal)

    def test_joint_first_commit_and_parent_certificate_gate(self):
        case, archived = source_case('layout_and_census', pending=False)
        validate_journal(archived)
        self.baseline(case, joint=True)
        parent = fact_versions.current_facts(case)
        journal = copy.deepcopy(archived)
        journal.pop('status', None)
        journal.update(fact_parent_version=parent.version_id,
                       fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json'),
                       old_world_sha256=sha256(parent.world_path),
                       old_registry_sha256=sha256(parent.registry_path))
        signed(journal)
        accepted = fact_versions.commit_world_closure(case, journal)
        self.assertEqual(accepted.census['sha256'], archived['census_certificate']['sha256'])
        self.assertEqual(fact_versions.commit_world_closure(case, journal).version_id,
                         accepted.version_id)
        another = copy.deepcopy(journal)
        another['receipt']['test_marker'] = 'different transaction'
        signed(another)
        with self.assertRaisesRegex(fact_versions.FactVersionError, 'parent fact version is stale'):
            fact_versions.commit_world_closure(case, another)

    def test_second_historical_joint_case(self):
        case, archived = source_case('layout_and_census', index=1)
        validate_journal(archived)
        self.baseline(case, joint=True)
        parent = fact_versions.current_facts(case)
        journal = copy.deepcopy(archived)
        journal.pop('status', None)
        journal.update(fact_parent_version=parent.version_id,
                       fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json'),
                       old_world_sha256=sha256(parent.world_path),
                       old_registry_sha256=sha256(parent.registry_path))
        signed(journal)
        accepted = fact_versions.commit_world_closure(case, journal)
        self.assertEqual(accepted.census['sha256'], archived['census_certificate']['sha256'])

    def test_pre_dispatch_behavior_retry_requires_abandoned_receipt(self):
        from _lib.runtime.bounded_recovery import request_digest
        case, _ = source_case('census_after_layout', pending=False)
        progress = dict(phase='failed', status='failed',
                        error=dict(stage='behavior', code='backend_unavailable'),
                        attempts={}, sessions=[], diagnostics=dict(model_task_reservations=[
                            dict(stage='behavior', status='abandoned_before_task_id',
                                 recovery_request=request_digest(case, 'behavior'))]))
        with patch.object(fact_versions, '_root', self.original_root):
            self.assertTrue(_abandoned_first_behavior_submission(case, progress))
            changed = copy.deepcopy(progress)
            for row in changed['diagnostics']['model_task_reservations']:
                if row.get('stage') == 'behavior':
                    row['task_id'] = 'unknown-task'
            self.assertFalse(_abandoned_first_behavior_submission(case, changed))


if __name__ == '__main__':
    unittest.main()
