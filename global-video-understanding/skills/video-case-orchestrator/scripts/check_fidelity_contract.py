#!/usr/bin/env python3
"""Generate or check the schema-derived interface inventory (no hand-written types)."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from _lib.contracts.fidelity import INVENTORY, inventory, preflight

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--generate',action='store_true')
    args=parser.parse_args()
    if args.generate:
        INVENTORY.write_text(json.dumps(inventory(),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'passed','fingerprint':preflight()}))
