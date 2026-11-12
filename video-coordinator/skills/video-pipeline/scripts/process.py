#!/usr/bin/env python3
"""Stable coordinator entrypoint for the complete video Case orchestrator."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path


AUTO_V2_MAX_DURATION_S = 2.1  # Include nominal 2s clips such as 2.002s.


def video_duration(video):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries',
         'stream=width,height:format=duration', '-select_streams', 'v:0',
         '-of', 'json', str(Path(video).resolve(strict=True))],
        check=True, capture_output=True, text=True, timeout=10)
    metadata = json.loads(result.stdout)
    if not metadata['streams']:
        raise ValueError('No video stream')
    duration = float(metadata['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid video duration')
    return duration


def select_default_workflow(args, parameters):
    """Select only unspecified new requests; saved contracts remain authoritative."""
    if args.case_id or 'workflow_version' in parameters or not args.video:
        return
    v3_options = (
        args.strict_delivery, args.core_scene_approximate,
        args.execution_scope, args.execution_planning)
    v3_parameters = {
        'video_fidelity_policy', 'parked_scenery_per_side', 'conversion_defaults',
        'execution_policy_version', 'confirmed_lane_assignments'}
    if any(v3_options) or v3_parameters.intersection(parameters):
        return
    try:
        duration = video_duration(args.video)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        print('[video-pipeline] Duration unavailable; keeping default workflow v3.', file=sys.stderr)
        return
    version = 'v2' if duration <= AUTO_V2_MAX_DURATION_S else 'v3'
    parameters['workflow_version'] = version
    print(f'[video-pipeline] duration={duration:g}s; selected workflow={version} '
          f'(automatic v2 limit={AUTO_V2_MAX_DURATION_S:g}s).', file=sys.stderr)


def compatible_python():
    if sys.version_info >= (3, 10):
        return sys.executable
    qwenpaw_home = Path(os.environ.get("QWENPAW_HOME", str(Path.home()/".qwenpaw")))
    candidate = qwenpaw_home/"venv/bin/python"
    if candidate.is_file():
        return str(candidate)
    raise SystemExit("video pipeline requires Python 3.10+; no compatible QwenPaw runtime was found")


def command(argv=None):
    parser = argparse.ArgumentParser(description="Run the complete video analysis Case pipeline")
    parser.add_argument("--video")
    parser.add_argument("--parameters-json", default="{}")
    parser.add_argument("--case-id")
    parser.add_argument("--new", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--review-behavior-scope", action="store_true")
    parser.add_argument('--review-execution-scope', action='store_true')
    parser.add_argument('--core-scene-actors', nargs='+', metavar='ACTOR_ID')
    parser.add_argument('--core-scene-approximate', action='store_true')
    parser.add_argument('--strict-delivery', action='store_true')
    parser.add_argument('--description-fallback', choices=('auto', 'prepare', 'resume', 'check', 'off'))
    parser.add_argument('--accept-early-association-independence', action='store_true')
    parser.add_argument('--reauthorize-behavior-after-timeout', metavar='FAILED_TASK_ID')
    parser.add_argument('--reauthorize-behavior-scope-after-quota', metavar='FAILED_TASK_ID')
    parser.add_argument('--scope-quota-retry-generation', type=int, choices=(1, 2), default=1)
    parser.add_argument("--authorize-transport-retry")
    parser.add_argument("--resume-conversion-session")
    parser.add_argument("--resume-world-closure-session")
    parser.add_argument("--enable-straight-preview", action="store_true")
    parser.add_argument("--enable-conversion-defaults", action="store_true")
    parser.add_argument('--enable-behavior-census-scope', action='store_true')
    parser.add_argument('--execution-scope', choices=['ego_vehicle_scene.v1', 'ego_vehicle_scene.v2', 'ego_vehicle_scene.v3', 'ego_vehicle_scene.v4'])
    parser.add_argument('--execution-scope-bindings')
    parser.add_argument('--execution-planning', choices=['ego_scene_approximation.v1'])
    parser.add_argument('--execution-evidence')
    parser.add_argument('--scene-corrections')
    parser.add_argument('--review-actor-motion')
    parser.add_argument('--actor-review-mode', choices=['motion', 'identity_link', 'identity_split'])
    args = parser.parse_args(argv)
    if args.description_fallback in {'prepare', 'resume', 'check'} and (not args.case_id or args.new or args.retry_failed):
        parser.error('description fallback prepare/resume/check requires --case-id without --new/--retry-failed')
    try:
        parameters = json.loads(args.parameters_json)
    except json.JSONDecodeError as error:
        parser.error(f'--parameters-json is not valid JSON: {error.msg}')
    if not isinstance(parameters, dict):
        parser.error('--parameters-json must decode to an object')
    if args.strict_delivery and args.core_scene_approximate:
        parser.error('--strict-delivery cannot combine with --core-scene-approximate')
    select_default_workflow(args, parameters)
    scenario_root = Path(__file__).resolve().parents[4]
    saved_approximate = False
    if args.case_id:
        cases = scenario_root/'video_extractor/cases'
        case = (cases/args.case_id).resolve()
        saved_approximate = (case.parent == cases.resolve()
                             and (case/'state/core_approximate_mode.json').is_file())
        if saved_approximate and args.strict_delivery:
            parser.error('A core approximation Case cannot resume as strict delivery')
        args.core_scene_approximate = args.core_scene_approximate or saved_approximate
    elif args.new and parameters.get('workflow_version', 'v3') == 'v3' and not args.strict_delivery:
        args.core_scene_approximate = True
    if args.core_scene_approximate and (
            args.core_scene_actors or (args.retry_failed and not saved_approximate) or args.review_execution_scope
            or args.review_behavior_scope or args.reauthorize_behavior_scope_after_quota
            or args.accept_early_association_independence
            or args.review_actor_motion or args.actor_review_mode or args.scene_corrections
            or args.execution_scope or args.execution_scope_bindings
            or args.execution_planning or args.execution_evidence or args.enable_straight_preview
            or args.enable_conversion_defaults or args.enable_behavior_census_scope
            or args.authorize_transport_retry or args.resume_conversion_session
            or args.resume_world_closure_session or args.new == bool(args.case_id)):
        parser.error('--core-scene-approximate requires --new or --case-id without other modes')
    if args.core_scene_actors and (not args.case_id or not args.retry_failed or args.new
            or args.review_execution_scope or args.review_behavior_scope or args.reauthorize_behavior_scope_after_quota
            or args.reauthorize_behavior_after_timeout
            or args.accept_early_association_independence
            or args.review_actor_motion or args.actor_review_mode or args.scene_corrections
            or args.resume_world_closure_session or args.resume_conversion_session or args.authorize_transport_retry
            or args.enable_straight_preview or args.enable_conversion_defaults or args.enable_behavior_census_scope
            or args.execution_scope or args.execution_scope_bindings or args.execution_planning
            or args.execution_evidence):
        parser.error('--core-scene-actors requires ordinary saved v3 retry without other recovery flags')
    if args.review_execution_scope and (not args.case_id or not args.retry_failed or args.new
            or args.review_behavior_scope or args.reauthorize_behavior_scope_after_quota
            or args.accept_early_association_independence or args.reauthorize_behavior_after_timeout
            or args.enable_straight_preview or args.enable_conversion_defaults or args.enable_behavior_census_scope
            or args.execution_scope or args.execution_scope_bindings or args.execution_planning
            or args.execution_evidence or args.authorize_transport_retry or args.resume_conversion_session
            or args.resume_world_closure_session or args.scene_corrections or args.review_actor_motion):
        parser.error('--review-execution-scope requires ordinary saved v3 retry without other recovery flags')
    if args.reauthorize_behavior_after_timeout and (not args.case_id or not args.retry_failed or args.new
            or args.review_behavior_scope
            or args.reauthorize_behavior_scope_after_quota or args.accept_early_association_independence
            or args.review_actor_motion or args.actor_review_mode or args.scene_corrections
            or args.resume_world_closure_session or args.resume_conversion_session or args.authorize_transport_retry
            or args.enable_straight_preview or args.enable_conversion_defaults or args.enable_behavior_census_scope
            or args.execution_scope or args.execution_scope_bindings or args.execution_planning
            or args.execution_evidence):
        parser.error('--reauthorize-behavior-after-timeout requires ordinary saved v3 retry')
    if args.accept_early_association_independence and (not args.case_id or not args.retry_failed or args.new
            or args.review_behavior_scope or args.reauthorize_behavior_scope_after_quota
            or args.review_actor_motion or args.actor_review_mode or args.scene_corrections
            or args.resume_world_closure_session or args.resume_conversion_session or args.authorize_transport_retry
            or args.enable_straight_preview or args.enable_conversion_defaults or args.enable_behavior_census_scope
            or args.execution_scope or args.execution_scope_bindings or args.execution_planning
            or args.execution_evidence):
        parser.error('--accept-early-association-independence requires an ordinary saved v3 retry')
    if args.scope_quota_retry_generation != 1 and not args.reauthorize_behavior_scope_after_quota:
        parser.error('--scope-quota-retry-generation 2 requires --reauthorize-behavior-scope-after-quota')
    if (args.review_behavior_scope or args.reauthorize_behavior_scope_after_quota) and (not args.case_id or not args.retry_failed or args.new
            or args.review_behavior_scope and args.reauthorize_behavior_scope_after_quota
            or args.review_actor_motion or args.scene_corrections or args.resume_world_closure_session
            or args.resume_conversion_session or args.authorize_transport_retry or args.enable_straight_preview
            or args.enable_conversion_defaults or args.execution_scope or args.execution_planning
            or args.enable_behavior_census_scope):
        parser.error("--review-behavior-scope requires saved v3 retry without other recovery flags")
    if args.enable_behavior_census_scope and (not args.case_id or not args.retry_failed or args.new
            or args.review_actor_motion or args.scene_corrections or args.resume_world_closure_session
            or args.resume_conversion_session or args.authorize_transport_retry or args.enable_straight_preview
            or args.enable_conversion_defaults or args.execution_scope or args.execution_planning):
        parser.error('--enable-behavior-census-scope requires ordinary saved-Case retry')
    if args.actor_review_mode and not args.review_actor_motion:
        parser.error('--actor-review-mode requires --review-actor-motion')
    if args.review_actor_motion and not (args.case_id and args.retry_failed):
        parser.error('--review-actor-motion requires --case-id --retry-failed')
    if args.scene_corrections and (not args.case_id or args.new or args.execution_planning or args.execution_scope):
        parser.error('--scene-corrections requires a completed --case-id and cannot combine policy migration')
    if args.execution_evidence and not args.execution_planning:
        parser.error('--execution-evidence requires --execution-planning')
    if args.execution_planning and args.case_id and not args.retry_failed:
        parser.error('--execution-planning on saved Case requires --retry-failed')
    if args.execution_scope_bindings and not args.execution_scope:
        parser.error('--execution-scope-bindings requires --execution-scope')
    if args.execution_scope and args.case_id and not args.retry_failed:
        parser.error('--execution-scope on a saved Case requires --retry-failed')

    if args.retry_failed and not args.case_id:
        parser.error("--retry-failed requires --case-id")
    if args.retry_failed and args.new:
        parser.error("--retry-failed cannot be combined with --new")
    if args.authorize_transport_retry and not (args.case_id and args.retry_failed):
        parser.error("--authorize-transport-retry requires --case-id --retry-failed")
    if args.resume_conversion_session and (args.authorize_transport_retry != 'conversion_semantic_gap'
            or args.review_actor_motion or args.scene_corrections):
        parser.error('--resume-conversion-session requires ordinary --authorize-transport-retry conversion_semantic_gap recovery')
    if args.resume_world_closure_session and (not args.case_id or not args.retry_failed or args.new
            or args.resume_conversion_session or args.authorize_transport_retry or args.review_actor_motion
            or args.scene_corrections or args.execution_scope or args.execution_planning
            or args.enable_straight_preview or args.enable_conversion_defaults):
        parser.error('--resume-world-closure-session requires ordinary saved-Case retry without other recovery/migration flags')
    if args.enable_straight_preview and not (args.case_id and args.retry_failed):
        parser.error("--enable-straight-preview requires --case-id --retry-failed")
    if args.enable_conversion_defaults and not (args.case_id and args.retry_failed):
        parser.error("--enable-conversion-defaults requires --case-id --retry-failed")
    if args.enable_conversion_defaults and (args.review_actor_motion or args.scene_corrections):
        parser.error("--enable-conversion-defaults cannot combine observation review or scene corrections")
    if args.enable_conversion_defaults and (args.enable_straight_preview or args.execution_scope or args.execution_planning):
        parser.error("--enable-conversion-defaults cannot combine other policy migrations")

    if args.review_execution_scope and parameters.get('workflow_version', 'v3') != 'v3':
        parser.error('--review-execution-scope requires saved v3 retry')
    if args.core_scene_actors and parameters.get('workflow_version', 'v3') != 'v3':
        parser.error('--core-scene-actors requires saved v3 retry')
    if args.core_scene_approximate and parameters.get('workflow_version', 'v3') != 'v3':
        parser.error('--core-scene-approximate requires v3')
    if args.accept_early_association_independence and parameters.get('workflow_version', 'v3') != 'v3':
        parser.error('--accept-early-association-independence requires v3')
    if args.reauthorize_behavior_scope_after_quota and parameters.get('workflow_version', 'v3') != 'v3':
        parser.error('--reauthorize-behavior-scope-after-quota requires saved v3 retry')
    if parameters.get("execution_policy_version") == "efficient_v1":
        parser.error("efficient_v1 is retired; use a new standard v3 Case without execution_policy_version, or select v2")
    # Project default for new work; a saved Case retains its original contract.
    # Explicit policy choices and restrictive degradation allowlists take precedence.
    if not args.case_id and parameters.get("workflow_version", "v3") == "v3":
        policy = parameters.setdefault("fallback_policy", {})
        if not isinstance(policy, dict):
            parser.error("fallback_policy must be an object")
        preview = policy.setdefault("straight_preview", {})
        if not isinstance(preview, dict):
            parser.error("straight_preview must be an object")
        allowed = parameters.get("allowed_degradations")
        preview.setdefault("enabled", allowed is None or "straight_preview_policy_default" in allowed)
    if not args.video and args.description_fallback not in {'prepare', 'resume', 'check'}:
        parser.error('--video is required for video processing')
    video = Path(args.video).resolve(strict=True) if args.video else None

    if args.review_actor_motion:
        if args.new or args.scene_corrections or args.execution_planning or args.execution_scope or parameters:
            parser.error('Actor motion review uses saved v3 parameters without migration')
        return [compatible_python(),str(scenario_root/'global-video-understanding/skills/video-case-orchestrator/scripts/review_actor_motion.py'),
                '--video',str(video),'--case-id',args.case_id,'--actor-id',args.review_actor_motion,
                *(['--mode', args.actor_review_mode] if args.actor_review_mode else [])]
    orchestrator = scenario_root/"global-video-understanding/skills/video-case-orchestrator/scripts/process.py"
    if not orchestrator.is_file():
        raise SystemExit(f"orchestrator not found: {orchestrator}")

    result = [compatible_python(), str(orchestrator), "--parameters-json", json.dumps(parameters, ensure_ascii=False)]
    if video is not None:
        result.extend(['--video', str(video)])
    if args.description_fallback:
        result.extend(['--description-fallback', args.description_fallback])
    if args.case_id:
        result.extend(["--case-id", args.case_id])
    if args.new:
        result.append("--new")
    if args.retry_failed:
        result.append("--retry-failed")
    if args.reauthorize_behavior_after_timeout:
        result.extend(['--reauthorize-behavior-after-timeout', args.reauthorize_behavior_after_timeout])
    if args.review_execution_scope:
        result.append("--review-execution-scope")
    if args.core_scene_actors:
        result.extend(['--core-scene-actors', *args.core_scene_actors])
    if args.core_scene_approximate:
        result.append('--core-scene-approximate')
    if args.strict_delivery:
        result.append('--strict-delivery')
    if args.review_behavior_scope:
        result.append("--review-behavior-scope")
    if args.accept_early_association_independence:
        result.append('--accept-early-association-independence')
    if args.reauthorize_behavior_scope_after_quota:
        result.extend(['--reauthorize-behavior-scope-after-quota', args.reauthorize_behavior_scope_after_quota])
        if args.scope_quota_retry_generation != 1:
            result.extend(['--scope-quota-retry-generation', str(args.scope_quota_retry_generation)])
    if args.authorize_transport_retry:
        result.extend(["--authorize-transport-retry", args.authorize_transport_retry])
    if args.resume_conversion_session:
        result.extend(["--resume-conversion-session", args.resume_conversion_session])
    if args.resume_world_closure_session:
        result.extend(["--resume-world-closure-session", args.resume_world_closure_session])
    if args.enable_straight_preview:
        result.append("--enable-straight-preview")
    if args.enable_conversion_defaults:
        result.append("--enable-conversion-defaults")
    if args.enable_behavior_census_scope:
        result.append('--enable-behavior-census-scope')
    if args.execution_scope:
        result.extend(['--execution-scope', args.execution_scope])
    if args.execution_scope_bindings:
        result.extend(['--execution-scope-bindings', str(Path(args.execution_scope_bindings).resolve(strict=True))])
    if args.execution_planning: result.extend(['--execution-planning',args.execution_planning])
    if args.scene_corrections: result.extend(['--scene-corrections',str(Path(args.scene_corrections).resolve(strict=True))])
    if args.execution_evidence: result.extend(['--execution-evidence',str(Path(args.execution_evidence).resolve(strict=True))])
    return result


def main():
    result = command()
    os.execv(result[0], result)


if __name__ == "__main__":
    main()
