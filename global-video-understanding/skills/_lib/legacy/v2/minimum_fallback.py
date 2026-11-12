"""Independent, deterministic v2 minimum delivery; never edits the source Case."""
import copy
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import asdict

from _lib.paths import SCENARIO_ROOT
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.contracts.conversion_policy import digest, build
from _lib.contracts import semantic_motion as motion, bounded_execution
from _lib.contracts.description_draft import DraftBlocked, SourceChanged

REVISION = 'v2-minimum-scene.v1'
POLICY = 'state/v2_minimum_policy.json'
CONTENT_STOPS = {'v2_batch_repeated_error', 'v2_batch_attempts_exhausted',
                 'model_attempts_exhausted', 'schema_validation_failed',
                 'short_compile_failed', 'conversion_failed'}


def eligible(result):
    if (result.get('status') != 'failed' or not result.get('case_id')
            or (result.get('error') or {}).get('code') not in CONTENT_STOPS):
        return False
    from _lib.runtime.description_fallback import source_case
    try:
        source = source_case(result['case_id'])
    except (OSError, ValueError):
        return False
    path = source/POLICY
    return (read_json(source/'case.json').get('workflow_contract') == 'video-case-v2-short-aware-packed'
            and path.is_file() and read_json(path).get('revision') == REVISION)


def _snapshot(source):
    record = read_json(source/'case.json')
    if record.get('workflow_contract') != 'video-case-v2-short-aware-packed':
        raise DraftBlocked('Minimum v2 fallback requires a v2 Case')
    progress = read_json(source/'state/progress.json')
    if progress.get('diagnostics', {}).get('unresolved_submissions'):
        raise DraftBlocked('Unresolved source submission must be reconciled first')
    if progress.get('status') not in {'failed', 'done', 'done_degraded'}:
        raise DraftBlocked('Source Case has not stopped; no fallback task was started')
    for row in progress.get('sessions', []):
        if (row.get('status') in {'submitted', 'pending', 'running', 'submission_unresolved'}
                or row.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
                and not row.get('deadline_task_resolved')):
            raise DraftBlocked('Reconcile outstanding source tasks before fallback')
    paths = {source/'case.json', *list((source/'state').glob('*.json')),
             *list((source/'batches').glob('*.json')),
             *list((source/'diagnostics/invalid_candidates').glob('*.json'))}
    for name in ('manifest.json', 'final/analysis.json'):
        if (source/name).is_file():
            paths.add(source/name)
    hashes, files = {}, {}
    for path in sorted(paths):
        if path.is_symlink():
            raise SourceChanged('Source JSON is a symlink')
        hashes[str(path)] = sha256(path)
        files[str(path.relative_to(source))] = read_json(path)
    binding = files['state/binding.json']
    video = Path(binding['source_path'])
    if sha256(video) != binding['source_sha256'] or binding['source_sha256'] != record['source']['sha256']:
        raise SourceChanged('Source video binding changed')
    hashes[str(video)] = binding['source_sha256']
    frames = []
    for name, value in files.items():
        if name in {'state/probe.json', 'state/global_frames.json', 'manifest.json'}:
            rows = value.get('frames', []) + value.get('decoded_frames', []) + value.get('global_frames', [])
            rows += [f for b in value.get('batches', []) for f in b.get('frames', [])]
            for frame in rows:
                if not isinstance(frame, dict) or not frame.get('path') or frame.get('time_s') is None:
                    continue
                path = Path(frame['path'])
                actual = sha256(path)
                if frame.get('sha256', actual) != actual:
                    raise SourceChanged('Source frame hash changed: '+str(path))
                hashes[str(path)] = actual
                frames.append(dict(frame_id=frame['frame_id'], time_s=frame['time_s'], path=str(path), sha256=actual))
    if not frames:
        raise DraftBlocked('No bound evidence frames are available')
    snapshot = dict(revision=REVISION, source_case=str(source), files=files,
                    source_hashes=hashes, frames=frames)
    _verify(snapshot)
    return snapshot


def _verify(snapshot):
    for name, expected in snapshot['source_hashes'].items():
        if not Path(name).is_file() or sha256(name) != expected:
            raise SourceChanged('Frozen source changed: '+name)
    source = Path(snapshot['source_case'])
    expected_paths = {source/'case.json', *list((source/'state').glob('*.json')),
                      *list((source/'batches').glob('*.json')),
                      *list((source/'diagnostics/invalid_candidates').glob('*.json'))}
    expected_paths.update(source/name for name in ('manifest.json', 'final/analysis.json') if (source/name).is_file())
    if set(snapshot['files']) != {str(p.relative_to(source)) for p in expected_paths}:
        raise SourceChanged('Source inventory changed')
    for relative, value in snapshot['files'].items():
        path = source/relative
        if (not path.resolve().is_relative_to(source) or str(path) not in snapshot['source_hashes']
                or read_json(path) != value):
            raise SourceChanged('Frozen JSON differs from original source: '+relative)
    expected_frames = set()
    for name in ('state/probe.json', 'state/global_frames.json', 'manifest.json'):
        value = snapshot['files'].get(name, {})
        rows = value.get('frames', []) + value.get('decoded_frames', []) + value.get('global_frames', [])
        rows += [f for batch in value.get('batches', []) for f in batch.get('frames', [])]
        for frame in rows:
            if isinstance(frame, dict) and frame.get('path') and frame.get('time_s') is not None:
                expected_frames.add((frame['frame_id'], frame['time_s'], str(frame['path'])))
    if expected_frames != {(f['frame_id'],f['time_s'],f['path']) for f in snapshot['frames']}:
        raise SourceChanged('Frozen frame selection or timestamps changed')
    if any(f['sha256'] != snapshot['source_hashes'].get(f['path']) for f in snapshot['frames']):
        raise SourceChanged('Frozen frame receipt changed')


def _frames(snapshot):
    result = {}
    for frame in snapshot['frames']:
        fid = frame['frame_id']
        if fid in result and abs(result[fid]['time_s']-frame['time_s']) > 1e-6:
            raise SourceChanged('One frame ID has conflicting timestamps')
        result[fid] = frame
    return result


def _raw_analysis(snapshot):
    files = snapshot['files']
    context = files.get('state/global_context.json')
    if not context:
        raise DraftBlocked('No archived Analysis or Global answer is available')
    duration = files['state/probe.json']['probe']['duration_s']
    if context.get('scene_type') == 'intersection':
        from .short_scene_compiler import compile_short_intersection_analysis
        return compile_short_intersection_analysis(context, duration)
    if context.get('scene_type') != 'highway':
        raise DraftBlocked('Archived Global road has no supported deterministic compiler')
    event_type = context.get('event_hypothesis', {}).get('event_type')
    if event_type not in {'none', 'none_discrete', 'none_discrete_steady_following',
                          'steady_following', 'lane_keeping', 'steady_cruise', 'straight_driving'}:
        raise DraftBlocked('Global main action needs a structured Analysis; it cannot be replaced by straight driving')
    facts = context.get('scene_facts', {})
    if facts.get('road_shape', facts.get('shape_type', 'straight')) != 'straight':
        raise DraftBlocked('Known road curvature cannot be replaced by straight geometry')
    count, lane = facts.get('travel_lane_count'), facts.get('ego_lane_from_right')
    if type(count) is not int or not 1 <= count <= 8 or type(lane) is not int or not 1 <= lane <= count:
        raise DraftBlocked('Global road lane count or ego lane is unknown/inconsistent')
    lane_ids = [f'lane_{i:03d}' for i in range(1, count+1)]
    points = []
    frames = sorted(_frames(snapshot).values(), key=lambda f:f['time_s'])
    for frame in (frames[0], frames[-1]):
        point = dict(frame_id=frame['frame_id'], time_s=frame['time_s'],
                     road_id='road_001', lane_id=lane_ids[lane-1])
        speed = facts.get('ego_speed_mps')
        if motion.number(speed) is not None:
            point['speed_mps'] = copy.deepcopy(speed) if isinstance(speed, dict) else dict(
                value=speed, status='estimated', confidence=0,
                reason='Archived Global estimate, not a measured speed')
            if point['speed_mps'].get('status') == 'observed':
                point['speed_mps'] = dict(value=motion.number(speed), status='estimated', confidence=0,
                    reason='Execution holds the archived scalar measurement; continuation is not measured')
        points.append(point)
    return dict(scene_type='highway', road=dict(
        roads=[dict(road_id='road_001', lane_ids=lane_ids,
                    road_geometry=dict(shape_type='straight', visible_length_m=None))],
        lanes=[dict(lane_id=lid, road_id='road_001', lane_type='driving',
                    travel_direction='same_as_ego', width_m=None) for lid in lane_ids],
        lane_markings=[], one_way=True), junctions=[], environment={},
        ego=dict(actor_id='ego', actor_type='ego_vehicle', identity_description='Ego simulation vehicle',
                 is_static=False, simulation_role='participant', trajectory=points),
        actors=[], events=[], limitations=[], metadata=dict(duration_s=duration), quality_report={})


def _compile(snapshot):
    from _lib.runtime.orchestrator import validate_analysis
    from _lib.contracts.v3 import normalize_parameters
    files = snapshot['files']
    full = copy.deepcopy(files.get('final/analysis.json') or _raw_analysis(snapshot))
    full['metadata'] = copy.deepcopy(full.get('metadata', {}))
    full['metadata']['duration_s'] = files['state/probe.json']['probe']['duration_s']
    for key in ('bounded_execution', 'core_identity_execution_context'):
        full['metadata'].pop(key, None)
    if full.get('ego', {}).get('actor_id') != 'ego':
        raise DraftBlocked('Source ego identity is not explicitly bound')
    by_id = {a['actor_id']:a for a in full.get('actors', [])}
    source_lanes = {lane['lane_id']:lane for lane in full['road']['lanes']}
    def peripheral(event):
        ids = set(event.get('involved_actor_ids', []))
        return bool(ids) and 'ego' not in ids and all(
            aid in by_id and by_id[aid].get('simulation_role') == 'background'
            and by_id[aid].get('trajectory') and all(
                source_lanes.get(p.get('lane_id'), {}).get('travel_direction') == 'opposite_to_ego'
                for p in by_id[aid]['trajectory']) for aid in ids)
    events = [e for e in full.get('events', []) if not peripheral(e)]
    allowed_events = {'lane_change', 'intersection_entry', 'ego_intersection_entry',
                      'ego_intersection_entry_and_crossing'}
    if any(e.get('event_type') not in allowed_events for e in events):
        raise DraftBlocked('A confirmed main action has no supported minimum execution representation')
    core = {'ego', *(aid for e in events for aid in e.get('involved_actor_ids', []))}
    lanes = {e.get(k) for e in events for k in ('from_lane_id', 'to_lane_id') if e.get(k)}
    core.update(a['actor_id'] for a in full.get('actors', [])
                if len(a.get('trajectory', [])) >= 2
                and any(p.get('lane_id') in lanes for p in a['trajectory']))
    if core - {'ego', *(a['actor_id'] for a in full.get('actors', []))}:
        raise DraftBlocked('Main action references an unavailable vehicle')
    omitted = [dict(actor_id=a.get('actor_id'), source=a,
                    reason='Omitted from the minimum action; identity, motion and interaction remain unverified')
               for a in full.get('actors', []) if a.get('actor_id') not in core]
    omitted += [dict(actor_id=a.get('actor_id'), source=a, reason='Global peripheral observation not modeled')
                for a in files.get('state/global_context.json', {}).get('actor_hypotheses', [])
                if a.get('actor_id') not in core]
    for name, value in files.items():
        if name.startswith(('batches/', 'diagnostics/invalid_candidates/')):
            if not isinstance(value, dict):
                continue
            omitted.extend(dict(actor_id=a.get('actor_id'), source=a, source_file=name,
                                reason='Archived registry observation not modeled')
                           for a in value.get('registry_after', []) if a.get('actor_id') not in core)
            for challenge in value.get('challenges', []):
                ids = set(challenge.get('actor_ids', []))
                if not ids or ids & core:
                    raise DraftBlocked('Unresolved source challenge may affect the minimum road/main action')
            for observation in value.get('observations', []):
                if (observation.get('status') == 'observed' and
                        observation.get('event_type') in {'lane_change','turn','collision','cut_in'} and
                        'ego' in observation.get('actor_ids', [])):
                    raise DraftBlocked('Archived action evidence requires explicit main-action reconciliation')
    full['actors'] = [a for a in full.get('actors', []) if a.get('actor_id') in core]
    full['events'] = events
    for scenario in full.get('simulation_scenarios', []):
        if 'involved_actor_ids' in scenario:
            scenario['involved_actor_ids'] = [a for a in scenario['involved_actor_ids'] if a in core]
        if 'initial_conditions' in scenario:
            scenario['initial_conditions'] = {k:v for k,v in scenario['initial_conditions'].items() if k in core}
    full['quality_report'] = dict(execution_ready=False,
        execution_projection='analysis_values_then_hardcoded_fallbacks')
    anchors = _frames(snapshot)
    def verify_anchors(value):
        if isinstance(value, dict):
            if value.get('frame_id'):
                frame = anchors.get(value['frame_id'])
                if frame is None or ('time_s' in value and abs(value['time_s']-frame['time_s']) > 1e-6):
                    raise SourceChanged('Execution evidence frame/time does not match frozen media')
            for child in value.values():
                verify_anchors(child)
        elif isinstance(value, list):
            for child in value:
                verify_anchors(child)
    verify_anchors(full)
    parameters = normalize_parameters(dict(files['case.json'].get('parameters', {}), workflow_version='v3'))
    if parameters['road_source'] != 'preview_json':
        raise DraftBlocked('Minimum fallback requires source-bound Preview JSON road geometry')
    validate_analysis(full)
    from _lib.delivery.converter import fallback_api
    policy = build(parameters, digest(full), full['scene_type'], files['case.json']['case_id'])
    execution, fallbacks, excluded = fallback_api()[0](full, full['scene_type'], policy=policy)
    if excluded:
        raise DraftBlocked('Original Converter would omit a required minimum Actor')
    return full, execution, parameters, dict(modeled_actor_ids=sorted(core),
        unmodeled_observations=omitted, original_converter_fallbacks=fallbacks,
        omitted_events=[e for e in snapshot['files'].get('final/analysis.json', {}).get('events', []) if peripheral(e)],
        unverified_evidence=[dict(source=name, questions=value.get('questions_to_verify', []),
                                 limitations=value.get('limitations', []), challenges=value.get('challenges', []))
                             for name,value in files.items() if isinstance(value, dict) and any(
                                 value.get(k) for k in ('questions_to_verify','limitations','challenges'))],
        assumptions=['Straight road geometry, lane widths and missing metrics use declared simulation policy where needed.',
                     'Constant-speed continuation outside observed samples is simulated, not measured.',
                     'No interaction or collision claim is made against omitted observations.'])


def _plan(execution, cached=None):
    from _lib.behavior.lane_change import from_analysis
    spec = from_analysis(execution) if execution['scene_type'] == 'highway' else None
    actors = [execution['ego'], *execution.get('actors', [])]
    if any(a.get('is_static') is not False for a in actors):
        raise DraftBlocked('Minimum motion audit requires a supported moving-vehicle representation')
    request = dict(duration_s=execution['metadata']['duration_s'], eligible=actors,
        initial_conditions=(execution.get('simulation_scenarios') or [{}])[0].get('initial_conditions', {}),
        constant_speed_actor_ids=[spec.actor_id] if spec else [], policy=copy.deepcopy(motion.POLICY),
        bounded_execution=dict(source_record=dict(preferences={}), lane_change=asdict(spec) if spec else None))
    if cached is not None:
        if cached['request'] != request:
            raise SourceChanged('Frozen numeric planning request changed')
        motion.validate_plans(request, cached['plans'])
        return request, cached['plans']
    plans = bounded_execution.plan(request, [])
    if plans is None:
        raise DraftBlocked('Original bounded numeric planner did not return a plan')
    motion.validate_plans(request, plans)
    return request, plans


def _audit(root, snapshot, full, execution, request, plans, output):
    from _lib.delivery.converter import mela_valid, converter_fingerprint
    from _lib.delivery.checks import validate_target
    from _lib.delivery.motion_check import decode
    from _lib.behavior.lane_change import actor_name
    _verify(snapshot)
    target_path, result_path = output/'target.json', output/'conversion_result.json'
    result, target = read_json(result_path), read_json(target_path)
    script = SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts/scenes'/full['scene_type']/'convert.py'
    if (result.get('status') not in {'done', 'done_degraded'} or
            result.get('input_sha256') != sha256(root/'analysis.json') or
            result.get('target_sha256') != sha256(target_path) or
            result.get('converter_sha256') != converter_fingerprint(script) or not mela_valid(target_path)):
        raise SourceChanged('Original Converter result or Target binding failed')
    from _lib.contracts.v3 import normalize_parameters
    expected_policy = build(normalize_parameters(dict(snapshot['files']['case.json'].get('parameters', {}),
                            workflow_version='v3')), sha256(root/'analysis.json'), full['scene_type'],
                            Path(snapshot['source_case']).name)
    if (result.get('conversion_policy') != expected_policy or
            read_json(root/'conversion_policy.json') != expected_policy or
            result.get('output_path') != str(root/'staging/target.json')):
        raise SourceChanged('Original conversion policy or output binding changed')
    checks = validate_target(full, target, 'preview_json', result)
    if checks != result.get('delivery_checks'):
        raise SourceChanged('Original delivery checks differ on replay')
    vehicles = {v['name']:v for v in target['scenario']['vehicles']}
    if set(vehicles) != {actor_name(p['actor_id']) for p in plans} or target['scenario'].get('pedestrians'):
        raise DraftBlocked('Target modeled-object coverage changed')
    profiles = {}
    for plan in plans:
        aid = plan['actor_id']
        actual = profiles[aid] = decode(vehicles[actor_name(aid)], request['duration_s'])
        expected = motion.segments(plan)
        for t in motion.critical_times(actual, expected, 0, request['duration_s']):
            if any(abs(a-b)>1e-4 for a,b in zip(motion.state(actual,t), motion.state(expected,t))):
                raise DraftBlocked('Target differs from the original bounded numeric plan: '+aid)
    motion.validate_plans(request, plans)
    motion.check_lane_clearance(request, profiles)
    from _lib.behavior.lane_change import from_analysis, audit_target
    spec = from_analysis(execution) if full['scene_type'] == 'highway' else None
    lane_check = audit_target(execution, target) if spec else None
    if spec and lane_check.get('collision_envelope') != 'continuous_passed':
        raise DraftBlocked('Continuous lane-change collision check did not pass')
    return dict(target_schema='passed', references='passed', continuity='passed',
                numeric_bounds='passed', numeric_plan='passed', same_lane_clearance='passed',
                lane_change_collision='continuous_passed' if spec else 'not_applicable',
                original_converter_checks=checks, render_verification='not_executed')


def run(action, source, root=None):
    from _lib.runtime.description_fallback import _root, ROOT, _write_once
    source = Path(source).resolve(strict=True)
    root = _root(source, root or ROOT/source.name/'v2_minimum_scene')
    root.mkdir(parents=True, exist_ok=True)
    base = dict(case_id=source.name, delivery_level='unverified', generation_status='not_generated',
                target_path=None, draft_path=str(root/'draft.json'))
    draft = dict(revision=REVISION, source_case=str(source), **base)
    try:
        if action not in {'prepare', 'resume', 'check'}:
            raise DraftBlocked('Unknown v2 minimum action')
        snapshot_path = root/'source.json'
        snapshot = read_json(snapshot_path) if snapshot_path.is_file() else _snapshot(source)
        _verify(snapshot)
        if snapshot['source_case'] != str(source):
            raise SourceChanged('Fallback source Case changed')
        _write_once(snapshot_path, snapshot)
        full, execution, parameters, projection = _compile(snapshot)
        plan_path = root/'numeric_plan.json'
        request, plans = _plan(execution, read_json(plan_path) if plan_path.is_file() else None)
        _write_once(root/'analysis.json', full)
        _write_once(root/'numeric_plan.json', dict(request=request, plans=plans))
        draft.update(status='prepared', analysis=full, projection=projection,
                     source_sha256=sha256(snapshot_path), failure=None)
        if (root/'output').is_dir():
            checks = _audit(root, snapshot, full, execution, request, plans, root/'output')
            expected = _manifest(root, snapshot, projection, plans, checks, root/'output')
            if read_json(root/'output/manifest.json') != expected:
                raise SourceChanged('Unverified manifest changed')
            return dict(base, status='unverified', generation_status='generated',
                        target_path=str(root/'output/target.json'), manifest_path=str(root/'output/manifest.json'))
        if action in {'prepare', 'check'}:
            atomic_json(root/'draft.json', draft)
            return dict(base, status='prepared')
        if (root/'attempt.json').exists():
            raise DraftBlocked('The single deterministic fallback attempt has already been used')
        atomic_json(root/'draft.json', draft)
        atomic_json(root/'attempt.json', dict(revision=REVISION, source_sha256=sha256(snapshot_path)))
        staging = root/'staging'
        staging.mkdir(exist_ok=True)
        policy = build(parameters, sha256(root/'analysis.json'), full['scene_type'], source.name)
        atomic_json(root/'conversion_policy.json', policy)
        command = [sys.executable, str(SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts/process.py'),
            '--input', str(root/'analysis.json'), '--output', str(staging/'target.json'),
            '--scene-type', full['scene_type'], '--result', str(staging/'conversion_result.json'),
            '--workflow-contract', 'video-case-world-behavior', '--road-source', 'preview_json',
            '--policy', str(root/'conversion_policy.json'), '--policy-sha256', policy['policy_sha256']]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode:
            result = read_json(staging/'conversion_result.json') if (staging/'conversion_result.json').exists() else {}
            raise DraftBlocked(result.get('message') or 'Original Converter failed without a successful result')
        checks = _audit(root, snapshot, full, execution, request, plans, staging)
        atomic_json(staging/'manifest.json', _manifest(root, snapshot, projection, plans, checks, staging))
        _verify(snapshot)
        os.replace(staging, root/'output')
        return dict(base, status='unverified', generation_status='generated',
                    target_path=str(root/'output/target.json'), manifest_path=str(root/'output/manifest.json'))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        rejected = root/'staging/target.json'
        if rejected.exists():
            os.replace(rejected, rejected.with_name('rejected_candidate.json'))
        draft.update(status='blocked', failure=dict(code=getattr(error,'code',type(error).__name__), message=str(error)))
        atomic_json(root/'draft.json', draft)
        return dict(base, status='blocked', error=draft['failure'])


def _manifest(root, snapshot, projection, plans, checks, output):
    return dict(revision=REVISION, case_id=Path(snapshot['source_case']).name, status='unverified',
        delivery_level='unverified', generation_status='generated', target_sha256=sha256(output/'target.json'),
        source_sha256=sha256(root/'source.json'), analysis_sha256=sha256(root/'analysis.json'),
        strict_validate_completed='not_claimed', core_approximate_acceptance='not_claimed',
        render_verification='not_executed', checks=checks, simplification=projection,
        numeric_assumptions=dict(status='simulated_not_video_measured', plans=plans),
        failed_evidence_checks=['No new video/identity verification was performed',
            'Unmodeled observations and uncertain interactions remain unverified'],
        source_stop=snapshot['files']['state/progress.json'].get('error'))
