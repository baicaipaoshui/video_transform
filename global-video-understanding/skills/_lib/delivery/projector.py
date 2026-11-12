#!/usr/bin/env python3
"""Independent declarative analysis-to-target semantic projection gate.

This module intentionally imports no converter code or helper.  It compares
stable semantic facts rather than implementation UUIDs.
"""
from __future__ import annotations

import json
from pathlib import Path

from _lib.contracts.parking import roadside_execution_type


class ProjectionError(ValueError):
    pass


_MELA_VEHICLE_CLASSES = frozenset({
    'sedan', 'suv', 'truck', 'bus', 'motorcycle', 'bicycle', 'van', 'tractor', 'pickup',
})


def _known_vehicle_class(value):
    if isinstance(value, dict):
        if value.get('status') == 'unknown':
            return None
        value = value.get('value')
    if isinstance(value, str):
        value = value.strip().lower()
    return value if isinstance(value, str) and value in _MELA_VEHICLE_CLASSES else None


def _number(value):
    if isinstance(value, dict):
        value = value.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _actor_target_name(actor_id, actor_type):
    if actor_id == "ego":
        return "Ego"
    prefix = "Pedestrian_" if actor_type == "pedestrian" else "Vehicle_"
    for source in ("pedestrian_", "vehicle_"):
        if actor_id.startswith(source):
            return prefix + actor_id[len(source):]
    return actor_id


def _direction(value):
    return "backward" if value in {"opposite_to_ego","opposite","backward"} else "forward"


def source_projection(analysis):
    lane_by_id = {lane["lane_id"]: lane for lane in analysis["road"]["lanes"]}
    roads, lane_names = [], {}
    for road_index, road in enumerate(analysis["road"]["roads"], 1):
        lanes = []
        anchors = {anchor['lane_id']: anchor['ordinal_from_right']
                   for anchor in (road.get('lane_knowledge') or {}).get('anchors', [])}
        source_lane_ids = road["lane_ids"]
        target_lane_ids = (list(reversed(source_lane_ids))
                           if analysis["scene_type"] == "highway" else source_lane_ids)
        for lane_index, lane_id in enumerate(target_lane_ids, 1):
            lane = lane_by_id[lane_id]
            source_ordinal = anchors.get(lane_id, source_lane_ids.index(lane_id))
            ordinal = (len(source_lane_ids) - 1 - source_ordinal
                       if analysis["scene_type"] == "highway" else source_ordinal)
            lane_names[lane_id] = f"Road_{road_index}_Seg_1_Lane_{ordinal + 1}"
            direction=lane.get("travel_direction") or road.get("traffic_direction")
            if direction is None and analysis["scene_type"]=="intersection":
                direction="backward" if road.get("road_role")=="outgoing" and abs((road.get("heading_rad") or 0)-1.57079632679)<0.1 else "forward"
            lanes.append({"name": lane_names[lane_id], "index": ordinal,
                          "type": lane.get("lane_type", "driving"), "width": _number(lane.get("width_m")),
                          "direction": _direction(direction)})
        roads.append({"lanes": lanes, "length": _number(road.get("road_geometry", {}).get("visible_length_m")),
                      "road_id": road["road_id"]})
    parking = []
    default_parking_roads={item["road_id"] for item in analysis["road"]["roads"]}
    if analysis["scene_type"]=="intersection" and analysis["road"]["roads"]:
        ego_road=analysis["road"]["roads"][0]["road_id"]; default_parking_roads={ego_road}
        for junction in analysis.get("junctions",[]):
            for connection in junction.get("lane_connections",[]):
                if connection.get("incoming_road_id")==ego_road and connection.get("outgoing_road_id"): default_parking_roads.add(connection["outgoing_road_id"])
    for area in analysis["road"].get("roadside_areas", []):
        if roadside_execution_type(area) == "paved_shoulder":
            for road_id in ({area["road_id"]} if area.get("road_id") else default_parking_roads):
                parking.append({"road_id": road_id, "side": area.get("side"), "width": _number(area.get("width_m"))})
    primary = (analysis.get("simulation_scenarios") or [{}])[0]
    conditions = primary.get("initial_conditions") or {}
    actor_by_id = {item["actor_id"]: item for item in analysis.get("actors", [])}
    actor_by_id[analysis.get("ego", {}).get("actor_id", "ego")] = analysis.get("ego", {})
    actors = []
    for actor_id in primary.get("involved_actor_ids", []):
        actor = actor_by_id.get(actor_id) or (analysis.get("ego") if actor_id == "ego" else None)
        if actor is None:
            raise ProjectionError(f"source scenario references unknown actor {actor_id}")
        actor_type = actor.get("actor_type", "vehicle")
        condition = conditions.get(actor_id, {})
        position = condition.get("position_ego_m", {})
        speed = _number(condition.get("speed_kmh"))
        motion = str(condition.get("motion_state", "")).lower()
        trajectory = actor.get('trajectory') or []
        initially_stopped = (analysis['scene_type'] == 'intersection' and len(trajectory) >= 2 and
                             all(_number(p.get('speed_mps')) == 0 for p in trajectory[:2]))
        actors.append({"name": _actor_target_name(actor_id, actor_type), "type": actor_type,
                       "vehicle_class": _known_vehicle_class(actor.get('vehicle_class')),
                       "lane": lane_names.get(condition.get("lane_id")), "x": _number(position.get("x")),
                       "speed_kmh": speed, "stopped": bool(actor.get("is_static")) or initially_stopped or motion in {"parked", "stationary", "stopped"}})
    events = [{"type": item.get("event_type"), "start_s": _number(item.get("start_s")), "end_s": _number(item.get("end_s")),
               "actors": list(item.get("involved_actor_ids", []))} for item in analysis.get("events", [])]
    facilities = {"traffic_lights": sum(len(item.get("traffic_signals", [])) for item in analysis.get("junctions", [])),
                  "crosswalks": sum(len(item.get("crosswalks", [])) for item in analysis.get("junctions", []))}
    return {"scene_type": analysis["scene_type"], "roads": roads, "parking": parking,
            "actors": actors, "events": events, "facilities": facilities,
            "description": primary.get("description"),
            "junction_signalized": [bool(item.get("traffic_signals") or [])
                                     for item in analysis.get("junctions", [])]}


def target_projection(target):
    roads = []
    parking = []
    for road_index, road in enumerate(target["scene"]["road_network"]["roads"], 1):
        segments = road.get("segments") or []
        if not segments:
            raise ProjectionError(f"target road {road_index} has no segments")
        segment = segments[0]
        roads.append({"lanes": [{"name": lane.get("name"), "index": lane.get("index"),
                                "type": lane.get("type"), "width": _number(lane.get("width")), "direction": lane.get("direction")} for lane in segment.get("lanes", [])],
                      "length": _number(segment.get("length") or road.get("length")), "road_id": None})
        for side in ("left", "right"):
            shoulder = segment.get(f"{side}_shoulder")
            if shoulder is not None:
                parking.append({"road_index": road_index, "side": side, "width": _number(shoulder.get("width")), "type": shoulder.get("type")})
    actors = []
    for actor_type, group in (("vehicle", target["scenario"].get("vehicles", [])), ("pedestrian", target["scenario"].get("pedestrians", []))):
        for actor in group:
            maneuver = (actor.get("timeline") or [{}])[0]
            action = maneuver.get("action") or {}
            position = (action.get("start_position") or {}).get("position") or {}
            actors.append({"name": actor.get("name"), "type": actor_type, "lane": position.get("reference_name"),
                           "vehicle_class": actor.get('vehicle_class') if actor_type == 'vehicle' else None,
                           "x": _number(position.get("longitudinal_value")), "speed_kmh": _number(action.get("start_longitudinal_velocity")),
                           "stopped": maneuver.get("type") == "stop", "maneuver": maneuver.get("type"),
                           "transition": (maneuver.get("transition") or {}).get("type")})
    entities = target.get("entities", {})
    junction_signalized = []
    for junction in target.get("scene", {}).get("road_network", {}).get("junctions", []):
        description = junction.get("description", "")
        if "signalized=True" in description:
            junction_signalized.append(True)
        elif "signalized=False" in description:
            junction_signalized.append(False)
        else:
            junction_signalized.append(None)
    return {"roads": roads, "parking": parking, "actors": actors,
            "facilities": {"traffic_lights": len(entities.get("traffic_lights", [])), "crosswalks": len(entities.get("crosswalks", []))},
            "description": target.get("scenario", {}).get("description"),
            "junction_signalized": junction_signalized}


def compare(analysis, target, tolerance=0.05, road_source="preview_json"):
    if analysis.get("quality_report", {}).get("motion_contract"):
        from _lib.delivery.motion_check import compare as compare_motion
        compare_motion(analysis, target)
    source, rendered = source_projection(analysis), target_projection(target)
    errors = []
    validate_parking_layout(analysis, target, tolerance)
    # Independent of converter helpers: explicit source facts must survive.
    for field, aliases in (('season', {'fall': 'autumn'}),
                           ('topology', {'uphill': 'hilly', 'downhill': 'hilly'})):
        value = analysis.get('environment', {}).get(field)
        if isinstance(value, dict):
            value = value.get('value')
        if value is None:
            continue
        expected = value.strip().lower() if isinstance(value, str) else value
        if isinstance(expected, str):
            expected = aliases.get(expected, expected)
        if target.get('environment', {}).get(field) != expected:
            errors.append(f'environment.{field} changed')
    if len(source["roads"]) != len(rendered["roads"]):
        errors.append("road count changed")
    for index, (left, right) in enumerate(zip(source["roads"], rendered["roads"]), 1):
        if len(left["lanes"]) != len(right["lanes"]):
            errors.append(f"road {index} lane count changed")
            continue
        if road_source=="preview_json" and (left["length"] is None or right["length"] is None or abs(left["length"]-right["length"])>tolerance):
            errors.append(f"road {index} length changed")
        for lane_index, (source_lane, target_lane) in enumerate(zip(left["lanes"], right["lanes"]), 1):
            if source_lane['name'] != target_lane['name'] or source_lane['index'] != target_lane['index']:
                errors.append(f"road {index} lane {lane_index} name/index changed")
            if source_lane["type"] != target_lane["type"]:
                errors.append(f"road {index} lane {lane_index} type changed")
            if source_lane["direction"] != target_lane["direction"]:
                errors.append(f"road {index} lane {lane_index} direction changed")
            if road_source=="preview_json" and (source_lane["width"] is None or target_lane["width"] is None or abs(source_lane["width"] - target_lane["width"]) > tolerance):
                errors.append(f"road {index} lane {lane_index} width changed")
    expected_parking = {(item.get("road_id"), item["side"], item["width"]) for item in source["parking"]}
    actual_sides = [(item["side"], item["width"], item.get("type")) for item in rendered["parking"]]
    if len(expected_parking) != len(actual_sides):
        errors.append("parking shoulder count changed")
    for item in rendered["parking"]:
        if item.get("type") != "paved":
            errors.append("parking was not represented as paved shoulder")
    road_indexes = {r['road_id']:i for i,r in enumerate(source['roads'],1)}
    source_parking_widths=sorted((road_indexes[item['road_id']],item["side"],item["width"]) for item in source["parking"])
    target_parking_widths=sorted((item['road_index'],item["side"],item["width"]) for item in rendered["parking"])
    if len(source_parking_widths)==len(target_parking_widths):
        for (source_road,source_side,source_width),(target_road,target_side,target_width) in zip(source_parking_widths,target_parking_widths):
            if source_road!=target_road or source_side!=target_side or source_width is None or target_width is None or abs(source_width-target_width)>tolerance:
                errors.append("parking shoulder side/width changed")
    expected_actors = {item["name"]: item for item in source["actors"]}
    actual_actors = {item["name"]: item for item in rendered["actors"]}
    # Static roadside entities remain renderable even when not dynamic participants.
    excluded = {r['actor_id'] for r in analysis.get('quality_report',{}).get('intersection_preview',{}).get('background_exclusions',[])}
    background = {_actor_target_name(a["actor_id"], "vehicle"): a for a in analysis.get("actors", [])
                  if a['actor_id'] not in excluded and a.get("actor_type") == "vehicle" and a.get("is_static") and _actor_target_name(a["actor_id"], "vehicle") not in expected_actors}
    for name,actor in background.items():
        item=actual_actors.get(name)
        first=(actor.get("trajectory") or [{}])[0]
        if item is None or not item["stopped"] or item["speed_kmh"] != 0 or item["x"] is None or abs(item["x"]-first.get("position_ego_m",{}).get("x",float("inf")))>tolerance:
            errors.append(f"{name} static background changed")
        expected_actors[name] = (dict(item, vehicle_class=_known_vehicle_class(actor.get('vehicle_class')))
                                 if item else {"lane":None,"stopped":True,"x":None,"speed_kmh":0,
                                               "vehicle_class":_known_vehicle_class(actor.get('vehicle_class'))})
    if set(expected_actors) != set(actual_actors):
        errors.append("actor set changed")
    for name in set(expected_actors) & set(actual_actors):
        left, right = expected_actors[name], actual_actors[name]
        if left['vehicle_class'] is not None and left['vehicle_class'] != right['vehicle_class']:
            errors.append(f"{name} vehicle_class changed")
        for field in ("lane", "stopped"):
            if left[field] != right[field]: errors.append(f"{name} {field} changed")
        for field in ("x", "speed_kmh"):
            if left[field] is None or right[field] is None or abs(left[field] - right[field]) > tolerance:
                errors.append(f"{name} {field} changed")
        if left["stopped"] and (right["maneuver"] != "stop" or right["transition"] != "simulation_time"):
            errors.append(f"{name} static maneuver changed")
    if source["facilities"] != rendered["facilities"]:
        errors.append("facility counts changed")
    if source["junction_signalized"] != rendered["junction_signalized"]:
        errors.append("junction signalized description conflicts with traffic signals")
    if source["description"] and source["description"] != rendered["description"]:
        errors.append("scenario description changed")
    if errors:
        raise ProjectionError("; ".join(errors))
    return {"passed": True, "source": source, "target": rendered}


def validate_parking_layout(analysis, target, tolerance=0.05):
    """Independent geometry checks, beyond replaying the Converter output."""
    actors = {a['name']: a for a in target.get('scenario', {}).get('vehicles', [])}
    source_roads = analysis['road']['roads']
    target_roads = target['scene']['road_network']['roads']
    duration = _number(analysis.get('metadata', {}).get('duration_s'))
    occupied = {}
    for actor in analysis.get('actors', []):
        layout = actor.get('_execution_parking')
        if not layout:
            continue
        aid = actor['actor_id']
        def fail(reason):
            raise ProjectionError(f'parking.{reason}: actors[{aid}]')
        vehicle = actors.get(_actor_target_name(aid, 'vehicle'))
        if vehicle is None:
            fail('actor_missing')
        timeline = vehicle.get('timeline') or []
        if len(timeline) != 1 or timeline[0]['type'] != 'stop':
            fail('static_action')
        action, transition = timeline[0]['action'], timeline[0]['transition']
        if any(_number(action.get(k)) != 0 for k in ('start_longitudinal_velocity', 'end_longitudinal_velocity', 'acceleration')):
            fail('nonzero_speed')
        if transition['type'] != 'simulation_time' or duration is None or abs(transition['value']-duration) > tolerance:
            fail('duration')
        road_index = next((i for i, r in enumerate(source_roads) if r['road_id'] == layout['road_id']), None)
        if road_index is None or road_index >= len(target_roads):
            fail('road_missing')
        segment = target_roads[road_index]['segments'][0]
        side = layout['side']
        if side not in {'left', 'right'}:
            fail('side')
        shoulder = segment.get(side + '_shoulder')
        if not shoulder or shoulder.get('type') != 'paved':
            fail('shoulder_missing')
        # World lane_ids number from the outer right edge inward. The highway
        # converter emits MELA lanes centre-outward (segment lane 0 is the
        # leftmost); intersection keeps the source order (lane 0 is the
        # rightmost). Select the shoulder-adjacent lane for each order.
        if analysis['scene_type'] == 'highway':
            lane = segment['lanes'][0 if side == 'left' else -1]
        else:
            lane = segment['lanes'][-1 if side == 'left' else 0]
        pos = action['start_position']['position']
        if pos['reference_name'] != lane['name'] or pos['lateral_specifier'] != side + '_of' or pos['lateral_unit'] != 'm':
            fail('lateral_reference')
        offset = _number(pos['lateral_value'])
        expected_offset = (lane['width'] + shoulder['width']) / 2
        if offset is None or abs(offset-expected_offset) > tolerance:
            fail('outside_shoulder')
        s = _number(pos['longitudinal_value'])
        length = layout['vehicle_length_m']
        if pos['longitudinal_unit'] != 'm' or pos['longitudinal_specifier'] != 'ahead_of' or s is None or not length/2 <= s <= segment['length']-length/2:
            fail('longitudinal_range')
        if abs(s-layout['s_m']) > tolerance:
            fail('slot_changed')
        key = road_index, side
        for other_s, other_length in occupied.get(key, []):
            if abs(s-other_s) < (length+other_length)/2 + layout['gap_m'] - tolerance:
                fail('vehicle_spacing')
        occupied.setdefault(key, []).append((s, length))
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Compare analysis and MELA target semantics")
    parser.add_argument("--analysis", required=True); parser.add_argument("--target", required=True); parser.add_argument("--road-source",choices=("preview_json","external_xodr"),default="preview_json")
    args = parser.parse_args()
    with Path(args.analysis).open(encoding="utf-8") as stream: analysis = json.load(stream)
    with Path(args.target).open(encoding="utf-8") as stream: target = json.load(stream)
    print(json.dumps(compare(analysis, target, road_source=args.road_source), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
