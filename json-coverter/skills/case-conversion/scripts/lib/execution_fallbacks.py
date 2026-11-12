"""Deterministic analysis-to-execution fallback projection.

The source analysis remains immutable.  This module creates the private copy
consumed by the MELA scene converters: usable analysis values win; missing,
``null``, ``unknown`` or target-incompatible values receive documented local
defaults.  No video, model session or Case state is consulted.
"""
from __future__ import annotations

import copy
import math
from collections import Counter
from typing import Any

from _lib.contracts.actors import is_vehicle, vehicle_class_hint
from _lib.contracts.parking import (canonical_parking_area_type, parking_area_type,
                                    roadside_execution_type, sidewalk_area_type,
                                    validate_sidewalk_area)
from _lib.world.topology import arm_bearing, validate_arm_bearings
from _lib.contracts.conversion_policy import authorize, validate as validate_policy


from _lib.contracts.conversion_policy import DEFAULTS

LANE_TYPES = {"driving", "shoulder", "bicycle", "parking", "bus", "auxiliary", "emergency"}
LANE_DIRECTIONS = {"forward", "backward", "bidirectional"}
VEHICLE_CLASSES = {"sedan", "suv", "truck", "bus", "motorcycle", "bicycle", "van", "tractor", "pickup"}
VEHICLE_COLORS = {"white", "black", "grey", "silver", "red", "blue", "yellow", "green", "brown", "orange"}
WEATHER = {"clear", "clouds", "overcast", "rain", "strong_rain", "snow", "strong_snowfall", "dust", "strong_dust", "fog", "strong_fog", "sleet", "thunderstorm"}
DAYTIME = {"morning", "afternoon", "evening", "night"}
SEASONS = {"spring", "summer", "autumn", "winter"}
TOPOLOGIES = {"hilly", "mountainous", "coastal", "desert", "flat"}


def _value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return None if value.get("status") == "unknown" else _value(value.get("value"))
    return value


def _finite(value: Any) -> float | None:
    value = _value(value)
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def _text(value: Any) -> str | None:
    value = _value(value)
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return None if not value or value == "unknown" else value


class Projection:
    def __init__(self, source: dict, scene_type: str, policy=None, semantic_gap=None):
        if policy is not None:
            validate_policy(policy, scene_type=scene_type)
        self.policy = policy
        self.defaults = copy.deepcopy(policy['defaults'] if policy is not None else DEFAULTS)
        self.source = source
        self.data = copy.deepcopy(source)
        self.scene_type = scene_type
        self.semantic_gap = semantic_gap
        self.fallbacks: list[dict] = []
        self.excluded_actor_ids: list[str] = []

    def set(self, owner: dict, key: str, value: Any, path: str, reason: str) -> Any:
        if owner.get(key) != value:
            original = copy.deepcopy(owner.get(key))
            owner[key] = copy.deepcopy(value)
            self.fallbacks.append({"field": path, "value": copy.deepcopy(value),
                                   "observed_value": original, "reason": reason})
        return owner[key]

    def scalar(self, owner: dict, key: str, default: Any, path: str) -> Any:
        value = _value(owner.get(key))
        if value is None or value == "unknown":
            return self.set(owner, key, default, path, "missing_or_unknown")
        if owner.get(key) != value:
            owner[key] = value
        return value

    def enum(self, owner: dict, key: str, supported: set[str], default: str, path: str,
             aliases: dict[str, str] | None = None) -> str:
        value = _text(owner.get(key))
        if aliases:
            value = aliases.get(value, value)
        if value not in supported:
            return self.set(owner, key, default, path, "missing_unknown_or_unsupported_enum")
        owner[key] = value
        return value


class ProjectionLaneTransitionError(ValueError):
    """A private execution default changed the source's lane-change facts."""

    code = "conversion_projection_blocked"

    def __init__(self, actor_id: str, added: Counter, removed: Counter):
        super().__init__(f"Execution projection changed known-lane transitions for {actor_id}")
        self.diagnostics = {
            "rule_id": "conversion.projection_lane_transition",
            "owner": "converter",
            "actor_id": actor_id,
            "added": list(added.elements()),
            "removed": list(removed.elements()),
            "retry_kind": "deterministic_contract",
        }

def validate_conversion_input(data: dict, scene_type: str) -> None:
    """Validate only the structure needed to start deterministic projection."""
    if not isinstance(data, dict):
        raise ValueError("analysis must be a JSON object")
    if scene_type not in {"highway", "intersection"}:
        raise ValueError(f"unsupported scene_type: {scene_type!r}")
    if data.get("scene_type") != scene_type:
        raise ValueError("scene_type mismatch")
    for key in ("road", "ego"):
        if not isinstance(data.get(key), dict):
            raise ValueError(f"analysis.{key} must be an object")
    if not isinstance(data.get("actors", []), list):
        raise ValueError("analysis.actors must be an array")
    for road in data.get("road", {}).get("roads", []):
        if not isinstance(road, dict):
            continue
        geometry = road.get("road_geometry") or {}
        shape = _text(geometry.get("shape_type", road.get("shape_type")))
        curvature = _finite(geometry.get("curvature"))
        if (shape not in {None, "straight"} or (curvature is not None and abs(curvature) > 1e-12)
                or _finite(geometry.get("road_radius", geometry.get("radius_m"))) is not None):
            raise ValueError("curved-to-straight projection is not a missing-value fallback")
    # Multi-state signals are handled downstream by _junctions() which
    # degrades them to single-state green.  No pre-check block needed.


def _duration(projection: Projection) -> float:
    metadata = projection.data.setdefault("metadata", {})
    duration = _finite(metadata.get("duration_s")) or _finite(metadata.get("video_duration_s"))
    if duration is None or duration <= 0:
        samples = [
            _finite(point.get("time_s", point.get("timestamp_s")))
            for actor in [projection.data.get("ego", {}), *projection.data.get("actors", [])]
            for point in actor.get("trajectory", []) if isinstance(point, dict)
        ]
        duration = max((value for value in samples if value is not None), default=projection.defaults["scenario_duration_s"])
    if duration <= 0:
        duration = projection.defaults["scenario_duration_s"]
    projection.scalar(metadata, "duration_s", duration, "metadata.duration_s")
    projection.scalar(metadata, "video_duration_s", duration, "metadata.video_duration_s")
    return float(duration)


def _canonical_road(index: int) -> dict:
    roles = ("incoming", "outgoing", "outgoing", "incoming")
    headings = (0.0, math.pi / 2, 0.0, math.pi / 2)
    return {
        "road_id": f"fallback_road_{index + 1:03d}",
        "road_role": roles[index],
        "heading_rad": headings[index],
        "lane_ids": [],
        "road_geometry": {},
    }


def _road_slot(road: dict) -> str | None:
    angle = arm_bearing(road)
    if angle is None:
        return None
    index = round(angle / (math.pi / 2))
    if abs(angle - index * math.pi / 2) > 0.1:
        return None
    return ("N", "W", "S", "E")[index % 4]


def _positive_dimension(value, path):
    raw = _value(value)
    if raw is None or raw == "unknown":
        return None
    number = _finite(raw)
    if number is None or number <= 0:
        raise ValueError("invalid positive dimension: " + path)
    return number


def _estimated_dimension_range(value, path):
    """Return a validated uncertainty range for an estimated dimension."""
    if not isinstance(value, dict) or value.get('status') != 'estimated':
        return None
    raw = value.get('range_m')
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError("invalid estimated dimension range: " + path)
    low, high = (_finite(item) for item in raw)
    if low is None or high is None or low <= 0 or low > high:
        raise ValueError("invalid estimated dimension range: " + path)
    estimate = _positive_dimension(value, path)
    if estimate is None or not low <= estimate <= high:
        raise ValueError("estimated dimension outside range: " + path)
    return low, high


def _ego_corridor(data):
    """Find a unique opposite exit from declared geometry, not array order."""
    roads = data.get('road', {}).get('roads', [])
    first = (data.get('ego', {}).get('trajectory') or [{}])[0]
    approach = next((r for r in roads if first.get('road_id') == r.get('road_id')
                     or first.get('lane_id') in r.get('lane_ids', [])), None)
    if approach is None or approach.get('road_role') != 'incoming':
        return approach, None
    slot = _road_slot(approach)
    opposite = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}.get(slot)
    exits = [r for r in roads if r.get('road_role') == 'outgoing'
             and opposite is not None and _road_slot(r) == opposite]
    return approach, exits[0] if len(exits) == 1 else None


def _lane_completion(projection, roads, road, road_index, lane_ids, used_lane_ids):
    knowledge = road.get('lane_knowledge') or {}
    kind, count = knowledge.get('kind'), knowledge.get('count')
    anchors = knowledge.get('anchors') or []
    required = len(lane_ids)
    reason = 'empty_road_requires_lane'
    if kind in {'exact', 'minimum'}:
        if type(count) is not int or count < 1:
            raise ValueError('lane_completion.invalid_count: ' + road['road_id'])
        if kind == 'exact' and len(lane_ids) > count:
            raise ValueError('lane_completion.exact_count_conflict: ' + road['road_id'])
        required = max(required, count)
        reason = 'declared_lane_count_completion'
    elif (projection.scene_type == 'intersection' and not lane_ids
          and len(projection.source.get('road', {}).get('roads', [])) == 4):
        incoming, outgoing = _ego_corridor(projection.data)
        if outgoing is road and incoming is not None:
            source_count = (incoming.get('lane_knowledge') or {}).get('count')
            required = max(len(incoming.get('lane_ids') or []),
                           source_count if type(source_count) is int else 0)
            reason = 'ego_corridor_same_order_lane_completion'
    required = max(1, required)
    slots = {}
    for anchor in anchors:
        lid, ordinal = anchor.get('lane_id'), anchor.get('ordinal_from_right')
        if (lid not in lane_ids or type(ordinal) is not int or ordinal < 0
                or ordinal in slots or lid in slots.values()):
            raise ValueError('lane_completion.anchor_conflict: ' + road['road_id'])
        if kind == 'exact' and ordinal >= required:
            raise ValueError('lane_completion.exact_anchor_conflict: ' + road['road_id'])
        required = max(required, ordinal + 1)
        slots[ordinal] = lid
    for lid in lane_ids:
        if lid not in slots.values():
            ordinal = next(i for i in range(required) if i not in slots)
            slots[ordinal] = lid
    for ordinal in range(required):
        if ordinal in slots:
            continue
        lid = f'fallback_lane_{road_index + 1:03d}_{ordinal + 1:02d}'
        while lid in used_lane_ids:
            lid += '_x'
        slots[ordinal] = lid
        used_lane_ids.add(lid)
    completed = [slots[i] for i in range(required)]
    projection.set(road, 'lane_ids', completed, f'road.roads[{road_index}].lane_ids', reason)
    return road['lane_ids']


def _roads(projection: Projection) -> tuple[dict[str, dict], str]:
    data = projection.data
    road_section = data.setdefault("road", {})
    roads = road_section.get("roads")
    if not isinstance(roads, list):
        roads = projection.set(road_section, "roads", [], "road.roads", "missing_or_invalid_collection")
    lanes = road_section.get("lanes")
    if not isinstance(lanes, list):
        lanes = projection.set(road_section, "lanes", [], "road.lanes", "missing_or_invalid_collection")
    if not isinstance(road_section.get("lane_markings"), list):
        projection.set(road_section, "lane_markings", [], "road.lane_markings", "missing_or_invalid_collection")

    if not roads:
        count = 4 if projection.scene_type == "intersection" else 1
        for index in range(count):
            road = _canonical_road(index)
            roads.append(road)
            projection.fallbacks.append({"field": f"road.roads[{index}]", "value": road["road_id"], "reason": "canonical_road"})
    elif projection.scene_type == "intersection" and len(roads) < 3:
        occupied = {_road_slot(road) for road in roads}
        for index in range(4):
            candidate = _canonical_road(index)
            if _road_slot(candidate) in occupied:
                continue
            roads.append(candidate)
            occupied.add(_road_slot(candidate))
            projection.fallbacks.append({"field": f"road.roads[{len(roads)-1}]", "value": candidate["road_id"], "reason": "canonical_four_way_completion"})

    lane_by_id = {lane.get("lane_id"): lane for lane in lanes if isinstance(lane, dict) and isinstance(lane.get("lane_id"), str)}
    used_road_ids: set[str] = set()
    used_lane_ids = set(lane_by_id)
    for road_index, road in enumerate(roads):
        if not isinstance(road, dict):
            roads[road_index] = road = _canonical_road(road_index % 4)
            projection.fallbacks.append({"field": f"road.roads[{road_index}]", "value": road["road_id"], "reason": "invalid_road_replaced"})
        road_id = road.get("road_id")
        if not isinstance(road_id, str) or not road_id or road_id in used_road_ids:
            road_id = f"fallback_road_{road_index + 1:03d}"
            while road_id in used_road_ids:
                road_id += "_x"
            projection.set(road, "road_id", road_id, f"road.roads[{road_index}].road_id", "missing_or_duplicate")
        used_road_ids.add(road_id)

        if projection.scene_type == "intersection":
            template = _canonical_road(road_index % 4)
            raw_role = road.get("road_role")
            valid_role = (_text(raw_role) in {"incoming", "outgoing", "bidirectional"}
                          or isinstance(raw_role, list)
                          and set(raw_role) == {"incoming", "outgoing"})
            if not valid_role:
                projection.set(road, "road_role", template["road_role"], f"road.roads[{road_index}].road_role", "missing_unknown_or_unsupported")
            if _finite(road.get("heading_rad")) is None:
                projection.set(road, "heading_rad", template["heading_rad"], f"road.roads[{road_index}].heading_rad", "missing_or_unknown")

        geometry = road.get("road_geometry")
        if not isinstance(geometry, dict):
            geometry = projection.set(road, "road_geometry", {}, f"road.roads[{road_index}].road_geometry", "missing_or_invalid_object")
        length_default = projection.defaults["intersection_road_length_m" if projection.scene_type == "intersection" else "highway_road_length_m"]
        length = _positive_dimension(geometry.get("visible_length_m"), f"road.roads[{road_index}].road_geometry.visible_length_m")
        if length is None or length <= 0:
            projection.set(geometry, "visible_length_m", length_default, f"road.roads[{road_index}].road_geometry.visible_length_m", "missing_or_unknown")
        else:
            geometry["visible_length_m"] = length

        lane_ids = road.get("lane_ids")
        if not isinstance(lane_ids, list):
            lane_ids = []
            projection.set(road, "lane_ids", lane_ids, f"road.roads[{road_index}].lane_ids", "missing_or_invalid_collection")
        lane_ids[:] = [lane_id for lane_id in lane_ids if isinstance(lane_id, str) and lane_id]
        lane_ids = _lane_completion(projection, roads, road, road_index, lane_ids, used_lane_ids)
        for lane_index, lane_id in enumerate(lane_ids):
            lane = lane_by_id.get(lane_id)
            if lane is None:
                lane = {"lane_id": lane_id, "road_id": road_id}
                lanes.append(lane)
                lane_by_id[lane_id] = lane
                projection.fallbacks.append({"field": f"road.lanes[{len(lanes)-1}]", "value": lane_id, "reason": "referenced_lane_missing"})
            owner = _text(lane.get("road_id"))
            if owner is not None and owner != road_id:
                raise ValueError(f"Lane {lane_id} belongs to {owner}, not {road_id}")
            if owner is None:
                projection.set(lane, "road_id", road_id, f"road.lanes[{lane_id}].road_id", "missing_or_unknown")
            projection.enum(lane, "lane_type", LANE_TYPES, "driving", f"road.lanes[{lane_id}].lane_type")
            width = _positive_dimension(lane.get("width_m"), f"road.lanes[{lane_id}].width_m")
            if width is None or width <= 0:
                projection.set(lane, "width_m", projection.defaults["driving_lane_width_m"], f"road.lanes[{lane_id}].width_m", "missing_or_unknown")
            else:
                lane["width_m"] = width
            direction = _text(lane.get("travel_direction"))
            aliases = {"same_direction": "forward", "same_as_ego": "forward", "opposite_direction": "backward", "opposite_to_ego": "backward", "oncoming": "backward"}
            direction = aliases.get(direction, direction)
            if direction not in LANE_DIRECTIONS:
                direction = "backward" if projection.scene_type == "intersection" and road.get("road_role") == "outgoing" and abs((_finite(road.get("heading_rad")) or 0) - math.pi / 2) < 0.1 else "forward"
                projection.set(lane, "travel_direction", direction, f"road.lanes[{lane_id}].travel_direction", "missing_unknown_or_unsupported_enum")
            else:
                lane["travel_direction"] = direction
            if not isinstance(lane.get("maneuver_permissions"), list):
                projection.set(lane, "maneuver_permissions", ["straight"], f"road.lanes[{lane_id}].maneuver_permissions", "missing_or_invalid_collection")

    areas = road_section.get("roadside_areas")
    if not isinstance(areas, list):
        areas = []
        projection.set(road_section, "roadside_areas", areas, "road.roadside_areas", "missing_or_invalid_collection")
    for index, area in enumerate(areas):
        if not isinstance(area, dict):
            continue
        validate_sidewalk_area(area, used_road_ids, road_section)
        category = parking_area_type(area)
        if category is None:
            category = canonical_parking_area_type(area.get("category"))
        if category == "curbside_parking":
            projection.set(area, "area_type", "curbside_parking", f"road.roadside_areas[{index}].area_type", "mela_parking_compatibility")
            if _text(area.get("side")) not in {"left", "right"}:
                projection.set(area, "side", "right", f"road.roadside_areas[{index}].side", "missing_or_unknown")
            width = _positive_dimension(area.get("width_m"), f"road.roadside_areas[{index}].width_m")
            if width is None or width <= 0:
                projection.set(area, "width_m", projection.defaults["parking_width_m"], f"road.roadside_areas[{index}].width_m", "missing_or_unknown")
            else:
                area["width_m"] = width
            if area.get("road_id") not in used_road_ids:
                projection.set(area, "road_id", roads[0]["road_id"], f"road.roadside_areas[{index}].road_id", "missing_or_unknown")
        elif roadside_execution_type(area) == "paved_shoulder":
            if sidewalk_area_type(area) == "sidewalk":
                quality = projection.source.get("quality_report") or {}
                required = set(quality.get("required_degradations") or [])
                allowed = set(quality.get("allowed_degradations") or [])
                if "sidewalk_as_paved_shoulder" not in required & allowed:
                    raise ValueError("Sidewalk-to-shoulder mapping is not authorized")
            projection.set(area, "source_area_type", area.get("area_type", area.get("type")),
                           f"road.roadside_areas[{index}].source_area_type", "mela_roadside_provenance")
            projection.set(area, "area_type", "paved_shoulder",
                           f"road.roadside_areas[{index}].area_type", "mela_paved_shoulder_compatibility")
            width = _positive_dimension(area.get("width_m"), f"road.roadside_areas[{index}].width_m")
            if width is None or width <= 0:
                projection.set(area, "width_m", projection.defaults["parking_width_m"],
                               f"road.roadside_areas[{index}].width_m", "missing_or_unknown")
            if area.get("road_id") not in used_road_ids:
                projection.set(area, "road_id", roads[0]["road_id"],
                               f"road.roadside_areas[{index}].road_id", "missing_or_unknown")

    return lane_by_id, roads[0]["lane_ids"][0]


def _topology_normalize(projection: Projection) -> None:
    """Normalize arm geometry without changing source lane identities."""
    if projection.scene_type != "intersection":
        return
    roads = projection.data.get("road", {}).get("roads", [])
    validate_arm_bearings(roads, path='/road/roads')
    # Snap non-cardinal heading_rad to nearest {0, π/2, π, 3π/2}
    for road_index, road in enumerate(roads):
        heading = _finite(road.get("heading_rad"))
        if heading is None:
            continue
        cardinals = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2]
        best = min(cardinals, key=lambda c: abs(math.atan2(math.sin(heading - c), math.cos(heading - c))))
        diff = abs(math.atan2(math.sin(heading - best), math.cos(heading - best)))
        if diff > 1e-6:
            projection.set(road, "heading_rad", best, f"road.roads[{road_index}].heading_rad",
                           "topology_arm_snap")
    # Snapping may create a collision too. Never resolve it by deleting a
    # road: actors, roadside areas, facilities and connections retain its ID.
    validate_arm_bearings(roads, path='/road/roads')
    # road.lanes is an entity registry, not a spatial ordering. Source IDs
    # remain stable; converters derive local index/name from roads[*].lane_ids.
    # Execution lane completion, when authorized, already remaps all references
    # and records provenance before this boundary.


def _junctions(projection: Projection) -> None:
    if projection.scene_type != "intersection":
        projection.data["junctions"] = []
        return
    roads = projection.data["road"]["roads"]
    junctions = projection.data.get("junctions")
    if not isinstance(junctions, list):
        junctions = []
        projection.set(projection.data, "junctions", junctions, "junctions", "missing_or_invalid_collection")
    if not junctions:
        junction = {"junction_id": "fallback_junction_001", "lane_connections": [], "traffic_signals": [], "crosswalks": []}
        junctions.append(junction)
        projection.fallbacks.append({"field": "junctions[0]", "value": junction["junction_id"], "reason": "canonical_intersection"})
    if len(junctions) > 1:
        removed = [row.get("junction_id") for row in junctions[1:] if isinstance(row, dict)]
        del junctions[1:]
        projection.fallbacks.append({"field": "junctions", "value": [junctions[0].get("junction_id")], "reason": f"single_mela_junction; omitted={removed}"})
    junction = junctions[0]
    if not isinstance(junction, dict):
        junctions[0] = junction = {"junction_id": "fallback_junction_001"}
    if not isinstance(junction.get("junction_id"), str):
        projection.set(junction, "junction_id", "fallback_junction_001", "junctions[0].junction_id", "missing_or_unknown")
    junction_kind = "t_junction" if len(roads) == 3 else "intersection"
    projection.enum(junction, "junction_type", {"intersection", "t_junction", "roundabout"}, junction_kind, "junctions[0].junction_type")

    for field, role in (("incoming_roads", "incoming"), ("outgoing_roads", "outgoing")):
        refs = junction.get(field)
        valid_ids = {road["road_id"] for road in roads}
        if not isinstance(refs, list):
            refs = []
        normalized = []
        seen = set()
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("road_id") not in valid_ids or ref.get("road_id") in seen:
                continue
            road = next(row for row in roads if row["road_id"] == ref["road_id"])
            member_lanes = ref.get("lane_ids")
            if not (isinstance(member_lanes, list) and member_lanes
                    and not (set(member_lanes) - set(road["lane_ids"]))):
                member_lanes = list(road["lane_ids"])
            normalized.append({"road_id": road["road_id"], "lane_ids": list(member_lanes)})
            seen.add(road["road_id"])
        for road in roads:
            road_roles = road.get("road_role")
            if (isinstance(road_roles, str) and road_roles in {role, "bidirectional"}
                    or isinstance(road_roles, list) and role in road_roles) and road["road_id"] not in seen:
                normalized.append({"road_id": road["road_id"], "lane_ids": list(road["lane_ids"])})
        if not normalized:
            candidates = roads[:1] if role == "incoming" else roads[-1:]
            normalized = [{"road_id": road["road_id"], "lane_ids": list(road["lane_ids"])} for road in candidates]
        if refs != normalized:
            projection.set(junction, field, normalized, f"junctions[0].{field}", "missing_or_incomplete_membership")

    if not isinstance(junction.get("lane_connections"), list):
        projection.set(junction, "lane_connections", [], "junctions[0].lane_connections", "missing_or_invalid_collection")
    if not isinstance(junction.get("traffic_signals"), list):
        projection.set(junction, "traffic_signals", [], "junctions[0].traffic_signals", "missing_or_invalid_collection")
    if not isinstance(junction.get("crosswalks"), list):
        projection.set(junction, "crosswalks", [], "junctions[0].crosswalks", "missing_or_invalid_collection")

    # traffic_signals is the structural source of truth.  An optional legacy
    # boolean may confirm it, but must never contradict the object list.
    declared_signalized = junction.get("signalized")
    derived_signalized = bool(junction["traffic_signals"])
    if declared_signalized is not None:
        if type(declared_signalized) is not bool:
            raise ValueError("junction.signalized must be boolean when present")
        if declared_signalized != derived_signalized:
            raise ValueError("junction.signalized conflicts with traffic_signals")

    if not junction["lane_connections"]:
        ego_approach, _ = _ego_corridor(projection.data)
        incoming = next((ref for ref in junction["incoming_roads"]
                         if ego_approach is not None and ref["road_id"] == ego_approach["road_id"]),
                        junction["incoming_roads"][0])
        incoming_road = next(road for road in roads if road["road_id"] == incoming["road_id"])
        opposite = {"N": "S", "S": "N", "E": "W", "W": "E"}.get(_road_slot(incoming_road))
        outgoing = next((ref for ref in junction["outgoing_roads"]
                         if _road_slot(next(road for road in roads if road["road_id"] == ref["road_id"])) == opposite),
                        junction["outgoing_roads"][0])
        connections = [{
            "incoming_road_id": incoming["road_id"],
            "incoming_lane_id": source_lane,
            "outgoing_road_id": outgoing["road_id"],
            "outgoing_lane_id": target_lane,
            "maneuver": "straight",
        } for source_lane, target_lane in zip(incoming["lane_ids"], outgoing["lane_ids"])]
        projection.set(junction, "lane_connections", connections,
                       "junctions[0].lane_connections", "missing_straight_execution_connection")

    road_ids = {road["road_id"] for road in roads}
    ego_road = junction["incoming_roads"][0]["road_id"]
    for index, signal in enumerate(junction["traffic_signals"]):
        if not isinstance(signal, dict):
            continue
        if signal.get("road_id") not in road_ids:
            projection.set(signal, "road_id", ego_road, f"junctions[0].traffic_signals[{index}].road_id", "missing_or_unknown")
        timeline = signal.get("state_timeline")
        states = {_text(row.get("state")) for row in timeline if isinstance(row, dict)} if isinstance(timeline, list) else set()
        states.discard(None)
        if len(states) != 1 or not states <= {"red", "yellow", "green"}:
            projection.set(signal, "state_timeline", [{"start_s": 0.0, "end_s": _duration(projection), "state": "green"}], f"junctions[0].traffic_signals[{index}].state_timeline", "missing_unknown_or_time_varying")
            projection.fallbacks[-1]["observed_value"] = copy.deepcopy(timeline)
        position = signal.get("position_ego_m")
        if not isinstance(position, dict) or _finite(position.get("x")) is None or _finite(position.get("y")) is None:
            projection.set(signal, "position_ego_m", projection.defaults["signal_position_m"], f"junctions[0].traffic_signals[{index}].position_ego_m", "missing_or_unknown")

    for index, crosswalk in enumerate(junction["crosswalks"]):
        if not isinstance(crosswalk, dict):
            continue
        if crosswalk.get("related_road_id") not in road_ids:
            projection.set(crosswalk, "related_road_id", ego_road, f"junctions[0].crosswalks[{index}].related_road_id", "missing_or_unknown")
        position = crosswalk.get("position_ego_m")
        if not isinstance(position, dict) or _finite(position.get("x")) is None or _finite(position.get("y")) is None:
            projection.set(crosswalk, "position_ego_m", projection.defaults["crosswalk_position_m"], f"junctions[0].crosswalks[{index}].position_ego_m", "missing_or_unknown")


def execution_actor_scope(data: dict) -> dict:
    """Retain individual vehicles unless a narrow v4 observation-only rule applies.

    Explicit reviewed previews retain their bound scope; ordinary scenario
    participant lists are not permission to discard observed vehicles.
    """
    quality = data.get("quality_report") or {}
    reviewed = quality.get("straight_preview") or quality.get("intersection_preview")
    primary = (data.get("simulation_scenarios") or [{}])[0]
    reviewed_ids = set(primary.get("involved_actor_ids") or []) if reviewed else None
    reviewed_exclusions = {row['actor_id'] for row in
                           (quality.get("intersection_preview") or {}).get("background_exclusions", [])}
    from _lib.contracts.semantic_motion import observation_only_background_ids
    observation_only = observation_only_background_ids(data)
    result = ["ego"]
    excluded = []
    for actor in data.get("actors", []):
        if not isinstance(actor, dict) or not isinstance(actor.get("actor_id"), str):
            continue
        if not is_vehicle(actor):
            excluded.append(actor["actor_id"])
            continue
        if actor["actor_id"] in observation_only:
            excluded.append(actor["actor_id"])
            continue
        user_scenery = actor.get("provenance", {}).get("source") == "user_requested_scenery"
        if user_scenery:
            continue
        if reviewed_ids is not None and (actor["actor_id"] in reviewed_exclusions or
                actor["actor_id"] not in reviewed_ids and not actor.get("is_static")):
            excluded.append(actor["actor_id"])
            continue
        result.append(actor["actor_id"])
    return {"included_actor_ids": list(dict.fromkeys(result)), "excluded_actor_ids": sorted(set(excluded))}


def _actor_ids(projection: Projection) -> list[str]:
    scope = execution_actor_scope(projection.data)
    projection.excluded_actor_ids.extend(scope['excluded_actor_ids'])
    return scope['included_actor_ids']


def _has_lane_change(data: dict, actor_id: str) -> bool:
    """Return whether the source explicitly assigns a lane change to an actor."""
    for event in data.get("events", []):
        involved = event.get("involved_actor_ids") if isinstance(event, dict) else None
        if not isinstance(involved, list) or actor_id not in involved:
            continue
        if "lane_change" in str(event.get("event_type") or "").lower():
            return True
    primitives = list(data.get("executor_primitives") or [])
    for scenario in data.get("simulation_scenarios") or []:
        if isinstance(scenario, dict) and isinstance(scenario.get("trigger_events"), list):
            primitives.extend(scenario["trigger_events"])
    for primitive in primitives:
        if not isinstance(primitive, dict) or primitive.get("actor_id") != actor_id:
            continue
        if "lane_change" in str(primitive.get("type") or "").lower():
            return True
    return False


def _highway_continuity_lane(projection: Projection, actor_id: str, points: list,
                             lane_ids: set[str], is_ego: bool) -> str | None:
    """Select one evidence-backed lane eligible for execution-only continuity.

    Unknown samples are never facts that the vehicle left its last known lane.
    The policy is deliberately unavailable at junctions, for Ego, after an
    explicit lane-change declaration, or when the source names multiple lanes.
    """
    if projection.scene_type != "highway" or is_ego or _has_lane_change(projection.source, actor_id):
        return None
    known = [_text(point.get("lane_id")) for point in points if isinstance(point, dict)]
    known = [lane for lane in known if lane in lane_ids]
    return known[0] if known and len(set(known)) == 1 else None


def _known_lane_transitions(actor: dict) -> Counter:
    """Count only time-ordered transitions between source-known lane samples."""
    known = []
    for point in actor.get("trajectory", []):
        if not isinstance(point, dict):
            continue
        lane = _text(point.get("lane_id"))
        time_s = _finite(point.get("time_s", point.get("timestamp_s")))
        if lane is not None and time_s is not None:
            known.append((time_s, lane))
    if any(right[0] <= left[0] for left, right in zip(known, known[1:])):
        return Counter()
    return Counter((left[1], right[1]) for left, right in zip(known, known[1:])
                   if left[1] != right[1])


def _verify_projection_lane_transitions(projection: Projection) -> None:
    """An execution default may place an actor, but may not invent its motion."""
    if projection.scene_type != "highway":
        return
    source_actors = {actor.get("actor_id"): actor for actor in
                     [projection.source.get("ego", {}), *projection.source.get("actors", [])]
                     if isinstance(actor, dict)}
    for actor in [projection.data["ego"], *projection.data.get("actors", [])]:
        actor_id = actor.get("actor_id")
        source = source_actors.get(actor_id)
        if source is None:
            continue
        before = _known_lane_transitions(source)
        after = _known_lane_transitions(actor)
        if before != after:
            raise ProjectionLaneTransitionError(actor_id, after - before, before - after)


def _normalize_actor(projection: Projection, actor: dict, lane_ids: set[str], default_lane: str,
                     duration: float, index: int, is_ego: bool,
                     source_condition: dict | None = None) -> dict:
    source_condition = copy.deepcopy(source_condition) if isinstance(source_condition, dict) else {}
    actor_id = "ego" if is_ego else actor["actor_id"]
    speed_kmh = projection.defaults["intersection_speed_kmh" if projection.scene_type == "intersection" else "highway_speed_kmh"]
    speed_mps = speed_kmh / 3.6
    actor["actor_id"] = actor_id
    class_hint = vehicle_class_hint(actor)
    actor["actor_type"] = "vehicle"
    if _text(actor.get("vehicle_class")) is None and class_hint is not None:
        actor["vehicle_class"] = class_hint
    projection.enum(actor, "vehicle_class", VEHICLE_CLASSES,
                    projection.defaults["ego_vehicle_class" if is_ego else "actor_vehicle_class"],
                    f"{'ego' if is_ego else 'actors['+actor_id+']'}.vehicle_class",
                    {"car": "sedan", "compact_car": "sedan", "hatchback": "sedan", "coupe": "sedan", "mpv": "van", "minivan": "van", "minivan_or_suv": "van", "box_truck": "truck", "truck_box": "truck"})
    projection.enum(actor, "color", VEHICLE_COLORS,
                    projection.defaults["ego_color" if is_ego else "actor_color"],
                    f"{'ego' if is_ego else 'actors['+actor_id+']'}.color",
                    {"gray": "grey", "dark_gray": "grey", "dark": "black", "red_with_black_roof": "red"})
    points = actor.get("trajectory")
    if not isinstance(points, list):
        points = []
    # Private execution hint: the source permits a distance-equivalent single
    # action only when every supplied speed is explicitly an estimate.  Exact
    # or observed speed changes must retain their piecewise timeline.
    supplied_speeds = [point.get("speed_mps") for point in points if isinstance(point, dict)]
    actor["_execution_single_action_motion"] = bool(supplied_speeds) and all(
        isinstance(value, dict) and value.get("status") == "estimated"
        and _finite(value) is not None and _finite(value) >= 0
        for value in supplied_speeds
    )
    valid_points = [copy.deepcopy(point) for point in points if isinstance(point, dict) and _finite(point.get("time_s", point.get("timestamp_s"))) is not None]
    valid_points.sort(key=lambda point: _finite(point.get("time_s", point.get("timestamp_s"))))
    if not valid_points:
        valid_points = [{"time_s": 0.0}, {"time_s": duration}]
        projection.fallbacks.append({"field": f"{'ego' if is_ego else 'actors['+actor_id+']'}.trajectory", "value": "canonical_constant_motion", "reason": "missing_or_invalid_collection"})
    continuity_lane = _highway_continuity_lane(
        projection, actor_id, valid_points, lane_ids, is_ego)
    last_lane_time = max(
        (_finite(point.get("time_s", point.get("timestamp_s"))) or 0.0
         for point in valid_points if _text(point.get("lane_id")) == continuity_lane),
        default=None)
    if (_finite(valid_points[0].get("time_s", valid_points[0].get("timestamp_s"))) or 0.0) > 0:
        start = copy.deepcopy(valid_points[0]); start["time_s"] = 0.0
        # This is an execution boundary, not another observation of the first visible frame.
        start.pop('frame_id', None)
        valid_points.insert(0, start)
        projection.fallbacks.append({"field": f"{'ego' if is_ego else 'actors['+actor_id+']'}.trajectory[0].time_s", "value": 0.0, "reason": "execution_requires_start_sample"})
    last_time = _finite(valid_points[-1].get("time_s", valid_points[-1].get("timestamp_s"))) or 0.0
    if last_time < duration:
        end = copy.deepcopy(valid_points[-1]); end["time_s"] = duration
        # A hold of the final velocity is an execution extension, not another
        # observed frame. Advance an available x instead of copying a stale x.
        end.pop('frame_id', None)
        if continuity_lane is not None:
            end['lane_id'] = continuity_lane
        end_position = end.get('position_ego_m')
        end_speed = _finite(end.get('speed_mps'))
        if isinstance(end_position, dict) and _finite(end_position.get('x')) is not None:
            if end_speed is not None:
                end_position['x'] = _finite(end_position['x']) + (0 if actor.get('is_static') else end_speed) * (duration-last_time)
            else:
                end_position.pop('x', None)
        valid_points.append(end)
        if continuity_lane is not None:
            projection.fallbacks.append({
                "field": f"actors[{actor_id}].trajectory[-1]",
                "value": {"time_s": duration, "lane_id": continuity_lane},
                "observed_value": {"last_known_lane_id": continuity_lane,
                                   "last_known_lane_time_s": last_lane_time,
                                   "source_trajectory_end_s": last_time},
                "reason": "unobserved_horizon_straight_continuation",
            })
        else:
            projection.fallbacks.append({"field": f"{'ego' if is_ego else 'actors['+actor_id+']'}.trajectory[-1].time_s", "value": duration, "reason": "execution_requires_end_sample"})

    # Start placement belongs to the first sample. An exit lane observed later
    # must not relocate the actor's initial state to the far side of a junction.
    lane = _text(valid_points[0].get('lane_id'))
    lane = lane if lane in lane_ids else None
    if lane is None and _text(source_condition.get("lane_id")) in lane_ids:
        lane = _text(source_condition.get("lane_id"))
    if lane is None:
        from _lib.contracts.lane_locations import lane_locations
        locations = lane_locations(projection.data['road'])
        initial_road = _text(valid_points[0].get('road_id')) or _text(source_condition.get('road_id'))
        ordinal = valid_points[0].get('ordinal_from_right')
        if ordinal is None:
            ordinal = source_condition.get('ordinal_from_right')
        if initial_road is not None:
            candidates = [lid for lid, (owner, position) in locations.items()
                          if owner == initial_road and (ordinal is None or ordinal == position)]
            if not candidates:
                raise ValueError('No executable lane matches the declared initial road/ordinal: ' + actor_id)
            lane = candidates[0]
    # A later *known* sample from this same actor is a safer private initial
    # placement than the road's first lane.  It remains a policy assumption,
    # never an observed fact; explicit initial conditions still take priority.
    inferred_lane = None
    inferred_point = None
    if lane is None and projection.scene_type == "highway":
        inferred_point = next((point for point in valid_points[1:]
                               if _text(point.get("lane_id")) in lane_ids), None)
        if inferred_point is not None:
            inferred_lane = _text(inferred_point["lane_id"])
            lane = inferred_lane
    lane = lane or default_lane
    initial_x = _finite((valid_points[0].get("position_ego_m") or {}).get("x"))
    if initial_x is None:
        initial_x = _finite((source_condition.get("position_ego_m") or {}).get("x"))
    if initial_x is None:
        from _lib.contracts.semantic_gap import IMAGE_REVISION, REVISION, decision_for, initial_gap
        chosen = None if is_ego else initial_gap(projection.semantic_gap, actor_id)
        semantic_row = None if is_ego else decision_for(projection.semantic_gap, actor_id)
        semantic_layout_default = (semantic_row is not None and projection.semantic_gap
                                   and projection.semantic_gap.get('revision') in {IMAGE_REVISION, REVISION})
        initial_x = (chosen[0] if chosen else
                     0.0 if is_ego else projection.defaults["actor_initial_gap_m"]
                     if semantic_layout_default else
                     projection.defaults["actor_initial_gap_m"] + index * projection.defaults["actor_gap_step_m"])
        semantic_reason = ('semantic_initial_gap' if chosen else
                           'semantic_gap_unknown' if not is_ego and projection.semantic_gap
                           and projection.semantic_gap.get('status') == 'accepted'
                           and any(row['actor_id'] == actor_id for row in projection.semantic_gap.get('decisions', [])) else
                           'semantic_gap_unavailable' if not is_ego and projection.semantic_gap
                           and projection.semantic_gap.get('status') == 'skipped' else 'missing_or_unknown')
        record = {"field": f"{'ego' if is_ego else 'actors['+actor_id+']'}.trajectory[0].position_ego_m.x",
                  "value": initial_x, "reason": semantic_reason}
        if chosen:
            row = chosen[1]
            record.update(category=row['category'], frame_id=row['frame_id'],
                          policy_revision=projection.semantic_gap['revision'])
            if 'evidence_quote' in row:
                record['evidence_quote'] = row['evidence_quote']
            else:
                record['evidence_quotes'] = copy.deepcopy(row['evidence_quotes'])
                if 'visual_evidence' in row:
                    record['visual_evidence'] = row['visual_evidence']
        projection.fallbacks.append(record)
    initial_y = _finite((valid_points[0].get("position_ego_m") or {}).get("y"))
    if initial_y is None:
        initial_y = _finite((source_condition.get("position_ego_m") or {}).get("y"))
    if initial_y is None:
        initial_y = 0.0
        projection.fallbacks.append({"field": f"{'ego' if is_ego else 'actors['+actor_id+']'}.trajectory[0].position_ego_m.y", "value": 0.0, "reason": "lane_center_default"})

    static = bool(actor.get("is_static"))
    condition_speed_kmh = _finite(source_condition.get("speed_kmh"))
    fallback_speed_mps = condition_speed_kmh / 3.6 if condition_speed_kmh is not None and condition_speed_kmh >= 0 else speed_mps
    actor_path = "ego" if is_ego else f"actors[{actor_id}]"
    previous_time = 0.0
    previous_x = initial_x
    previous_speed = 0.0 if static else fallback_speed_mps
    for point_index, point in enumerate(valid_points):
        point["time_s"] = _finite(point.get("time_s", point.get("timestamp_s"))) or 0.0
        # Only the initial lane is required to place an actor.  Later null
        # lane IDs may intentionally denote time spent inside a junction and
        # must not be turned into a false return to the approach lane.
        if _text(point.get("lane_id")) not in lane_ids and point_index == 0:
            original_lane = copy.deepcopy(point.get("lane_id"))
            point["lane_id"] = lane
            fallback = {"field": f"{actor_path}.trajectory[{point_index}].lane_id",
                        "value": lane, "observed_value": original_lane,
                        "reason": ("same_actor_earliest_known_lane_execution_assumption"
                                   if inferred_lane is not None else "missing_or_unknown")}
            if inferred_point is not None:
                fallback["basis"] = {"frame_id": inferred_point.get("frame_id"),
                                     "time_s": _finite(inferred_point.get("time_s")),
                                     "lane_id": inferred_lane}
            projection.fallbacks.append(fallback)
        elif _text(point.get("lane_id")) not in lane_ids and continuity_lane is not None:
            original_lane = copy.deepcopy(point.get("lane_id"))
            point["lane_id"] = continuity_lane
            projection.fallbacks.append({
                "field": f"{actor_path}.trajectory[{point_index}].lane_id",
                "value": continuity_lane,
                "observed_value": original_lane,
                "reason": ("unobserved_horizon_straight_continuation"
                           if last_lane_time is not None and point["time_s"] >= last_lane_time
                           else "same_actor_known_lane_execution_assumption"),
            })
        sample_speed = _finite(point.get("speed_mps"))
        if sample_speed is None or sample_speed < 0:
            continue_previous = (continuity_lane is not None and point_index > 0
                                 and last_lane_time is not None and point["time_s"] >= last_lane_time)
            point["speed_mps"] = 0.0 if static else previous_speed if continue_previous else fallback_speed_mps
            projection.fallbacks.append({
                "field": f"{actor_path}.trajectory[{point_index}].speed_mps",
                "value": point["speed_mps"],
                "reason": ("unobserved_horizon_straight_continuation"
                           if continue_previous else "missing_or_unknown"),
            })
        else:
            point["speed_mps"] = sample_speed
        if point_index == 0:
            x, y = initial_x, initial_y
        else:
            position = point.get("position_ego_m") if isinstance(point.get("position_ego_m"), dict) else {}
            x, y = _finite(position.get("x")), _finite(position.get("y"))
            if x is None:
                delta = point["time_s"] - previous_time
                x = previous_x + (previous_speed + point["speed_mps"]) * max(delta, 0.0) / 2.0
                projection.fallbacks.append({"field": f"{actor_path}.trajectory[{point_index}].position_ego_m.x", "value": x, "reason": "missing_or_unknown"})
            if y is None:
                y = initial_y
                projection.fallbacks.append({"field": f"{actor_path}.trajectory[{point_index}].position_ego_m.y", "value": y, "reason": "missing_or_unknown"})
        point["position_ego_m"] = {"x": x, "y": y}
        previous_time, previous_x, previous_speed = point["time_s"], x, point["speed_mps"]
    actor["trajectory"] = valid_points
    condition = source_condition
    lane_center_offset = _finite(source_condition.get("lane_center_offset_m"))
    if lane_center_offset is None:
        lane_center_offset = initial_y
    semantic_plan = next((row for row in (projection.data.get('_semantic_motion') or {}).get('plans', [])
                          if row['actor_id'] == actor_id), None)
    initial_stop = bool(semantic_plan and all(row['speed_kmh'] == 0
                                            for row in semantic_plan['speed_knots'][:2]))
    condition.update({"lane_id": lane, "position_ego_m": {"x": initial_x, "y": initial_y},
                      "lane_center_offset_m": lane_center_offset,
                      "speed_kmh": {"value": 0.0 if static else valid_points[0]["speed_mps"] * 3.6},
                      "motion_state": "stopped" if static or initial_stop else "moving"})
    return condition


def _apply_semantic_layout(projection: Projection, actors: list[dict], conditions: dict) -> None:
    """Resolve pairwise language relations after per-actor gap anchoring."""
    from _lib.contracts.semantic_gap import IMAGE_REVISION, REVISION
    decision = projection.semantic_gap
    if (not decision or decision.get('status') != 'accepted'
            or decision.get('revision') not in {IMAGE_REVISION, REVISION}):
        return
    rows = {row['actor_id']: row for row in decision['decisions']}
    movable = set(rows)
    actor_by_id = {actor['actor_id']: actor for actor in actors}
    positions = {actor_id: condition['position_ego_m']['x']
                 for actor_id, condition in conditions.items()}
    lanes = {actor_id: condition['lane_id'] for actor_id, condition in conditions.items()}
    spacing = float(projection.defaults['actor_gap_step_m'])
    constraints = []
    direct_by_lane = {}
    for actor_id, row in rows.items():
        for relation in row['relations']:
            if relation['type'] == 'ahead_of':
                constraints.append((actor_id, relation['actor_id'], 'ahead_of'))
            elif relation['type'] == 'direct_lead_of':
                if lanes.get(actor_id) != lanes.get('ego'):
                    raise ValueError('semantic direct lead is not in the ego lane: ' + actor_id)
                previous = direct_by_lane.setdefault(lanes[actor_id], actor_id)
                if previous != actor_id:
                    raise ValueError('multiple semantic direct leads in one lane')
    for lane, direct_id in direct_by_lane.items():
        for other_id, other_lane in lanes.items():
            if (other_id not in {'ego', direct_id} and other_lane == lane
                    and positions.get(other_id, 0.0) > 0):
                if other_id not in movable:
                    if positions[other_id] < positions[direct_id] + spacing:
                        raise ValueError('semantic direct lead conflicts with a measured actor position')
                else:
                    constraints.append((other_id, direct_id, 'direct_lead_of'))
    original = copy.deepcopy(positions)
    # Difference constraints x[ahead] >= x[behind] + spacing. Repeated
    # relaxation is deterministic and exposes contradictory relation cycles.
    for iteration in range(max(1, len(movable))):
        changed = False
        for ahead_id, behind_id, _ in constraints:
            if ahead_id not in movable or behind_id not in positions:
                raise ValueError('semantic layout relation references an unavailable actor')
            required = positions[behind_id] + spacing
            if positions[ahead_id] + 1e-9 < required:
                positions[ahead_id] = required
                changed = True
        if not changed:
            break
    else:
        raise ValueError('semantic layout relations contain a cycle')
    for actor_id in sorted(movable):
        delta = positions[actor_id] - original[actor_id]
        if abs(delta) <= 1e-9:
            continue
        actor = actor_by_id[actor_id]
        for point in actor['trajectory']:
            point['position_ego_m']['x'] += delta
        conditions[actor_id]['position_ego_m']['x'] = positions[actor_id]
        projection.fallbacks.append({
            'field': f'actors[{actor_id}].trajectory[0].position_ego_m.x',
            'observed_value': original[actor_id], 'value': positions[actor_id],
            'reason': 'semantic_relation_layout',
            'relations': copy.deepcopy(rows[actor_id]['relations']),
            'policy_revision': decision['revision'],
        })


def _actors(projection: Projection, default_lane: str, duration: float) -> None:
    data = projection.data
    # A reviewed straight preview is already a complete deterministic
    # execution projection with hashes bound to the original actor tracks.
    # Re-projecting or filtering those actors would invalidate that binding.
    if data.get("quality_report", {}).get("straight_preview"):
        return
    lane_ids = {lane["lane_id"] for lane in data["road"]["lanes"]}
    included = _actor_ids(projection)
    by_id = {actor.get("actor_id"): actor for actor in data.get("actors", []) if isinstance(actor, dict)}
    actors = [by_id[actor_id] for actor_id in included if actor_id != "ego" and actor_id in by_id]
    scenery = [actor for actor in by_id.values()
               if actor.get("provenance", {}).get("source") == "user_requested_scenery"]
    data["actors"] = actors + scenery
    previous = (data.get("simulation_scenarios") or [{}])[0]
    if not isinstance(previous, dict):
        previous = {}
    source_conditions = previous.get("initial_conditions") if isinstance(previous.get("initial_conditions"), dict) else {}
    from parking_layout import allocate
    conditions = {"ego": _normalize_actor(projection, data["ego"], lane_ids, default_lane,
                                            duration, 0, True, source_conditions.get("ego"))}
    entry_binding = _entry_geometry_from_source(projection, conditions['ego'])
    allocate(projection, actors, source_conditions)
    for index, actor in enumerate(actors):
        conditions[actor["actor_id"]] = _normalize_actor(
            projection, actor, lane_ids, default_lane, duration, index, False,
            source_conditions.get(actor["actor_id"]))
    _apply_semantic_layout(projection, actors, conditions)
    if entry_binding:
        entry_road, entry_length = entry_binding
        growth = entry_road['road_geometry']['visible_length_m'] - entry_length
        if growth > 1e-8:
            # Capacity was added to the approach. Move the moving corridor's
            # road-local origin by the same amount, preserving entry time.
            for actor in [data['ego'], *actors]:
                aid = actor['actor_id']
                if actor.get('is_static') or conditions[aid]['lane_id'] not in entry_road['lane_ids']:
                    continue
                track = copy.deepcopy(actor['trajectory'])
                for point in track:
                    point['position_ego_m']['x'] += growth
                projection.set(actor, 'trajectory', track, f'actors[{aid}].trajectory',
                               'parking_capacity_entry_origin_shift')
                conditions[aid]['position_ego_m']['x'] += growth
        _bound_entry_actor_defaults(projection, actors, conditions, source_conditions, entry_road)
    scenario = {
        "scenario_id": previous.get("scenario_id") or "fallback_scenario_001",
        "name": previous.get("name") or ("Intersection execution" if projection.scene_type == "intersection" else "Highway execution"),
        "description": previous.get("description") or "Deterministic MELA execution projection",
        "duration_s": duration,
        "involved_actor_ids": included,
        "initial_conditions": conditions,
        "trigger_events": previous.get("trigger_events") if isinstance(previous.get("trigger_events"), list) else [],
    }
    if data.get("simulation_scenarios") != [scenario]:
        data["simulation_scenarios"] = [scenario]
        projection.fallbacks.append({"field": "simulation_scenarios[0]", "value": "deterministic_execution_scenario", "reason": "missing_or_incomplete_execution_scenario"})


def _bound_entry_actor_defaults(projection, actors, conditions, source_conditions, road):
    """Keep unknown initial placements inside the newly timed approach.

    This changes execution-only defaults, never an observed actor position.
    Existing numeric positions and Ego are reserved before assigning slots.
    """
    originals = {a['actor_id']: a for a in projection.source.get('actors', [])}
    length = road['road_geometry']['visible_length_m']
    for lane in road['lane_ids']:
        members = [a for a in actors if not a.get('is_static')
                   and conditions[a['actor_id']]['lane_id'] == lane]
        unknown, reserved = [], []
        if conditions['ego']['lane_id'] == lane:
            reserved.append(conditions['ego']['position_ego_m']['x'])
        for actor in members:
            aid = actor['actor_id']
            original = (originals.get(aid, {}).get('trajectory') or [{}])[0]
            prior = source_conditions.get(aid) or {}
            known = any(_finite((row.get('position_ego_m') or {}).get('x')) is not None
                        for row in (original, prior))
            x = conditions[aid]['position_ego_m']['x']
            if known:
                if not 0 <= x <= length:
                    raise ValueError('entry.actor_position_outside_road: ' + aid)
                reserved.append(x)
            else:
                unknown.append(actor)
        if not any(not 6 <= conditions[a['actor_id']]['position_ego_m']['x'] <= length-6
                   for a in unknown):
            continue
        ceiling = length - 6
        for actor in sorted(unknown, key=lambda a: (conditions[a['actor_id']]['position_ego_m']['x'],
                                                    a['actor_id']), reverse=True):
            aid = actor['actor_id']
            current = conditions[aid]['position_ego_m']['x']
            ego_x = conditions['ego']['position_ego_m']['x']
            floor = max(6.0, ego_x+8.0) if current > ego_x else 6.0
            slot = min(current, ceiling)
            while slot >= floor and any(abs(slot-x) < 8-1e-8 for x in reserved):
                slot -= 1.0
            if slot < floor:
                raise ValueError('entry.actor_initial_capacity: ' + lane)
            track = copy.deepcopy(actor['trajectory'])
            for point in track:
                point['position_ego_m']['x'] += slot-current
            projection.set(actor, 'trajectory', track, f'actors[{aid}].trajectory',
                           'entry_road_bounded_initial_position')
            conditions[aid]['position_ego_m']['x'] = slot
            reserved.append(slot)
            ceiling = slot - 8.0


def _environment(projection: Projection) -> None:
    environment = projection.data.get("environment")
    if not isinstance(environment, dict):
        environment = {}
        projection.set(projection.data, "environment", environment, "environment", "missing_or_invalid_object")
    projection.enum(environment, "target_weather_compatibility", WEATHER, projection.defaults["weather"], "environment.target_weather_compatibility",
                    {"sunny": "clear", "cloudy": "overcast"})
    projection.enum(environment, "daytime", DAYTIME, projection.defaults["daytime"], "environment.daytime",
                    {"day": "afternoon", "daytime": "afternoon"})
    projection.enum(environment, "season", SEASONS, projection.defaults["season"], "environment.season", {"fall": "autumn"})
    projection.enum(environment, "topology", TOPOLOGIES, projection.defaults["topology"], "environment.topology",
                    {"uphill": "hilly", "downhill": "hilly"})


def _entry_geometry_from_source(projection: Projection, ego_condition):
    """Bind the normalized execution speed integral to an observed time window."""
    data = projection.data
    quality = data.get('quality_report') or {}
    if (projection.scene_type != 'intersection' or quality.get('intersection_preview')
            or quality.get('motion_contract')):
        return
    from _lib.contracts.entry import ego_entry_intervals, ENTRY_TYPES
    intervals = ego_entry_intervals(projection.source)
    if not intervals:
        return
    road, _ = _ego_corridor(data)
    if road is None or road.get('road_role') != 'incoming':
        return
    lo = max(row[1] for row in intervals)
    hi = min(row[2] for row in intervals)
    duration = data['metadata']['duration_s']
    if not 0 <= lo <= hi <= duration:
        raise ValueError('entry.incompatible_time_windows')
    trigger = (lo + hi) / 2
    primitives = (projection.source.get('simulation_scenarios') or [{}])[0].get('trigger_events') or []
    explicit = [_finite(p.get('trigger_time_s')) for p in primitives
                if p.get('actor_id') == 'ego' and p.get('type') in ENTRY_TYPES
                and p.get('event_id') in {row[0] for row in intervals}]
    if explicit:
        if any(t is None or not lo <= t <= hi for t in explicit):
            raise ValueError('entry.trigger_outside_interval')
        trigger = explicit[0]
    points = data['ego']['trajectory']
    x0 = ego_condition['position_ego_m']['x']
    source_road = next((r for r in projection.source['road']['roads']
                        if r['road_id'] == road['road_id']), {})
    original = source_road.get('road_geometry', {}).get('visible_length_m')
    known_length = _positive_dimension(original, 'ego approach visible_length_m')
    estimated_range = _estimated_dimension_range(original, 'ego approach visible_length_m')
    source_points = projection.source['ego'].get('trajectory') or []
    policy_speed = any(_finite(p.get('speed_mps')) is None for p in source_points)
    if (known_length is not None and source_points
            and all(_finite(p.get('speed_mps')) is None for p in source_points)
            and trigger > 0 and known_length > x0):
        speed = (known_length - x0) / trigger
        track = copy.deepcopy(points)
        for point in track:
            point['speed_mps'] = speed
            if not any(_finite((p.get('position_ego_m') or {}).get('x')) is not None
                       and _finite(p.get('time_s', p.get('timestamp_s'))) == point['time_s']
                       for p in source_points):
                point['position_ego_m']['x'] = x0 + speed * point['time_s']
        projection.set(data['ego'], 'trajectory', track, 'ego.trajectory',
                       'speed_from_known_geometry_and_entry')
        ego_condition['speed_kmh'] = {'value': speed * 3.6}
        points = track

    def distance(t):
        x = x0
        for a, b in zip(points, points[1:]):
            start, end = a['time_s'], b['time_s']
            if end <= start:
                raise ValueError('entry.non_increasing_execution_time')
            dt = max(0.0, min(t, end) - start)
            x += a['speed_mps'] * dt + (b['speed_mps']-a['speed_mps']) * dt*dt / (2*(end-start))
        return x
    motion_low, motion_high = sorted((distance(lo), distance(hi)))
    if estimated_range is not None:
        overlap_low = max(estimated_range[0], motion_low)
        overlap_high = min(estimated_range[1], motion_high)
        if overlap_low > overlap_high + 1e-6:
            raise ValueError('entry.geometry_motion_conflict')
        length = min(max(distance(trigger), overlap_low), overlap_high)
        binding_reason = 'resolved_estimated_geometry_motion_overlap'
    else:
        length = distance(trigger) if known_length is None else known_length
        binding_reason = ('derived_from_entry_event_and_policy_speed' if policy_speed
                          else 'derived_from_source_motion_and_entry')
    if length <= x0 or not motion_low-1e-6 <= length <= motion_high+1e-6:
        raise ValueError('entry.geometry_motion_conflict')
    index = data['road']['roads'].index(road)
    projection.set(road['road_geometry'], 'visible_length_m', length,
                   f'road.roads[{index}].road_geometry.visible_length_m',
                   binding_reason)
    data.setdefault('quality_report', {})['entry_geometry_bindings'] = [dict(
        road_id=road['road_id'], event_id=eid, entry_interval_s=[start, end],
        estimated_length_m=length, trigger_time_s=trigger,
        source_length_range_m=list(estimated_range) if estimated_range is not None else None,
        motion_source='policy_default' if policy_speed else 'source') for eid, start, end in intervals]
    return road, length


def project_analysis(source: dict, scene_type: str, *, policy=None, semantic_gap=None) -> tuple[dict, list[dict], list[str]]:
    """Return a complete private execution copy and its deterministic audit."""
    validate_conversion_input(source, scene_type)
    if policy is not None:
        from _lib.contracts.conversion_policy import PolicyError
        for ji, junction in enumerate(source.get('junctions', [])):
            for si, signal in enumerate(junction.get('traffic_signals', [])):
                states = {_text(row.get('state')) for row in signal.get('state_timeline', []) if isinstance(row, dict)} - {None}
                if len(states) > 1:
                    raise PolicyError('Known changing signal timeline cannot be replaced by a constant policy state',
                                      'conversion.known_signal_timeline', path=f'junctions[{ji}].traffic_signals[{si}].state_timeline')
    if scene_type == 'intersection':
        # Check declared geometry before policy defaults or angle snapping.
        validate_arm_bearings(source.get('road', {}).get('roads', []), path='/road/roads')
    if semantic_gap is not None:
        from _lib.contracts.semantic_gap import validate as validate_semantic_gap
        validate_semantic_gap(source, semantic_gap['analysis_sha256'], semantic_gap)
    projection = Projection(source, scene_type, policy, semantic_gap)
    duration = _duration(projection)
    _, default_lane = _roads(projection)
    _topology_normalize(projection)
    _junctions(projection)
    from _lib.contracts.lane_locations import validate_actor_locations, lane_locations, validate_location
    road = projection.data['road']
    validate_actor_locations([projection.data['ego'], *projection.data.get('actors', [])], road)
    locations = lane_locations(road)
    road_ids = {arm['road_id'] for arm in road['roads']}
    for si, scenario in enumerate(projection.data.get('simulation_scenarios') or []):
        for actor_id, condition in (scenario.get('initial_conditions') or {}).items():
            validate_location(condition, locations, road_ids,
                              f'/simulation_scenarios/{si}/initial_conditions/{actor_id}', actor_id)
    from _lib.contracts import semantic_motion
    if semantic_gap is not None and semantic_gap.get('revision') == semantic_motion.REVISION:
        semantic_motion.project(projection, semantic_gap)
    _actors(projection, default_lane, duration)
    _verify_projection_lane_transitions(projection)
    if projection.data.get('metadata', {}).get('bounded_execution'):
        from _lib.behavior.lane_change import from_analysis
        spec = from_analysis(projection.data)
        if spec and spec.timing_assumption:
            actor_path = 'ego' if spec.actor_id == 'ego' else f'actors[{spec.actor_id}]'
            projection.fallbacks.append(dict(field=actor_path+'.execution_interval.time_s',
                value=[spec.start_s,spec.end_s],reason='bounded_lane_change_timing',
                **spec.timing_assumption))
    validate_actor_locations([projection.data['ego'], *projection.data.get('actors', [])], road)
    _environment(projection)
    projection.excluded_actor_ids = sorted(set(projection.excluded_actor_ids))
    return projection.data, authorize(projection.fallbacks, policy), projection.excluded_actor_ids
