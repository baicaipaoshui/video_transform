#!/usr/bin/env python3
"""Read-only preview candidate and execution readiness inspection."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _lib.behavior.repair import materialize_preview
from _lib.behavior.compile import evidence_frames, validate_compiled_behavior, execution_issues
from _lib.contracts.v3 import build_analysis
from _lib.runtime.case_core import read_json
from _lib.world.readiness import ego_carriageway_execution_scope


def inspect_preview(case, candidate):
    case=Path(case)
    world,behavior,certificate=materialize_preview(case,read_json(candidate))
    frames=evidence_frames(case); registry=read_json(case/'state/actor_registry.json')
    validate_compiled_behavior(behavior,world,registry,frames,execution=False)
    analysis,status=build_analysis(world,behavior,read_json(case/'case.json')['parameters'],
        observation_issues=execution_issues(behavior,world,registry,frames),preview_review=certificate)
    result={'review_status':'accepted','outcome':certificate['outcome'],
            'execution_status':status,'execution_issues':analysis['quality_report']['execution_issues'],
            'render_verification':'not_executed'}
    road=world['world']['road']
    if road.get('one_way') is False and not ego_carriageway_execution_scope(world,behavior):
        result['required_change']={'rule_id':'world.opposing_separation_evidence_required',
            'path':'world.road.roads[0].road_geometry.opposing_separation',
            'message':'World owner must establish evidenced physical separation before excluding opposing lane-count uncertainty.'}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir',required=True,type=Path)
    parser.add_argument('--candidate',required=True,type=Path)
    args=parser.parse_args()
    try:
        result=inspect_preview(args.case_dir,args.candidate)
    except Exception as error:
        result={'review_status':'needs_review' if getattr(error,'code',None)=='preview_evidence_insufficient' else 'rejected',
                'error':getattr(error,'diagnostics',{'message':str(error)}),'render_verification':'not_executed'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    sys.exit(0 if result['review_status']=='accepted' else 1)
