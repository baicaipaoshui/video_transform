#!/usr/bin/env python3
"""Explicit local policy adoption before any Behavior task; no provider calls."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _lib.contracts.unused_association import adopt

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', type=Path, required=True)
    args = parser.parse_args()
    receipt = adopt(args.case_dir.resolve(strict=True))
    print(json.dumps({'status': 'adopted', 'receipt': receipt}, ensure_ascii=False))
