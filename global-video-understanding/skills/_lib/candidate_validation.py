"""Read-only worker candidate checks using the production consumer functions."""
import argparse
import json
from pathlib import Path

from _lib.runtime.case_core import read_json, validate_evidence
from _lib.contracts.v3 import validate_world
from _lib.world.readiness import normalize_world, check_world_shape, readiness_issues
from _lib.behavior.compile import (evidence_frames, prepare_behavior_candidate,
                                  prepare_behavior_execution, validate_compiled_behavior)


def validate_world_candidate(candidate, case=None):
    from _lib.world import admission
    if case and admission.enabled(case):
        from _lib.world.semantic import materialize_world
        from _lib.world.single_world import enabled as single_enabled
        raw = read_json(candidate)
        world_fields = raw.get('world') if isinstance(raw, dict) else None
        # A committed/reconciled full World is not an initial parts candidate.
        if (not isinstance(world_fields, dict) or 'road' not in world_fields or 'request_sha256' in raw or
                Path(candidate).name == 'world_context.candidate.json' or
                (single_enabled(case) and not (Path(case)/'state/world_context.json').is_file())):
            raw = materialize_world(case, raw, initial=True)
        data, repairs = admission.normalize(raw, case=case,
            normalize_actor_paths=(not (Path(case)/'state/world_context.json').is_file()))
        probe = Path(case)/'state/probe.json'
        duration = read_json(probe)['probe']['duration_s'] if probe.is_file() else None
        blocking = admission.blocking_issues(admission.issues(data, duration, case=case))
        if blocking:
            raise admission.AdmissionError(blocking)
        return {'candidate_validation':'passed','handoff_validation':'passed','handoff_issues':[],
                'normalizations':repairs,'execution_validation':'not_executed','case_modified':False}
    from _lib.world.gaps import enabled
    observation_contract=bool(case and enabled(case))
    data = normalize_world(read_json(candidate))
    validate_world(data, admission.duration_for(case) if case else None, observation_contract=observation_contract)
    check_world_shape(data, observation_contract=observation_contract)
    validate_evidence(data)
    issues = readiness_issues(data, observation_contract=observation_contract)
    return {"candidate_validation": "passed", "handoff_validation": "blocked" if issues else "passed",
            "handoff_issues": issues, "case_modified": False}


def validate_behavior_candidate(candidate, case, observation_only=False):
    case = Path(case).resolve(strict=True)
    raw = read_json(candidate)
    from _lib.contracts.census_scope import split, validate_review
    raw, scope_review = split(raw)
    from _lib.behavior.repair import materialize
    raw = materialize(case, raw)
    frames = evidence_frames(case)
    registry = read_json(case / "state/actor_registry.json")
    world = read_json(case / "state/world_context.json")
    if observation_only:
        from _lib.behavior.compile import execution_issues
        data, repairs = prepare_behavior_candidate(raw, frames, registry, world, case=case)
        from _lib.contracts.behavior_census_scope import enabled as deferred_scope_enabled
        from _lib.contracts.unused_association import enabled as unused_enabled
        from _lib.contracts.execution_assumptions import enabled as assumptions_enabled
        validate_compiled_behavior(data, world, registry, frames, execution=False,
            allow_pending_identity=(scope_review is not None or unused_enabled(case) or assumptions_enabled(case)) and deferred_scope_enabled(case))
        from _lib.contracts.core_lane_continuity import validate as validate_core_lane_continuity
        validate_core_lane_continuity(case, data)
        from _lib.runtime.census_scope_recovery import validate_patch
        validate_patch(case, data)
        checked_review = validate_review(case, data, scope_review)
        from _lib.contracts.behavior_handoff import validate_execution
        validate_execution(case, world, data, frames, checked_review)
        from _lib.runtime.execution_scope import validate_behavior_scope
        validate_behavior_scope(case, world, data)
        issues = execution_issues(data, world, registry, frames)
        return {'candidate_validation':'passed','case_modified':False,
                'normalizations': repairs, 'execution_ready':not issues,
                'execution_issues':issues,'delivery_validation':'not_executed'}
    data, repairs = prepare_behavior_execution(raw, frames, registry, world, case=case)
    validate_compiled_behavior(data, world,
                               registry, frames, allow_execution_extensions=True)
    from _lib.contracts.core_lane_continuity import validate as validate_core_lane_continuity
    validate_core_lane_continuity(case, data)
    from _lib.runtime.census_scope_recovery import validate_patch
    validate_patch(case, data)
    checked_review = validate_review(case, data, scope_review)
    from _lib.contracts.behavior_handoff import validate_execution
    validate_execution(case, world, data, frames, checked_review)
    from _lib.runtime.execution_scope import validate_behavior_scope
    validate_behavior_scope(case, world, data)
    return {"candidate_validation": "passed", "case_modified": False,
            "normalizations": repairs, "delivery_validation": "not_executed"}


def main(stage):
    parser = argparse.ArgumentParser(description=f"Read-only {stage} candidate validation")
    parser.add_argument("--candidate", required=True, type=Path)
    if stage == "world":
        parser.add_argument('--case-dir', type=Path, help='read frozen fact revision; omit for legacy validation')
        parser.add_argument("--handoff", action="store_true", help="require full World handoff readiness")
    else:
        parser.add_argument("--case-dir", required=True, type=Path)
        parser.add_argument('--observation-only', action='store_true', help='validate observation acceptance without rewriting motion; report execution gaps')
    args = parser.parse_args()
    try:
        result = (validate_world_candidate(args.candidate,args.case_dir) if stage == "world" else
                  validate_behavior_candidate(args.candidate, args.case_dir, args.observation_only))
    except Exception as error:
        print(json.dumps({"candidate_validation": "failed", "case_modified": False,
                          "error_type": type(error).__name__, "error": str(error),
                          "diagnostics": getattr(error, "diagnostics", None)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return int(stage == "world" and args.handoff and result["handoff_validation"] != "passed")
