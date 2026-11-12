#!/usr/bin/env python3
"""Unified deterministic scene converter with hash-based reuse."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from _lib.contracts.fidelity import validator

HERE = Path(__file__).resolve()
from _lib.paths import SKILLS, SCENARIO_ROOT
from _lib.runtime.case_core import atomic_json, read_json, sha256  # noqa: E402
from _lib.contracts.conversion_policy import validate as validate_policy, authorize_scene


def fallback_api():
    """Load Converter-owned projection without copying its policy into Delivery."""
    lib = SCENARIO_ROOT / "json-coverter" / "skills" / "case-conversion" / "scripts" / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from execution_fallbacks import project_analysis, validate_conversion_input
    return project_analysis, validate_conversion_input


def mela_valid(target):
    lib=SCENARIO_ROOT/"json-coverter"/"skills"/"case-conversion"/"scripts"/"lib"
    if str(lib) not in sys.path: sys.path.insert(0,str(lib))
    from validators import validate_data
    data=read_json(target)
    return validate_data(data)["valid"]


def dual_valid(target):
    """Backward-compatible alias; the authoritative acceptance gate is Mela."""
    return mela_valid(target)


def converter_fingerprint(script):
    """Hash the scene converter and its shared generation helpers."""
    script=Path(script).resolve()
    lib=script.parents[2]/"lib"
    files=[HERE,script,*sorted(lib.glob("*.py")),
           SKILLS/"_lib/delivery/projector.py",
           SKILLS/"_lib/world/topology.py", SKILLS/"_lib/behavior/motion.py",
           SKILLS/"_lib/behavior/compile.py", SKILLS/"_lib/contracts/v3.py",
           SKILLS/"_lib/contracts/lane_locations.py",
           SKILLS/"_lib/contracts/parking.py", SKILLS/"_lib/contracts/entry.py",
           SKILLS/"_lib/contracts/conversion_policy.py",
           SKILLS/"_lib/contracts/semantic_gap.py",
           SKILLS/"_lib/contracts/semantic_motion.py",
           SKILLS/"_lib/delivery/source_constraints.py",
           SKILLS/"_lib/contracts/actors.py", SKILLS/"schemas/actor_fields.schema.json",
           SKILLS/"_lib/behavior/intersection.py", SKILLS/"_lib/experimental/constraint_solver.py",
           SKILLS/"_lib/behavior/lane_change.py",
           SKILLS/"_lib/behavior/lane_completion.py", SKILLS/"_lib/behavior/scene_revision.py", SKILLS/"_lib/runtime/execution_plan.py",
           SKILLS/"_lib/runtime/execution_scope.py",
           SKILLS/"_lib/contracts/census_scope.py", SKILLS/"_lib/contracts/census_adjudication.py",
           SKILLS/"_lib/contracts/behavior_census_scope.py", SKILLS/"_lib/contracts/census_execution.py", SKILLS/"_lib/contracts/census_relevance.py",
           SKILLS/"_lib/contracts/census_history.py", SKILLS/"_lib/contracts/census_information.py",
           SKILLS/"_lib/contracts/unused_association.py",
           SKILLS/"_lib/contracts/execution_assumptions.py",
           SKILLS/"_lib/contracts/peripheral_uncertainty.py",
           SKILLS/"_lib/delivery/checks.py", SKILLS/"_lib/delivery/motion_check.py",
           SKILLS/"_lib/contracts/fidelity.py", SKILLS/"_lib/behavior/fidelity_motion.py",
           SKILLS/"_lib/behavior/longitudinal.py", SKILLS/"schemas/longitudinal_motion.schema.json",
           SKILLS/"schemas/video_fidelity.schema.json", SKILLS/"references/video-fidelity-interface.generated.json",
           SKILLS/"references/fallback-policy.v1.json",
           SKILLS/"references/mela-capabilities.v1.json"]
    digest=hashlib.sha256()
    for path in files:
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def strict_v3_preflight(data, road_source="preview_json"):
    """Compatibility API: only malformed roots block deterministic projection."""
    _, validate_conversion_input = fallback_api()
    validate_conversion_input(data, data.get("scene_type"))
    if data.get('quality_report',{}).get('scene_revision'):
        from _lib.behavior.scene_revision import validate
        validate(data)
    if data.get("quality_report", {}).get("straight_preview"):
        from _lib.behavior.compile import validate_preview_analysis
        validate_preview_analysis(data)
    return True


def fail(result_path, scene_type, input_hash, code, message, *, error=None):
    diagnostics = dict(getattr(error, 'diagnostics', {}) or {})
    for key, attribute in (('rule_id', 'rule_id'), ('path', 'path'),
                           ('related_paths', 'related_paths'), ('kind', 'kind'),
                           ('actor_id', 'actor_id')):
        value = getattr(error, attribute, None)
        if value is not None:
            diagnostics[key] = value
    if diagnostics.get('rule_id') == 'world.topology.arm_overlap':
        diagnostics.update(owner='world', retry_kind='world_adjudication',
                           recovery_hint='Resolve reference-line heading/role against bound evidence; do not merge road IDs or retry unchanged analysis.')
    diagnostics.update(input_sha256=input_hash)
    atomic_json(result_path, {"status":"failed","scene_type":scene_type,"input_sha256":input_hash,"target_sha256":None,"output_path":None,"error_code":code,"message":message,
                              "diagnostics": diagnostics})
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--scene-type", required=True, choices=("highway", "intersection")); parser.add_argument("--result", required=True)
    parser.add_argument("--workflow-contract", default="video-case-v2-short-aware-packed")
    parser.add_argument("--road-source", choices=("preview_json","external_xodr"), default="preview_json")
    parser.add_argument("--policy", help="Private frozen conversion policy (new Cases)")
    parser.add_argument("--policy-sha256", help="Expected canonical policy digest")
    parser.add_argument("--semantic-gap", help="Frozen versioned semantic gap or temporal motion decision")
    args = parser.parse_args(); source, target, result = map(lambda value: Path(value).resolve(), (args.input,args.output,args.result))
    input_hash = sha256(source)
    data = read_json(source)
    from _lib.contracts.census_scope import validate_analysis as validate_scope_analysis
    try:
        scope_sha256 = validate_scope_analysis(data)
    except ValueError as error:
        return fail(result,args.scene_type,input_hash,getattr(error,'code','conversion_failed'),str(error),error=error)
    try: validator("analysis.schema.json").validate(data)
    except Exception as error: return fail(result,args.scene_type,input_hash,"schema_validation_failed",str(error))
    if data.get("scene_type") != args.scene_type: return fail(result,args.scene_type,input_hash,"schema_validation_failed","scene_type mismatch")
    current_contract = args.workflow_contract == "video-case-world-behavior"
    semantic_gap = None
    if args.semantic_gap:
        if not current_contract:
            return fail(result,args.scene_type,input_hash,'conversion_failed',
                        'semantic gap decisions require the v3 workflow contract')
        try:
            from _lib.contracts.semantic_gap import validate as validate_semantic_gap
            semantic_gap = validate_semantic_gap(data, input_hash, read_json(Path(args.semantic_gap)))
        except (OSError, ValueError) as error:
            return fail(result,args.scene_type,input_hash,'conversion_failed',str(error),error=error)
    policy = None
    try:
        if args.policy_sha256 and not args.policy:
            raise ValueError('--policy-sha256 requires --policy')
        if args.policy:
            if not current_contract:
                raise ValueError('conversion policy requires the v3 workflow contract')
            policy = validate_policy(read_json(Path(args.policy)), input_sha256=input_hash,
                                     scene_type=args.scene_type, road_source=args.road_source)
            if args.policy_sha256 and policy['policy_sha256'] != args.policy_sha256:
                raise ValueError('conversion policy digest differs from frozen request')
    except (OSError, ValueError) as error:
        return fail(result, args.scene_type, input_hash, 'conversion_policy_blocked', str(error), error=error)
    if current_contract:
        try: strict_v3_preflight(data,args.road_source)
        except Exception as error: return fail(result,args.scene_type,input_hash,"conversion_failed",str(error))
    elif data.get("quality_report", {}).get("execution_ready") is False:
        return fail(result, args.scene_type, input_hash, "analysis_execution_blocked",
                    "analysis execution is blocked; deterministic fallbacks require the current workflow contract")
    script = SCENARIO_ROOT/"json-coverter"/"skills"/"case-conversion"/"scripts"/"scenes"/args.scene_type/"convert.py"
    fallback_api()
    converter_hash = converter_fingerprint(script)
    if result.exists() and target.exists():
        old = read_json(result)
        if (old.get("status") in ("done", "done_degraded") and old.get("input_sha256") == input_hash
                and old.get("workflow_contract") == args.workflow_contract and old.get("road_source") == args.road_source
                and old.get('policy_sha256') == (policy['policy_sha256'] if policy else None)
                and old.get('semantic_gap') == semantic_gap
                and old.get("converter_sha256") == converter_hash and old.get("target_sha256") == sha256(target) and mela_valid(target)):
            if current_contract:
                from scene_generation import RECEIPT_VERSION
                from _lib.delivery.checks import validate_target
                if old.get("fallback_receipt_version") == RECEIPT_VERSION:
                    try:
                        validate_target(data, read_json(target), args.road_source, old)
                    except ValueError as error:
                        return fail(result, args.scene_type, input_hash, "conversion_failed", str(error))
                    print(json.dumps(old, ensure_ascii=False)); return 0
            else:
                print(json.dumps(old, ensure_ascii=False)); return 0
    if current_contract:
        project_analysis, _ = fallback_api()
        try:
            projected, fallbacks, excluded_actor_ids = project_analysis(
                data, args.scene_type, policy=policy, semantic_gap=semantic_gap)
            from _lib.contracts.unused_association import preserve_conversion
            preserve_conversion(data, excluded_actor_ids)
        except Exception as error:
            return fail(result,args.scene_type,input_hash,getattr(error, 'code', 'conversion_failed'),str(error), error=error)
    else:
        projected, fallbacks, excluded_actor_ids = data, [], []
    target.parent.mkdir(parents=True, exist_ok=True)
    fd,temp_name=tempfile.mkstemp(prefix="."+target.name,suffix=".json",dir=target.parent); os.close(fd); temporary=Path(temp_name)
    fd,projected_name=tempfile.mkstemp(prefix=".projected-analysis-",suffix=".json",dir=target.parent); os.close(fd); projected_path=Path(projected_name)
    fd,fallbacks_name=tempfile.mkstemp(prefix=".scene-fallbacks-",suffix=".json",dir=target.parent); os.close(fd); fallbacks_path=Path(fallbacks_name)
    atomic_json(projected_path, projected)
    env = dict(os.environ, SCENE_ANALYSIS_INPUT=str(projected_path), SCENE_TARGET_OUTPUT=str(temporary),
               SCENE_WORKFLOW_CONTRACT=args.workflow_contract, SCENE_FALLBACKS_OUTPUT=str(fallbacks_path))
    try:
        completed = subprocess.run([sys.executable,str(script)],env=env,capture_output=True,text=True)
    finally:
        projected_path.unlink(missing_ok=True)
    if completed.returncode or not temporary.exists():
        temporary.unlink(missing_ok=True)
        fallbacks_path.unlink(missing_ok=True)
        return fail(result,args.scene_type,input_hash,"conversion_failed",(completed.stderr or completed.stdout)[-2000:])
    # An explicit [] means no scene defaults; missing/corrupt records are errors.
    fallback_api()  # install the Converter library path
    from scene_generation import read_scene_fallbacks, fallback_receipt
    try:
        scene_fallbacks = authorize_scene(read_scene_fallbacks(fallbacks_path), policy)
        staged_receipt = fallback_receipt(fallbacks, scene_fallbacks, excluded_actor_ids) if current_contract else {
            "fallbacks": fallbacks + scene_fallbacks, "excluded_actor_ids": excluded_actor_ids}
        if policy is not None:
            staged_receipt.update(conversion_policy=policy, policy_sha256=policy['policy_sha256'], input_sha256=input_hash)
        if semantic_gap is not None:
            staged_receipt['semantic_gap'] = semantic_gap
        if scope_sha256:
            staged_receipt['census_scope_sha256'] = scope_sha256
    except ValueError as error:
        temporary.unlink(missing_ok=True)
        return fail(result, args.scene_type, input_hash, getattr(error, 'code', 'conversion_failed'), str(error), error=error)
    finally:
        fallbacks_path.unlink(missing_ok=True)
    if not mela_valid(temporary):
        temporary.unlink(missing_ok=True)
        return fail(result,args.scene_type,input_hash,"conversion_failed",(completed.stderr or completed.stdout)[-2000:])
    if current_contract:
        from _lib.delivery.checks import validate_target
        try:
            checks = validate_target(data, read_json(temporary), args.road_source,
                                     staged_receipt)
        except ValueError as error:
            temporary.unlink(missing_ok=True)
            return fail(result,args.scene_type,input_hash,'conversion_failed',str(error))
    else:
        checks = None
    os.replace(temporary,target)
    directory=os.open(target.parent,os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    # Merge all fallbacks and compute degraded status
    all_fallbacks = fallbacks + scene_fallbacks
    SIGNIFICANT_REASONS = {
        "executable_field_missing", "canonical_road", "canonical_intersection",
        "canonical_four_way_completion", "topology_arm_snap", "topology_arm_merge",
        "lane_renumber", "missing_or_invalid_collection", "missing_or_unknown",
        "missing_unknown_or_unsupported_enum", "missing_or_invalid_speed_samples",
        "trajectory_does_not_cover_duration", "lane_change_construction_failed",
        "topology_duplicate_arm_skipped", "topology_endpoint_already_connected",
        "topology_arm_pad_to_3",
        "missing_unknown_or_time_varying",
    }
    degraded = bool(all_fallbacks) if current_contract else any(
        f.get("reason") in SIGNIFICANT_REASONS for f in all_fallbacks)
    status = "done_degraded" if degraded else "done"
    record = {"status":status,"scene_type":args.scene_type,"input_sha256":input_hash,"converter_sha256":converter_hash,"target_sha256":sha256(target),"output_path":str(target),"error_code":None,
              "workflow_contract":args.workflow_contract,"road_source":args.road_source,
              **staged_receipt}
    if checks is not None:
        record['delivery_checks'] = checks
    atomic_json(result,record); print(json.dumps(record,ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
