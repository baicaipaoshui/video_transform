#!/usr/bin/env python3
"""Read-only deterministic replay of a v3 Case through target conversion."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
SKILLS = HERE.parents[2]
sys.path.insert(0, str(SKILLS))

from _lib.behavior.compile import evidence_frames, prepare_behavior_candidate, validate_compiled_behavior  # noqa: E402
from _lib.stages.batch import source_world, validate_history_inputs, validate_registry_checkpoint  # noqa: E402
from _lib.behavior.repair import materialize  # noqa: E402
from _lib.delivery.converter import strict_v3_preflight  # noqa: E402
from _lib.delivery.checks import validate_target  # noqa: E402
from _lib.runtime.orchestrator import schema, validate_analysis  # noqa: E402
from _lib.contracts.frame_evidence import validate_batch_frames  # noqa: E402
from _lib.contracts.v3 import build_analysis, gate_degradations, normalize_batch_candidate, normalize_parameters, validate_batch, validate_behavior, validate_capabilities, validate_world  # noqa: E402
from _lib.runtime.case_core import atomic_json, read_json, sha256, validate_evidence, validate_evidence_items, validate_registry  # noqa: E402
from _lib.world.readiness import check_world_shape, normalize_world  # noqa: E402


def behavior_source(case: Path, progress: dict) -> tuple[Path, str]:
    from _lib.behavior.input_binding import is_complete_behavior, replayable_archives
    committed = case / "state/behavior.json"
    candidate = case / "state/behavior.candidate.json"
    choices = [(committed, "committed")]
    if candidate.is_file() and is_complete_behavior(read_json(candidate)):
        choices.append((candidate, "candidate"))
    choices.extend((path, "invalid_archive") for path in replayable_archives(case))
    for path, source_type in choices:
        if path.is_file():
            if not path.resolve().is_relative_to(case.resolve()):
                raise ValueError("Behavior archive belongs to another Case")
            return path, source_type
    raise ValueError("no Behavior checkpoint or archived candidate is available")


def frozen_conversion_inputs(case, rebuilt):
    """Reuse a saved motion decision only with its unchanged, rebuilt source facts."""
    decision_path = case/'state/conversion_semantic_gap.json'
    if not decision_path.is_file():
        return None
    source = case/'final/analysis.json'
    frozen = read_json(source)
    comparison = copy.deepcopy(rebuilt)
    # Scope implementation hashes identify releases, not different observations.
    # Every other scope field (inputs, exclusions, policy and review) must match.
    current = comparison.get('quality_report', {}).get('execution_actor_scope')
    previous = frozen.get('quality_report', {}).get('execution_actor_scope')
    if isinstance(current, dict) and isinstance(previous, dict):
        for key in ('implementation_sha256', 'sha256'):
            if key in previous: current[key] = previous[key]
            else: current.pop(key, None)
    if comparison != frozen:
        raise ValueError('Rebuilt analysis differs from frozen motion inputs; a new bound decision is required')
    from _lib.contracts.semantic_gap import validate
    validate(frozen, sha256(source), read_json(decision_path))
    return source, decision_path


def replay(case: Path) -> dict:
    case = case.resolve(strict=True)
    progress = read_json(case / "state/progress.json")
    record = read_json(case / "case.json")
    binding = read_json(case / "state/binding.json")
    if sha256(Path(binding["source_path"])) != binding["source_sha256"]:
        raise ValueError("source binding mismatch")
    parameters = normalize_parameters(record.get("parameters"))
    from _lib.contracts.census_adjudication import ACCEPTED as CENSUS_ACCEPTED
    from _lib.runtime.identity_revision import checkpoint_proof
    if not (case/CENSUS_ACCEPTED).is_file():
        checkpoint_proof(case)
    world = normalize_world(read_json(case / "state/world_context.json"))
    from _lib.world.gaps import enabled as observation_contract_enabled
    from _lib.world import admission, actor_facts
    observation_contract = observation_contract_enabled(case)
    if admission.enabled(case):
        findings = admission.blocking_issues(admission.issues(world, admission.duration_for(case), case=case))
        if findings: raise admission.AdmissionError(findings)
    else:
        validate_world(world, admission.duration_for(case), observation_contract=observation_contract)
        check_world_shape(world, observation_contract=observation_contract)
    stages = [{"stage": "world", "status": "passed"}]

    manifest_path = case / "manifest.json"
    manifest, previous = {}, None
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        from _lib.contracts.batch_capacity import validate_manifest
        validate_manifest(case, manifest)
        previous = None
        for spec in manifest.get("batches", []):
            batch_id = spec["batch_id"]
            batch_path = case / "batches" / f"{batch_id}.json"
            if not batch_path.is_file():
                raise ValueError(f"missing committed Batch checkpoint: {batch_id}")
            batch = read_json(batch_path)
            batch, _ = normalize_batch_candidate(
                batch, case_id=case.name, batch_id=batch_id,
                session_id=batch.get("session_id") or "offline-preflight",
                world_evidence_epoch=spec["world_evidence_epoch"])
            batch_world = source_world(case, spec)
            if previous is None:
                previous = batch_world["actor_hypotheses"]
            validate_batch(batch, batch_world, spec["assigned_question_ids"],
                           spec.get("assigned_ambiguity_ids", []),
                           allow_collection_item_conflicts=True,
                           observation_contract=observation_contract)
            validate_evidence_items(batch["observations"], ("observations",))
            validate_evidence(batch)
            validate_batch_frames(batch, spec["frames"], previous)
            refs = [actor for item in batch["observations"] for actor in item.get("actor_ids", [])]
            validate_registry(previous, batch["registry_after"], refs)
            from _lib.contracts.census_adjudication import ACCEPTED
            validate_history_inputs(case, spec, previous, batch, required=(case/ACCEPTED).is_file())
            previous = batch["registry_after"]
            stages.append({"stage": batch_id, "status": "passed"})

    registry = read_json(case / "state/actor_registry.json")
    validate_registry_checkpoint(case, previous, manifest)
    source_path, source_type = behavior_source(case, progress)
    from _lib.behavior.input_binding import verify as verify_behavior_inputs, require_session
    verify_behavior_inputs(case)
    if source_type != 'committed':
        require_session(case)
    frames = evidence_frames(case)
    from _lib.contracts import census_scope
    raw, raw_scope_review = census_scope.split(read_json(source_path))
    behavior, _ = prepare_behavior_candidate(
        materialize(case, raw), frames, registry, world, case=case)
    # Delivery replay validates the accepted observation contract here.  Any
    # missing execution-only values are projected by the deterministic
    # Converter later in this function, so they must not stop the replay at
    # the Behavior boundary.
    from _lib.contracts.unused_association import enabled as unused_enabled
    from _lib.contracts.execution_assumptions import enabled as assumptions_enabled
    validate_compiled_behavior(behavior, world, registry, frames, execution=False,
                              allow_pending_identity=unused_enabled(case) or assumptions_enabled(case))
    from _lib.contracts.census_scope import verified_review
    verified_review(case, behavior)
    stages.append({"stage": "behavior", "status": "passed", "source_type": source_type,
                   "source_path": str(source_path)})

    from _lib.behavior.compile import execution_issues
    from _lib.runtime import execution_scope
    from _lib.runtime.routing import select_route, effective_parameters, CapabilityBlocked, require_lane_evidence
    from _lib.world.gaps import IdentityEvidenceGap
    scope = None
    if execution_scope.enabled(case):
        # Replay the same projection and signed dispositions as the online Builder.
        # Candidate-only replay keeps the validated candidate path above.
        if source_type == 'committed':
            world, behavior, scope = execution_scope.inputs(case, persist=False)
            registry = execution_scope.registry(case, behavior)
        else:
            raise ValueError('Execution-scope replay requires a committed Behavior checkpoint')
    observations = execution_issues(behavior, world, registry, frames, allow_execution_extensions=True)
    route = select_route(world, behavior, parameters, observations,
                         intersection_enabled=bool(read_json(case/'state/execution_policy.json').get('intersection_approximation')),
                         observation_contract=observation_contract,
                         actor_fact_review=actor_facts.enabled(case),
                         duration_s=read_json(case/'state/probe.json')['probe'].get('duration_s'))
    if route['route'] == 'unsupported': raise CapabilityBlocked(route['issues'])
    require_lane_evidence(route)
    if route['route'] == 'blocked':
        if any(row.get('rule_id') == 'identity_uncertain' for row in route['issues']):
            raise IdentityEvidenceGap(route['issues'])
        raise ValueError('Offline replay requires additional execution evidence: ' + route['reason'])
    if route['route'] != 'full':
        raise ValueError('Offline replay requires a validated approximation certificate: ' + route['route'])
    parameters = effective_parameters(parameters, route)
    analysis, build_status = build_analysis(
        world, behavior, parameters,
        observation_issues=observations,
        observation_contract=observation_contract,
        media_probe=read_json(case/'state/probe.json')['probe'],
    )
    validate_analysis(analysis, registry)
    build_status = execution_scope.annotate(case, analysis, build_status, persist=False)
    from _lib.stages.delivery import record_batch_class_fallbacks
    record_batch_class_fallbacks(analysis, progress)
    validate_analysis(analysis, registry)
    gate_degradations(analysis["quality_report"])
    strict_v3_preflight(analysis, parameters.get("road_source", "preview_json"))
    frozen_inputs = frozen_conversion_inputs(case, analysis)
    validate_capabilities(parameters["road_source"], analysis["scene_type"])
    stages.append({"stage": "builder_and_preflight", "status": "passed", "build_status": build_status})
    if build_status == "placeholder_generated":
        raise ValueError(f"only placeholder can be generated: {analysis['quality_report'].get('placeholder_reasons', [])}")

    with tempfile.TemporaryDirectory(prefix="video_case_preflight_") as folder:
        temporary = Path(folder)
        analysis_path = temporary / "analysis.json"
        target_path = temporary / "target.json"
        result_path = temporary / "conversion_result.json"
        if frozen_inputs:
            shutil.copyfile(frozen_inputs[0], analysis_path)
            analysis = read_json(analysis_path)
        else:
            atomic_json(analysis_path, analysis)
        command = [sys.executable, str(SKILLS / 'case-delivery/scripts/convert.py'),
                   "--input", str(analysis_path), "--output", str(target_path),
                   "--result", str(result_path), "--scene-type", analysis["scene_type"],
                   "--workflow-contract", "video-case-world-behavior",
                   "--road-source", parameters.get("road_source", "preview_json")]
        if frozen_inputs:
            command.extend(['--semantic-gap', str(frozen_inputs[1])])
        from _lib.contracts.conversion_policy import enabled as policy_enabled, build as build_policy, default_completion
        if policy_enabled(case):
            policy = build_policy(parameters, sha256(analysis_path), analysis['scene_type'], record['case_id'],
                                  default_completion=default_completion(case))
            policy_path = temporary/'conversion_policy.json'
            atomic_json(policy_path, policy)
            command.extend(['--policy', str(policy_path), '--policy-sha256', policy['policy_sha256']])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        conversion = read_json(result_path) if result_path.is_file() else {}
        if completed.returncode or conversion.get("status") not in {"done", "done_degraded"} or not target_path.is_file():
            raise ValueError("offline conversion failed: " + (conversion.get("message") or completed.stderr or completed.stdout)[-1500:])
        stages.append({"stage": "converter", "status": "passed",
                       "target_sha256": conversion["target_sha256"], "conversion_status": conversion['status'],
                       "fallback_count": len(conversion.get('fallbacks', [])),
                       "excluded_actor_ids": conversion.get('excluded_actor_ids', [])})
        checks = validate_target(analysis, read_json(target_path), parameters["road_source"], conversion)
        stages.append({"stage": "target_checks", **checks})
        atomic_json(temporary/'target_checks.json', checks)
        report = subprocess.run([sys.executable, str(SKILLS / 'case-delivery/scripts/report.py'),
                                 "--input", str(analysis_path),
                                 "--markdown", str(temporary / "report.md"),
                                 "--docx", str(temporary / "report.docx"),
                                 '--conversion', str(result_path), '--checks', str(temporary/'target_checks.json'),
                                 '--target', str(target_path)],
                                capture_output=True, text=True, timeout=120)
        if report.returncode or any(not (temporary / name).is_file()
                                    for name in ("report.md", "report.docx")):
            raise ValueError("offline report failed: " + report.stderr[-1500:])
        stages.append({"stage": "report", "status": "passed"})

    return {
        "case_id": case.name,
        "offline_preflight": "passed",
        "mode": "checkpoint-replay",
        # Until replay and online recovery use one complete dependency plan,
        # format success must not promise that no model task will be scheduled.
        "safe_to_retry_without_model": False,
        "recovery_plan_verification": "not_executed",
        "publication_verification": "not_executed",
        "expected_terminal_status": 'done_degraded' if conversion['status'] == 'done_degraded' else build_status,
        "behavior_source_type": source_type,
        "network_and_model_runtime_checked": False,
        "stages": stages,
    }


def verify_publication(case: Path) -> dict:
    """Reconvert the published bytes, never rebuild analysis or mutate the Case."""
    from _lib.stages.delivery import CaseDelivery
    from _lib.contracts.conversion_policy import validate as validate_policy, enabled as policy_enabled
    case = case.resolve(strict=True)
    progress = read_json(case/'state/progress.json')
    record = read_json(case/'case.json')
    if record['case_id'] != case.name or progress['case_id'] != case.name:
        raise ValueError('Publication case_id mismatch')
    if progress.get('status') not in {'done', 'done_degraded'}:
        raise ValueError('Publication verification requires a completed Case')
    CaseDelivery.validate_completed(case, progress)
    for name, artifact in progress['artifacts'].items():
        if artifact['sha256'] != sha256(case/'final'/name):
            raise ValueError('Published and local artifact hashes differ: ' + name)
    conversion = read_json(case/'state/conversion_result.json')
    analysis_path = case/'final/analysis.json'
    analysis = read_json(analysis_path)
    policy = conversion.get('conversion_policy')
    if policy_enabled(case) and policy is None:
        raise ValueError('New-contract publication lacks frozen conversion policy')
    if policy is not None:
        validate_policy(policy, input_sha256=sha256(analysis_path), scene_type=analysis['scene_type'],
                        road_source=conversion['road_source'])
        frozen = case/'state/conversion_policies'/f"{policy['policy_sha256']}.json"
        if read_json(frozen) != policy:
            raise ValueError('Published receipt differs from frozen conversion policy')
    digests = []
    converter_hashes = []
    with tempfile.TemporaryDirectory(prefix='publication_verify_') as directory:
        root = Path(directory)
        for index in range(2):
            target, receipt = root/f'target_{index}.json', root/f'conversion_{index}.json'
            command = [sys.executable, str(SKILLS/'case-delivery/scripts/convert.py'),
                       '--input', str(analysis_path), '--output', str(target), '--result', str(receipt),
                       '--scene-type', analysis['scene_type'], '--workflow-contract', record['workflow_contract'],
                       '--road-source', conversion.get('road_source', record['parameters']['road_source'])]
            if policy is not None:
                policy_path = root/'policy.json'; atomic_json(policy_path, policy)
                command += ['--policy', str(policy_path), '--policy-sha256', policy['policy_sha256']]
            semantic = case/'state/conversion_semantic_gap.json'
            if semantic.is_file():
                command += ['--semantic-gap', str(semantic)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
            result = read_json(receipt) if receipt.is_file() else {}
            if completed.returncode or result.get('status') not in {'done', 'done_degraded'}:
                raise ValueError('Published analysis replay failed: ' + str(result.get('message') or completed.stderr)[-1500:])
            digests.append(sha256(target))
            converter_hashes.append(result.get('converter_sha256'))
        deterministic = digests[0] == digests[1]
        matches = all(value == conversion['target_sha256'] for value in digests)
    return dict(case_id=case.name, mode='publication-verify',
                offline_preflight='passed' if deterministic and matches else 'failed',
                publication_verification='passed' if deterministic and matches else 'failed',
                artifact_hashes='passed', analysis_sha256=sha256(analysis_path),
                published_target_sha256=conversion['target_sha256'], replay_target_sha256=digests,
                cross_process_deterministic=deterministic, matches_published_target=matches,
                scope_certificate_validation='passed',
                published_converter_sha256=conversion.get('converter_sha256'),
                replay_converter_sha256=converter_hashes,
                converter_revision_changed=any(h != conversion.get('converter_sha256') for h in converter_hashes),
                replay_difference=('none' if matches else 'published_target_bytes_differ'),
                safe_to_retry_without_model=False, network_and_model_runtime_checked=False,
                render_verification='not_executed')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", required=True)
    parser.add_argument('--mode', choices=('checkpoint-replay', 'publication-verify'), default='checkpoint-replay')
    args = parser.parse_args()
    try:
        result = (verify_publication if args.mode == 'publication-verify' else replay)(Path(args.case_dir))
    except Exception as error:
        result = {"case_id": Path(args.case_dir).name, "offline_preflight": "failed", 'mode': args.mode,
                  "safe_to_retry_without_model": False, "error_type": type(error).__name__,
                  "error": str(error)}
        if isinstance(getattr(error, 'diagnostics', None), dict):
            result['diagnostics'] = error.diagnostics
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['offline_preflight'] == 'passed' else 1


if __name__ == "__main__":
    raise SystemExit(main())
