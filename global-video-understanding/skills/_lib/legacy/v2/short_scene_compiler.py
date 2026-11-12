#!/usr/bin/env python3
"""Pure deterministic compilers for short video scenes.

This module deliberately performs no file I/O and no model calls so both
workflow versions can share the same topology and actor projection rules.
"""
from __future__ import annotations

import copy


class ShortCompileError(ValueError):
    """A stable, actionable failure from a matched short-video fast path."""

    code = "short_compile_failed"

    def __init__(self, reason, *, actor_id=None, field=None):
        self.reason = reason
        self.actor_id = actor_id
        self.field = field
        details = [reason]
        if actor_id:
            details.append(f"actor_id={actor_id}")
        if field:
            details.append(f"field={field}")
        super().__init__("; ".join(details))

    def diagnostics(self):
        result = {"code": self.code, "reason": self.reason}
        if self.actor_id:
            result["actor_id"] = self.actor_id
        if self.field:
            result["field"] = self.field
        return result


def measurement_value(value, default=None):
    raw = value.get("value") if isinstance(value, dict) else value
    return float(raw) if isinstance(raw, (int, float)) else default


def measurement_confidence(value, default=0.4):
    confidence = value.get("confidence") if isinstance(value, dict) else None
    return float(confidence) if isinstance(confidence, (int, float)) else default


def _position(value, actor_id, field):
    if not isinstance(value, dict):
        raise ShortCompileError("participant position is missing", actor_id=actor_id, field=field)
    x, y = value.get("x"), value.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise ShortCompileError("participant position requires numeric x/y", actor_id=actor_id, field=field)
    return {"x": float(x), "y": float(y)}


def _default_role(actor):
    if actor.get("simulation_role") in {"participant", "background"}:
        return actor["simulation_role"]
    return "participant" if actor.get("actor_type") == "vehicle" and not actor.get("is_static") else "background"


def compile_intersection_topology(facts):
    """Compile normalized intersection facts into roads, lanes and junction links."""
    width = 3.5
    lane_count = int(facts["travel_lane_count"])
    ego_travel = int(facts["ego_lane_from_right"])
    if lane_count < 1 or not 1 <= ego_travel <= lane_count:
        raise ShortCompileError("lane count or ego lane is inconsistent", field="scene_facts")
    lanes = [
        {"lane_id": f"lane_{index:03d}", "road_id": "road_001", "width_m": width,
         "lane_type": "driving", "maneuver_permissions": ["straight"]}
        for index in range(1, lane_count + 1)
    ]
    lane_ids = [lane["lane_id"] for lane in lanes]
    markings = [
        {"marking_id": f"marking_{index:03d}", "marking_type": "dashed_white",
         "between": [lane_ids[index - 1], lane_ids[index]], "road_id": "road_001"}
        for index in range(1, lane_count)
    ]
    cross_out = {"lane_id": f"lane_{lane_count + 1:03d}", "road_id": "road_002",
                 "width_m": width, "lane_type": "driving", "maneuver_permissions": []}
    exit_lanes = [
        {"lane_id": f"lane_{lane_count + 1 + index:03d}", "road_id": "road_003",
         "width_m": width, "lane_type": "driving", "maneuver_permissions": ["straight"]}
        for index in range(1, lane_count + 1)
    ]
    cross_in = {"lane_id": f"lane_{2 * lane_count + 2:03d}", "road_id": "road_004",
                "width_m": width, "lane_type": "driving", "maneuver_permissions": []}
    outgoing_ids = [lane["lane_id"] for lane in exit_lanes]
    return {
        "width": width,
        "lane_count": lane_count,
        "ego_travel": ego_travel,
        "lane_ids": lane_ids,
        "outgoing_ids": outgoing_ids,
        "lanes": [*lanes, cross_out, *exit_lanes, cross_in],
        "markings": markings,
        "cross_out": cross_out,
        "cross_in": cross_in,
    }


def compile_short_intersection_analysis(context, duration):
    """Compile visual facts to executable analysis without another model pass."""
    facts = context["scene_facts"]
    topology = compile_intersection_topology(facts)
    width = topology["width"]
    lane_count = topology["lane_count"]
    ego_travel = topology["ego_travel"]
    lane_ids = topology["lane_ids"]
    outgoing_ids = topology["outgoing_ids"]
    ego_lane = lane_ids[ego_travel - 1]
    ego_exit_lane = outgoing_ids[ego_travel - 1]
    ego_speed = measurement_value(facts.get("ego_speed_mps"), 8.0)
    speed_confidence = measurement_confidence(facts.get("ego_speed_mps"))
    crossing_time = measurement_value(facts.get("ego_stop_line_crossing_s"))
    estimated_stop_x = measurement_value(facts.get("stop_line_x_m"))
    stop_x = round(ego_speed * crossing_time, 3) if crossing_time is not None else estimated_stop_x
    if stop_x is None or stop_x <= 0:
        raise ShortCompileError("stop-line distance is missing", field="scene_facts.stop_line_x_m")
    road_length = float(stop_x)
    ego_crosses = crossing_time is not None and crossing_time < duration
    ego_first = {"time_s": 0.0, "lane_id": ego_lane, "position_ego_m": {"x": 0.0, "y": 0.0},
                 "speed_mps": ego_speed, "status": "estimated", "confidence": speed_confidence}
    ego_last = {"time_s": duration, "lane_id": ego_exit_lane if ego_crosses else ego_lane,
                "position_ego_m": {"x": round(ego_speed * duration, 3), "y": 0.0},
                "speed_mps": ego_speed, "status": "estimated", "confidence": speed_confidence}
    actors = []
    participants = ["ego"]
    conditions = {"ego": {"lane_id": ego_lane, "position_ego_m": {"x": 0.0, "y": 0.0},
                          "speed_kmh": round(ego_speed * 3.6, 3)}}
    for source in context.get("actor_hypotheses", []):
        actor = copy.deepcopy(source)
        actor_id = actor.get("actor_id")
        actor_type = actor.get("actor_type")
        static = bool(actor.get("is_static"))
        role = _default_role(actor)
        actor["simulation_role"] = role
        if actor_type == "vehicle":
            if static and role == "participant":
                raise ShortCompileError("static vehicle cannot be a dynamic participant", actor_id=actor_id,
                                        field="simulation_role")
            speed = measurement_value(actor.get("speed_mps"), 0.0)
            entry = measurement_value(actor.get("intersection_entry_s"))
            if static:
                lane_id = None
                x = measurement_value(actor.get("initial_longitudinal_m"), 0.0)
                roadside_offset = lane_count * width / 2 + 1.5
                y = -roadside_offset if actor.get("parking_side") == "right" else roadside_offset
                travel_index = None
            else:
                travel_index = max(1, min(lane_count, int(actor.get("lane_from_right", 1))))
                lane_id = lane_ids[travel_index - 1]
                x = round(road_length - speed * entry, 3) if entry is not None else measurement_value(actor.get("initial_longitudinal_m"))
                if x is None or not 0 <= x < road_length:
                    raise ShortCompileError("moving vehicle initial position is outside incoming road",
                                            actor_id=actor_id, field="initial_longitudinal_m")
                y = round((travel_index - ego_travel) * width, 3)
            confidence = measurement_confidence(actor.get("speed_mps"))
            first = {"time_s": 0.0, "lane_id": lane_id, "position_ego_m": {"x": x, "y": y},
                     "speed_mps": speed, "status": "estimated", "confidence": confidence}
            actor_crosses = not static and entry is not None and entry < duration
            last_lane = outgoing_ids[travel_index - 1] if actor_crosses else lane_id
            last = {"time_s": duration, "lane_id": last_lane,
                    "position_ego_m": {"x": round(x + (0 if static else speed * duration), 3), "y": y},
                    "speed_mps": speed, "status": "estimated", "confidence": confidence}
            actor["trajectory"] = [first, last]
            if not static and role == "participant":
                participants.append(actor_id)
                conditions[actor_id] = {"lane_id": lane_id, "position_ego_m": dict(first["position_ego_m"]),
                                        "speed_kmh": round(speed * 3.6, 3)}
        elif actor_type == "pedestrian":
            if role == "participant":
                start = _position(actor.get("initial_position_ego_m"), actor_id, "initial_position_ego_m")
                end = _position(actor.get("final_position_ego_m"), actor_id, "final_position_ego_m")
                speed = measurement_value(actor.get("speed_mps"))
                if speed is None or speed < 0:
                    raise ShortCompileError("participant pedestrian speed is missing", actor_id=actor_id,
                                            field="speed_mps")
                confidence = measurement_confidence(actor.get("speed_mps"))
                actor["trajectory"] = [
                    {"frame_id": actor["first_seen_frame_id"], "position_ego_m": start, "speed_mps": speed,
                     "status": "estimated", "confidence": confidence},
                    {"time_s": duration, "position_ego_m": end, "speed_mps": speed,
                     "status": "estimated", "confidence": confidence},
                ]
                participants.append(actor_id)
                conditions[actor_id] = {"position_ego_m": dict(start), "speed_kmh": round(speed * 3.6, 3)}
            else:
                actor["trajectory"] = [{"frame_id": actor["first_seen_frame_id"], "status": "observed"}]
        elif actor_type == "vehicle_group":
            if role == "participant":
                raise ShortCompileError("vehicle groups must be expanded before simulation",
                                        actor_id=actor_id, field="simulation_role")
            actor["trajectory"] = [{"frame_id": actor["first_seen_frame_id"], "status": "observed"}]
        else:
            raise ShortCompileError("unsupported actor type", actor_id=actor_id, field="actor_type")
        actors.append(actor)

    phase_events = []
    for index, phase in enumerate(context.get("event_phases", []), 1):
        start = phase.get("start_s", 0.0)
        end = phase.get("end_s", start)
        phase_events.append({"event_id": f"event_{index:03d}",
                             "event_type": phase.get("phase") or phase.get("name") or "scene_phase",
                             "involved_actor_ids": participants, "start_s": start, "end_s": end,
                             "description": phase.get("description", ""),
                             "evidence": [{"status": "observed", "time_s": start}]})
    cross_out, cross_in = topology["cross_out"], topology["cross_in"]
    exit_lanes = [lane for lane in topology["lanes"] if lane["road_id"] == "road_003"]
    roads = [
        {"road_id": "road_001", "road_role": "incoming", "road_type": "urban_road", "heading_rad": 0.0,
         "road_geometry": {"visible_length_m": road_length}, "lane_ids": lane_ids},
        {"road_id": "road_002", "road_role": "outgoing", "road_type": "urban_road", "heading_rad": 1.5708,
         "road_geometry": {"visible_length_m": road_length}, "lane_ids": [cross_out["lane_id"]]},
        {"road_id": "road_003", "road_role": "outgoing", "road_type": "urban_road", "heading_rad": 0.0,
         "road_geometry": {"visible_length_m": road_length}, "lane_ids": [lane["lane_id"] for lane in exit_lanes]},
        {"road_id": "road_004", "road_role": "incoming", "road_type": "urban_road", "heading_rad": 1.5708,
         "road_geometry": {"visible_length_m": road_length}, "lane_ids": [cross_in["lane_id"]]},
    ]
    junction = {
        "junction_id": "junction_001", "junction_type": "four_leg_crossroads",
        "type": "signalized_intersection", "signalized": True,
        "incoming_roads": [{"road_id": "road_001", "lane_ids": lane_ids},
                           {"road_id": "road_004", "lane_ids": [cross_in["lane_id"]]}],
        "outgoing_roads": [{"road_id": "road_002", "lane_ids": [cross_out["lane_id"]]},
                           {"road_id": "road_003", "lane_ids": outgoing_ids}],
        "lane_connections": [
            {"connection_id": f"connection_{index:03d}", "incoming_road_id": "road_001",
             "incoming_lane_id": source, "outgoing_road_id": "road_003", "outgoing_lane_id": target,
             "maneuver": "straight"}
            for index, (source, target) in enumerate(zip(lane_ids, outgoing_ids), 1)
        ],
        "traffic_signals": [
            {"signal_id": f"signal_{index + 1:03d}", "signal_type": "circular",
             "position_ego_m": {"x": stop_x, "y": 0.0, "z": 5.0},
             "state_timeline": [{"state": "green", "time_s": 0.0}]}
            for index in range(int(facts["traffic_light_count"]))
        ],
        "crosswalks": [
            {"crosswalk_id": f"crosswalk_{index + 1:03d}",
             "related_road_id": "road_001" if index == 0 else "road_003",
             "position_ego_m": {"x": stop_x + index * 6.0, "y": 0.0}, "width_m": 4.0}
            for index in range(int(facts["crosswalk_count"]))
        ],
    }
    roadside_areas = [
        {"area_id": f"roadside_parking_{side}", "area_type": "curbside_parking", "side": side,
         "source": "observed"}
        for side in ("right", "left") if facts.get(f"{side}_parking_strip")
    ]
    limitations = list(context.get("questions_to_verify", []))
    limitations.append("Road length and actor positions are derived from estimated monocular-video speed; absolute scale is not calibrated.")
    metadata = {"duration_s": duration}
    if crossing_time is not None:
        metadata.update({"ego_stop_line_crossing_s": crossing_time,
                         "ego_stop_line_crossing_evidence": facts["ego_stop_line_crossing_s"]})
    derived_from = ["ego_speed_mps", "ego_stop_line_crossing_s"] if crossing_time is not None else ["stop_line_x_m"]
    quality = {"source": "global_visual_facts", "deterministic_compilation": True,
               "derived_measurements": {"incoming_road_length_m": {"value": road_length,
               "status": "estimated", "confidence": speed_confidence, "derived_from": derived_from}}}
    road = {"traffic_direction": "one_way", "lane_count_opposite_direction": 0,
            "travel_lane_count": lane_count, "ego_lane_from_right": ego_travel,
            "roads": roads, "lanes": topology["lanes"], "lane_markings": topology["markings"],
            "roadside_areas": roadside_areas}
    return {"scene_type": "intersection", "metadata": metadata,
            "environment": {"weather": "clear", "daytime": "day", "road_surface": "dry"},
            "road": road, "junctions": [junction], "ego": {"actor_id": "ego", "trajectory": [ego_first, ego_last]},
            "actors": actors, "events": phase_events, "quality_report": quality, "limitations": limitations,
            "simulation_scenarios": [{"scenario_id": "scenario_001",
              "description": context.get("event_hypothesis", {}).get("summary", "Original-video baseline"),
              "involved_actor_ids": participants, "initial_conditions": conditions}]}
