#!/usr/bin/env python3
"""Explicit World-owner review of one historical question against original frames."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json
from _lib.runtime.question_review import ROOT, commit, prepare, prepare_unregistered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-dir', required=True, type=Path)
    actions = parser.add_subparsers(dest='action', required=True)
    start = actions.add_parser('prepare')
    start.add_argument('--question-id', required=True)
    start.add_argument('--frame-id', action='append', default=[])
    start.add_argument('--scope-event-id')
    actions.add_parser('prepare-observations')
    submit = actions.add_parser('submit')
    submit.add_argument('--request-path', required=True, type=Path)
    submit.add_argument('--decision-draft', required=True, type=Path)
    args = parser.parse_args()
    case = args.case_dir.resolve()
    if args.action == 'prepare':
        path = prepare(case, args.question_id, extra_frame_ids=args.frame_id,
                       scope_event_id=args.scope_event_id)
        print(json.dumps({'request_path': str(path), 'request': read_json(path)}, ensure_ascii=False))
        return
    if args.action == 'prepare-observations':
        paths = prepare_unregistered(case)
        print(json.dumps({'request_paths': [str(path) for path in paths]}, ensure_ascii=False))
        return
    request_path = args.request_path.resolve()
    if not request_path.is_relative_to((case/ROOT).resolve()):
        raise ContractError('Review request belongs to another Case', rule_id='model_out_of_scope')
    request = read_json(request_path)
    draft = read_json(args.decision_draft)
    if not isinstance(draft, dict) or 'decision_sha256' in draft:
        raise ContractError('Submit an unsigned World review decision object',
                            rule_id='model_out_of_scope')
    if draft.get('issue_id') != request['issue_id'] or draft.get('request_sha256') != request['request_sha256']:
        raise ContractError('World review decision must name its frozen request and issue',
                            rule_id='model_out_of_scope')
    signed = dict(draft, decision_sha256=digest(draft))
    decision_path = case/ROOT/(request['review_id']+'.'+signed['decision_sha256'][:16]+'.decision.json')
    if decision_path.is_file() and read_json(decision_path) != signed:
        raise ContractError('Signed World review decision changed', rule_id='fact_conflict')
    if not decision_path.is_file():
        atomic_json(decision_path, signed)
    print(json.dumps(commit(case, request_path, decision_path), ensure_ascii=False))


if __name__ == '__main__':
    main()
