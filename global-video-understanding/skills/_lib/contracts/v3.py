#!/usr/bin/env python3
"""Deterministic v3 world/behavior contract, builder, and release gates."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from _lib.paths import SKILLS as HERE
from _lib.contracts.actors import is_vehicle
CONTRACT = "video-case-world-behavior"
CORE_IMPACT_DOMAINS = frozenset({
    "road_topology", "lane_count", "actor_set", "actor_identity", "actor_lane",
    "event_type", "event_order", "executor_primitive",
})
DEFAULT_ALLOWED_DEGRADATIONS = (
    "parking_as_paved_shoulder", "weather_not_applied_downstream",
    "road_length_policy_default", "driving_lane_width_policy_default",
    "parking_width_policy_default",
)
ORIGIN_PRIORITY = {"placeholder": 0, "policy_default": 1, "estimated": 2, "observed": 3}


class ContractError(ValueError):
    """A stable v3 gate failure."""

    def __init__(self, message, *, rule_id=None, actor_id=None):
        super().__init__(message)
        self.rule_id = rule_id
        self.actor_id = actor_id
        if rule_id == 'identity.conflict':
            self.code = 'identity_conflict'


class ConflictTargetError(ContractError):
    """A Batch conflict cannot be applied to its declared World target."""

    code = "schema_validation_failed"

    def __init__(self, message, *, index, world_path, expected=None, actual=None,
                 required_change=None):
        super().__init__(message, rule_id="conflict.target_shape_mismatch")
        self.absolute_path = ("semantic_conflicts", index, "world_path")
        self.path = self.absolute_path
        self.diagnostics = {
            "rule_id": self.rule_id,
            "field_path": "/semantic_conflicts/" + str(index) + "/world_path",
            "world_path": world_path,
            "expected": expected,
            "actual": actual,
            "retry_kind": "content_repair",
            "required_change": required_change,
        }


class BehaviorSemanticBlocked(ContractError):
    code = "behavior_semantic_blocked"


def _read(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _schema(name, data):
    from _lib.contracts.fidelity import validator
    errors = sorted(validator(name).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        error = errors[0]
        where = ".".join(map(str, error.absolute_path)) or "<root>"
        if error.instance is None and 'position_ego_m' in error.absolute_path and list(error.absolute_path)[-1] in {'x','y','z'}:
            raise ContractError(f"{name}:{where}: omit this unknown coordinate component; null is not accepted (do not fill zero)")
        raise ContractError(f"{name}:{where}: {error.message}")


def fallback_policy(overrides=None):
    """Expand and validate the complete versioned policy before fingerprinting."""
    policy = _read(HERE / "references" / "fallback-policy.v1.json")
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ContractError("fallback_policy must be an object")
    unknown = set(overrides) - set(policy)
    if unknown:
        raise ContractError(f"unknown fallback policy fields: {sorted(unknown)}")
    if "default_allowed_degradations" in overrides and tuple(overrides["default_allowed_degradations"]) != DEFAULT_ALLOWED_DEGRADATIONS:
        raise ContractError("project default degradation IDs are immutable")
    expected = _read(HERE / 'references' / 'fallback-policy.v1.json')['straight_preview']
    override_values = copy.deepcopy(overrides)
    preview_overrides = override_values.pop('straight_preview', {})
    if not isinstance(preview_overrides, dict):
        raise ContractError('fallback_policy.straight_preview must be an object')
    unknown_preview = set(preview_overrides) - set(expected)
    if unknown_preview:
        raise ContractError(f'unknown straight preview policy fields: {sorted(unknown_preview)}')
    policy.update(override_values)
    policy['straight_preview'].update(preview_overrides)
    preview = policy['straight_preview']
    if not isinstance(preview, dict) or set(preview) != set(expected):
        raise ContractError('invalid straight preview policy fields')
    for key, value in expected.items():
        if isinstance(value, bool):
            valid = type(preview[key]) is bool
        elif isinstance(value, str):
            valid = preview[key] == value
        else:
            valid = type(preview[key]) in (int,float) and math.isfinite(preview[key]) and preview[key] > 0
        if not valid: raise ContractError(f'invalid straight preview policy: {key}')
    if policy["mode"] != "controlled":
        raise ContractError("v3 only supports controlled fallback policy")
    for key in ("local_road_length_m", "highway_length_m", "driving_lane_width_m", "parking_width_m"):
        if not isinstance(policy[key], (int, float)) or policy[key] <= 0:
            raise ContractError(f"invalid fallback policy value: {key}")
    return policy


def normalize_parameters(parameters=None):
    """Return canonical parameters; caller persists this exact object in Case."""
    result = copy.deepcopy(parameters or {})
    if "quality_profile" in result or "contract_revision" in result:
        raise ContractError("quality_profile and contract_revision are no longer supported")
    explicit_version = result.get("workflow_version")
    version = explicit_version or "v3"
    if 'video_fidelity_policy' in result:
        from _lib.contracts.fidelity import validator
        document = _read(HERE/'schemas/video_fidelity.schema.json')
        if version != 'v3':
            raise ContractError('video_fidelity_policy requires v3 and a supported revision')
        try: validator('video_fidelity.schema.json').evolve(schema=document['$defs']['parameters']).validate(result)
        except Exception as error: raise ContractError('video_fidelity parameters: '+str(error)) from error
    elif 'parked_scenery_per_side' in result:
        raise ContractError('parked_scenery_per_side requires video_fidelity_policy')
    if version not in {"v2", "v3"}:
        raise ContractError("workflow_version must be v2 or v3")
    if version == "v2":
        if 'conversion_defaults' in result:
            raise ContractError('conversion_defaults requires v3')
        if "execution_policy_version" in result:
            raise ContractError("execution_policy_version requires v3")
        return result
    result["workflow_version"] = "v3"
    # Absence is legacy: do not change old canonical parameters/fingerprints.
    if "execution_policy_version" in result and (not isinstance(result["execution_policy_version"], str)
            or result["execution_policy_version"] not in {"legacy", "efficient_v1"}):
        raise ContractError("execution_policy_version must be legacy or efficient_v1")
    if 'confirmed_lane_assignments' in result:
        assignments = result['confirmed_lane_assignments']
        if (not isinstance(assignments, dict) or not assignments or
                any(not isinstance(aid, str) or not aid or not isinstance(lid, str) or
                    not re.fullmatch(r'lane_[0-9]{3}', lid) for aid,lid in assignments.items())):
            raise ContractError('confirmed_lane_assignments must map actor IDs to lane_NNN')
    road_source = result.setdefault("road_source", "preview_json")
    if road_source not in {"preview_json", "external_xodr"}:
        raise ContractError("road_source must be preview_json or external_xodr")
    raw_policy = result.get('fallback_policy') or {}
    explicitly_enabled = (isinstance(raw_policy, dict)
                          and isinstance(raw_policy.get('straight_preview'), dict)
                          and raw_policy['straight_preview'].get('enabled') is True)
    result["fallback_policy"] = fallback_policy(raw_policy)
    explicit_allowed = 'allowed_degradations' in result
    allowed = result.get("allowed_degradations", result["fallback_policy"]["default_allowed_degradations"])
    if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
        raise ContractError("allowed_degradations must be an array of IDs")
    known = set(_read(HERE / "references" / "mela-capabilities.v1.json")["degradations"]) - {"single_lane_topology_placeholder"}
    from _lib.contracts.conversion_policy import DEGRADATIONS, validate_defaults
    known |= DEGRADATIONS
    # Standard requests use the configured execution defaults automatically.
    # Persisted Cases already carry an explicit allowlist, so normalization
    # keeps their frozen parameters and deliberate restrictions unchanged.
    if not explicit_allowed:
        allowed = list(set(allowed) | DEGRADATIONS | {'sidewalk_as_paved_shoulder'})
    if 'conversion_defaults' in result:
        validate_defaults(result['conversion_defaults'])
    unknown = set(allowed) - known
    if unknown:
        raise ContractError(f"unknown degradations: {sorted(unknown)}")
    allowed = set(allowed)
    if explicitly_enabled and not explicit_allowed:
        allowed.add('straight_preview_policy_default')
    if result['fallback_policy']['straight_preview']['enabled'] and 'straight_preview_policy_default' not in allowed:
        raise ContractError('straight_preview.enabled requires straight_preview_policy_default authorization')
    result["allowed_degradations"] = sorted(allowed)
    return result


def contract_fingerprint(parameters):
    payload = json.dumps(normalize_parameters(parameters), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_capabilities(workflow=None, scene_type=None):
    """Validate the distributable MELA contract without requiring MELA sources."""
    manifest = _read(HERE / "references" / "mela-capabilities.v1.json")
    _schema("mela_capabilities.schema.json", manifest)
    if manifest["gate_mode"] != "manifest_contract" or manifest["runtime_source_required"]:
        raise ContractError("capability manifest requires an unsupported runtime source audit")
    if workflow is not None:
        capability = manifest["workflows"].get(workflow)
        if capability is None:
            raise ContractError(f"unsupported MELA workflow: {workflow}")
        if scene_type is not None and scene_type not in capability["scene_types"]:
            raise ContractError(f"unsupported MELA scene type for {workflow}: {scene_type}")
    return manifest


def _validate_actor_lane_hypotheses(actors, world, collection):
    """Known hypotheses reference World lanes; omitted/null lanes remain unknown."""
    lane_ids = {lane['lane_id'] for lane in world['world']['road'].get('lanes', [])}
    for index, actor in enumerate(actors):
        lane = actor.get('hypothesized_lane')
        if lane is not None and lane not in lane_ids:
            error = ContractError('Actor lane hypothesis references an undefined lane: ' + lane,
                                  rule_id='world.reference', actor_id=actor['actor_id'])
            error.path = f'/{collection}/{index}/hypothesized_lane'
            error.diagnostics = {'rule_id': error.rule_id, 'path': error.path,
                                 'actor_id': actor['actor_id']}
            raise error


def validate_world(world, duration_s=None, *, observation_contract=False):
    _schema("world_context.schema.json", world)
    return validate_world_facts(world, duration_s, observation_contract=observation_contract)


def validate_world_facts(world, duration_s=None, *, observation_contract=False):
    """Semantic checks after public Schema validation at the same boundary.

    Admission reports all schema errors itself; it reuses these checks without
    running the same schema again. Other callers use validate_world.
    """
    road_summary = world['world']['road']
    if not observation_contract and any(not road.get('lane_ids') for road in road_summary.get('roads', [])):
        raise ContractError('World road lane_ids must be non-empty outside the observation contract',
                            rule_id='world.structure')
    if 'opposite_direction_lanes' in road_summary:
        opposing = road_summary['opposite_direction_lanes']
        one_way = road_summary.get('one_way')
        if one_way is True and opposing not in (0,):
            error = ContractError(
                'one_way=true conflicts with an existing or unknown opposing carriageway; use one_way=false and preserve the opposing count uncertainty',
                rule_id='world.directionality_conflict')
            error.path = '/world/road/one_way'
            raise error
        if one_way is False and isinstance(opposing, int) and opposing < 1:
            raise ContractError('one_way=false requires a positive opposing lane count or a bound null uncertainty')
        if opposing is None:
            if one_way is not False:
                raise ContractError('opposite_direction_lanes=null requires one_way=false')
            bound = [item for collection in ('questions_to_verify','ambiguities')
                     for item in world[collection]
                     if item.get('resolution') == 'unresolved'
                     and item.get('world_paths') == ['world.road.opposite_direction_lanes']]
            evidence = world.get('field_evidence', {}).get('world.road.opposite_direction_lanes', [])
            if not bound or not any(item.get('status') == 'unknown' for item in evidence):
                error = ContractError(
                    'opposite_direction_lanes=null requires unknown field evidence and a dedicated '
                    'unresolved item whose world_paths is exactly '
                    '["world.road.opposite_direction_lanes"]; bind opposing_separation separately',
                    rule_id='world.opposing_count_unknown_binding')
                error.path = '/world/road/opposite_direction_lanes'
                raise error
    for index, road in enumerate(road_summary.get('roads', [])):
        separation = (road.get('road_geometry') or {}).get('opposing_separation')
        if separation and separation['physically_separated'] is True:
            path=f'world.road.roads[{index}].road_geometry.opposing_separation'
            records=world.get('field_evidence',{}).get(path,[])
            if (separation['side']=='unknown' or not any(e.get('status')=='observed' and
                    e.get('frame_id') and str(e.get('reason','')).strip() for e in records)):
                raise ContractError('physical separation requires observed frame evidence at '+path)
    actor_ids = [item['actor_id'] for item in world['actor_hypotheses']]
    if len(actor_ids) != len(set(actor_ids)):
        raise ContractError('duplicate World actor ID', rule_id='identity.conflict')
    _validate_actor_lane_hypotheses(world['actor_hypotheses'], world, 'actor_hypotheses')
    for collection in ('questions_to_verify', 'ambiguities'):
        for item in world[collection]:
            if item.get('resolution') == 'unresolved' and 'actor_identity' in item.get('impact_domains', []) and not observation_contract:
                raise ContractError('unresolved World actor identity conflict', rule_id='identity.conflict')
            binding=item.get('identity_binding')
            if binding is not None:
                if (not isinstance(binding,dict) or binding.get('kind') not in {'unknown','physically_separated_opposing'}
                        or not isinstance(binding.get('actor_ids'),list) or not binding['actor_ids']
                        or any(not isinstance(a,str) for a in binding['actor_ids'])
                        or set(binding['actor_ids'])-set(actor_ids)
                        or not isinstance(binding.get('evidence_frame_ids'),list) or not binding['evidence_frame_ids']
                        or any(not isinstance(f,str) or not f for f in binding['evidence_frame_ids'])):
                    raise ContractError('invalid structured identity binding',rule_id='identity.binding')
    for road in world['world']['road'].get('roads', []):
        heading = road.get('heading_rad')
        if heading is not None and not math.isfinite(heading):
            raise ContractError(f"road {road['road_id']}: heading_rad must be finite")
    question_ids = [item["question_id"] for item in world["questions_to_verify"]]
    if len(question_ids) != len(set(question_ids)):
        raise ContractError("duplicate question ID")
    ambiguity_ids = [item['ambiguity_id'] for item in world['ambiguities']]
    if len(ambiguity_ids) != len(set(ambiguity_ids)):
        raise ContractError('duplicate ambiguity ID')
    # Ego is a reserved identity even when the camera vehicle is not separately
    # included in actor_hypotheses. All other object references must be declared.
    def references(item, key, allowed, path):
        values = item.get(key, [])
        if (not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                or set(values) - allowed):
            error = ContractError('World references undefined or invalid ' + key,
                                  rule_id='world.reference')
            error.path = path + '/' + key
            error.diagnostics = {'rule_id': error.rule_id, 'path': error.path}
            raise error

    for collection in ('questions_to_verify', 'ambiguities'):
        for index, item in enumerate(world[collection]):
            references(item, 'actor_ids', set(actor_ids) | {'ego'}, f'/{collection}/{index}')
            if collection == 'ambiguities':
                references(item, 'related_question_ids', set(question_ids), f'/{collection}/{index}')
    for index, interval in enumerate(world["critical_intervals"]):
        if interval["end_s"] <= interval["start_s"]:
            raise ContractError("critical interval must have positive duration")
        if duration_s is not None and interval["end_s"] > duration_s:
            raise ContractError("critical interval exceeds video duration")
        references(interval, 'question_ids', set(question_ids), f'/critical_intervals/{index}')
        references(interval, 'ambiguity_ids', set(ambiguity_ids), f'/critical_intervals/{index}')
    from _lib.world.point_review import points
    points(world)
    return world


def validate_behavior(behavior, world_epoch=None, world=None, *, execution=True,
                      allow_pending_identity=False):
    # A model's uncertainty label is not a solver certificate. In particular,
    # absence of a label does not establish boundedness for legacy candidates.
    _schema("behavior.schema.json", behavior)
    if world_epoch is not None and behavior["world_evidence_epoch"] != world_epoch:
        raise ContractError("behavior world evidence epoch mismatch")
    if world is not None:
        road = world.get('world', {}).get('road', {})
        from _lib.contracts.parking import actor_parking_binding
        from _lib.contracts.actors import is_vehicle
        for actor in behavior['actors']:
            if is_vehicle(actor) and actor.get('is_static'):
                actor_parking_binding(actor, road.get('roadside_areas', []))
        if road.get('roads') and road.get('lanes'):
            from _lib.contracts.lane_locations import validate_actor_locations
            validate_actor_locations(behavior['actors'], road, primitives=behavior['executor_primitives'])
    # The producer's execution_effect is a routing declaration, not a solver
    # certificate.  Explicit bounded uncertainty may proceed to the concrete
    # trajectory/primitive/evidence gates below and is delivered as degraded by
    # build_analysis.  An unlabeled non-report limitation remains unsafe for
    # legacy candidates, while an explicit execution block always stops here.
    blocking_limitations=[]
    for item in behavior["semantic_limitations"]:
        effect=item.get("execution_effect")
        if effect == "blocks_execution" or (effect is None and
                set(item.get("impact_domains", [])) - {"appearance", "report"}):
            blocking_limitations.append(item)
    if blocking_limitations and execution:
        raise BehaviorSemanticBlocked("Behavior cannot execute: " + json.dumps(blocking_limitations,ensure_ascii=False))
    for item in behavior['semantic_limitations']:
        if (not allow_pending_identity and 'actor_identity' in item.get('impact_domains', [])
                and item.get('execution_effect') == 'blocks_execution'):
            raise ContractError('unresolved actor identity conflict', rule_id='identity.conflict')
    validate_crossing_witnesses(behavior)
    actor_ids = [item["actor_id"] for item in behavior["actors"]]
    if len(actor_ids) != len(set(actor_ids)):
        raise ContractError("duplicate behavior actor ID", rule_id='identity.conflict')
    if "ego" not in actor_ids:
        raise ContractError("behavior must contain explicit ego actor")
    for actor_index, actor in enumerate(behavior["actors"]):
        trajectory = actor.get("trajectory")
        if not isinstance(trajectory, list) or not trajectory:
            raise ContractError(f"actors[{actor_index}].trajectory must be a non-empty array")
        for point_index, point in enumerate(trajectory):
            if not isinstance(point, dict):
                raise ContractError(f"actors[{actor_index}].trajectory[{point_index}] must be an object")
            if point.get('actor_id', actor['actor_id']) != actor['actor_id']:
                raise ContractError('trajectory actor identity conflict', rule_id='identity.conflict', actor_id=actor['actor_id'])
        identities = {point['object_id'] for point in trajectory if isinstance(point.get('object_id'), str)}
        if len(identities) > 1:
            raise ContractError('one actor_id references different objects', rule_id='identity.conflict', actor_id=actor['actor_id'])
        times = [point.get("time_s") for point in trajectory if isinstance(point.get("time_s"), (int, float))]
        if any(later < earlier for earlier, later in zip(times, times[1:])):
            raise ContractError(f"actors[{actor_index}].trajectory time_s must be non-decreasing")
    known = set(actor_ids) | {"ego"}
    event_ids = [item["event_id"] for item in behavior["events"]]
    if len(event_ids) != len(set(event_ids)):
        raise ContractError("duplicate behavior event ID")
    for event in behavior["events"]:
        if set(event["involved_actor_ids"]) - known:
            raise ContractError(f"event references unknown actor: {event['event_id']}")
        for evidence_index, evidence in enumerate(event.get("evidence", [])):
            if not isinstance(evidence, dict):
                raise ContractError(f"event evidence must be an object: {event['event_id']}[{evidence_index}]")
            if evidence.get("status") == "estimated":
                confidence=evidence.get("confidence")
                if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                        or not 0 <= confidence <= 1):
                    raise ContractError(f"estimated event evidence requires confidence: {event['event_id']}[{evidence_index}]")
    primitive_ids = [item["primitive_id"] for item in behavior["executor_primitives"]]
    if len(primitive_ids) != len(set(primitive_ids)):
        raise ContractError("duplicate executor primitive ID")
    initial_by_actor = {}
    for primitive in behavior["executor_primitives"]:
        if primitive["actor_id"] not in known:
            raise ContractError(f"primitive references unknown actor: {primitive['primitive_id']}")
        if primitive["type"] == "initial_state":
            actor_id = primitive["actor_id"]
            if actor_id in initial_by_actor:
                raise ContractError(f"duplicate initial_state primitive for actor: {actor_id}")
            initial_by_actor[actor_id] = primitive
    participants = {item["actor_id"] for item in behavior["actors"] if item["simulation_role"] == "participant"}
    if "ego" not in participants:
        raise ContractError("ego must have simulation_role=participant")
    missing_initial = participants - set(initial_by_actor)
    if missing_initial and execution:
        raise ContractError(f"participant actors missing initial_state primitive: {sorted(missing_initial)}")
    actor_by_id={item["actor_id"]:item for item in behavior["actors"]}
    for actor_id,primitive in initial_by_actor.items():
        if not execution:
            continue
        actor=actor_by_id.get(actor_id)
        if actor is None: continue
        first=actor["trajectory"][0]
        first_position=first.get("position_ego_m") or {}; initial_position=primitive.get("position_ego_m") or {}
        first_speed=_scalar(first.get("speed_mps")); initial_speed=_scalar(primitive.get("speed_kmh"))
        if (first.get("lane_id") != primitive.get("lane_id")
                or not all(isinstance(value,(int,float)) and not isinstance(value,bool)
                           for value in (first_position.get("x"),first_position.get("y"),initial_position.get("x"),initial_position.get("y"),first_speed,initial_speed))):
            raise ContractError(f"trajectory initial state missing for {actor_id}")
        if (abs(float(first_position["x"])-float(initial_position["x"]))>0.05
                or abs(float(first_position["y"])-float(initial_position["y"]))>0.05
                or abs(float(first_speed)*3.6-float(initial_speed))>0.05):
            raise ContractError(f"initial_state primitive diverges from trajectory for {actor_id}")
    # Speed is a magnitude, not the sign of displacement in the frozen ego
    # frame. Oncoming/background observations may have decreasing x. Executable
    # straight-motion consistency belongs to behavior.motion.check_samples.
    if world is not None:
        challenge_ids=[]
        for item in behavior['challenge_resolutions']:
            challenge_id=item.get('challenge_id'); challenge_ids.append(challenge_id)
            if item.get('resolution') == 'resolved_no_event':
                if (item.get('execution_effect') != 'exclude_unconfirmed_event' or
                        not all(isinstance(item.get(key),str) and item[key].strip()
                                for key in ('decision','rationale','consistency')) or
                        not isinstance(item.get('evidence_frame_ids'),list) or not item['evidence_frame_ids']):
                    raise ContractError(f"resolved_no_event requires explicit execution decision and evidence: {challenge_id}")
        if len(challenge_ids) != len(set(challenge_ids)):
            raise ContractError('duplicate Behavior challenge resolution')
        required = {item["ambiguity_id"] for item in world.get("ambiguities", [])
                    if item.get("owner") == "behavior" and item.get("resolution") == "unresolved"}
        resolved = {item.get("challenge_id") for item in behavior["challenge_resolutions"]
                    if item.get("resolution") not in (None, "", "unresolved", "unknown")}
        if required - resolved:
            raise ContractError(f"Behavior did not resolve owned ambiguities: {sorted(required - resolved)}")
    return behavior


def validate_crossing_witnesses(behavior):
    """A disappearance or a signal observation is not an actor crossing witness.

    This structural evidence gate cannot itself establish visual truth. The
    caller additionally binds witness frames/times to this Case's frame index.
    """
    events = {event["event_id"]: event for event in behavior["events"]}
    crossing_ids = {event["event_id"] for event in events.values()
                    if event["event_type"] in {"pedestrian_crossing", "cross_roadway"}}
    from _lib.contracts.entry import ENTRY_TYPES
    entry_types = ENTRY_TYPES | {'ego_intersection_entry_and_crossing'}
    entry_ids = {event['event_id'] for event in events.values() if event['event_type'] in entry_types}
    for primitive in behavior['executor_primitives']:
        if primitive['type'] in entry_types:
            event = events.get(primitive.get('event_id'))
            if event is None or primitive['actor_id'] not in event['involved_actor_ids']:
                raise ContractError(f"intersection_entry {primitive['primitive_id']} requires actor-bound event_id")
            entry_ids.add(event['event_id'])
    for event_id in entry_ids:
        event = events[event_id]
        interval = event.get('time_range_s', event.get('time_interval_s'))
        if isinstance(interval, dict):
            interval = [interval.get('start'), interval.get('end')]
        if (not isinstance(interval, list) or len(interval) != 2 or
                not all(type(t) in (int, float) and math.isfinite(t) for t in interval) or
                interval[0] < 0 or interval[0] >= interval[1]):
            raise ContractError(f'event {event_id}: intersection entry requires bounded time_range_s')
        for actor_id in event['involved_actor_ids']:
            observed = [e for e in event.get('evidence', []) if isinstance(e, dict)
                        and e.get('status') == 'observed' and e.get('actor_id') == actor_id
                        and isinstance(e.get('boundary_id'), str) and e['boundary_id']
                        and isinstance(e.get('frame_id'), str)
                        and type(e.get('time_s')) in (int, float) and math.isfinite(e['time_s'])]
            pairs = [(a, b) for a in observed for b in observed
                     if a['boundary_id'] == b['boundary_id'] and a['frame_id'] != b['frame_id']
                     and a.get('boundary_state') == 'before' and b.get('boundary_state') in {'inside', 'after'}
                     and a['time_s'] < b['time_s']]
            pairs = [(a, b) for a, b in pairs if
                     a['time_s'] == max(e['time_s'] for e in observed
                         if e['boundary_id'] == a['boundary_id'] and e.get('boundary_state') == 'before') and
                     b['time_s'] == min(e['time_s'] for e in observed
                         if e['boundary_id'] == a['boundary_id'] and e.get('boundary_state') in {'inside', 'after'})]
            if not any(abs(interval[0]-a['time_s']) < 1e-6 and abs(interval[1]-b['time_s']) < 1e-6
                       for a, b in pairs):
                raise ContractError(f'event {event_id}: intersection entry interval must match actor-bound before/inside boundary frames')
        for primitive in behavior['executor_primitives']:
            if primitive.get('event_id') == event_id and primitive['type'] in entry_types:
                t = _scalar(primitive.get('trigger_time_s'))
                if type(t) not in (int, float) or not math.isfinite(t) or not interval[0] <= t <= interval[1]:
                    raise ContractError(f'event {event_id}: trigger_time_s outside observed entry interval')
    for primitive in behavior["executor_primitives"]:
        if primitive["type"] == "cross_roadway":
            event = events.get(primitive.get("event_id"))
            if event is None or primitive["actor_id"] not in event["involved_actor_ids"]:
                raise BehaviorSemanticBlocked("cross_roadway requires an actor-bound evidenced event")
            crossing_ids.add(event["event_id"])
    for event_id in sorted(crossing_ids):
        event = events[event_id]
        for actor_id in event["involved_actor_ids"]:
            witnesses = [item for item in event["evidence"] if isinstance(item, dict)
                         and item.get("status") == "observed" and item.get("actor_id") == actor_id
                         and isinstance(item.get("frame_id"), str)
                         and type(item.get("time_s")) in (int, float)
                         and item.get("boundary_state") in {"before", "inside", "after"}
                         and isinstance(item.get("boundary_id"), str)]
            proven = any(a["frame_id"] != b["frame_id"] and a["time_s"] < b["time_s"]
                         and a["boundary_id"] == b["boundary_id"]
                         and (a["boundary_state"], b["boundary_state"]) in {
                             ("before", "inside"), ("before", "after"), ("inside", "after")}
                         for a in witnesses for b in witnesses)
            if not proven:
                raise BehaviorSemanticBlocked(
                    f"event {event_id}: crossing evidence missing for {actor_id}; "
                    "require two actor-bound observed boundary states, not disappearance or signal state")


def normalize_batch_answer_ids(batch):
    """Canonicalize redundant answer indexes from observation references.

    Batch workers occasionally emit a valid, evidenced observation but omit its
    ID from one of the top-level ``answered_*_ids`` arrays.  Those arrays are an
    index, not an independent video fact, so rebuilding the missing membership
    is deterministic and must not consume another model attempt.

    Explicit memberships are retained for backwards compatibility (some
    historical conflict-only answers have no observation).  Consequently the
    normal coverage checks below still reject an assigned item that appears in
    neither the explicit index nor observations.
    """
    if not isinstance(batch, dict):
        return batch
    observations = batch.get("observations")
    if not isinstance(observations, list):
        return batch
    mappings = (
        ("answered_question_ids", "question_id"),
        ("answered_ambiguity_ids", "ambiguity_id"),
    )
    for index_key, observation_key in mappings:
        explicit = batch.get(index_key, [])
        if not isinstance(explicit, list) or any(not isinstance(value, str) for value in explicit):
            continue
        derived = {
            item.get(observation_key)
            for item in observations
            if isinstance(item, dict) and isinstance(item.get(observation_key), str)
        }
        batch[index_key] = sorted(set(explicit).union(derived))
    return batch


def normalize_batch_candidate(batch, *, case_id, batch_id, session_id, world_evidence_epoch):
    """Fill only orchestration-owned Batch envelope fields.

    Existing, conflicting values are deliberately not overwritten: a worker
    writing output for another Case, Batch, or World epoch is a real integrity
    failure.  Missing values are transport/shape defects and can be restored
    from the frozen request without another visual-model call.
    """
    if not isinstance(batch, dict):
        return batch, []
    repairs = []
    if not isinstance(session_id, str) or not session_id.strip():
        raise ContractError("batch session provenance unavailable")
    trusted = {
        "contract_version": CONTRACT,
        "case_id": case_id,
        "batch_id": batch_id,
        "session_id": session_id,
        "world_evidence_epoch": world_evidence_epoch,
    }
    for key, value in trusted.items():
        if key == "session_id" and (batch.get(key) is None or
                isinstance(batch.get(key), str) and not batch[key].strip()):
            batch[key] = value
            repairs.append({"path": key, "action": "filled_from_frozen_request"})
        if key in batch and batch[key] != value:
            raise ContractError(f"batch envelope mismatch: {key}")
        if key not in batch:
            batch[key] = value
            repairs.append({"path": key, "action": "filled_from_frozen_request"})
    if "semantic_conflicts" not in batch:
        batch["semantic_conflicts"] = []
        repairs.append({"path": "semantic_conflicts", "action": "default_empty_collection"})
    before = {key: copy.deepcopy(batch.get(key)) for key in ("answered_question_ids", "answered_ambiguity_ids")}
    normalize_batch_answer_ids(batch)
    for key, old_value in before.items():
        if batch.get(key) != old_value:
            repairs.append({"path": key, "action": "derived_from_observations"})
    return batch, repairs


def normalize_unbound_vehicle_classes(batch, previous_registry=(), *, execution_default_authorized=False):
    """Withdraw only an optional, unbound observed class claim.

    The original candidate remains in the case snapshot. Never derive a frame
    from prose or actor-level evidence, and never erase a previously admitted
    observed class. Conversion may default the admitted unknown only when its
    separate policy authorizes that execution value.
    """
    from _lib.contracts.actors import is_vehicle

    def bound(value):
        if not isinstance(value, dict):
            return False
        if value.get('frame_id') or value.get('time_s') is not None:
            return True
        evidence = value.get('evidence')
        return isinstance(evidence, list) and any(
            isinstance(item, dict) and item.get('status') == 'observed'
            and (item.get('frame_id') or item.get('time_s') is not None)
            for item in evidence)

    previous = {row.get('actor_id'): row for row in previous_registry if isinstance(row, dict)}
    repairs = []
    for index, actor in enumerate(batch.get('registry_after', [])):
        if not isinstance(actor, dict) or not is_vehicle(actor):
            continue
        claim = actor.get('vehicle_class')
        if not isinstance(claim, dict) or claim.get('status') != 'observed' or bound(claim):
            continue
        prior = previous.get(actor.get('actor_id'), {}).get('vehicle_class')
        if isinstance(prior, dict) and prior.get('status') == 'observed' and bound(prior):
            if claim.get('value') != prior.get('value'):
                continue  # A conflicting known class still fails the evidence gate.
            actor['vehicle_class'] = copy.deepcopy(prior)
            action = 'restore_frozen_vehicle_class_evidence'
        else:
            actor['vehicle_class'] = {
                'status': 'unknown', 'value': None,
                'reason': 'Observed vehicle class claim lacked field-bound video evidence; claim not admitted.',
            }
            action = 'unbound_vehicle_class_to_unknown'
        repairs.append({
            'action': action, 'actor_id': actor.get('actor_id'),
            'path': f'registry_after.{index}.vehicle_class',
            'source_claim': copy.deepcopy(claim),
            'admitted_value': copy.deepcopy(actor['vehicle_class']),
            'execution_default_authorized': bool(execution_default_authorized),
        })
    return batch, repairs


def withdraw_unverified_batch_last_seen(batch, frames, previous_registry=()):
    """Remove only a new optional last-seen claim outside the frozen Batch frames.

    Historical Registry evidence may be carried forward unchanged. It cannot
    establish a new last-seen assertion in a Batch that did not receive that
    frame. Keep the actor and its evidence, and audit the withdrawn assertion.
    Changes to an existing last-seen assertion still reach the evidence gate.
    """
    supplied = {row.get('frame_id') for row in frames if isinstance(row, dict)}
    previous = {row.get('actor_id'): row for row in previous_registry
                if isinstance(row, dict)}
    repairs = []
    for index, actor in enumerate(batch.get('registry_after', [])):
        if not isinstance(actor, dict) or 'last_seen_frame_id' not in actor:
            continue
        actor_id = actor.get('actor_id')
        if not isinstance(actor_id, str) or actor_id not in previous:
            continue
        prior = previous[actor_id]
        if 'last_seen_frame_id' in prior or 'last_seen_time_s' in prior:
            continue
        frame_id = actor['last_seen_frame_id']
        if not isinstance(frame_id, str) or frame_id in supplied:
            continue
        claim = {key: copy.deepcopy(actor[key]) for key in
                 ('last_seen_frame_id', 'last_seen_time_s') if key in actor}
        for key in claim:
            actor.pop(key)
        repairs.append({
            'action': 'withdraw_new_last_seen_outside_batch_frames',
            'actor_id': actor_id,
            'path': f'registry_after.{index}.last_seen_frame_id',
            'source_claim': claim,
            'admitted_value': None,
        })
    return batch, repairs


def validate_batch(batch, world, question_ids, ambiguity_ids=(), *,
                   allow_collection_item_conflicts=False, observation_contract=False):
    normalize_batch_answer_ids(batch)
    _schema("batch_v3.schema.json", batch)
    _validate_actor_lane_hypotheses(batch['registry_after'], world, 'registry_after')
    if batch["world_evidence_epoch"] != world["evidence_epoch"]:
        raise ContractError("batch world evidence epoch mismatch")
    if set(batch["answered_question_ids"]) - set(question_ids):
        raise ContractError("batch answered an unassigned question")
    if set(batch.get("answered_ambiguity_ids", [])) - set(ambiguity_ids):
        raise ContractError("batch answered an unassigned ambiguity")
    if set(question_ids) - set(batch["answered_question_ids"]):
        raise ContractError("batch did not answer every assigned question")
    if set(ambiguity_ids) - set(batch.get("answered_ambiguity_ids", [])):
        raise ContractError("batch did not answer every assigned ambiguity")
    conflict_ids = {item['conflict_id'] for item in batch['semantic_conflicts']}
    from _lib.world.single_world import layout_review, validate_layout, LAYOUT_REVISION
    review = layout_review(world)
    layout_rows = []
    for observation in batch['observations']:
        if 'layout_observation' in observation:
            if (not review or observation.get('question_id') != review['question_id']
                    or observation.get('layout_review_revision') != LAYOUT_REVISION
                    or not isinstance(observation.get('request_sha256'), str)):
                raise ContractError('Unassigned layout observation', rule_id='batch.layout.scope')
            validate_layout(observation['layout_observation'])
            layout_rows.append(observation)
        if set(observation.get('conflict_ids', [])) - conflict_ids:
            raise ContractError('observation references an unknown Batch conflict')
    if review and review['question_id'] in question_ids and len(layout_rows) != 1:
        raise ContractError('Assigned layout review requires one independent observation', rule_id='batch.layout.missing')
    validate_batch_conflict_targets(
        batch, world, allow_collection_item_conflicts=allow_collection_item_conflicts,
        observation_contract=observation_contract)
    return batch


def can_skip_batches(world, duration_s, global_frame_count, timeline_covered=True, *, observation_contract=False):
    """The deterministic N=0 gate; duration/frame count are only prerequisites."""
    validate_world(world, duration_s, observation_contract=observation_contract)
    if duration_s > 5.0 or global_frame_count > 12 or not timeline_covered:
        return False, ["short_video_prerequisite"]
    blockers = []
    for item in world["questions_to_verify"]:
        if item["resolution"] == "unresolved" and item.get("owner") != "behavior" and CORE_IMPACT_DOMAINS.intersection(item["impact_domains"]):
            blockers.append(item["question_id"])
    for item in world["ambiguities"]:
        if item["resolution"] == "unresolved" and CORE_IMPACT_DOMAINS.intersection(item["impact_domains"]):
            blockers.append(item["ambiguity_id"])
    road = world["world"].get("road", {})
    if not road.get("roads") or not road.get("lanes"):
        blockers.append("world.road.topology")
    required_paths = {"world.scene_type", "world.road.roads", "world.road.lanes"}
    if not required_paths <= set(world["field_evidence"]):
        blockers.append("world.field_evidence")
    return not blockers, sorted(set(blockers))


_WORLD_PATH_RE = re.compile(r"^world(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[[0-9]+\])+$")


def _set_world_path(document, path, value):
    """Set a validated World path, supporting both dotted keys and list indexes."""
    if not isinstance(path, str) or not _WORLD_PATH_RE.fullmatch(path):
        raise ContractError(f"invalid world conflict path: {path!r}")
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*|[0-9]+", path)
    if len(tokens) == 4 and tokens[0] == "world" and tokens[1] in {"actors", "actor_hypotheses"}:
        actor_id, field = tokens[2:]
        mutable = {"lane": "hypothesized_lane", "lane_id": "hypothesized_lane",
                   "hypothesized_lane": "hypothesized_lane", "is_static": "is_static",
                   "roadside_area_id": "roadside_area_id", "evidence": "evidence"}
        if field not in mutable:
            raise ContractError(f"actor conflict field is not mutable: {field}")
        actor = next((item for item in document["actor_hypotheses"] if item.get("actor_id") == actor_id), None)
        if actor is None:
            raise ContractError(f"actor conflict references unknown actor: {actor_id}")
        actor[mutable[field]] = copy.deepcopy(value)
        return
    cursor = document
    for token in tokens[:-1]:
        if isinstance(cursor, list):
            cursor = cursor[int(token)]
        elif isinstance(cursor, dict):
            cursor = cursor[token]
        else:
            raise ContractError(f"world conflict path crosses scalar: {path}")
    leaf = tokens[-1]
    if isinstance(cursor, list):
        cursor[int(leaf)] = copy.deepcopy(value)
    elif isinstance(cursor, dict):
        cursor[leaf] = copy.deepcopy(value)
    else:
        raise ContractError(f"world conflict path parent is scalar: {path}")


def world_conflict_value(document, path):
    """Read the same conceptual conflict paths accepted by reconciliation."""
    if not isinstance(path, str) or not _WORLD_PATH_RE.fullmatch(path):
        raise ContractError(f"invalid world conflict path: {path!r}")
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*|[0-9]+", path)
    if len(tokens) == 4 and tokens[0] == "world" and tokens[1] in {"actors", "actor_hypotheses"}:
        actor_id, field = tokens[2:]
        aliases={"lane":"hypothesized_lane","lane_id":"hypothesized_lane",
                 "hypothesized_lane":"hypothesized_lane","is_static":"is_static",
                 "roadside_area_id":"roadside_area_id","evidence":"evidence"}
        if field not in aliases:
            raise ContractError(f"actor conflict field is not readable: {field}")
        actor=next((item for item in document["actor_hypotheses"] if item.get("actor_id")==actor_id),None)
        if actor is None: raise ContractError(f"actor conflict references unknown actor: {actor_id}")
        return actor.get(aliases[field])
    cursor=document
    for token in tokens:
        cursor=cursor[int(token)] if isinstance(cursor,list) else cursor[token]
    return cursor


_COLLECTION_CONFLICT_SELECTORS = {
    "world.road.roadside_areas": "area_id",
}
_CONFLICT_PROVENANCE_FIELDS = frozenset({"basis"})


def normalize_batch_conflicts(batch, world, *, observation_contract=False):
    """Narrow one uniquely addressed sparse collection patch to a leaf patch.

    Batch output sometimes names a collection while proposing one member.  That
    is representation damage, not a new visual fact, but it is safe to repair
    only when the frozen World supplies a unique stable identity and exactly one
    existing leaf value changes.  The original candidate remains available in
    the candidate snapshot; the returned audit records everything removed from
    the wire representation.
    """
    if not isinstance(batch, dict):
        return batch, []
    repairs = []
    for index, conflict in enumerate(batch.get("semantic_conflicts", [])):
        if not isinstance(conflict, dict):
            continue
        path = conflict.get("world_path")
        selector = _COLLECTION_CONFLICT_SELECTORS.get(path)
        proposed = conflict.get("proposed_value")
        if selector is None or not isinstance(proposed, dict):
            continue
        try:
            collection = world_conflict_value(world, path)
        except (ContractError, KeyError, IndexError, TypeError):
            continue
        identity = proposed.get(selector)
        if not isinstance(collection, list) or not isinstance(identity, str) or not identity:
            continue
        matches = [(item_index, item) for item_index, item in enumerate(collection)
                   if isinstance(item, dict) and item.get(selector) == identity]
        if len(matches) != 1:
            continue
        item_index, current = matches[0]
        candidate_fields = set(proposed) - {selector} - _CONFLICT_PROVENANCE_FIELDS
        if not candidate_fields or any(field not in current for field in candidate_fields):
            continue
        changed = [field for field in sorted(candidate_fields)
                   if proposed[field] != current[field]]
        if len(changed) != 1:
            continue
        field = changed[0]
        canonical_path = f"{path}[{item_index}].{field}"
        candidate = copy.deepcopy(world)
        try:
            _set_world_path(candidate, canonical_path, proposed[field])
            validate_world(candidate, observation_contract=observation_contract)
        except (ContractError, KeyError, IndexError, ValueError, TypeError):
            continue
        original_value = copy.deepcopy(proposed)
        conflict["world_path"] = canonical_path
        conflict["proposed_value"] = copy.deepcopy(proposed[field])
        repairs.append({
            "conflict_id": conflict.get("conflict_id"),
            "action": "narrow_unique_collection_member_to_leaf",
            "source_world_path": path,
            "canonical_world_path": canonical_path,
            "selector": {selector: identity},
            "source_proposed_value": original_value,
            "preserved_provenance": {
                key: copy.deepcopy(proposed[key])
                for key in sorted(_CONFLICT_PROVENANCE_FIELDS) if key in proposed
            },
        })
    return batch, repairs


def validate_batch_conflict_targets(batch, world, *, allow_collection_item_conflicts=False,
                                    observation_contract=False):
    """Reject path/value mismatches before a new Batch becomes formal input."""
    missing = object()
    for index, conflict in enumerate(batch.get("semantic_conflicts", [])):
        path = conflict.get("world_path")
        proposed = conflict.get("proposed_value")
        if (not allow_collection_item_conflicts and isinstance(path, str)
                and re.match(r'^(?:world\.)?(?:questions_to_verify|ambiguities)\[', path)
                and '.identity_binding' in path):
            raise ConflictTargetError('Identity questions must be answered through observations and census for World adjudication',
                index=index, world_path=path, expected='identity observation/census', actual='World patch',
                required_change='Keep question_id/ambiguity_id and frame evidence in observations; retain unresolved census hypotheses for World closure. Do not patch identity_binding.')
        try:
            current = world_conflict_value(world, path)
        except (ContractError, KeyError, IndexError, TypeError):
            # Historical accepted conflicts may deliberately challenge an
            # immutable actor field and must remain an unresolved conflict at
            # reconciliation.  A missing final dictionary leaf is also a valid
            # add when the resulting World passes its authoritative gate.
            if allow_collection_item_conflicts:
                continue
            current = missing
        if isinstance(current, list) and isinstance(proposed, dict):
            if allow_collection_item_conflicts:
                continue
            selector = _COLLECTION_CONFLICT_SELECTORS.get(path)
            identity = proposed.get(selector) if selector else None
            suffix = (f"; locate the unique {selector}={identity!r} member and target its changed leaf"
                      if selector and identity else "; target one exact member leaf")
            raise ConflictTargetError(
                f"semantic_conflicts.{index}: collection target cannot be replaced by one object{suffix}",
                index=index, world_path=path, expected="array", actual="object",
                required_change="Change world_path and proposed_value together so the path names the exact leaf and the value has that leaf's shape.")
        candidate = copy.deepcopy(world)
        try:
            _set_world_path(candidate, path, proposed)
            validate_world(candidate, observation_contract=observation_contract)
        except (ContractError, KeyError, IndexError, ValueError, TypeError) as error:
            raise ConflictTargetError(
                f"semantic_conflicts.{index}: proposed value is invalid for {path}: {error}",
                index=index, world_path=path,
                expected="missing leaf or compatible value" if current is missing else type(current).__name__,
                actual=type(proposed).__name__,
                required_change="Keep the evidenced fact, but use an exact compatible World leaf path and value.") from error


def reconcile_world(world, batches, *, observation_contract=False, duration_s=None,
                    roadside_geometry=False, actor_fact_review=False, evidence_frames=(),
                    strict_question_contract=False):
    """Apply unanimous valid challenges; reserve review for genuine divergence."""
    validate_world(world, duration_s, observation_contract=observation_contract)
    conflicts = {}
    conflict_path_aliases = {}
    conflict_normalizations = []
    answered_questions, answered_ambiguities = {}, {}
    normalized_batches = []
    for source_batch in batches:
        batch = copy.deepcopy(source_batch)
        batch, repairs = normalize_batch_conflicts(
            batch, world, observation_contract=observation_contract)
        for repair in repairs:
            conflict_normalizations.append({"batch_id": batch.get("batch_id"), **repair})
            conflict_path_aliases.setdefault(repair["conflict_id"], set()).add(
                repair["source_world_path"])
        normalized_batches.append(batch)
    for batch in normalized_batches:
        known_questions = {q["question_id"] for q in world["questions_to_verify"]}
        known_ambiguities = {a["ambiguity_id"] for a in world["ambiguities"]}
        if set(batch["answered_question_ids"]) - known_questions:
            raise ContractError("batch answered an unknown World question")
        if set(batch.get("answered_ambiguity_ids", [])) - known_ambiguities:
            raise ContractError("batch answered an unknown World ambiguity")
        validate_batch(batch, world, batch["answered_question_ids"],
                       batch.get("answered_ambiguity_ids", []),
                       allow_collection_item_conflicts=True,
                       observation_contract=observation_contract)
        for observation in batch["observations"]:
            if observation.get("question_id") in batch["answered_question_ids"]:
                answered_questions.setdefault(observation["question_id"], []).append(observation)
            if observation.get("ambiguity_id") in batch.get("answered_ambiguity_ids", []):
                answered_ambiguities.setdefault(observation["ambiguity_id"], []).append(observation)
        for conflict in batch["semantic_conflicts"]:
            conflicts.setdefault(conflict["world_path"], []).append(conflict)
    unresolved, applied = [], []
    result = copy.deepcopy(world)
    for path, items in sorted(conflicts.items()):
        encoded = {json.dumps(item["proposed_value"], sort_keys=True, ensure_ascii=False) for item in items}
        if len(encoded) != 1:
            unresolved.extend(item["conflict_id"] for item in items)
            continue
        try:
            candidate = copy.deepcopy(result)
            _set_world_path(candidate, path, items[0]["proposed_value"])
            validate_world(candidate, observation_contract=observation_contract)
            result = candidate
        except (ContractError, KeyError, IndexError, ValueError, TypeError):
            unresolved.extend(item["conflict_id"] for item in items)
            continue
        applied.extend(item["conflict_id"] for item in items)
    resolved, pending = [], []
    if strict_question_contract:
        from _lib.world.actor_facts import question_contracts
        issue_contracts = {(row['collection'], row['issue_id']): row for row in
                           question_contracts(result, evidence_frames)}
    for collection, key, evidence_by_id in ((result["questions_to_verify"], "question_id", answered_questions),
                                             (result["ambiguities"], "ambiguity_id", answered_ambiguities)):
        for item in collection:
            if item.get("resolution") == "unresolved" and evidence_by_id.get(item[key]):
                observations = evidence_by_id[item[key]]
                if strict_question_contract and item.get('impact_domains') == ['actor_lane']:
                    from _lib.world.actor_facts import point_confirmation_covers
                    contract = issue_contracts[("questions_to_verify" if key == "question_id"
                                                else "ambiguities", item[key])]
                    if not point_confirmation_covers(contract, item, observations, evidence_frames):
                        pending.append(item[key])
                        continue
                if strict_question_contract and len(item.get('impact_domains', [])) != 1:
                    pending.append(item[key])
                    continue
                core = bool(CORE_IMPACT_DOMAINS.intersection(item.get('impact_domains', [])))
                def patch_answers(observation):
                    ids = set(observation.get('conflict_ids', []))
                    if not ids or not ids <= set(applied):
                        return False
                    # A changed sign description does not establish lane count,
                    # even if the producer labels that patch with lane_count.
                    # Only the World owner's assigned fact paths define scope.
                    paths = set(item.get('world_paths', []))
                    changed = {path for path, proposals in conflicts.items()
                               if any(proposal['conflict_id'] in ids for proposal in proposals)}
                    for conflict_id in ids:
                        changed.update(conflict_path_aliases.get(conflict_id, ()))
                    return not core or bool(paths) and paths <= changed
                # Provenance and adjudication are different facts. Seeing a
                # parked car does not answer the road's traffic direction.
                # Legacy core observations without an explicit confirmation
                # remain available to World closure, never silently approved.
                confirms = any(
                    observation.get('status') != 'unknown'
                    and (observation.get('answer_resolution', 'legacy') in
                         ({'confirmed'} if core else {'confirmed', 'legacy'})
                         or (observation.get('answer_resolution') == 'needs_world_patch'
                             and patch_answers(observation)))
                    for observation in observations)
                pending_patch = any(
                    observation.get('answer_resolution') == 'needs_world_patch'
                    and not patch_answers(observation)
                    for observation in observations)
                # An occluded earlier frame does not refute a later explicit
                # confirmation. Retain unknown evidence in the audit below;
                # contradictory proposals still block through unresolved.
                partial = any(observation.get('answer_resolution') == 'partial' for observation in observations)
                if partial and actor_fact_review:
                    from _lib.world.actor_facts import confirmation_covers
                    partial = not confirmation_covers(result, item, observations, evidence_frames)
                if item.get('owner') == 'behavior' or not confirms or pending_patch or partial or unresolved:
                    pending.append(item[key])
                    continue
                # A Batch observation is a conclusion envelope; World expects
                # the provenance records inside observation.evidence.  Never
                # embed the whole observation and force a later model rewrite.
                evidence=[]
                for observation in evidence_by_id[item[key]]:
                    records=observation.get("evidence") if isinstance(observation.get("evidence"),list) else [observation]
                    for record in records:
                        if record not in evidence: evidence.append(copy.deepcopy(record))
                # Resolution is a World fact too.  A Batch can confirm that a
                # value is unobservable; that must not remove an unresolved
                # binding which makes the null value legal.  Validate the
                # proposed transition before publishing it, and retain the
                # question when the rest of the World still depends on it.
                candidate = copy.deepcopy(result)
                candidate_item = next(entry for entry in candidate[
                    "questions_to_verify" if key == "question_id" else "ambiguities"
                ] if entry[key] == item[key])
                candidate_item.update(resolution="resolved", evidence=copy.deepcopy(evidence))
                try:
                    validate_world(candidate, duration_s,
                                   observation_contract=observation_contract)
                except ContractError:
                    pending.append(item[key])
                    continue
                item["resolution"] = "resolved"
                item["evidence"] = evidence
                resolved.append(item[key])
    from _lib.world.single_world import (layout_review, layout_differences,
                                         layout_roadside_differences)
    layout_reviews = []
    review = layout_review(result)
    if review:
        rows = [observation for batch in normalized_batches for observation in batch['observations']
                if observation.get('question_id') == review['question_id'] and 'layout_observation' in observation]
        for observation in rows:
            differences = layout_differences(result, observation['layout_observation'])
            if roadside_geometry:
                differences = sorted(set(differences + layout_roadside_differences(
                    result, observation['layout_observation'])))
            layout_reviews.append(dict(question_id=review['question_id'], rule_ids=differences,
                observation=copy.deepcopy(observation['layout_observation']),
                request_sha256=observation['request_sha256']))
        if rows and not any(row['rule_ids'] for row in layout_reviews):
            item = next(q for q in result['questions_to_verify'] if q['question_id'] == review['question_id'])
            if item['resolution'] == 'unresolved':
                item.update(resolution='resolved', evidence=copy.deepcopy(rows[0]['evidence']))
                resolved.append(review['question_id'])
                pending = [qid for qid in pending if qid != review['question_id']]
    if applied or resolved:
        result["evidence_epoch"] += 1
    # Synchronise junction membership with road roles resolved by batch evidence.
    _sync_junction_membership(result)
    return {"world": result, "applied_conflict_ids": applied, "unresolved_conflict_ids": unresolved,
            **({'layout_reviews': layout_reviews} if layout_reviews else {}),
            "resolved_verification_ids": sorted(resolved),
            "pending_verification_ids": sorted(pending),
            "conflict_normalizations": conflict_normalizations,
            "status": "needs_review" if unresolved else "reconciled"}


def _sync_junction_membership(world_document):
    """Ensure roads with a resolved road_role appear in junction membership.

    When batch evidence resolves a road's direction (e.g. road_role changes from
    None to 'outgoing'), the junction's incoming_roads/outgoing_roads lists must
    include that road.  reconcile_world only patches individual fields via
    _set_world_path and cannot infer this structural derivation.
    """
    road_section = world_document.get('road', {})
    roads = road_section.get('roads', [])
    junctions = world_document.get('junctions', [])
    if not roads or not junctions:
        return
    roads_by_id = {r.get('road_id'): r for r in roads if isinstance(r, dict)}
    for junction in junctions:
        if not isinstance(junction, dict):
            continue
        for field, role in (('incoming_roads', 'incoming'), ('outgoing_roads', 'outgoing')):
            existing_ids = {ref.get('road_id') for ref in junction.get(field, [])
                           if isinstance(ref, dict)}
            for road_id, road in roads_by_id.items():
                road_role = road.get('road_role')
                if road_role not in (role, 'bidirectional'):
                    continue
                if road_id in existing_ids:
                    continue
                # Road has the right role but is missing from junction membership
                lane_ids = road.get('lane_ids', [])
                junction.setdefault(field, []).append({
                    'road_id': road_id,
                    'lane_ids': list(lane_ids),
                })


def _value_and_origin(value):
    if isinstance(value, dict):
        return value.get("value"), value.get("status") or value.get("value_origin")
    return value, "observed" if value is not None else None


def _record_default(report, field, value, policy_id, reason="unavailable_after_targeted_batch"):
    report.append({"field": field, "observed_value": None, "effective_value": value,
                   "value_origin": "policy_default", "policy_id": policy_id,
                   "reason": reason, "confidence": 0.0})


def _scalar(value):
    return value.get("value") if isinstance(value, dict) else value


def _canonical_actor(actor, *, ego=False):
    """Project evidence-rich Behavior identity to converter-supported values."""
    result=copy.deepcopy(actor)
    trajectory=result.get("trajectory")
    if not isinstance(trajectory,list) or not trajectory:
        raise ContractError(f"behavior actor {result.get('actor_id','<unknown>')} trajectory must be a non-empty array")
    if any(not isinstance(point,dict) for point in trajectory):
        raise ContractError(f"behavior actor {result.get('actor_id','<unknown>')} trajectory points must be objects")
    raw_type=str(result.get("actor_type") or "")
    result["source_actor_type"]=raw_type
    if ego or raw_type in {'car','van','truck','suv','sedan'} or (raw_type.startswith("vehicle") and raw_type != "vehicle_group"):
        result["actor_type"]="vehicle"
        raw_class=str(_scalar(result.get("vehicle_class")) or raw_type.removeprefix("vehicle_") or "").lower()
        aliases={"hatchback":"compact_car","minivan_or_suv":"minivan","car":"car","unknown":"sedan","":"sedan"}
        vehicle_class=aliases.get(raw_class,raw_class)
        if vehicle_class not in {"car","compact_car","sedan","coupe","mpv","minivan","van","suv","box_truck","truck"}:
            vehicle_class="sedan" if ego else "car"
        raw_color=str(_scalar(result.get("color")) or "").lower()
        color_aliases={"dark_gray_or_black":"dark_gray","unknown":"silver","":"silver"}
        color=color_aliases.get(raw_color,raw_color)
        if color not in {"red","white","silver","black","dark","dark_gray","gray","grey","maroon","blue","green","brown","orange","yellow","red_with_black_roof"}:
            color="silver"
        if isinstance(result.get("vehicle_class"),dict):
            source=result["vehicle_class"]
            result["source_vehicle_class"]={"value":source.get("value"),"origin":source.get("status"),
                                             "confidence":source.get("confidence"),"reason":source.get("reason")}
        if isinstance(result.get("color"),dict):
            source=result["color"]
            result["source_color"]={"value":source.get("value"),"origin":source.get("status"),
                                     "confidence":source.get("confidence"),"reason":source.get("reason")}
        # Keep an explicit unknown observation through analysis; the converter
        # may apply the authorized class default only to its execution copy.
        source_class=result.get("vehicle_class")
        result["vehicle_class"]=(copy.deepcopy(source_class)
                                 if isinstance(source_class,dict) and source_class.get("status")=="unknown"
                                 else vehicle_class)
        result["color"]=color
    elif raw_type.startswith("pedestrian") and raw_type != "pedestrian_group":
        result["actor_type"]="pedestrian"
    for point in trajectory:
        speed=point.get("speed_mps")
        if isinstance(speed,dict):
            point["source_speed_mps"]={"value":speed.get("value"),"origin":speed.get("status"),
                                       "confidence":speed.get("confidence"),"reason":speed.get("reason")}
            point["speed_mps"]=speed.get("value")
    return result




def build_analysis(world, behavior, parameters=None, *, observation_issues=None, preview_review=None,
                   execution_scope_excluded_actor_ids=(), observation_contract=False, media_probe=None,
                   road_minimum_decision=None):
    """Build analysis from frozen domains without asking a model to re-interpret facts."""
    duration = (media_probe or {}).get('duration_s')
    validate_world(world, duration, observation_contract=observation_contract)
    # Contradictory declared geometry is not a missing execution value.
    from _lib.world.topology import validate_arm_bearings
    if world['world'].get('scene_type') == 'intersection':
        try:
            validate_arm_bearings(world['world']['road'].get('roads', []), path='/world/road/roads')
        except ValueError as cause:
            error = ContractError(str(cause), rule_id=getattr(cause, 'rule', 'world.topology'))
            error.diagnostics = dict(rule_id=error.rule_id, path=getattr(cause, 'path', '/world/road'),
                                     related_paths=getattr(cause, 'related_paths', []),
                                     owner='world', retry_kind='world_adjudication')
            raise error from cause
    if observation_contract:
        # Always validate declarations, even without pending questions. Missing
        # executable arms are allowed; bad references and known conflicts are not.
        from _lib.world.admission import issues, blocking_issues, AdmissionError
        blocking = blocking_issues(issues(world, duration))
        if blocking:
            raise AdmissionError(blocking)
    # Ensure junction membership reflects resolved road roles before topology checks.
    _sync_junction_membership(world)
    from _lib.world.readiness import readiness_issues, world_uncertainties, world_execution_issues
    uncertainties = world_uncertainties(world)
    world_issues = world_execution_issues(uncertainties, world, behavior, parameters,
                                          road_minimum_decision=road_minimum_decision)
    if road_minimum_decision and road_minimum_decision['decisions']:
        authorized = set((parameters or {}).get('allowed_degradations', []))
        if 'road_topology_policy_default' not in authorized:
            raise ContractError('Noncore road minimum has no frozen topology authorization',
                                rule_id='degradation.unauthorized')
        covered_ids = {item['issue_id'] for item in road_minimum_decision['decisions']}
        world_issues = [issue for issue in world_issues
                        if not (issue.get('dependency') == 'controlled_approximation'
                                and issue['message'].removeprefix('unresolved ') in covered_ids)]
    if observation_contract and world['world'].get('scene_type') == 'intersection':
        # Completeness depends on declarations, not on whether a model happened
        # to create an unresolved question about them. Observation admission above
        # has already checked the declared facts before this execution-only test.
        from _lib.world.topology import validate_topology
        try:
            validate_topology(world['world'])
        except ValueError as error:
            world_issues.append(dict(
                rule_id='world.execution_topology_incomplete', kind='execution_gap',
                owner='world', field_path='/world/junctions', world_paths=[],
                message='Executable intersection topology requires Converter projection: ' + str(error),
                retry_kind='deterministic_projection'))
    if world_issues and not observation_contract:
        # Direct Builder callers must not use a deferred question to bypass
        # structural/topology checks when taking the observation-only path.
        hard_issues = readiness_issues(world, execution=False, observation_contract=False)
        # Topology and consistency issues are recoverable by converter fallbacks.
        # Only block on structural prerequisites (missing roads/lanes entirely).
        structural = [i for i in hard_issues
                      if not i.startswith('world road topology:')
                      and 'consistency' not in i.lower()]
        degraded_topology = [i for i in hard_issues if i not in structural]
        if structural:
            raise ContractError('World observation invalid: ' + '; '.join(structural))
        if degraded_topology:
            # Readiness reports topology/consistency findings as plain strings;
            # every execution-issue consumer (routing gaps, execution-plan
            # classification, delivery diagnostics) requires the structured
            # issue contract. Keep them world-owned and policy-recoverable
            # instead of triggering a World patch.
            world_issues = list(world_issues) + [
                dict(rule_id='world.topology', kind='execution_gap', owner='world',
                     message=text, world_paths=[], retry_kind='new_evidence')
                for text in degraded_topology]
    observation_issues = copy.deepcopy(observation_issues or [])
    observation_issues.extend(issue for issue in world_issues if issue not in observation_issues)
    from _lib.behavior.compile import (lane_position_report, lateral_position_issues,
                                       validate_lane_references, validate_behavior_evidence)
    validate_behavior(behavior, world['evidence_epoch'], execution=False)
    delivery_behavior, evidence_reason_bindings = validate_behavior_evidence(behavior)
    validate_lane_references(behavior, world)
    lane_check = lane_position_report(behavior, world)
    observation_issues.extend(i for i in lane_check['issues'] + lateral_position_issues(behavior) if i not in observation_issues)
    excluded = set(execution_scope_excluded_actor_ids)
    if excluded:
        backgrounds = {a['actor_id'] for a in behavior['actors'] if a.get('simulation_role') == 'background'}
        if not excluded <= backgrounds:
            raise ContractError('Execution scope cannot exclude a participant')
        observation_issues = [i for i in observation_issues if not (
            i.get('actor_id') in excluded and i.get('rule_id') in {
                'execution.lateral_position_unknown', 'execution.lane_unknown'})]
    parameters = normalize_parameters({"workflow_version": "v3", **(parameters or {})})
    actors_by_id = {a['actor_id']: a for a in behavior['actors']}
    for aid,lid in parameters.get('confirmed_lane_assignments', {}).items():
        actor = actors_by_id.get(aid)
        if actor is None or not actor['trajectory'] or any(p.get('lane_id') != lid for p in actor['trajectory']):
            observation_issues.append({'rule_id':'execution.confirmed_lane_conflict','actor_id':aid,
                'message':f'User-confirmed lane {lid} conflicts with actor trajectory; reconcile before execution'})
    preview = None
    if parameters['fallback_policy']['straight_preview']['enabled']:
        from _lib.behavior.compile import build_straight_preview, preview_limitation_issues
        from _lib.behavior.lane_change import derive as derive_lane_change, LaneChangeBlocked
        try:
            lane_change = derive_lane_change(world, behavior, (media_probe or {}).get('duration_s'))
        except LaneChangeBlocked:
            lane_change = None
        observation_issues.extend(preview_limitation_issues(world, behavior, preview_review))
        background_ids = {a['actor_id'] for a in behavior['actors'] if a.get('simulation_role') == 'background'}
        def covered(issue):
            return (issue.get('rule_id')=='execution.lateral_position_unknown' or
                (issue.get('rule_id')=='execution.lane_unknown' and issue.get('actor_id') in background_ids) or (
                issue.get('rule_id')=='execution.lane_unknown' and lane_change is not None
                and issue.get('actor_id') == lane_change.actor_id) or (
                issue.get('rule_id')=='execution.contract' and
                issue.get('message','').startswith(('participant actors missing initial_state primitive:',
                                                  'Behavior cannot execute:'))))
        uncovered = [issue for issue in observation_issues if not covered(issue)]
        if uncovered:
            # Report the minimal unsatisfied prerequisites. The omitted issues
            # remain in observations/limitations and will be synthesized only
            # after every independent preview prerequisite is satisfied.
            observation_issues = uncovered
        else:
            try:
                if 'straight_preview_policy_default' not in parameters['allowed_degradations']:
                    raise ContractError('straight preview policy is not authorized')
                preview = build_straight_preview(world,behavior,parameters['fallback_policy'],preview_review)
                observation_issues = []
            except ContractError as error:
                observation_issues = [{'rule_id':'execution.preview_blocked','actor_id':None,
                                       'message':str(error)}]
    converter_handoff = observation_contract and preview is None
    observation_only = bool(observation_issues)
    validate_behavior(behavior, world["evidence_epoch"], execution=not observation_only and preview is None)
    parameters = normalize_parameters({"workflow_version": "v3", **(parameters or {})})
    policy = parameters["fallback_policy"]
    analysis = copy.deepcopy(world["world"])
    from _lib.delivery.media_time import bind_metadata
    bind_metadata(analysis.setdefault('metadata', {}), media_probe)
    # World accepts observational notes, but analysis has a closed root schema.
    # Keep the annotation in metadata without changing the frozen source or
    # relaxing the execution contract for other unexpected fields.
    if "notes" in analysis:
        metadata = analysis.setdefault("metadata", {})
        if "world_notes" in metadata and metadata["world_notes"] != analysis["notes"]:
            raise ContractError("World notes conflict with metadata.world_notes")
        metadata["world_notes"] = analysis.pop("notes")
    scene_type = analysis.pop("scene_type")
    analysis["scene_type"] = scene_type
    # Preserve World ownership and append only Behavior-owned domains.
    behavior_actors = [copy.deepcopy(item) if observation_only or converter_handoff else
                      _canonical_actor(item,ego=item.get("actor_id")=="ego")
                      for item in delivery_behavior["actors"]]
    if preview:
        for actor,source in zip(behavior_actors,delivery_behavior['actors']):
            actor['trajectory']=copy.deepcopy(source['trajectory'])
    analysis["ego"] = next(item for item in behavior_actors if item["actor_id"] == "ego")
    analysis["actors"] = [item for item in behavior_actors if item["actor_id"] != "ego"]
    analysis["events"] = copy.deepcopy(delivery_behavior["events"])
    analysis["limitations"] = copy.deepcopy(delivery_behavior["semantic_limitations"])
    for actor in behavior_actors:
        if actor.get("actor_type") in {"parked_vehicles","vehicle_group","pedestrian_group"}:
            analysis["limitations"].append({"limitation_id":"collective_"+actor["actor_id"],
                "description":"Collective observation retained in analysis/report; individual count and executable tracks not inferred.",
                "impact_domains":["report"],"actor_id":actor["actor_id"]})
    primitives = copy.deepcopy(delivery_behavior["executor_primitives"])
    from _lib.contracts.entry import execution_primitive
    for primitive in primitives:
        primitive["type"] = execution_primitive(primitive["type"])
    if scene_type == 'intersection' and not (analysis.get('road', {}).get('roads') and analysis['road'].get('lanes')):
        raise ContractError('intersection requires observed expanded topology; template compilation is not authorized')
    if scene_type == "intersection":
        # Optional absent facilities stay absent; observed facilities need geometry.
        junctions=analysis.setdefault("junctions",[])
        if junctions:
            junction=junctions[0]
            signals=junction.get("traffic_signals")
            if not isinstance(signals,list):
                source_state=junction.get("signal_state_for_ego")
                if signals or source_state:
                    raise ContractError('traffic_signals require explicit position and state_timeline; no default signal placement')
                junction["traffic_signals"]=[]
            crosswalks=junction.get("crosswalks")
            if not (isinstance(crosswalks,list) and all(isinstance(item,dict) for item in crosswalks)):
                markings=[item for item in analysis["road"].get("lane_markings",[])
                          if isinstance(item,dict) and "crosswalk" in str(item.get("type") or item.get("marking_type") or "").lower()]
                if crosswalks or markings:
                    raise ContractError('crosswalks require explicit geometry; no default crosswalk placement')
                junction["crosswalks"]=[]
            metadata=analysis.setdefault("metadata",{})
            ego_actor=next((item for item in behavior_actors if item.get("actor_id")=="ego"),{})
            times=[point.get("time_s") for point in ego_actor.get("trajectory",[]) if isinstance(point.get("time_s"),(int,float))]
            if times and 'duration_s' not in metadata: metadata["duration_s"]=max(times)
    analysis["simulation_scenarios"] = [preview[0]] if preview else ([] if observation_only else _build_simulation(primitives, analysis))
    from _lib.world.topology import normalize_topology
    normalize_topology(analysis)
    from _lib.behavior.motion import bind_entry_geometry
    try:
        entry_repairs = [] if observation_only or converter_handoff else bind_entry_geometry(analysis, primitives)
    except ValueError as error:
        raise ContractError(str(error)) from error
    defaults, required = [], set()
    if road_minimum_decision and road_minimum_decision['decisions']:
        required.add('road_topology_policy_default')
        for decision in road_minimum_decision['decisions']:
            for road in decision['roads']:
                count = road['proved']['minimum_lane_count']
                defaults.append({
                    'field': f"world.road.roads[{road['road_index']}].lane_knowledge.count",
                    'road_id': road['road_id'], 'observed_value': {'kind': 'minimum', 'count': count},
                    'effective_value': count, 'value_origin': 'policy_default',
                    'policy_id': 'road_topology_policy_default',
                    'reason': 'noncore_road_minimum.v1: accurate count is unavailable; '
                              'retain only the observed minimum in Target',
                    'confidence': 0.0, 'evidence_frame_ids': road['proved']['evidence_frame_ids'],
                    'issue_id': decision['issue_id'],
                    'still_unknown': copy.deepcopy(road['still_unknown']),
                    'core_impact': road['impact'],
                })
    if preview:
        defaults.extend(preview[1]); required.add('straight_preview_policy_default')
    measurements = []
    road = analysis.setdefault("road", {})
    roads, lanes = road.get("roads"), road.get("lanes")
    placeholder_reasons = []
    bounded_uncertainty_reasons = []
    for limitation in behavior["semantic_limitations"]:
        if limitation.get("execution_effect") == "blocks_execution" and preview is None:
            placeholder_reasons.append(limitation["limitation_id"])
        elif limitation.get("execution_effect") == "bounded_uncertainty":
            bounded_uncertainty_reasons.append(limitation["limitation_id"])
    if behavior["actors"] and not primitives and preview is None:
        placeholder_reasons.append("actor_execution_primitives_unknown")
    if not isinstance(roads, list) or not roads:
        placeholder_reasons.append("road_set_unknown")
        roads = road["roads"] = [{"road_id": "road_001", "lane_ids": ["lane_001"], "road_geometry": {}}]
    if not isinstance(lanes, list) or not lanes:
        placeholder_reasons.append("lane_topology_unknown")
        lanes = road["lanes"] = [{"lane_id": "lane_001", "road_id": roads[0]["road_id"], "lane_type": "driving"}]
        roads[0]["lane_ids"] = ["lane_001"]
        required.add("single_lane_topology_placeholder")
    lane_by_id = {lane.get("lane_id"): lane for lane in lanes}
    if any(not road_item.get("lane_ids") for road_item in roads) and not (
            observation_contract and observation_only):
        raise ContractError("empty road lane_ids cannot be published")
    if any(lane_id not in lane_by_id for item in roads for lane_id in item["lane_ids"]):
        raise ContractError("road references unknown lane")
    if converter_handoff:
        dimension_fields = [
            (f"road.roads[{i}].road_geometry.visible_length_m", item.get("road_geometry", {}).get("visible_length_m"))
            for i, item in enumerate(roads)]
        dimension_fields += [(f"road.lanes[{i}].width_m", lane.get("width_m")) for i, lane in enumerate(lanes)]
        dimension_fields += [(f"road.roadside_areas[{i}].width_m", area.get("width_m"))
                             for i, area in enumerate(road.get("roadside_areas", []))]
        for path, raw in dimension_fields:
            value, origin = _value_and_origin(raw)
            if value is None or value == "unknown" or origin == "unknown":
                observation_issues.append({"rule_id": "execution.metric_unknown", "actor_id": None,
                    "path": path, "message": path + ": Converter must supply an execution value"})
            elif type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ContractError("invalid positive dimension: " + path, rule_id="metric.invalid")
            elif isinstance(raw, dict):
                measurements.append({"field": path, "measurement": copy.deepcopy(raw)})
    if parameters["road_source"] == "preview_json" and not converter_handoff:
        for index, item in enumerate(roads):
            geometry = item.setdefault("road_geometry", {})
            value, origin = _value_and_origin(geometry.get("visible_length_m"))
            if value is None:
                value = policy["highway_length_m" if scene_type == "highway" else "local_road_length_m"]
                geometry["visible_length_m"] = value
                _record_default(defaults, f"world.road.roads[{index}].road_geometry.visible_length_m", value, "road_length_policy_default")
                required.add("road_length_policy_default")
            elif not isinstance(value, (int, float)) or value <= 0:
                raise ContractError("invalid road length")
            else:
                if isinstance(geometry.get("visible_length_m"),dict):
                    measurements.append({"field":f"road.roads[{index}].road_geometry.visible_length_m","measurement":copy.deepcopy(geometry["visible_length_m"])})
                geometry["visible_length_m"]=value
        for index, lane in enumerate(lanes):
            if lane.get("lane_type", "driving") != "driving":
                continue
            value, origin = _value_and_origin(lane.get("width_m"))
            if value is None:
                value = policy["driving_lane_width_m"]
                lane["width_m"] = value
                _record_default(defaults, f"world.road.lanes[{index}].width_m", value, "driving_lane_width_policy_default")
                required.add("driving_lane_width_policy_default")
            elif not isinstance(value, (int, float)) or value <= 0:
                raise ContractError("invalid lane width")
            else:
                if isinstance(lane.get("width_m"),dict):
                    measurements.append({"field":f"road.lanes[{index}].width_m","measurement":copy.deepcopy(lane["width_m"])})
                lane["width_m"]=value
    from _lib.contracts.parking import (parking_area_type, roadside_execution_type,
                                        sidewalk_area_type, validate_sidewalk_area)
    roadside_representations = []
    road_ids = {item['road_id'] for item in road.get('roads', [])}
    for index, area in enumerate(road.get("roadside_areas", [])):
        try:
            validate_sidewalk_area(area, road_ids, road)
        except ValueError as exc:
            raise ContractError(str(exc), rule_id='roadside.sidewalk_evidence') from exc
        target_area_type = roadside_execution_type(area)
        if target_area_type != "paved_shoulder":
            continue
        sidewalk = sidewalk_area_type(area) == 'sidewalk'
        degradation_id = ('sidewalk_as_paved_shoulder' if sidewalk
                          else 'parking_as_paved_shoulder')
        required.add(degradation_id)
        observed_area_type = area.get("area_type") if area.get("area_type") is not None else area.get("type")
        source_area_type = parking_area_type(area) or observed_area_type
        representation = {"source":source_area_type, "target":"paved_shoulder",
                          "degradation_id":degradation_id}
        if representation not in roadside_representations:
            roadside_representations.append(representation)
        # Converter handoff preserves the accepted observation verbatim.  The
        # private execution copy performs the recorded compatibility mapping.
        if converter_handoff or sidewalk:
            continue
        area["source_area_type"] = source_area_type
        area["area_type"] = ("curbside_parking" if parking_area_type(area) == "curbside_parking"
                             else "paved_shoulder")
        if area.get("side") not in {"left", "right"}:
            raise ContractError("roadside band side missing")
        value, origin = _value_and_origin(area.get("width_m"))
        if value is None:
            value = policy["parking_width_m"]
            area["width_m"] = value
            _record_default(defaults, f"world.road.roadside_areas[{index}].width_m", value, "parking_width_policy_default")
            required.add("parking_width_policy_default")
        elif not isinstance(value, (int, float)) or value <= 0:
            raise ContractError("invalid parking width")
        else:
            if isinstance(area.get("width_m"),dict):
                measurements.append({"field":f"road.roadside_areas[{index}].width_m","measurement":copy.deepcopy(area["width_m"])})
            area["width_m"]=value
    observed_weather = analysis.setdefault("environment", {}).get("weather")
    analysis["environment"]["target_weather_compatibility"] = policy["weather_compatibility_value"]
    if observed_weather != policy["weather_compatibility_value"]:
        required.add("weather_not_applied_downstream")
    allowed = set(parameters["allowed_degradations"])
    normal_required = required - {"single_lane_topology_placeholder"}
    unauthorized = normal_required - allowed
    if unauthorized:
        raise ContractError(f"unauthorized degradations: {sorted(unauthorized)}")
    status = "placeholder_generated" if placeholder_reasons else ("done_degraded" if required or bounded_uncertainty_reasons else "done")
    analysis["quality_report"] = {
        "builder": "v3-deterministic", "world_evidence_epoch": world["evidence_epoch"],
        "allowed_degradations": sorted(allowed), "required_degradations": sorted(required),
        "applied_degradations": sorted(required), "applied_defaults": defaults,
        "metric_measurements": measurements,
        'entry_geometry_bindings': entry_repairs,
        "deterministic_topology_compilation": False,
        "deterministic_compilation": False,
        "representations": roadside_representations,
        "placeholder_reasons": placeholder_reasons,
        "bounded_uncertainty_reasons": bounded_uncertainty_reasons,
        "inherited_evidence_reasons": evidence_reason_bindings,
    }
    from _lib.world.readiness import execution_dependencies
    analysis['quality_report']['execution_dependencies'] = execution_dependencies(
        world, behavior, parameters, road_minimum_decision=road_minimum_decision)
    if road_minimum_decision is not None:
        analysis['quality_report']['road_minimum_decision'] = copy.deepcopy(road_minimum_decision)
    analysis['quality_report']['lane_position_check'] = lane_check
    from _lib.world.readiness import ego_carriageway_execution_scope
    execution_scope = ego_carriageway_execution_scope(world, behavior)
    if execution_scope:
        analysis['quality_report']['execution_scope'] = execution_scope
    from _lib.behavior.compile import observation_scope_exclusions
    scope_exclusions = observation_scope_exclusions(world, behavior)
    if scope_exclusions:
        analysis['quality_report']['behavior_scope_exclusions'] = scope_exclusions
    if preview:
        analysis['quality_report'].update(execution_ready=True, execution_issues=[],
            straight_preview={'review':copy.deepcopy(preview_review),'policy':copy.deepcopy(policy),
                              'source_world':copy.deepcopy(world),'envelopes':preview[2]})
    if uncertainties['questions_to_verify'] or uncertainties['ambiguities']:
        analysis['quality_report']['world_uncertainties'] = uncertainties
    if not placeholder_reasons and not observation_only:
        from _lib.world.topology import normalize_topology, validate_actor_routes
        try:
            repairs = normalize_topology(analysis)
            validate_actor_routes(analysis, behavior_actors)
        except ValueError as error:
            raise ContractError(str(error)) from error
        analysis['quality_report']['topology_index_repairs'] = repairs
        analysis['quality_report']['topology_validation'] = 'passed'
    if converter_handoff:
        # Observation gaps remain explicit, but they no longer prevent the
        # deterministic Converter from creating its private executable copy.
        # Converter defaults never flow back into World/Behavior/analysis.
        analysis['quality_report'].update(
            # The observation artifact itself remains incomplete.  Delivery
            # is allowed to continue because the Converter constructs and
            # validates a separate executable projection.
            execution_ready=False,
            execution_issues=copy.deepcopy(observation_issues),
            execution_projection='analysis_values_then_hardcoded_fallbacks',
        )
        analysis['limitations'].extend({'limitation_id':f'execution_gap_{i}',
            'description':issue['message'], 'impact_domains':['executor_primitive'],
            'execution_effect':'bounded_uncertainty'} for i, issue in enumerate(observation_issues))
        if observation_issues:
            status = 'done_degraded'
    elif observation_only:
        analysis['quality_report'].update(
            execution_ready=False,
            execution_issues=copy.deepcopy(observation_issues),
        )
        status = 'needs_review'
    return analysis, status


def _build_simulation(primitives, analysis):
    conditions, triggers, participants = {}, [], []
    actor_by_id = {item["actor_id"]: item for item in analysis.get("actors", [])}
    actor_by_id["ego"] = analysis.get("ego", {"actor_id": "ego"})
    for primitive in primitives:
        actor_id = primitive["actor_id"]
        participants.append(actor_id)
        if primitive["type"] == "initial_state":
            required = ("lane_id", "position_ego_m", "speed_kmh")
            if any(key not in primitive for key in required):
                raise ContractError(f"initial_state primitive incomplete: {primitive['primitive_id']}")
            conditions[actor_id] = {key: copy.deepcopy(primitive[key]) for key in required}
        else:
            triggers.append(copy.deepcopy(primitive))
    participants = list(dict.fromkeys(participants))
    if set(participants) - set(actor_by_id):
        raise ContractError("simulation references unknown actor")
    return [{"scenario_id": "scenario_001", "description": "Original-video baseline",
             "involved_actor_ids": participants, "initial_conditions": conditions,
             "trigger_events": triggers}]


def gate_degradations(quality_report):
    allowed = set(quality_report.get("allowed_degradations", []))
    required = set(quality_report.get("required_degradations", []))
    applied = set(quality_report.get("applied_degradations", []))
    if applied != required:
        raise ContractError("applied_degradations must equal required_degradations")
    if (required - {"single_lane_topology_placeholder"}) - allowed:
        raise ContractError("required degradation was not authorized")
    default_ids = {item.get("policy_id") for item in quality_report.get("applied_defaults", [])}
    if {item for item in required if item.endswith("_policy_default")} - default_ids:
        raise ContractError("policy default provenance missing")
    ruling = quality_report.get('road_minimum_decision')
    if ruling is not None:
        from _lib.contracts.conversion_policy import digest
        body = dict(ruling)
        signature = body.pop('sha256', None)
        scope = quality_report.get('execution_actor_scope') or {}
        if (ruling.get('revision') != 'noncore_road_minimum.v1'
                or signature != digest(body)
                or ruling.get('execution_scope_sha256') != scope.get('sha256')
                or ruling.get('policy_sha256') != ruling.get('input_hashes', {}).get(
                    'state/execution_policy.json')
                or ruling.get('fact_version_id') != (
                    (scope.get('effective_facts') or {}).get('version_id'))):
            raise ContractError('Noncore road minimum ruling is stale or unbound',
                                rule_id='stale_version')
        expected = {(row['issue_id'], road['road_id'],
                     f"world.road.roads[{road['road_index']}].lane_knowledge.count",
                     road['proved']['minimum_lane_count'])
                    for row in ruling['decisions'] for road in row['roads']}
        actual = {(row.get('issue_id'), row.get('road_id'), row.get('field'),
                   row.get('effective_value'))
                  for row in quality_report.get('applied_defaults', [])
                  if row.get('reason', '').startswith('noncore_road_minimum.v1:')}
        classifications = {row['issue_id']: row['decision'] for row in
                           quality_report.get('execution_dependencies', {}).get('decisions', [])}
        if (actual != expected
                or any(classifications.get(row['issue_id']) != 'controlled_approximation'
                       for row in ruling['decisions'])
                or bool(expected) != ('road_topology_policy_default' in required)):
            raise ContractError('Noncore road minimum provenance does not match the ruling',
                                rule_id='degradation.provenance')
    return True


def completion_envelope(status, artifacts=None, diagnostics=None, render_verified=False):
    if status not in {"done", "done_degraded", "placeholder_generated", "needs_review", "failed"}:
        raise ContractError("invalid terminal status")
    generated = status if status in {"done", "done_degraded", "placeholder_generated"} else "not_generated"
    result = {
        "status": status,
        "generation_status": generated,
        "verification_level": "render_verified" if render_verified else ("source_contract_validated" if status in {"done","done_degraded"} else "not_verified"),
        "source_contract_validation": "passed" if status in {"done","done_degraded"} else "not_passed",
        "render_verification": "passed" if render_verified else "not_executed",
        "artifacts": artifacts or {},
        "diagnostics": diagnostics or {},
    }
    if status == "placeholder_generated" and result["artifacts"]:
        raise ContractError("placeholder must not contain published artifacts")
    return result
