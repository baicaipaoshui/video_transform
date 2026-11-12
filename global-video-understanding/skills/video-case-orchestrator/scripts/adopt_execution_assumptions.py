#!/usr/bin/env python3
"""Explicit local simulation-policy adoption; no model or budget operation."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _lib.contracts.execution_assumptions import adopt

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', type=Path, required=True)
    parser.add_argument('--retain-unobserved-motion', action='store_true',
                        help='Also adopt source-bound retained-vehicle unseen-tail approximation')
    parser.add_argument('--bounded-execution', action='store_true',
                        help='Also adopt source-bound wording, timing and bounded numeric planning')
    args = parser.parse_args()
    case = args.case_dir.resolve(strict=True)
    if args.bounded_execution:
        from _lib.contracts.bounded_execution import adopt as adopt_bounded
        receipt = adopt_bounded(case)
    elif args.retain_unobserved_motion:
        from _lib.contracts.retained_motion import adopt as adopt_motion
        receipt = adopt_motion(case)
    else:
        receipt = adopt(case)
    print(json.dumps({'status':'adopted', 'receipt':receipt}, ensure_ascii=False))
