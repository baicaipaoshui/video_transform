"""One bounded, evidence-backed lane change on a straight highway.

This is an internal interpretation of existing World/Behavior fields.  It is
deliberately not a persisted schema: routing, conversion and delivery derive
the same value independently from their own inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from _lib.contracts.actors import is_vehicle
from _lib.contracts.lane_locations import lane_locations


CONTRACT = "highway_lane_change.v1"
_FORWARD = {None, "forward", "same_as_ego", "same_direction"}


@dataclass(frozen=True)
class LaneChangeSpec:
    actor_id: str
    event_id: str
    source_lane_id: str
    target_lane_id: str
    source_ordinal: int
    target_ordinal: int
    direction: str
    start_s: float
    end_s: float
    duration_s: float
    source_frame_id: str
    target_frame_id: str
    contract: str = CONTRACT
    timing_assumption: dict | None = None


class LaneChangeBlocked(ValueError):
    code = "unsupported_scene_type"

    def __init__(self, rule_id, message, *, actor_id=None, actual=None, path=None):
        super().__init__(message)
        self.diagnostics = {
            "rule_id": rule_id,
            "message": message,
            "owner": "behavior",
            "actor_id": actor_id,
            "field_path": path or ("/events" if actor_id is None else f"/actors/{actor_id}/trajectory"),
            "expected": "one evidence-bound adjacent lane change on a junction-free straight highway",
            "actual": actual,
            "retry_kind": "unsupported_capability",
        }


def is_lane_change_kind(value):
    value = str(value or "").strip().lower().replace("-", "_")
    return value in {"lane_change", "change_lane", "cut_in"} or "lane_change" in value


def _number(value):
    if isinstance(value, dict):
        if value.get("status") in {"unknown", "placeholder"}:
            return None
        value = value.get("value")
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _time(point):
    return _number(point.get("time_s", point.get("timestamp_s")))


def _scene(value):
    return value.get("world", value)


def _parts(value):
    if "ego" in value:
        scenario = (value.get("simulation_scenarios") or [{}])[0]
        return ([value["ego"], *value.get("actors", [])], value.get("events", []),
                scenario.get("trigger_events", []))
    return value.get("actors", []), value.get("events", []), value.get("executor_primitives", [])


def _trajectory_lane_transitions(actor):
    """Return consecutive known-lane changes; null samples stay inside a gap."""
    known = []
    for point in actor.get("trajectory", []):
        if not isinstance(point, dict) or point.get("lane_id") is None:
            continue
        time_s = _time(point)
        if time_s is None:
            continue
        known.append((time_s, point))
    if any(right[0] <= left[0] for left, right in zip(known, known[1:])):
        return []
    return [(before, after) for (_, before), (_, after) in zip(known, known[1:])
            if before.get("lane_id") != after.get("lane_id")]


def normalize_event_intervals(behavior):
    """Expand a lane-change event envelope to its trajectory transition.

    This is a fact-neutral wire repair: it never creates evidence, changes a
    trajectory, or narrows an event.  The actor trajectory is the sole source
    of the interval: the last known source-lane sample and first known
    target-lane sample surrounding the null-lane gap.  Event evidence only
    verifies that those two trajectory frames were actually submitted as
    observed witnesses.  Any disagreement is left for the validator to reject.
    """
    if not isinstance(behavior, dict):
        return behavior, []
    actors = {row.get("actor_id"): row for row in behavior.get("actors", [])
              if isinstance(row, dict) and isinstance(row.get("actor_id"), str)}
    repairs = []
    for event_index, event in enumerate(behavior.get("events", [])):
        if not isinstance(event, dict) or not is_lane_change_kind(event.get("event_type")):
            continue
        involved = event.get("involved_actor_ids")
        if not isinstance(involved, list) or len(involved) != 1 or involved[0] not in actors:
            continue
        actor_id = involved[0]
        actor = actors[actor_id]
        transitions = _trajectory_lane_transitions(actor)
        if len(transitions) != 1:
            continue
        before, after = transitions[0]
        start, end = _time(before), _time(after)
        if start is None or end is None or not start < end:
            continue
        by_frame = {point.get("frame_id"): point
                    for point in actor.get("trajectory", [])
                    if isinstance(point, dict) and isinstance(point.get("frame_id"), str)}
        observed_frames = set()
        valid = True
        for evidence in event.get("evidence", []):
            if (not isinstance(evidence, dict) or evidence.get("status") != "observed"
                    or evidence.get("actor_id") != actor_id
                    or not isinstance(evidence.get("frame_id"), str)):
                continue
            point = by_frame.get(evidence["frame_id"])
            point_time = _time(point) if point is not None else None
            evidence_time = _number(evidence.get("time_s", evidence.get("timestamp_s")))
            if (point_time is None or evidence_time is not None
                    and abs(evidence_time - point_time) > 1e-7):
                valid = False
                break
            if evidence.get("lane_id") is not None and evidence.get("lane_id") != point.get("lane_id"):
                valid = False
                break
            observed_frames.add(evidence["frame_id"])
        boundary_frames = {before.get("frame_id"), after.get("frame_id")}
        if (not valid or None in boundary_frames
                or not boundary_frames <= observed_frames):
            continue
        key = "time_range_s" if "time_range_s" in event else (
            "time_interval_s" if "time_interval_s" in event else None)
        interval = event.get(key) if key else None
        if (not isinstance(interval, list) or len(interval) != 2
                or any(_number(value) is None for value in interval)):
            continue
        lo, hi = (_number(value) for value in interval)
        expanded = [min(lo, start), max(hi, end)]
        if expanded != [lo, hi]:
            event[key] = expanded
            repairs.append({
                "path": f"events.{event_index}.{key}",
                "action": "expand_lane_change_interval_to_trajectory_bounds",
                "before": interval,
                "after": expanded,
                "actor_id": actor_id,
                "trajectory_bounds_s": [start, end],
                "trajectory_frame_ids": [before.get("frame_id"), after.get("frame_id")],
            })
    return behavior, repairs


def _duration(scene, supplied):
    value = _number(supplied)
    if value is not None:
        return value
    metadata = scene.get("metadata") or {}
    for key in ("duration_s", "source_duration_s", "video_duration_s"):
        value = _number(metadata.get(key))
        if value is not None:
            return value
    return None


def derive(world, behavior, duration_s=None):
    """Return a LaneChangeSpec, None when absent, or reject an unsafe attempt."""
    scene = _scene(world)
    actors, events, primitives = _parts(behavior)
    roads = scene.get("road", {}).get("roads", [])
    lanes = {row.get("lane_id"): row for row in scene.get("road", {}).get("lanes", [])}
    owners = {lane_id: road.get("road_id") for road in roads for lane_id in road.get("lane_ids", [])}

    transitions = []
    for actor in actors:
        for before, after in _trajectory_lane_transitions(actor):
            source, target = before.get("lane_id"), after.get("lane_id")
            if source != target and owners.get(source) is not None and owners.get(source) == owners.get(target):
                transitions.append((actor.get("actor_id"), source, target))
    lane_events = [row for row in events if is_lane_change_kind(row.get("event_type"))]
    lane_primitives = [row for row in primitives if is_lane_change_kind(row.get("type"))]
    if not (transitions or lane_events or lane_primitives):
        return None

    if scene.get("scene_type") != "highway" or scene.get("junctions"):
        raise LaneChangeBlocked("lane_change.scene", "Lane-change v1 requires a junction-free highway")
    if len(lane_events) != 1:
        raise LaneChangeBlocked(
            "lane_change.cardinality",
            "Lane-change v1 requires exactly one evidence-backed lane-change event",
            actual={"transitions": transitions, "lane_change_events": len(lane_events)},
        )
    event = lane_events[0]
    if str(event.get("event_type", "")).strip().lower().replace("-", "_") == "cut_in":
        raise LaneChangeBlocked("lane_change.event_scope",
                                "Cut-in semantics are outside the bounded adjacent lane-change contract",
                                actual=event.get("event_type"))
    involved = event.get("involved_actor_ids")
    if not isinstance(involved, list) or len(involved) != 1:
        raise LaneChangeBlocked("lane_change.event_actor", "Lane-change event must bind exactly one actor", actual=involved)
    actor_id = involved[0]
    actor = next((row for row in actors if row.get("actor_id") == actor_id), None)
    if (actor is None or not is_vehicle(actor) or actor.get("is_static")
            or actor.get("simulation_role") not in {None, "participant"}):
        raise LaneChangeBlocked("lane_change.actor_scope", "Lane-change actor must be one moving participant vehicle",
                                actor_id=actor_id)

    if len(lane_primitives) > 1 or any(row.get("actor_id") != actor_id for row in lane_primitives):
        raise LaneChangeBlocked("lane_change.primitive_scope",
                                "At most one matching lane-change primitive may bind the changing actor",
                                actor_id=actor_id, actual=lane_primitives)
    if lane_primitives:
        primitive = lane_primitives[0]
        if str(primitive.get("type", "")).strip().lower().replace("-", "_") == "cut_in":
            raise LaneChangeBlocked("lane_change.primitive_scope", "Cut-in primitives are unsupported",
                                    actor_id=actor_id, actual=primitive.get("type"))
        if primitive.get("event_id") not in {None, event.get("event_id")}:
            raise LaneChangeBlocked("lane_change.primitive_binding",
                                    "Lane-change primitive must reference the lane-change event",
                                    actor_id=actor_id, actual=primitive.get("event_id"))

    # A second motion-changing action would require a separate composition
    # contract, so reject it. Descriptive events may coexist and do not become
    # part of this actor-local spec.
    incompatible = ("turn", "cut_in", "collision", "loss_of_control", "evasive_steering")
    for row in events:
        if row is event:
            continue
        kind = str(row.get("event_type", "")).lower()
        if any(word in kind for word in incompatible):
            raise LaneChangeBlocked("lane_change.composite_motion",
                                    "Lane-change v1 cannot compose another incompatible motion event",
                                    actor_id=actor_id, actual=kind)
    for row in primitives:
        if row in lane_primitives or row.get("type") == "initial_state":
            continue
        raise LaneChangeBlocked("lane_change.composite_motion",
                                "Lane-change v1 cannot compose another executable motion primitive",
                                actor_id=actor_id, actual=row.get("type"))

    trajectory = actor.get("trajectory", [])
    by_frame = {}
    for point in trajectory:
        frame_id = point.get("frame_id")
        if isinstance(frame_id, str):
            if frame_id in by_frame:
                raise LaneChangeBlocked("lane_change.trajectory_evidence",
                                        "Trajectory frame IDs must be unique", actor_id=actor_id, actual=frame_id)
            by_frame[frame_id] = point
    observed_frames = set()
    for evidence in event.get("evidence", []):
        if (not isinstance(evidence, dict) or evidence.get("status") != "observed"
                or evidence.get("actor_id") != actor_id or not isinstance(evidence.get("frame_id"), str)):
            continue
        point = by_frame.get(evidence["frame_id"])
        if point is None or _time(point) is None:
            raise LaneChangeBlocked("lane_change.event_evidence",
                                    "Observed event frame must bind one timed trajectory point",
                                    actor_id=actor_id, actual=evidence.get("frame_id"))
        evidence_time = _number(evidence.get("time_s", evidence.get("timestamp_s")))
        if evidence_time is not None and abs(evidence_time - _time(point)) > 1e-7:
            raise LaneChangeBlocked("lane_change.event_evidence",
                                    "Event evidence time disagrees with its bound trajectory frame",
                                    actor_id=actor_id, actual=[evidence_time, _time(point)])
        if evidence.get("lane_id") is not None and evidence.get("lane_id") != point.get("lane_id"):
            raise LaneChangeBlocked("lane_change.event_evidence",
                                    "Event evidence lane disagrees with its bound trajectory frame",
                                    actor_id=actor_id, actual=[evidence.get("lane_id"), point.get("lane_id")])
        observed_frames.add(evidence["frame_id"])
    actor_transitions = _trajectory_lane_transitions(actor)
    if len(actor_transitions) != 1:
        raise LaneChangeBlocked("lane_change.cardinality",
                                "Changing actor trajectory must contain exactly one known-lane transition",
                                actor_id=actor_id,
                                actual=[(left.get("lane_id"), right.get("lane_id"))
                                        for left, right in actor_transitions])
    before, after = actor_transitions[0]
    source_lane, target_lane = before.get("lane_id"), after.get("lane_id")
    if before.get("frame_id") not in observed_frames or after.get("frame_id") not in observed_frames:
        raise LaneChangeBlocked("lane_change.event_evidence",
                                "Event evidence must include the trajectory-derived source and target boundary frames",
                                actor_id=actor_id,
                                actual={"required": [before.get("frame_id"), after.get("frame_id")],
                                        "observed": sorted(observed_frames)})
    start, end = _time(before), _time(after)
    unexpected = [row for row in transitions
                  if row[0] != actor_id or row[1:] != (source_lane, target_lane)]
    if unexpected:
        raise LaneChangeBlocked("lane_change.cardinality",
                                "Lane-change v1 supports one actor crossing one adjacent boundary",
                                actor_id=actor_id, actual=unexpected)

    interval = event.get("time_range_s", event.get("time_interval_s"))
    if isinstance(interval, dict):
        interval = [interval.get("start", interval.get("start_s")), interval.get("end", interval.get("end_s"))]
    if interval is not None:
        if not isinstance(interval, list) or len(interval) != 2 or any(_number(value) is None for value in interval):
            raise LaneChangeBlocked("lane_change.event_interval", "Lane-change event interval must contain two finite times",
                                    actor_id=actor_id, actual=interval)
        lo, hi = (_number(value) for value in interval)
        if not lo <= start < end <= hi:
            raise LaneChangeBlocked("lane_change.event_interval",
                                    "Evidence-boundary frames must lie inside the event interval",
                                    actor_id=actor_id, actual={"event": interval, "evidence": [start, end]})

    for point in trajectory:
        time_s, lane_id = _time(point), point.get("lane_id")
        if time_s is None:
            continue
        if time_s <= start and lane_id != source_lane:
            raise LaneChangeBlocked("lane_change.source_segment",
                                    "Actor must stay in the source lane through the start boundary",
                                    actor_id=actor_id, actual={"time_s": time_s, "lane_id": lane_id})
        if time_s >= end and lane_id != target_lane:
            raise LaneChangeBlocked("lane_change.target_segment",
                                    "Actor must stay in the target lane after the end boundary",
                                    actor_id=actor_id, actual={"time_s": time_s, "lane_id": lane_id})
        if start < time_s < end and lane_id not in {None, source_lane, target_lane}:
            raise LaneChangeBlocked("lane_change.transition_segment",
                                    "Transition samples may only use source, target or straddling unknown lane",
                                    actor_id=actor_id, actual={"time_s": time_s, "lane_id": lane_id})

    if owners.get(source_lane) is None or owners.get(source_lane) != owners.get(target_lane):
        raise LaneChangeBlocked("lane_change.same_road", "Lane change must stay on one road",
                                actor_id=actor_id, actual=[source_lane, target_lane])
    try:
        locations = lane_locations(scene.get("road", {}))
    except Exception as error:
        raise LaneChangeBlocked("lane_change.lane_position", str(error), actor_id=actor_id) from error
    source_owner, source_ordinal = locations.get(source_lane, (None, None))
    target_owner, target_ordinal = locations.get(target_lane, (None, None))
    if (source_owner != target_owner or source_ordinal is None or target_ordinal is None
            or abs(source_ordinal - target_ordinal) != 1):
        raise LaneChangeBlocked("lane_change.adjacency",
                                "Only one adjacent transition with known road-local lane positions is supported",
                                actor_id=actor_id, actual=[source_lane, source_ordinal, target_lane, target_ordinal])
    owner_road = next(row for row in roads if row.get("road_id") == source_owner)
    geometry = owner_road.get("road_geometry", {})
    if geometry.get("shape_type") not in {None, "straight"} or owner_road.get("segments"):
        raise LaneChangeBlocked("lane_change.geometry", "Lane-change road must be straight and unsegmented",
                                actor_id=actor_id, actual=geometry)
    if owner_road.get("traffic_direction") in {"opposite_to_ego", "opposite", "backward"}:
        raise LaneChangeBlocked("lane_change.direction_scope", "Lane-change road must be in the executable ego direction",
                                actor_id=actor_id)
    if lanes.get(source_lane, {}).get("travel_direction") not in _FORWARD or lanes.get(target_lane, {}).get("travel_direction") not in _FORWARD:
        raise LaneChangeBlocked("lane_change.direction_scope", "Both lane-change lanes must travel with ego",
                                actor_id=actor_id)

    duration = _duration(scene, duration_s)
    from _lib.contracts.bounded_execution import timing
    start, end, timing_assumption = timing(scene, actor_id, before['frame_id'], after['frame_id'],
                                          start, end, duration) if duration else (start, end, None)
    if duration is None or not 0 < start < end < duration:
        raise LaneChangeBlocked("lane_change.interval",
                                "Lane-change evidence must leave nonempty pre-change and post-change segments",
                                actor_id=actor_id, actual=[start, end, duration])
    known_speeds = [_number(point.get("speed_mps")) for point in trajectory]
    known_speeds = [value for value in known_speeds if value is not None]
    if known_speeds and max(known_speeds) - min(known_speeds) > 1e-6:
        raise LaneChangeBlocked("lane_change.longitudinal_scope",
                                "Lane-change v1 requires constant or unknown longitudinal speed",
                                actor_id=actor_id, actual=known_speeds)
    return LaneChangeSpec(
        actor_id=actor_id,
        event_id=event.get("event_id"),
        source_lane_id=source_lane,
        target_lane_id=target_lane,
        source_ordinal=source_ordinal,
        target_ordinal=target_ordinal,
        direction="left" if target_ordinal > source_ordinal else "right",
        start_s=start,
        end_s=end,
        duration_s=duration,
        source_frame_id=before["frame_id"],
        target_frame_id=after["frame_id"],
        timing_assumption=timing_assumption,
    )


def from_analysis(analysis):
    return derive(analysis, analysis)


def actor_name(actor_id):
    return "Ego" if actor_id == "ego" else actor_id.replace("vehicle_", "Vehicle_", 1)


def lane_names(scene):
    roads = [row for row in scene.get("road", {}).get("roads", [])
             if row.get("traffic_direction") not in {"opposite_to_ego", "opposite", "backward"}]
    roads = roads or scene.get("road", {}).get("roads", [])[:1]
    return {lane_id: f"Road_{ri + 1}_Seg_1_Lane_{li + 1}"
            for ri, road in enumerate(roads)
            for li, lane_id in enumerate(reversed(road.get("lane_ids", [])))}


def _target_lane_widths(analysis):
    result = {}
    for lane in analysis.get("road", {}).get("lanes", []):
        value = _number(lane.get("width_m"))
        if value is not None and value > 0:
            result[lane.get("lane_id")] = value
    return result


def audit_target(analysis, target):
    """Independently re-derive and verify the exact MELA timeline and safety."""
    spec = from_analysis(analysis)
    if spec is None:
        return None
    names = lane_names(analysis)
    widths = _target_lane_widths(analysis)
    if spec.source_lane_id not in names or spec.target_lane_id not in names:
        raise ValueError("lane-change source or target lane is absent from executable highway")
    if spec.source_lane_id not in widths or spec.target_lane_id not in widths:
        raise ValueError("lane-change metric lane width missing")
    actors = {row["name"]: row for row in target["scenario"]["vehicles"]}
    name = actor_name(spec.actor_id)
    if name not in actors:
        raise ValueError("lane-change actor missing from target")
    timeline = actors[name].get("timeline") or []
    expected_times = [spec.start_s, spec.end_s, spec.duration_s]
    if ([row.get("type") for row in timeline] != ["drive_straight", "lane_change", "drive_straight"]
            or [row.get("transition", {}).get("type") for row in timeline] != ["simulation_time"] * 3
            or any(abs(_number(row["transition"].get("value")) - value) > 1e-7
                   for row, value in zip(timeline, expected_times))):
        raise ValueError("lane-change target timeline changed")
    source_name, target_name = names[spec.source_lane_id], names[spec.target_lane_id]
    if (timeline[0]["transition"].get("reference_name") != source_name
            or any(row["transition"].get("reference_name") != target_name for row in timeline[1:])):
        raise ValueError("lane-change target lane reference changed")
    if timeline[1]["action"].get("direction") != spec.direction:
        raise ValueError("lane-change direction changed")
    displacement = (widths[spec.source_lane_id] + widths[spec.target_lane_id]) / 2
    lateral_speed = round(displacement / (spec.end_s - spec.start_s) * 3.6, 4)
    if abs(_number(timeline[1]["action"].get("start_lateral_velocity")) - lateral_speed) > 1e-7:
        raise ValueError("lane-change lateral velocity changed")
    for index, row in enumerate(timeline):
        if (index == 0) != (row["action"].get("start_position") is not None):
            raise ValueError("lane-change target contains a teleport")

    from _lib.delivery.motion_check import at, decode
    decoded = {key: decode(value, spec.duration_s) for key, value in actors.items()}
    lane_centres = {}
    for road in [row for row in analysis["road"]["roads"]
                 if row.get("traffic_direction") not in {"opposite_to_ego", "opposite", "backward"}]:
        edge = 0.0
        for lane_id in road.get("lane_ids", []):
            if lane_id not in widths or lane_id not in names:
                continue
            lane_centres[names[lane_id]] = edge + widths[lane_id] / 2
            edge += widths[lane_id]

    def initial_lateral(vehicle):
        position = vehicle["timeline"][0]["action"]["start_position"]["position"]
        if position["reference_name"] not in lane_centres:
            return None
        value = lane_centres[position["reference_name"]]
        if position.get("lateral_unit") == "m":
            offset = _number(position.get("lateral_value")) or 0.0
            value += offset if position.get("lateral_specifier") == "left_of" else -offset
        return value

    changing_start = initial_lateral(actors[name])
    changing_end = lane_centres[target_name]
    if changing_start is None:
        raise ValueError("lane-change actor initial lateral position is unauditable")

    def changing_lateral(time_s):
        if time_s <= spec.start_s:
            return changing_start
        if time_s >= spec.end_s:
            return changing_end
        ratio = (time_s - spec.start_s) / (spec.end_s - spec.start_s)
        return changing_start + (changing_end - changing_start) * ratio

    source_actors = {actor_name(row["actor_id"]): row
                     for row in [analysis["ego"], *analysis.get("actors", [])]}
    from _lib.contracts.v3 import fallback_policy
    policy = fallback_policy()["straight_preview"]
    minimum_length = _number(policy.get("envelope_length_m"))
    minimum_width = _number(policy.get("envelope_width_m"))
    if minimum_length is None or minimum_width is None or minimum_length <= 0 or minimum_width <= 0:
        raise ValueError("lane-change collision envelope policy is invalid")

    def dimensions(vehicle_name):
        source = source_actors.get(vehicle_name, {})
        return (max(minimum_length, _number(source.get("length_m")) or 0.0),
                max(minimum_width, _number(source.get("width_m")) or 0.0))

    def segment_state(segments, time_s):
        segment = next(row for row in segments if row[0] - 1e-9 <= time_s <= row[1] + 1e-9)
        start, _, _, speed, acceleration = segment
        return speed + acceleration * (time_s - start), acceleration

    def lateral_intervals(lo, hi, other_y, half_width):
        left, right = changing_lateral(lo) - other_y, changing_lateral(hi) - other_y
        cuts = [lo, hi]
        if abs(right - left) > 1e-12:
            for boundary in (-half_width, half_width):
                crossing = lo + (boundary - left) * (hi - lo) / (right - left)
                if lo < crossing < hi:
                    cuts.append(crossing)
        cuts = sorted(set(cuts))
        return [(a, b) for a, b in zip(cuts, cuts[1:])
                if abs(changing_lateral((a + b) / 2) - other_y) <= half_width + 1e-9]

    changing_length, changing_width = dimensions(name)
    changing_ref = timeline[0]["action"]["start_position"]["position"]["reference_name"]
    changing_road = changing_ref.split("_Seg_", 1)[0]
    for other_name, other in actors.items():
        if other_name == name:
            continue
        other_position = other["timeline"][0]["action"].get("start_position")
        if not other_position:
            raise ValueError("target actor initial position is unauditable")
        other_ref = other_position["position"].get("reference_name", "")
        if other_ref.split("_Seg_", 1)[0] != changing_road:
            continue
        other_y = initial_lateral(other)
        if other_y is None:
            raise ValueError("target actor lateral position is unauditable")
        other_length, other_width = dimensions(other_name)
        half_length = (changing_length + other_length) / 2
        half_width = (changing_width + other_width) / 2
        boundaries = sorted({0.0, spec.start_s, spec.end_s, spec.duration_s,
                             *(value for segment in decoded[name] for value in segment[:2]),
                             *(value for segment in decoded[other_name] for value in segment[:2])})
        for lo, hi in zip(boundaries, boundaries[1:]):
            if hi <= lo:
                continue
            for overlap_start, overlap_end in lateral_intervals(lo, hi, other_y, half_width):
                midpoint = (overlap_start + overlap_end) / 2
                speed, acceleration = segment_state(decoded[name], midpoint)
                other_speed, other_acceleration = segment_state(decoded[other_name], midpoint)
                dx = at(decoded[name], overlap_start) - at(decoded[other_name], overlap_start)
                dv = speed - acceleration * (midpoint - overlap_start)
                dv -= other_speed - other_acceleration * (midpoint - overlap_start)
                da = acceleration - other_acceleration
                span = overlap_end - overlap_start
                values = [dx, dx + dv * span + 0.5 * da * span * span]
                if abs(da) > 1e-12:
                    vertex = -dv / da
                    if 0 < vertex < span:
                        values.append(dx + dv * vertex + 0.5 * da * vertex * vertex)
                if min(values) <= half_length and max(values) >= -half_length:
                    raise ValueError(f"lane-change collision envelope overlap: {spec.actor_id}/{other_name}")
    return {
        "status": "passed",
        "contract": CONTRACT,
        "actor_id": spec.actor_id,
        "transition_interval_s": [spec.start_s, spec.end_s],
        "lateral_displacement_m": displacement,
        "lateral_speed_kmh": lateral_speed,
        "collision_envelope": "continuous_passed",
        "render_verification": "not_executed",
    }
