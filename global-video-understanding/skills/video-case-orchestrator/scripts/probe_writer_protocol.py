#!/usr/bin/env python3
"""Classify a three-session writer probe transcript for v3 rollout gating."""
import argparse
import json
from pathlib import Path


def classify(records):
    if len(records)!=3: raise ValueError("exactly three probe records are required")
    if all(item.get("overwrite_safe") for item in records): return "overwrite_safe"
    if all(item.get("multiple_files_safe") for item in records): return "immutable_numbered_drafts"
    if all(item.get("writes_per_session")==1 for item in records): return "v3_rollout_blocked_single_write"
    return "unsupported"


def main():
    parser=argparse.ArgumentParser(description="Evaluate three QwenPaw file-write probes")
    parser.add_argument("--records",required=True); parser.add_argument("--output")
    args=parser.parse_args(); records=json.loads(Path(args.records).read_text(encoding="utf-8")); result={"probe_count":len(records),"protocol":classify(records)}
    text=json.dumps(result,ensure_ascii=False,indent=2)+"\n"
    if args.output: Path(args.output).write_text(text,encoding="utf-8")
    print(text,end="")


if __name__=="__main__": main()
