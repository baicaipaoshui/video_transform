#!/usr/bin/env python3
"""Accept one archived, completed json-coverter relations task without model calls."""
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _lib.runtime.case_core import read_json
from _lib.runtime.conversion_work import ConversionWork, STAGES, enabled


class ProgressReader:
    def progress(self, case):
        return read_json(Path(case)/'state/progress.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'accept', 'prepare-numeric'))
    parser.add_argument('--isolation-root', required=True, type=Path)
    parser.add_argument('--case-dir', required=True, type=Path)
    parser.add_argument('--attempt-dir', type=Path)
    parser.add_argument('--history-db', type=Path)
    parser.add_argument('--task-id')
    parser.add_argument('--expected-coverage', type=int)
    parser.add_argument('--expected-relations', type=int)
    args = parser.parse_args()
    root = args.isolation_root.resolve(strict=True)
    case = args.case_dir.resolve(strict=True)
    if not case.is_relative_to(root) or not enabled(case):
        parser.error('Case must be isolated and use the frozen conversion continuation policy')
    request = read_json(case/'state/conversion_semantic_gap.request.json')
    owner = ProgressReader() if args.action == 'prepare-numeric' else None
    work = ConversionWork(owner, case, request)
    if args.action == 'prepare-numeric':
        entry = work.state['stages'].get(STAGES[0], {})
        if not entry.get('complete') or not entry.get('external_completion'):
            parser.error('External relations must be accepted first')
        work.finish_stage(STAGES[0])
        numeric = work.state['stages'].setdefault(STAGES[1], {'runs': []})
        if numeric['runs']:
            run = numeric['runs'][-1]
            path = work.root/STAGES[1]/f"run_{run['generation']:04d}"/'request.json'
            if read_json(path) != run['request']:
                parser.error('Existing numeric stage request changed')
        else:
            run = work.new_run(STAGES[1], numeric)
            path = work.root/STAGES[1]/f"run_{run['generation']:04d}"/'request.json'
        print(json.dumps({'numeric_request': str(path),
                          'request_sha256': run['request']['request_sha256'],
                          'remaining_actor_ids': run['request']['remaining_actor_ids']}))
        return
    if not all((args.attempt_dir, args.history_db, args.task_id)):
        parser.error('check/accept require --attempt-dir, --history-db and --task-id')
    value = work.adopt_external_relations(
        isolation_root=root, attempt_dir=args.attempt_dir, history_db=args.history_db,
        task_id=args.task_id, expected_coverage=args.expected_coverage,
        expected_relations=args.expected_relations) if args.action == 'accept' else None
    if args.action == 'check':
        receipt = work.external_relations_receipt(root, args.attempt_dir, args.history_db, args.task_id)
        work.verify_external_relations(receipt)
        value = work.finish_stage(STAGES[0])
        if (args.expected_coverage is not None and len(value['coverage']) != args.expected_coverage
                or args.expected_relations is not None and len(value['relations']) != args.expected_relations):
            parser.error('Accepted relation totals differ')
    print(json.dumps({'action': args.action, 'task_id': args.task_id,
                      'coverage': len(value['coverage']), 'relations': len(value['relations']),
                      'stage_complete': work.state['stages'][STAGES[0]].get('complete') is True}))


if __name__ == '__main__':
    main()
