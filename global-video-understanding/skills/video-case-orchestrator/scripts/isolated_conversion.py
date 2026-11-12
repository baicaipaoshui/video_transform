#!/usr/bin/env python3
"""NO-LLM continuation probe for an accepted Case, starting at conversion_relations.

This entry never accepts a live backend. It copies an already accepted Case to a
temporary root, installs a process/child write guard, and stops at model submit.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.input_binding import accepted_validation
from _lib.contracts import bounded_execution, motion_witnesses, retained_motion, semantic_motion
from _lib.runtime.case_core import read_json, sha256
from _lib.runtime.fact_versions import current_facts
from _lib.runtime.orchestrator import validate_analysis
from _lib.runtime.v3 import V3Orchestrator

BEHAVIOR_SHA = 'a740b75eae6a5843d481c52c7006ab0a77be41d45ea90b7b9d20bf614e46dfb5'
ANALYSIS_SHA = '4a100131efdfc5dd7393e3a6e3839e423d8d752e34903962552c76d5f05fca08'
FACT_VERSION = 'v000001-31372956ae65'
REQUEST_SHA = '80a596a7a2eed2a000c0b6e45c10993c4254a1efb43931cab969b6a9ec9880ac'
EXPECTED_REMAINING = 47
GUARD_DIR = Path(__file__).parent/'isolation_guard'


class SubmissionBoundary(BaseException):
    """Stops a dry-run before any external task can be created."""


class NoModelBackend:
    def __init__(self):
        self.stages = []

    def submit(self, agent, prompt, *, session_id=None):
        self.stages.append({'agent': agent, 'session_id': session_id})
        raise SubmissionBoundary()

    def poll(self, task_id, agent):
        raise AssertionError('Dry-run must not poll an external model task')


def inventory(root):
    """Read-only path and content inventory; symlinks are recorded, not followed."""
    root = Path(root)
    if not root.exists():
        return None
    rows = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows[name] = ['link', os.readlink(path)]
        elif path.is_file():
            rows[name] = ['file', sha256(path)]
        elif path.is_dir():
            rows[name] = ['directory']
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(), len(rows)


def preflight(case):
    case = Path(case).resolve(strict=True)
    if not (case/'case.json').is_file() or 'f8fdd6a0' not in case.name:
        raise ValueError('Expected the accepted f8fdd6a0 Case')
    if sha256(case/'state/behavior.json') != BEHAVIOR_SHA:
        raise ValueError('Behavior SHA-256 changed')
    analysis_path = case/'final/analysis.json'
    if sha256(analysis_path) != ANALYSIS_SHA:
        raise ValueError('Analysis SHA-256 changed')
    view = current_facts(case, FACT_VERSION)
    accepted_validation(case, view.version_id)
    analysis = read_json(analysis_path)
    validate_analysis(analysis)
    request = semantic_motion.request(analysis, ANALYSIS_SHA,
                                      witness_revision=motion_witnesses.REVISION)
    if request['request_sha256'] != REQUEST_SHA:
        raise ValueError('Motion-witness request hash changed')
    frozen = case/'state/conversion_semantic_gap.request.json'
    if frozen.is_file() and read_json(frozen) != request:
        raise ValueError('Frozen semantic request differs from current accepted input')
    if (case/'state/conversion_semantic_gap.json').exists():
        raise ValueError('A semantic decision already exists; no new model task is needed')
    coverage, relations = bounded_execution.complete_relations(request, [], [])
    coverage, relations = retained_motion.complete_relations(request, coverage, relations)
    remaining = sorted({row['source'] for row in request['evidence']}
                       - {row['source'] for row in coverage})
    if len(remaining) != EXPECTED_REMAINING:
        raise ValueError('Remaining semantic evidence changed: ' + str(len(remaining)))
    progress = read_json(case/'state/progress.json')
    if (progress.get('substage') != 'building'
            or any(row.get('status') in {'submitted', 'pending', 'running'}
                   for row in progress.get('sessions', []))):
        raise ValueError('Case is not at a safe conversion continuation checkpoint')
    return request, remaining


def install_guard(root):
    os.environ['VIDEO_CONVERSION_ISOLATION_ROOT'] = str(root)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['PYTHONPATH'] = str(GUARD_DIR) + os.pathsep + os.environ.get('PYTHONPATH', '')
    spec = importlib.util.spec_from_file_location('_conversion_isolation_guard', GUARD_DIR/'sitecustomize.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_outputs(root, case):
    base = case/'state/conversion_work'
    return {
        'request': str(case/'state/conversion_semantic_gap.request.json'),
        'stage_request': str(base/'conversion_relations/run_0001/request.json'),
        'worker_saves': str(base/'conversion_relations/run_0001/save_NNNN.json'),
        'conditional_motion_request': str(base/'conversion_semantic_gap/run_0001/request.json'),
        'conditional_motion_saves': str(base/'conversion_semantic_gap/run_0001/save_NNNN.json'),
        'accepted_checkpoints': str(base/'accepted'),
        'workflow': str(base/'workflow.json'),
        'task_reservations_and_sessions': str(case/'state/progress.json'),
        'model_candidate': str(case/'state/conversion_semantic_gap.candidate.json'),
        'decision': str(case/'state/conversion_semantic_gap.json'),
        'diagnostics': str(case/'diagnostics'),
        'converter_policy_and_receipts': str(case/'state/conversion_policies'),
        'converter_receipt': str(case/'state/conversion_result.json'),
        'target': str(case/'final/target.json'),
        'target_checks': str(case/'state/target_checks.json'),
        'reports': [str(case/'final/report.md'), str(case/'final/report.docx')],
        'publish': [str(root/'media/output'/(case.name + '_' + name))
                    for name in ('analysis.json', 'report.md', 'report.docx', 'target.json')],
        'task_lease': str(root/'state/agent_leases'),
        'provider_sessions': 'BLOCKED: no isolated QwenPaw Agent workspace/session binding',
    }


def run(source, original, formal_output):
    source = Path(source).resolve(strict=True)
    original = Path(original).resolve(strict=True)
    formal_output = Path(formal_output).resolve(strict=True)
    request, remaining = preflight(source)  # No writes precede input binding.
    protected_before = {'source_checkpoint': inventory(source),
                        'original_case': inventory(original),
                        'formal_media_output': inventory(formal_output)}
    root = Path(tempfile.mkdtemp(prefix='f8_conversion_isolated_')).resolve(strict=True)
    guard = install_guard(root)
    (root/'tmp').mkdir()
    os.environ['TMPDIR'] = str(root/'tmp')
    os.environ['TMP'] = str(root/'tmp')
    os.environ['TEMP'] = str(root/'tmp')
    case = root/'cases'/source.name
    result = {'isolation_root': str(root), 'case': str(case),
              'remaining_text_evidence': len(remaining),
              'eligible_actor_ids': [row['actor_id'] for row in request['eligible']],
              'request_sha256': request['request_sha256'], 'fact_version': FACT_VERSION,
              'expected_outputs': expected_outputs(root, case)}
    try:
        for value in result['expected_outputs'].values():
            for path in value if isinstance(value, list) else [value]:
                if Path(path).is_absolute():
                    guard.check_write(path)
        shutil.copytree(source, case)
        (root/'media/output').mkdir(parents=True)
        preflight(case)
        # The default publisher remains untouched; this override exists only in
        # this guarded process and is inherited by no external process.
        from _lib.stages import delivery
        delivery.EXTRACTOR = root
        backend = NoModelBackend()
        owner = V3Orchestrator(backend)
        try:
            owner.ensure_semantic_gap(case)
        except SubmissionBoundary:
            pass
        else:
            raise AssertionError('Conversion did not reach the model submission boundary')
        if backend.stages != [{'agent': 'json-coverter', 'session_id': None}]:
            raise AssertionError('Unexpected model stage or existing session')
        work = read_json(case/'state/conversion_work/workflow.json')
        if (list(work['stages']) != ['conversion_relations']
                or len(work['stages']['conversion_relations']['runs']) != 1):
            raise AssertionError('Continuation resumed at the wrong stage')
        if any((case/name).exists() for name in (
                'state/conversion_semantic_gap.json',
                'state/conversion_semantic_gap.candidate.json', 'final/target.json')):
            raise AssertionError('Dry-run created a decision, candidate, or Target')
        result.update(status='passed', first_model_stage='conversion_relations',
                      model_tasks_submitted=0, target_delivered=False)
    except BaseException as error:
        result.update(status='failed', error=type(error).__name__ + ': ' + str(error),
                      traceback=traceback.format_exc(limit=12))
    finally:
        after = {'source_checkpoint': inventory(source),
                 'original_case': inventory(original),
                 'formal_media_output': inventory(formal_output)}
        result['protected_inputs_unchanged'] = after == protected_before
        if after != protected_before:
            result.update(status='failed', error='Protected source, original Case, or formal media/output changed')
        result['isolated_inventory'] = inventory(root)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-case', type=Path, required=True)
    parser.add_argument('--original-case', type=Path, required=True)
    parser.add_argument('--formal-output', type=Path, required=True)
    args = parser.parse_args()
    outcome = run(args.source_case, args.original_case, args.formal_output)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
