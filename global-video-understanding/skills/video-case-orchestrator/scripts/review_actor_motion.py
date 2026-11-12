#!/usr/bin/env python3
"""Explicit failed-case observation review followed by the full v3 pipeline."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from _lib.paths import EXTRACTOR
from _lib.runtime.case_core import read_json, sha256, now
from _lib.runtime.orchestrator import QwenPawBackend
from _lib.runtime.v3 import V3Orchestrator
from _lib.behavior.actor_motion_review import run

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--video',required=True); parser.add_argument('--case-id',required=True)
    parser.add_argument('--actor-id',required=True)
    parser.add_argument('--mode', choices=['motion', 'identity_link', 'identity_split'], default='motion')
    args=parser.parse_args()
    case=(EXTRACTOR/'cases'/args.case_id).resolve()
    if case.parent!=(EXTRACTOR/'cases').resolve(): parser.error('Invalid case')
    binding=read_json(case/'state/binding.json')
    if Path(args.video).resolve()!=Path(binding['source_path']).resolve() or sha256(args.video)!=binding['source_sha256']:
        parser.error('Source changed')
    runner=V3Orchestrator(QwenPawBackend())
    try:
        # Parent completion is immutable even if revision allocation/validation fails.
        from _lib.runtime.identity_revision import derive, LINEAGE
        if (args.mode in {'identity_link', 'identity_split'} and not (case/LINEAGE).exists()
                and runner.progress(case).get('status') in {'done', 'done_degraded'}):
            case = derive(runner, case, args.video, args.actor_id, mode=args.mode)
        if args.mode == 'motion' or runner.progress(case).get('status') not in {'done', 'done_degraded'}:
            run(runner,case,args.actor_id, mode=args.mode)
        result=runner.run_v3(args.video,False,case.name,read_json(case/'case.json')['parameters'],True)
    except Exception as error:
        result=runner.progress(case)
        last=next((s for s in reversed(result.get('sessions',[]))
                   if s.get('stage')==('actor_identity_review_' if args.mode in {'identity_link', 'identity_split'} else 'actor_motion_review_')+args.actor_id),{})
        code=getattr(error,'code',None) or str(error)
        message=str(error)
        if message=='model_attempts_exhausted' and last.get('error_code'):
            code=last['error_code']; message=last.get('message') or message
        result.update(status='needs_review',phase='failed',artifacts={},generation_status='not_generated',updated_at=now(),
            error=dict(code=code,message=message,resume_from='aggregating'))
        if runner.progress(case).get('status') not in {'done', 'done_degraded'}:
            runner.save_progress(case,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
