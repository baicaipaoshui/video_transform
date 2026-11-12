"""Shared deterministic delivery gates; no network or Case writes."""
import math

from _lib.contracts.actors import is_vehicle

from _lib.delivery.projector import compare as compare_projection
from _lib.delivery.motion_check import compare as compare_motion


def validate_references(target):
    """Resolve names across all four sections, not inside isolated models."""
    from collections import Counter
    objects = []
    def walk(value):
        if isinstance(value, dict):
            objects.append(value)
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(target)
    names = Counter(row['name'] for row in objects if isinstance(row.get('name'), str))
    ids = Counter(row['id'] for row in objects if isinstance(row.get('id'), str))
    if any(count != 1 for count in ids.values()):
        raise ValueError('duplicate target ID')
    for row in objects:
        if 'reference_name' in row and (not isinstance(row['reference_name'], str) or names[row['reference_name']] != 1):
            raise ValueError(f"unresolved or ambiguous reference_name: {row['reference_name']}")
        for key in ('parent_id', 'scene_id'):
            if row.get(key) is not None and (not isinstance(row[key], str) or ids[row[key]] != 1):
                raise ValueError(f'unresolved target {key}: {row[key]}')
    return True


def _source_motion_complete(analysis):
    """Whether the observation already supports the strict source-motion gate."""
    scenario = (analysis.get("simulation_scenarios") or [{}])[0]
    involved = scenario.get("involved_actor_ids") or []
    actors = {actor.get("actor_id"): actor for actor in [analysis.get("ego", {}), *analysis.get("actors", [])]}
    quality = analysis.get("quality_report") or {}
    if not (quality.get("straight_preview") or quality.get("intersection_preview")):
        involved = list(dict.fromkeys([*involved, *(
            aid for aid, actor in actors.items() if is_vehicle(actor)
            and actor.get("provenance", {}).get("source") != "user_requested_scenery"
        )]))

    def finite(value):
        if isinstance(value, dict):
            if value.get("status") == "unknown":
                return False
            value = value.get("value")
        return type(value) in (int, float) and math.isfinite(value)

    checked = False
    for actor_id in involved:
        actor = actors.get(actor_id) or (analysis.get("ego") if actor_id == "ego" else None)
        if not isinstance(actor, dict):
            return False
        if not is_vehicle(actor) or actor.get("is_static"):
            continue
        checked = True
        points = actor.get("trajectory") or []
        if len(points) < 2:
            return False
        previous_time = None
        for point in points:
            position = point.get("position_ego_m") or {}
            if not all(finite(value) for value in (
                    point.get("time_s", point.get("timestamp_s")), point.get("speed_mps"),
                    position.get("x"), position.get("y"))):
                return False
            time = point.get("time_s", point.get("timestamp_s"))
            time = time.get("value") if isinstance(time, dict) else time
            if time < 0 or previous_time is not None and time <= previous_time:
                return False
            previous_time = time
    return checked


def validate_target(analysis, target, road_source, conversion=None):
    from _lib.contracts.census_scope import validate_analysis
    scope_sha256 = validate_analysis(analysis)
    if scope_sha256 and (conversion is None or conversion.get('census_scope_sha256') != scope_sha256):
        raise ValueError('Target receipt does not bind the current census observation scope')
    if analysis.get('quality_report', {}).get('road_minimum_decision') and conversion is None:
        raise ValueError('Noncore road minimum requires a frozen conversion receipt')
    return _validate_target(analysis, target, road_source, conversion)


def _equivalent_road_heading(source, execution):
    """Accept only a periodic heading or its cardinal snap at source precision."""
    from decimal import Decimal
    from _lib.world.topology import finite
    if type(source) not in (int, float) or type(execution) not in (int, float):
        return False
    try:
        observed, projected = finite(source), finite(execution)
    except ValueError:
        return False
    # Source JSON numbers carry their recorded decimal precision. A snap may
    # replace 1.5708 with pi/2, but may not change the represented direction
    # beyond half of that final decimal unit.
    precision = Decimal(str(observed)).as_tuple().exponent
    rounding = float(Decimal(5).scaleb(precision - 1)) if observed else 0.0
    numeric = 8 * math.ulp(2 * math.pi)
    if math.ulp(observed) > numeric or math.ulp(projected) > numeric:
        return False
    distance = abs(math.remainder(observed - projected, 2 * math.pi))
    if distance > rounding + numeric:
        return False
    if distance <= numeric:
        return True
    cardinals = (0.0, math.pi / 2, math.pi, 3 * math.pi / 2)
    return any(abs(math.remainder(projected - cardinal, 2 * math.pi)) <= numeric
               for cardinal in cardinals)


def _validate_noncore_road_target(analysis, projected, target):
    """Protect observed execution relations while accepting an abstract side arm."""
    from _lib.contracts.v3 import gate_degradations
    from _lib.runtime.execution_scope import validate_analysis_scope
    quality = analysis['quality_report']
    ruling = quality['road_minimum_decision']
    gate_degradations(quality)
    validate_analysis_scope(analysis)
    if analysis.get('scene_type') != 'intersection':
        raise ValueError('Noncore road minimum is limited to evidenced intersections')
    original_roads = analysis['road']['roads']
    projected_roads = projected['road']['roads']
    target_roads = target['scene']['road_network']['roads']
    if len(original_roads) != len(projected_roads) or len(original_roads) != len(target_roads):
        raise ValueError('Noncore road projection changed junction road arms')
    for source, execution in zip(original_roads, projected_roads):
        for field in ('road_id', 'road_role', 'lane_ids'):
            if source.get(field) != execution.get(field):
                raise ValueError('Noncore road projection changed a known road relation: ' + field)
        if not _equivalent_road_heading(source.get('heading_rad'), execution.get('heading_rad')):
            raise ValueError('Noncore road projection changed a known road relation: heading_rad')
    if analysis.get('events') != projected.get('events'):
        raise ValueError('Noncore road projection changed retained events')
    source_junctions, execution_junctions = analysis.get('junctions', []), projected.get('junctions', [])
    if len(source_junctions) != len(execution_junctions):
        raise ValueError('Noncore road projection changed junction count')
    for source, execution in zip(source_junctions, execution_junctions):
        for field in ('road_ids', 'incoming_roads', 'outgoing_roads', 'lane_connections'):
            if source.get(field) != execution.get(field):
                raise ValueError('Noncore road projection changed required junction connections')
        for field, key in (('crosswalks', 'crosswalk_id'), ('traffic_signals', 'signal_id')):
            original = {row.get(key): row for row in source.get(field, [])}
            current = {row.get(key): row for row in execution.get(field, [])}
            if set(original) != set(current):
                raise ValueError('Noncore road projection changed observed facilities')
            for entity_id, row in original.items():
                for owner in ('related_road_id', 'road_id'):
                    if row.get(owner) is not None and row.get(owner) != current[entity_id].get(owner):
                        raise ValueError('Noncore road projection changed facility road ownership')
    original_actors = [analysis['ego'], *analysis.get('actors', [])]
    execution_actors = {row['actor_id']: row for row in [projected['ego'], *projected.get('actors', [])]}
    excluded = {row['actor_id'] for row in projected.get('quality_report', {})
                .get('execution_actor_scope', {}).get('excluded_actors', [])
                if isinstance(row, dict) and row.get('actor_id')}
    for actor in original_actors:
        aid = actor['actor_id']
        if aid in excluded:
            continue
        actual = execution_actors.get(aid)
        if actual is None:
            raise ValueError('Noncore road projection omitted a retained Actor: ' + aid)
        for point in actor.get('trajectory', []):
            lane_id = point.get('lane_id')
            if lane_id is None:
                continue
            matches = [item for item in actual.get('trajectory', [])
                       if item.get('time_s') == point.get('time_s')
                       and (point.get('frame_id') is None or item.get('frame_id') == point.get('frame_id'))]
            if not any(item.get('lane_id') == lane_id for item in matches):
                raise ValueError('Noncore road projection changed a retained Actor lane path: ' + aid)
    for source in analysis.get('executor_primitives', []):
        matches = [item for item in projected.get('executor_primitives', [])
                   if item.get('primitive_id') == source.get('primitive_id')]
        if len(matches) != 1 or any(source.get(key) != matches[0].get(key)
                                    for key in ('actor_id', 'event_id', 'type') if source.get(key) is not None):
            raise ValueError('Noncore road projection changed a retained primitive')
    for decision in ruling['decisions']:
        for road in decision['roads']:
            index = road['road_index']
            count = road['execution_approximation']['lane_count']
            source = original_roads[index]
            knowledge = source.get('lane_knowledge') or {}
            if (source.get('road_id') != road['road_id'] or knowledge.get('kind') != 'minimum'
                    or knowledge.get('count') != count or len(source.get('lane_ids', [])) != count
                    or len(target_roads[index]['segments'][0]['lanes']) != count):
                raise ValueError('Target side arm differs from the authorized observed minimum')
    return {'status': 'authorized_abstract_projection_passed',
            'real_lane_count_and_junction_geometry': 'unknown',
            'ruling_sha256': ruling['sha256']}


def _validate_target(analysis, target, road_source, conversion=None):
    """Check execution copies only after the public entry verified source scope."""
    # Certify the published source, not the Converter's private execution copy
    # recursively checked below after authorized defaults have been applied.
    if conversion is not None and analysis.get('quality_report',{}).get('scene_revision'):
        from _lib.behavior.scene_revision import validate
        validate(analysis)
    validate_references(target)
    from _lib.behavior.lane_change import from_analysis as derive_lane_change
    source_lane_change = derive_lane_change(analysis)
    from _lib.delivery.source_constraints import check as check_source_constraints
    if conversion is not None and (
            any(key in conversion for key in (
                "fallback_receipt_version", "execution_fallbacks", "scene_fallbacks"))
            or conversion.get("workflow_contract") == "video-case-world-behavior"
            or conversion.get("fallbacks")):
        # The Converter's private execution copy intentionally differs from
        # observation-only analysis.  Rebuild that copy independently, bind
        # the receipt to it, then run the normal projection/motion gates
        # against the projected contract.  Defaults are never compared back
        # to unknown source values, while known source values remain strict.
        import sys
        from _lib.paths import SCENARIO_ROOT
        lib = SCENARIO_ROOT / "json-coverter" / "skills" / "case-conversion" / "scripts" / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        from execution_fallbacks import project_analysis
        from validators import check_downstream_road_network
        from _lib.contracts.conversion_policy import validate as validate_policy, authorize_scene
        policy = conversion.get('conversion_policy')
        if policy is not None:
            validate_policy(policy, input_sha256=conversion.get('input_sha256'),
                            scene_type=analysis.get('scene_type'), road_source=road_source)
            if conversion.get('policy_sha256') != policy['policy_sha256']:
                raise ValueError('conversion receipt policy hash mismatch')
        elif conversion.get('policy_sha256') is not None:
            raise ValueError('conversion receipt is missing its policy')
        semantic_gap = conversion.get('semantic_gap')
        if semantic_gap is not None:
            from _lib.contracts.semantic_gap import validate as validate_semantic_gap
            validate_semantic_gap(analysis, conversion.get('input_sha256'), semantic_gap)
        projected, fallbacks, excluded_actor_ids = project_analysis(
            analysis, analysis.get("scene_type"), policy=policy, semantic_gap=semantic_gap)
        from _lib.contracts.unused_association import preserve_conversion
        preserve_conversion(analysis, excluded_actor_ids)
        projected_lane_change = derive_lane_change(projected)
        if projected_lane_change != source_lane_change:
            raise ValueError("execution projection changed the evidence-derived lane-change specification")
        from scene_generation import (RECEIPT_VERSION, replay_scene_conversion,
                                      validate_fallback_list)
        expected_target, scene_fallbacks = replay_scene_conversion(projected, analysis.get("scene_type"))
        scene_fallbacks = authorize_scene(scene_fallbacks, policy)
        version = conversion.get("fallback_receipt_version")
        if "fallback_receipt_version" in conversion:
            if version != RECEIPT_VERSION:
                raise ValueError("unsupported fallback receipt version: " + str(version))
            execution_records = validate_fallback_list(conversion.get("execution_fallbacks"), "execution")
            scene_records = validate_fallback_list(conversion.get("scene_fallbacks"), "scene")
            if fallbacks != execution_records:
                raise ValueError("execution fallback receipt differs from deterministic projection")
            if scene_fallbacks != scene_records:
                raise ValueError("scene fallback receipt differs from deterministic scene replay")
        elif "execution_fallbacks" in conversion or "scene_fallbacks" in conversion:
            raise ValueError("staged fallback records require a receipt version")
        # Historical receipts have one aggregate list; compare the entire replay,
        # never split records by prose/reason or alter a persisted Case receipt.
        aggregate = validate_fallback_list(conversion.get("fallbacks"), "aggregate")
        if aggregate != fallbacks + scene_fallbacks:
            raise ValueError("conversion fallback receipt differs from complete deterministic replay")
        if expected_target != target:
            raise ValueError("target differs from deterministic scene replay")
        road_minimum_check = (_validate_noncore_road_target(analysis, projected, target)
                              if analysis.get('quality_report', {}).get('road_minimum_decision') else None)
        if excluded_actor_ids != list(conversion.get("excluded_actor_ids", [])):
            raise ValueError("conversion actor exclusions differ from deterministic projection")
        from _lib.behavior.lane_change import audit_target as audit_lane_change
        lane_change_check = audit_lane_change(projected, target)
        source_checks = check_source_constraints(analysis, target) if road_source == 'preview_json' else {'status': 'not_applicable'}
        if (_source_motion_complete(analysis)
                or analysis.get("quality_report", {}).get("straight_preview")):
            projected_checks = _validate_target(projected, target, road_source)
        else:
            compare_projection(projected, target, road_source=road_source)
            from _lib.world.topology import compare_connections
            projected_checks = {
                "projection": "passed",
                "topology": compare_connections(projected, target),
                "motion": "policy_default_not_source_verified",
            }
        errors = check_downstream_road_network(target)
        if errors:
            raise ValueError("OpenX input contract failed: " + "; ".join(errors))
        result = {
            "projection": "deterministic_fallbacks_applied",
            "references": "passed",
            "topology": "target_contract_passed",
            "motion": projected_checks.get("motion", "policy_default_not_source_verified"),
            "projected_contract": "passed",
            "source_constraints": source_checks,
            "render_verification": "not_executed",
            "fallback_count": len(conversion["fallbacks"]),
            "execution_fallback_count": len(fallbacks),
            "scene_fallback_count": len(scene_fallbacks),
            "execution_fallback_receipt": "passed",
            "scene_fallback_receipt": "passed",
            "scene_replay": "passed",
            "excluded_actor_ids": list(conversion.get("excluded_actor_ids", [])),
        }
        if road_minimum_check is not None:
            result['noncore_road_minimum'] = road_minimum_check
        if lane_change_check is not None:
            result["highway_lane_change"] = lane_change_check
            result["motion"] = "passed"
        from _lib.contracts import semantic_motion
        if semantic_gap is not None and semantic_gap.get('revision') == semantic_motion.REVISION:
            result['semantic_motion'] = semantic_motion.audit(analysis, target, semantic_gap)
            from _lib.contracts import motion_witnesses
            witness_revision = semantic_gap['policy'].get(motion_witnesses.POLICY_KEY)
            if witness_revision:
                request = semantic_motion.request(analysis, semantic_gap['analysis_sha256'], witness_revision=witness_revision)
                result['semantic_motion']['execution_owners'] = motion_witnesses.check_projection(request, analysis, projected)
            result['motion'] = 'semantic_execution_approximation_checked'
        return result
    compare_projection(analysis, target, road_source=road_source)
    source_checks = check_source_constraints(analysis, target) if road_source == 'preview_json' else {'status': 'not_applicable'}
    from _lib.world.topology import compare_connections
    result = {"projection": "passed", "references": "passed", "motion": "not_executed",
              "render_verification": "not_executed", "source_constraints": source_checks}
    result['topology'] = compare_connections(analysis, target)
    if analysis.get('quality_report',{}).get('straight_preview'):
        from _lib.behavior.compile import validate_preview_analysis
        validate_preview_analysis(analysis)
        scenario=analysis['simulation_scenarios'][0]
        actors={v['name']:v for v in target['scenario']['vehicles']}
        for aid,condition in scenario['initial_conditions'].items():
            name='Ego' if aid=='ego' else aid.replace('vehicle_','Vehicle_')
            timeline=actors[name]['timeline']
            if len(timeline)!=1 or timeline[0]['type']!='drive_straight':
                raise ValueError('preview generated unobserved maneuver: '+aid)
            action=timeline[0]['action']; speed=condition['speed_kmh']['value']
            if any(abs(action[key]-speed)>1e-6 for key in ('start_longitudinal_velocity','end_longitudinal_velocity')):
                raise ValueError('preview constant velocity changed: '+aid)
            transition=timeline[0]['transition']
            expected=speed/3.6*scenario['duration_s']
            if transition['type']!='traveled_distance' or abs(transition['value']-expected)>1e-5:
                raise ValueError('preview maneuver distance/time changed: '+aid)
            position=action['start_position']['position']; offset=condition['lane_center_offset_m']
            if (position['lateral_unit']!='m' or abs(position['lateral_value']-abs(offset))>1e-6 or
                position['lateral_specifier']!=('left_of' if offset>=0 else 'right_of')):
                raise ValueError('preview lane centre offset changed: '+aid)
        result['preview']='relations_and_constant_motion_passed'
    if analysis.get("quality_report", {}).get("motion_contract") is not None:
        result["motion_result"] = compare_motion(analysis, target)
        result["motion"] = "passed"
    elif analysis.get('scene_type') == 'intersection':
        from _lib.behavior.motion import compare_target
        result['motion_result'] = compare_target(analysis, target)
        result['motion'] = result['motion_result']['status']
    if source_lane_change is not None:
        from _lib.behavior.lane_change import audit_target as audit_lane_change
        result['highway_lane_change'] = audit_lane_change(analysis, target)
        result['motion'] = 'passed'
    if analysis.get('quality_report',{}).get('video_fidelity'):
        from _lib.behavior.fidelity_motion import audit_target
        result['video_fidelity']=audit_target(analysis,target)
    from _lib.behavior.longitudinal import audit_target as audit_longitudinal
    audit_longitudinal(analysis,target)
    return result
