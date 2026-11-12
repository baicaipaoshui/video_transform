#!/usr/bin/env python3
"""Explicitly establish a fact-version baseline for one unfinished legacy Case."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _lib.paths import EXTRACTOR
from _lib.runtime.case_core import atomic_json, now, read_json, sha256
from _lib.runtime.fact_versions import establish_baseline
from _lib.contracts.v3 import ContractError


def adopt(case):
    case = Path(case).resolve(strict=True)
    if case.parent != (EXTRACTOR/'cases').resolve() or not (case/'case.json').is_file():
        raise ContractError('Fact-version adoption requires one saved extractor Case')
    progress = read_json(case/'state/progress.json')
    if progress.get('status') in {'done', 'done_degraded'}:
        raise ContractError('A delivered legacy Case cannot be implicitly reinterpreted')
    if (progress.get('diagnostics', {}).get('unresolved_submissions')
            or any(row.get('status') in {'submitted', 'pending', 'running'}
                   for row in progress.get('sessions', []))):
        raise ContractError('Resolve active model tasks before fact-version adoption')
    before = {name: sha256(case/'state'/name) for name in (
        'world_context.json', 'actor_registry.json', 'reconciliation.json',
        'census_adjudication.accepted.json') if (case/'state'/name).is_file()}
    view = establish_baseline(case, explicit=True)
    receipt = dict(revision='case-facts-adoption.v1', case_id=case.name,
                   version_id=view.version_id, original_hashes=before, adopted_at=now())
    path = case/'diagnostics/fact_version_adoption.json'
    if path.is_file():
        prior = read_json(path)
        if prior['version_id'] != view.version_id or prior['original_hashes'] != before:
            raise ContractError('Legacy fact-version adoption receipt changed')
        return prior
    atomic_json(path, receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'status': 'adopted', 'receipt': adopt(args.case_dir)}, ensure_ascii=False))
