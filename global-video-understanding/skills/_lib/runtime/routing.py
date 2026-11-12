"""Shared execution planning from accepted observations, never from scene labels alone."""
import copy
from pathlib import Path

from _lib.runtime.case_core import atomic_json, read_json, sha256


def automatic(case):
    return (Path(case)/'state/execution_policy.json').is_file()


def issue(rule, message, path, actual, *, owner='world', actor_id=None):
    return dict(rule_id=rule, message=message, owner=owner, actor_id=actor_id,
                field_path=path, expected='supported straight-road execution',
                actual=actual, retry_kind='unsupported_capability')


def capability_issues(world, behavior=None, duration_s=None):
    scene = world.get('world', world)
    issues = []
    lane_spec = None
    lane_error_actor = None
    if behavior:
        from _lib.behavior.lane_change import derive as derive_lane_change, LaneChangeBlocked
        try:
            lane_spec = derive_lane_change(world, behavior, duration_s)
        except LaneChangeBlocked as error:
            issues.append(error.diagnostics)
            lane_error_actor = error.diagnostics.get('actor_id')
    for i, road in enumerate(scene.get('road', {}).get('roads', [])):
        geom = road.get('road_geometry', {})
        shape = geom.get('shape_type', road.get('shape_type'))
        curvature = geom.get('curvature')
        curvature = curvature.get('value') if isinstance(curvature, dict) else curvature
        if shape not in (None, 'straight', 'unknown') or curvature not in (None, 0, 'straight', 'unknown') or any(
                geom.get(k) is not None for k in ('road_radius', 'radius_m')):
            issues.append(issue('capability.curved_road', 'Current Converter cannot preserve curved roads',
                                f'/world/road/roads/{i}/road_geometry', geom))
        if road.get('segments'):
            issues.append(issue('capability.road_segments', 'Explicit road segments are unsupported',
                                f'/world/road/roads/{i}/segments', road['segments']))
    junctions = scene.get('junctions', [])
    if len(junctions) > 1:
        issues.append(issue('capability.multiple_junctions', 'Current Converter supports one junction',
                            '/world/junctions', len(junctions)))
    # Time-varying signals use the authorized all-green execution fallback.
    # Their observed timelines remain in World and the conversion audit.
    if behavior:
        owners={lane['lane_id']:lane.get('road_id') for lane in scene.get('road',{}).get('lanes',[])}
        connections={(row.get('incoming_lane_id'),row.get('outgoing_lane_id')):row.get('maneuver')
                     for j in junctions for row in (j.get('lane_connections') or [])}
        for actor in behavior.get('actors',[]):
            if actor.get('simulation_role') != 'participant': continue
            points=actor.get('trajectory',[])
            for before,after in zip(points,points[1:]):
                first,last=before.get('lane_id'),after.get('lane_id')
                if first and last and first!=last and (
                        (owners.get(first) is not None and owners.get(first)==owners.get(last)) or
                        connections.get((first,last)) in {'left','right','u_turn'}):
                    if (lane_spec is not None and actor.get('actor_id') == lane_spec.actor_id
                            and {first, last} == {lane_spec.source_lane_id, lane_spec.target_lane_id}):
                        continue
                    if lane_error_actor == actor.get('actor_id') and owners.get(first) == owners.get(last):
                        continue
                    issues.append(issue('capability.motion','Observed lane transition requires unsupported turn/lane-change mapping',
                                        '/actors/'+actor['actor_id']+'/trajectory',[first,last],owner='behavior',actor_id=actor['actor_id']))
        for collection in ('events', 'executor_primitives'):
            for i, row in enumerate(behavior.get(collection, [])):
                kind = row.get('event_type', row.get('type', ''))
                from _lib.behavior.lane_change import is_lane_change_kind
                if is_lane_change_kind(kind):
                    actor_id = row.get('actor_id')
                    if actor_id is None and collection == 'events':
                        bound = row.get('involved_actor_ids') or []
                        actor_id = bound[0] if len(bound) == 1 else None
                    if lane_spec is not None and actor_id == lane_spec.actor_id:
                        continue
                    if lane_error_actor is None or actor_id != lane_error_actor:
                        issues.append(issue('capability.motion', 'Lane-change execution is unsupported',
                                            f'/{collection}/{i}', kind, owner='behavior', actor_id=actor_id))
                elif any(word in kind.lower() for word in ('turn', 'cut_in')):
                    issues.append(issue('capability.motion', 'Turn/lane-change execution is unsupported',
                                        f'/{collection}/{i}', kind, owner='behavior', actor_id=row.get('actor_id')))
    return issues


def select_route(world, behavior, parameters, observation_issues=None, *, intersection_enabled=False,
                 observation_contract=False, duration_s=None, actor_fact_review=False, identity_review=False,
                 road_minimum_decision=None):
    from _lib.contracts.v3 import build_analysis
    from _lib.behavior.compile import preview_scale_limitation, observation_scope_exclusions
    if identity_review:
        from _lib.contracts.actor_identity_revision import issues as identity_issues
        unresolved = identity_issues(world, behavior)
        if unresolved:
            return {'route': 'blocked', 'issues': unresolved, 'reason': 'Temporal Actor identity needs review'}
    issues = capability_issues(world, behavior, duration_s)
    if issues:
        return {'route': 'unsupported', 'issues': issues}
    from _lib.behavior.lane_change import derive as derive_lane_change
    lane_change = derive_lane_change(world, behavior, duration_s)
    if observation_contract:
        from _lib.world.gaps import classify_gaps, identity_exclusion
        identity=[]
        for collection in ('questions_to_verify','ambiguities'):
            for item in world.get(collection,[]):
                if (item.get('resolution')=='unresolved' and 'actor_identity' in item.get('impact_domains',[])
                        and not identity_exclusion(world,behavior,item)):
                    identity.extend(r for r in classify_gaps(world) if r.get('issue_id')==item.get('question_id',item.get('ambiguity_id')))
        if identity: return {'route':'blocked','issues':identity,'reason':'Identity evidence remains required'}
    if actor_fact_review and world['world'].get('scene_type') == 'highway':
        lane_gaps = unobserved_actor_lanes(world, behavior)
        if lane_gaps:
            return {'route': 'blocked', 'issues': lane_gaps,
                    'reason': 'Participant has no known lane; straight continuation needs a known lane'}
    full = copy.deepcopy(parameters)
    full.setdefault('fallback_policy', {}).setdefault('straight_preview', {})['enabled'] = False
    try:
        analysis, status = build_analysis(world, behavior, full, observation_issues=observation_issues,
                                         observation_contract=observation_contract,
                                         road_minimum_decision=road_minimum_decision)
    except ValueError as error:
        # Geometry-less observed facilities are precisely a projection input,
        # but malformed facts and unrelated Builder failures are not bypassed.
        if not (intersection_enabled and world['world'].get('scene_type') == 'intersection'
                and str(error).startswith(('traffic_signals require explicit', 'crosswalks require explicit'))):
            raise
        analysis, status = {'quality_report':{'execution_issues':[dict(rule_id='intersection.facility_geometry',
            message=str(error),owner='world',retry_kind='new_evidence')]}}, 'needs_review'
    gaps = analysis.get('quality_report', {}).get('execution_issues', [])
    if status in {'done', 'done_degraded'}:
        return {'route': 'full', 'issues': gaps,
                'reason': 'Accepted observations may use recorded deterministic Converter defaults'}
    if intersection_enabled and world['world'].get('scene_type') == 'intersection':
        from _lib.behavior.intersection import prerequisites, IntersectionBlocked
        try:
            prerequisites(world, behavior)
        except IntersectionBlocked as error:
            return {'route':'blocked', 'issues':[error.diagnostics], 'reason':str(error)}
        return {'route':'intersection_preview', 'issues':gaps,
                'reason':'Review four-arm straight relations before authorized execution projection'}
    scene = world['world']; roads = scene.get('road', {}).get('roads', [])
    eligible = (parameters.get('fallback_policy', {}).get('straight_preview', {}).get('enabled') is True
                and scene.get('scene_type') == 'highway' and len(roads) == 1
                and not scene.get('junctions') and roads[0].get('road_geometry', {}).get('shape_type') in (None, 'straight')
                and ((not behavior.get('events')
                      and all(p.get('type') == 'initial_state' for p in behavior.get('executor_primitives', [])))
                     or lane_change is not None))
    excluded = {row['limitation_id'] for row in observation_scope_exclusions(world, behavior)}
    for limitation in behavior.get('semantic_limitations', []):
        if limitation.get('execution_effect') == 'blocks_execution' and limitation['limitation_id'] not in excluded:
            eligible &= preview_scale_limitation(limitation)
    # Identity and participant lane/path uncertainty cannot be synthesized by policy.
    eligible &= not any(row.get('rule_id') in {'execution.lane_unknown', 'identity.conflict'}
                        and row.get('actor_id') in {a['actor_id'] for a in behavior['actors']
                                                  if a.get('simulation_role') == 'participant'}
                        and not (row.get('rule_id') == 'execution.lane_unknown'
                                 and lane_change is not None
                                 and row.get('actor_id') == lane_change.actor_id)
                        for row in gaps)
    eligible &= not any(row.get('rule_id','').startswith('motion.') for row in gaps)
    for row in gaps:
        row.setdefault('owner','world' if row['rule_id'].startswith('world.') else 'behavior')
        row.setdefault('retry_kind','new_evidence')
        row.setdefault('field_path',(row.get('paths') or row.get('world_paths') or [None])[0])
    return {'route': 'straight_preview' if eligible else 'blocked', 'issues': gaps,
            'reason': 'Review qualitative relations before authorized scale synthesis' if eligible
                      else 'Execution evidence is incomplete; no applicable approximation'}


def record_route(case, world, behavior, parameters, observation_issues=None):
    from _lib.stages.intersection import enabled as intersection_enabled
    from _lib.world.gaps import enabled as observation_enabled
    from _lib.runtime.execution_scope import inputs, enabled, registry
    scope_audit = None
    if enabled(case):
        from _lib.behavior.compile import execution_issues, evidence_frames
        world, behavior, scope_audit = inputs(case)
        observation_issues = execution_issues(behavior,world,registry(case,behavior),evidence_frames(case))
    sources = ['state/world_context.json', 'state/behavior.json', 'state/actor_registry.json',
               'case.json', 'manifest.json', 'state/execution_policy.json', 'state/global_frames.json', 'state/behavior_frames.json']
    sources += [str(p.relative_to(case)) for p in sorted((Path(case)/'batches').glob('*.json'))]
    input_hashes = {p: sha256(Path(case)/p) for p in sources if (Path(case)/p).is_file()}
    policy_path = Path(case)/'state/execution_policy.json'
    policy = read_json(policy_path) if policy_path.is_file() else {}
    previous_path = Path(case)/'state/execution_route.json'
    previous = read_json(previous_path) if previous_path.is_file() else {}
    road_minimum_decision = None
    if previous.get('road_minimum_decision') and policy.get('noncore_road_minimum') != 'noncore_road_minimum.v1':
        from _lib.contracts.v3 import ContractError
        raise ContractError('Frozen noncore road policy changed after the ruling',
                            rule_id='stale_version')
    if policy.get('noncore_road_minimum') == 'noncore_road_minimum.v1':
        from _lib.world.readiness import (decide_noncore_road_minimum,
                                          validate_noncore_road_minimum)
        if scope_audit is None:
            from _lib.contracts.v3 import ContractError
            raise ContractError('Noncore road minimum requires an accepted execution scope')
        road_minimum_decision = previous.get('road_minimum_decision')
        if road_minimum_decision is None:
            road_minimum_decision = decide_noncore_road_minimum(
                world, behavior, scope_audit, input_hashes, sha256(policy_path))
        else:
            validate_noncore_road_minimum(
                road_minimum_decision, world, behavior,
                scope_sha256=scope_audit['sha256'], input_hashes=input_hashes,
                policy_sha256=sha256(policy_path))
            if road_minimum_decision.get('fact_version_id') != (
                    (scope_audit.get('effective_facts') or {}).get('version_id')):
                from _lib.contracts.v3 import ContractError
                raise ContractError('Noncore road decision fact version is stale',
                                    rule_id='stale_version')
    probe_path = Path(case)/'state/probe.json'
    probe = read_json(probe_path).get('probe', {}) if probe_path.is_file() else {}
    from _lib.world.actor_facts import enabled as actor_fact_enabled
    from _lib.runtime.identity_revision import enabled as identity_enabled
    result = select_route(world, behavior, parameters, observation_issues,
                          intersection_enabled=intersection_enabled(case), observation_contract=observation_enabled(case),
                          actor_fact_review=actor_fact_enabled(case),
                          identity_review=identity_enabled(case),
                          duration_s=probe.get('duration_s'),
                          road_minimum_decision=road_minimum_decision)
    from _lib.contracts.execution_gaps import enabled as gap_recovery, diagnostics
    if gap_recovery(case):
        result['execution_gap_issues'] = diagnostics(case, world, behavior)
    result['input_hashes'] = input_hashes
    if road_minimum_decision is not None:
        result['road_minimum_decision'] = road_minimum_decision
    from _lib.runtime.execution_plan import record as record_plan
    plan=record_plan(case,world,behavior,result)
    if plan: result['execution_plan_sha256']=plan['sha256']
    result['execution_scope'] = scope_audit
    from _lib.paths import SKILLS
    from _lib.behavior.compile import preview_digest
    result['planner_sha256'] = preview_digest({p:sha256(SKILLS/p) for p in (
        '_lib/runtime/routing.py','_lib/world/actor_facts.py','_lib/behavior/intersection.py','_lib/behavior/longitudinal.py',
        '_lib/behavior/fidelity_motion.py','_lib/behavior/lane_change.py',
        'schemas/longitudinal_motion.schema.json')})
    atomic_json(Path(case)/'state/execution_route.json', result)
    return result


def unobserved_actor_lanes(world, behavior):
    """Report all-null participant lanes without changing observation samples."""
    from _lib.contracts.lane_locations import lane_locations
    from _lib.contracts.actors import is_vehicle
    locations = lane_locations(world['world']['road'])
    issues = []
    for actor in behavior.get('actors', []):
        if actor.get('simulation_role') != 'participant' or not is_vehicle(actor):
            continue
        points = actor.get('trajectory', [])
        declarations = points + [p for p in behavior.get('executor_primitives', [])
                                 if p.get('type') == 'initial_state' and p.get('actor_id') == actor['actor_id']]
        known = any(p.get('lane_id') in locations or (
            type(p.get('ordinal_from_right')) is int and p.get('road_id') is not None
            and sum(value == (p['road_id'], p['ordinal_from_right']) for value in locations.values()) == 1)
            for p in declarations)
        if not known:
            issues.append(dict(rule_id='execution.lane_unobserved', actor_id=actor['actor_id'],
                actor_ids=[actor['actor_id']], owner='behavior', retry_kind='new_evidence',
                field_path='/actors/' + actor['actor_id'] + '/trajectory',
                message=f"actor {actor['actor_id']}: no known lane; do not substitute the road's first lane"))
    return issues


class LaneEvidenceGap(ValueError):
    code = 'lane_evidence_insufficient'

    def __init__(self, issues):
        super().__init__('Participant lane evidence is required before Converter continuation')
        self.diagnostics = {**issues[0], 'issues': issues, 'retry_kind': 'new_evidence'}


def require_lane_evidence(route, *, allow_review=False):
    issues = [i for i in route.get('issues', []) if i.get('rule_id') == 'execution.lane_unobserved']
    if issues and not allow_review:
        raise LaneEvidenceGap(issues)


def repair_known_gap(runner, case, route):
    """Dispatch only scopes with existing authoritative patch implementations."""
    if route['route'] != 'blocked': return False
    from _lib.behavior.compile import preview_digest
    from _lib.contracts.v3 import ContractError
    state=runner.progress(case)
    if any(s.get('status') in {'submitted','pending','running'} or
           (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and not s.get('deadline_task_resolved'))
           for s in state.get('sessions',[])) or state.get('diagnostics',{}).get('unresolved_submissions'):
        raise ContractError('Resolve existing tasks before execution repair')
    from _lib.runtime.identity_revision import enabled as identity_enabled, ACCEPTED
    identity = next((i for i in route['issues'] if i['rule_id'] == 'identity.temporal_binding'
                     and i.get('actor_id') and not set(i.get('impact_domains', [])) -
                     {'actor_identity', 'actor_motion'}), None)
    if identity and identity_enabled(case) and not (Path(case)/ACCEPTED).exists():
        from _lib.behavior.actor_motion_review import run
        run(runner, case, identity['actor_id'], mode='identity_link')
        runner.behavior(case)
        return True
    actionable=next((i for i in route['issues'] if i['rule_id']=='world.unresolved_fact' and i.get('world_paths')),None)
    if actionable is None:
        from _lib.contracts.execution_gaps import enabled as gap_recovery
        lane_rules = {'execution.lane_unknown'}
        if gap_recovery(case):
            lane_rules.add('execution.lane_unobserved')
        actionable=next((i for i in route['issues'] if i['rule_id'] in lane_rules and i.get('actor_id')),None)
    if actionable is None: return False
    if actionable['owner'] != 'world':
        prior = state.get('diagnostics', {}).get('lane_review')
        if prior and (prior['actor_id'] != actionable['actor_id'] or prior['status'] in {'completed','failed'}):
            return False
    key=preview_digest({'inputs':route.get('input_hashes'),'issue':actionable,'planner':route.get('planner_sha256')})
    records=state.setdefault('diagnostics',{}).setdefault('execution_repairs',{})
    if key in records: return False
    from _lib.runtime.bounded_recovery import available
    if available(case, state)<=0:
        raise ContractError('model_recovery_budget_exhausted')
    records[key]={'status':'requested','issue':actionable}
    runner.save_progress(case,state)
    if actionable['owner']=='world':
        runner.ensure_world_ready(case,execution=True)
        runner.behavior(case)
    else:
        runner.review_actor_lane(case,actionable['actor_id'])
    state=runner.progress(case);state['diagnostics']['execution_repairs'][key]['status']='completed'
    runner.save_progress(case,state)
    return True


def effective_parameters(parameters, route):
    result = copy.deepcopy(parameters)
    result['fallback_policy']['straight_preview']['enabled'] = route['route'] == 'straight_preview'
    return result


class CapabilityBlocked(ValueError):
    code = 'unsupported_scene_type'

    def __init__(self, issues):
        super().__init__('; '.join(row['message'] for row in issues))
        self.diagnostics = {**issues[0], 'issues': issues}


def check_world_capabilities(case):
    if not automatic(case):
        return
    issues = capability_issues(read_json(Path(case)/'state/world_context.json'))
    atomic_json(Path(case)/'state/capability_preflight.json', {'issues': issues})
    if issues:
        raise CapabilityBlocked(issues)
