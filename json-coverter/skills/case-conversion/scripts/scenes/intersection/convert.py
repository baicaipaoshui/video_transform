#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路口场景转换脚本 -- 读取 media/input/intersection_scene_analysis.json，
按 scenes/intersection/skill.md 规则转换为 Mela 格式 JSON，并自动校验。

共享代码（路径设置 / 枚举映射 / 构造器 / 校验）全部从本 Skill 的 scripts/lib/ 导入。
本脚本只保留路口场景特定的构建逻辑。
"""
import os
import sys
import json

# Windows 控制台 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---- 注入 lib/ 到 sys.path 并导入共享模块 ----
_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from path_setup import MEDIA_INPUT, MEDIA_OUTPUT          # noqa: E402
from _lib.contracts.parking import roadside_execution_type
from enums import COLOR_MAP, VCLASS_MAP                    # noqa: E402
from builders import (configure_ids, uid, mps_to_kmh, mk_orient, mk_pos,  # noqa: E402
                      mk_oriented_point, mk_action, mk_transition,
                      mk_maneuver, decide_direction)
from validators import validate_data, check_downstream_road_network  # noqa: E402
from normalization import (evidence_value, normalized_text,         # noqa: E402
                           lane_boundary_markings, environment_season_topology)

# ============================================================================
# 读取源 JSON
# ============================================================================

INPUT_FILE = "intersection_scene_analysis.json"
OUTPUT_FILE = "intersection_scene_target.json"

INPUT_PATH = os.environ.get("SCENE_ANALYSIS_INPUT", os.path.join(MEDIA_INPUT, INPUT_FILE))
OUTPUT_PATH = os.environ.get("SCENE_TARGET_OUTPUT", os.path.join(MEDIA_OUTPUT, OUTPUT_FILE))
WORKFLOW_V3 = os.environ.get("SCENE_WORKFLOW_CONTRACT") == "video-case-world-behavior"

with open(INPUT_PATH, "r", encoding="utf-8") as f:
    src = json.load(f)
configure_ids(src)

# 构建查找表
src_roads = src["road"]["roads"]
src_lane_list = src["road"]["lanes"]
src_lanes = {l["lane_id"]: l for l in src_lane_list}
src_markings = lane_boundary_markings(
    src_roads, src_lane_list, src["road"]["lane_markings"])
src_junctions = src.get("junctions", [])


def safe_number(value, default=0.0):
    return float(value) if isinstance(value, (int, float)) else float(default)


def numeric(value):
    return float(value) if isinstance(value, (int, float)) else None


_SENTINEL = object()
conversion_fallbacks = []


def require(value, path, default=_SENTINEL):
    if value is None:
        if default is not _SENTINEL:
            conversion_fallbacks.append({"field": path, "value": default, "reason": "executable_field_missing"})
            return default
        raise ValueError(f"executable field missing: {path}")
    return value


def width_value(lane):
    value=lane.get("width_m")
    return safe_number(value.get("value"),3.5) if isinstance(value,dict) else safe_number(value,3.5)

# 车道名映射: lane_id -> "Road_N_Seg_1_Lane_i"
lane_name_map = {}
for road_idx, road in enumerate(src_roads):
    road_name = f"Road_{road_idx + 1}"
    for lane_idx, lane_id in enumerate(road["lane_ids"]):
        lane_name_map[lane_id] = f"{road_name}_Seg_1_Lane_{lane_idx + 1}"
default_lane_name = next(iter(lane_name_map.values()), "Road_1_Seg_1_Lane_1")
def roadside_sides_by_road():
    """Map observed parking or paved strips to executable paved shoulders."""
    if not src_roads:
        return {}
    ego_road_id = src_roads[0]["road_id"]
    default_roads = {ego_road_id}
    for junction in src_junctions:
        for connection in junction.get("lane_connections", []):
            if connection.get("incoming_road_id") == ego_road_id:
                outgoing = connection.get("outgoing_road_id")
                if outgoing:
                    default_roads.add(outgoing)
    result = {}
    for area in src.get("road", {}).get("roadside_areas", []):
        if roadside_execution_type(area) != "paved_shoulder":
            continue
        side = normalized_text(area.get("side"), "")
        if side not in {"left", "right"}:
            continue
        road_ids = {area["road_id"]} if area.get("road_id") else default_roads
        for road_id in road_ids:
            width = evidence_value(area.get("width_m"), None)
            if (not isinstance(width, (int, float)) or width <= 0) and not WORKFLOW_V3:
                width = 2.5
            if not isinstance(width, (int, float)) or width <= 0:
                raise ValueError(f"paved roadside width missing for {road_id}:{side}")
            result.setdefault(road_id, {})[side] = float(width)
    return result


roadside_sides = roadside_sides_by_road()


def mk_parking_shoulder(seg_uid, road_name, side, width):
    return {
        "id": uid(),
        "parent_id": seg_uid,
        "name": f"{road_name}_Seg_1_{side.title()}ParkingArea",
        "description": f"Execution paved curbside parking area on the {side}; provenance is recorded in the conversion receipt",
        "width": width,
        # The current Mela model has no parking shoulder enum. A paved
        # shoulder is the supported representation consumed by OpenX.
        "type": "paved",
    }


def first_scenario_description(default):
    scenarios = src.get("simulation_scenarios") or []
    return scenarios[0].get("description", default) if scenarios else default


def primary_scenario():
    scenarios = src.get("simulation_scenarios") or []
    return scenarios[0] if scenarios and isinstance(scenarios[0], dict) else {}


def speed_mps_from(condition):
    speed_kmh = evidence_value(condition.get("speed_kmh"), None)
    return float(speed_kmh) / 3.6 if isinstance(speed_kmh, (int, float)) else None


def map_color(raw_color):
    raw = str(raw_color or "silver").lower()
    if raw in COLOR_MAP:
        return COLOR_MAP[raw]
    if "red" in raw:
        return "red"
    if "blue" in raw:
        return "blue"
    if "white" in raw:
        return "white"
    if "black" in raw or "dark" in raw:
        return "black"
    if "gray" in raw or "grey" in raw:
        return "grey"
    return "silver"


def map_vclass(subtype):
    raw = str(subtype or "car").lower()
    if raw in {'sedan', 'suv', 'truck', 'bus', 'motorcycle', 'bicycle', 'van', 'tractor', 'pickup'}:
        return raw
    for key, value in VCLASS_MAP.items():
        if key in raw:
            return value
    if "truck" in raw:
        return "truck"
    if "van" in raw:
        return "van"
    if "suv" in raw:
        return "suv"
    return "sedan"


primary = primary_scenario()
initial_conditions = primary.get("initial_conditions") or {}
included_actor_ids = set(primary.get("involved_actor_ids") or [])
scenario_duration = safe_number(
    primary.get("duration_s"),
    safe_number(src.get("metadata", {}).get("duration_s"), 5.0),
)

# ============================================================================
# 构建目标 JSON
# ============================================================================

scene_id = uid()
rn_id = uid()
entities_id = uid()
scenario_id = uid()
env_id = uid()

# ---- Scene / RoadNetwork ----

mela_roads = []
for road_idx, road in enumerate(src_roads):
    road_name = f"Road_{road_idx + 1}"
    road_uid = uid()
    seg_uid = uid()
    road_direction = decide_direction(
        road.get("road_role", ""), road.get("heading_rad", 0.0))
    road_length = safe_number(
        road.get("road_geometry", {}).get("visible_length_m"), 70.0)
    parking_sides = roadside_sides.get(road.get("road_id"), {})
    mela_lanes = []
    if not road["lane_ids"]:
        # 空车道兜底（skill.md 4.3 规则 7）
        mela_lanes.append({
            "id": uid(),
            "parent_id": seg_uid,
            "name": f"{road_name}_Seg_1_Lane_1",
            "description": "Default lane for road with unobserved lanes "
                           "(lane_ids empty in source)",
            "index": 0,
            "width": 3.5,
            "type": "driving",
            "direction": road_direction,
            "allowed_maneuvers": [],
            "left_lane_marking_type": "solid",
            "left_lane_marking_color": "white",
            "right_lane_marking_type": "solid",
            "right_lane_marking_color": "white",
        })
    else:
        for lane_idx, lane_id in enumerate(road["lane_ids"]):
            lane = src_lanes[lane_id]
            lane_uid = uid()
            lane_markings = src_markings[lane_id]
            left_type = lane_markings["left"]
            right_type = lane_markings["right"]
            mela_lanes.append({
                "id": lane_uid,
                "parent_id": seg_uid,
                "name": lane_name_map[lane_id],
                "description": f"Lane {lane_idx + 1} of {road_name}, "
                               f"{lane.get('lane_type', 'driving')} lane",
                "index": lane_idx,
                "width": width_value(lane),
                "type": lane.get("lane_type", "driving"),
                "direction": {'same_direction':'forward','opposite_direction':'backward'}.get(
                    lane.get('travel_direction'), lane.get('travel_direction',road_direction)),
                "allowed_maneuvers": lane.get("maneuver_permissions", []),
                "left_lane_marking_type": left_type,
                "left_lane_marking_color": "white",
                "right_lane_marking_type": right_type,
                "right_lane_marking_color": "white",
            })
    mela_roads.append({
        "id": road_uid,
        "name": road_name,
        "description": f"{road.get('road_role', 'intersection_arm')} road, "
                       f"{road.get('road_type', 'urban_road')}",
        "parent_id": rn_id,
        "connection_points": {},
        "segments": [{
            "id": seg_uid,
            "parent_id": road_uid,
            "name": f"{road_name}_Seg_1",
            "description": f"First segment of {road_name}, straight",
            "order_index": 0,
            "length": road_length,
            "shape_type": "straight",
            "road_radius": None,
            "lanes": mela_lanes,
            "left_shoulder": mk_parking_shoulder(seg_uid, road_name, "left", parking_sides["left"]) if "left" in parking_sides else None,
            "right_shoulder": mk_parking_shoulder(seg_uid, road_name, "right", parking_sides["right"]) if "right" in parking_sides else None,
            "left_sidewalk": None,
            "right_sidewalk": None,
        }],
        "length": road_length,
    })

# Junction
mela_junctions = []
for j_idx, jct in enumerate(src_junctions):
    j_name = f"Junction_{j_idx + 1}"
    n_in = len(jct.get("incoming_roads", []))
    n_out = len(jct.get("outgoing_roads", []))
    num_conn = max(n_in + n_out, 3)
    signalized = bool(jct.get("traffic_signals") or [])
    mela_junctions.append({
        "id": uid(),
        "name": j_name,
        "description": f"{jct.get('junction_type', jct.get('type', 'intersection'))}, "
                       f"signalized={signalized}",
        "parent_id": rn_id,
        "connection_points": {},
        "type": "intersection",
        "num_connections": num_conn,
        "cardinal_positions": ["S", "N", "E", "W"],
    })

if WORKFLOW_V3:
    from topology_connections import connect_junctions
    connect_junctions(src, mela_roads, mela_junctions, fallbacks=conversion_fallbacks)

road_network = {
    "id": rn_id,
    "parent_id": scene_id,
    "name": "Intersection Road Network",
    "description": first_scenario_description(
        "Urban signalized intersection with ego straight-through"),
    "traffic_type": "right_hand_traffic",
    "country": "usa",
    "roads": mela_roads,
    "junctions": mela_junctions,
}

scene = {
    "id": scene_id,
    "name": primary.get("name", "Signalized Intersection Straight-Through"),
    "type": "intersection",
    "description": first_scenario_description("Urban signalized intersection"),
    "road_network": road_network,
    "off_road_surface": None,
}

# ---- Entities (traffic_lights + crosswalks) ----

src_jct = src_junctions[0] if src_junctions else {}
signals = src_jct.get("traffic_signals", [])
crosswalks_src = src_jct.get("crosswalks", [])
ego = src["ego"]
ego_trajectory = ego.get("trajectory") or []
ego_condition = initial_conditions.get("ego") or {}
ego_t0 = ego_trajectory[0] if ego_trajectory else {}
ego_lane_id = ego_t0.get("lane_id") or evidence_value(
    ego_condition.get("lane_id"), None)
ego_lane_name = lane_name_map.get(ego_lane_id)
ego_lane_name = require(ego_lane_name, "ego.lane_id", default=default_lane_name)

traffic_lights = []
for sig in signals:
    st = sig.get("signal_type", "circular")
    if st == "pedestrian":
        shape = "circle"
        structure = "1spot"
    else:
        shape = "circle"
        structure = "Vertical_3spots"
    timeline = sig.get("state_timeline") or []
    if WORKFLOW_V3:
        states = {row.get('state') for row in timeline if isinstance(row, dict)} if timeline else set()
        states.discard(None)
        if len(states) == 1 and states <= {'red', 'yellow', 'green'}:
            state_tl = timeline[0]['state']
        else:
            state_tl = 'green'
            conversion_fallbacks.append({"field": "traffic_signal.state_timeline",
                                         "value": "green", "reason": "executable_field_missing"})
        pos = sig.get("position_ego_m") or {}
        if numeric(pos.get('x')) is None or numeric(pos.get('y')) is None:
            pos = {"x": 30, "y": 0, "z": 5}
            conversion_fallbacks.append({"field": "traffic_signal.position_ego_m",
                                         "value": pos, "reason": "executable_field_missing"})
    else:
        state_tl = timeline[0].get('state', 'green') if timeline else 'green'
        pos = sig.get("position_ego_m", {"x": 30, "y": 0, "z": 5})
    lat_spec = "left_of" if pos.get("y", 0) >= 0 else "right_of"
    lat_val = abs(pos.get("y", 0))
    signal_reference = ego_lane_name
    if WORKFLOW_V3 and sig.get('road_id'):
        signal_road = next((r for r in src_roads if r['road_id'] == sig['road_id']), {})
        signal_reference = require(lane_name_map.get((signal_road.get('lane_ids') or [None])[0]),
                                   'traffic_signal.road_id')
    traffic_lights.append({
        "id": uid(),
        "parent_id": entities_id,
        "name": f"TrafficLight_{len(traffic_lights) + 1}",
        "description": f"{st} signal, state={state_tl}",
        "type": "traffic_light",
        "pose": mk_oriented_point(
            mk_pos(signal_reference, "ahead_of",
                   pos.get("x", 30), lat_spec, lat_val, "m"),
            mk_orient(signal_reference, "facing_towards", 0.0),
        ),
        "color": state_tl,
        "duration": scenario_duration,
        "mode": "on",
        "shape": shape,
        "structure_type": structure,
    })

crosswalks = []
for cw in crosswalks_src:
    related_road_id = cw.get("related_road_id", "road_001")
    cw_pos = cw.get("position_ego_m") or {}
    cw_pos_x = require(numeric(cw_pos.get("x")),
                       f"crosswalk {cw.get('crosswalk_id', '')}.position_ego_m.x",
                       default=25.0)
    cw_y = numeric(cw_pos.get("y")) or 0.0
    lat_s = "left_of" if cw_y >= 0 else "right_of"
    lat_v = abs(cw_y)
    reference_road = next((r for r in src_roads if r['road_id'] == related_road_id), {})
    reference_lane = (reference_road.get('lane_ids') or [None])[0]
    crosswalk_lane_name = require(lane_name_map.get(reference_lane),
                                  f'crosswalk {cw.get("crosswalk_id")}.related_road_id')
    crosswalks.append({
        "id": uid(),
        "parent_id": entities_id,
        "name": f"Crosswalk_{len(crosswalks) + 1}",
        "description": f"Crosswalk on {related_road_id}, width "
                       f"{cw.get('width_m', 3.0)} m",
        "type": "crosswalk",
        "pose": mk_oriented_point(
            mk_pos(crosswalk_lane_name, "ahead_of",
                   cw_pos_x, lat_s, lat_v, "m"),
            mk_orient(crosswalk_lane_name, "facing_towards", 0.0),
        ),
    })

entities = {
    "id": entities_id,
    "scene_id": scene_id,
    "assets": [],
    "traffic_lights": traffic_lights,
    "traffic_signs": [],
    "crosswalks": crosswalks,
}

# ---- Scenario (vehicles + pedestrians) ----

vehicles = []

# --- Ego ---
ego_speed = numeric(ego_t0.get("speed_mps"))
if ego_speed is None:
    ego_speed = speed_mps_from(ego_condition)
ego_speed = require(ego_speed, "ego.speed_mps", default=30/3.6)
ego_vel = mps_to_kmh(ego_speed)
ego_start = ego_t0.get("position_ego_m") or ego_condition.get("position_ego_m") or {}
ego_x = require(numeric(ego_start.get("x")), "ego.position_ego_m.x", default=0.0)
ego_dist = numeric(ego_condition.get("distance_m"))
if ego_dist is None and len(ego_trajectory) > 1:
    end_pos = ego_trajectory[-1].get("position_ego_m") or {}
    end_x = numeric(end_pos.get("x"))
    ego_dist = abs(end_x - ego_x) if end_x is not None else None
if ego_dist is None:
    ego_dist = ego_speed * scenario_duration
ego_start_pos = mk_oriented_point(
    mk_pos(ego_lane_name, "ahead_of", ego_x, "right_of", 0.0, "lane"),
    mk_orient(ego_lane_name, "facing_towards", 0.0),
)
ego_uid = uid()
ego_maneuver = mk_maneuver(
    "Drive straight through junction", "drive_straight",
    mk_action("forward", ego_start_pos, ego_vel, ego_vel, 0.0),
    mk_transition(ego_lane_name, "traveled_distance", ego_dist),
    ego_uid,
    "Constant speed straight through the intersection on green",
)
vehicles.append({
    "id": ego_uid,
    "name": "Ego",
    "description": "Ego vehicle driving straight through signalized "
                   "junction on green",
    "is_ego_actor": True,
    "parent_id": scenario_id,
    "tracking_id": None,
    "vehicle_class": map_vclass(ego.get("vehicle_class") or ego.get("subtype")) if WORKFLOW_V3 else "sedan",
    "color": map_color(ego.get("color")) if WORKFLOW_V3 else "silver",
    "timeline": [ego_maneuver],
})

# --- Actor vehicles ---
for actor in src["actors"]:
    if actor["actor_type"] != "vehicle":
        continue
    aid = actor["actor_id"]
    approved_exclusions = {r['actor_id'] for r in src.get('quality_report',{}).get('intersection_preview',{}).get('background_exclusions',[])}
    if WORKFLOW_V3 and aid in approved_exclusions:
        continue
    condition = initial_conditions.get(aid) or {}
    motion_state = normalized_text(condition.get("motion_state"), "")
    is_static = bool(actor.get("is_static")) or motion_state in {
        "parked", "stationary", "stopped"
    }
    # involved_actor_ids defines dynamic event participation, not visibility.
    # Static vehicles remain renderable background entities.
    if included_actor_ids and aid not in included_actor_ids and not is_static:
        continue
    identity = actor.get("identity_description", "")
    color = map_color(actor.get("color") or identity)
    vclass = map_vclass(actor.get("vehicle_class") or actor.get("subtype") or identity)
    traj = actor.get("trajectory") or []
    t0 = traj[0] if traj else None
    if t0 is None:
        t0 = {"time_s": 0.0, "speed_mps": 0.0, "position_ego_m": {}}
        conversion_fallbacks.append({"field": f"actors.{aid}.trajectory[0]",
                                     "value": "synthetic_t0", "reason": "executable_field_missing"})
    lane_id = t0.get("lane_id") or evidence_value(condition.get("lane_id"), None)
    lane_name = lane_name_map.get(lane_id)
    pos0 = t0.get("position_ego_m") or condition.get("position_ego_m") or {}
    x0 = require(numeric(pos0.get("x")), f"actors.{aid}.position_ego_m.x",
                 default=25.0 + len(vehicles) * 15.0)

    parking_side = normalized_text(actor.get("parking_side"), "")
    parking_layout = actor.get('_execution_parking')
    if is_static and parking_layout:
        lane_name = lane_name_map[parking_layout['reference_lane_id']]
        x0 = parking_layout['s_m']
        lateral_specifier = parking_layout['side'] + '_of'
        lateral_value = parking_layout['offset_m']
        lateral_unit = 'm'
    elif is_static and lane_name is None:
        incoming_lane_ids = src_roads[0].get("lane_ids") or []
        require(incoming_lane_ids, "road.roads[0].lane_ids", default=[])
        if parking_side not in {"left", "right"}:
            y0 = numeric(pos0.get("y"))
            parking_side = "left" if y0 is not None and y0 > 0 else "right"
            conversion_fallbacks.append({"field": f"actors.{aid}.parking_side",
                                         "value": parking_side, "reason": "executable_field_missing"})
        reference_lane_id = (
            incoming_lane_ids[-1] if parking_side == "left"
            else incoming_lane_ids[0]
        ) if incoming_lane_ids else None
        lane_name = require(
            lane_name_map.get(reference_lane_id) if reference_lane_id else None,
            f"actors.{aid}.parking_reference_lane",
            default=default_lane_name,
        )
        lateral_specifier = "left_of" if parking_side == "left" else "right_of"
        reference_road = source_road_for_lane.get(reference_lane_id, src_roads[0]) if "source_road_for_lane" in globals() and reference_lane_id else src_roads[0]
        parking_width = roadside_sides.get(reference_road.get("road_id"), {}).get(parking_side)
        if parking_width is None:
            # Parking areas without an explicit road apply to the ego approach.
            parking_width = roadside_sides.get(src_roads[0].get("road_id"), {}).get(parking_side)
        if parking_width is None:
            parking_width = 2.5
            conversion_fallbacks.append({"field": f"actors.{aid}.parking_width",
                                         "value": 2.5, "reason": "executable_field_missing"})
        lateral_value = round(width_value(src_lanes.get(reference_lane_id, {"width_m": 3.5})) / 2.0 + parking_width / 2.0, 3)
        lateral_unit = "m"
    else:
        lane_name = require(lane_name, f"actors.{aid}.lane_id", default=default_lane_name)
        lateral_specifier = "right_of"
        lateral_value = 0.0
        lateral_unit = "lane"

    if is_static:
        speed0 = 0.0
    else:
        speed0 = numeric(t0.get("speed_mps"))
        if speed0 is None:
            speed0 = speed_mps_from(condition)
        speed0 = require(speed0, f"actors.{aid}.speed_mps", default=30/3.6)

    veh_uid = uid()
    veh = {
        "id": veh_uid,
        "name": ("Vehicle_"+aid[len("vehicle_"):] if aid.startswith("vehicle_") else aid),
        "description": f"{actor.get('subtype', 'car')}, "
                       f"color={actor.get('color')}",
        "is_ego_actor": False,
        "parent_id": scenario_id,
        "tracking_id": None,
        "vehicle_class": vclass,
        "color": color,
        "timeline": [],
    }

    start_pos = mk_oriented_point(
        mk_pos(
            lane_name, "ahead_of", float(x0), lateral_specifier,
            lateral_value, lateral_unit,
        ),
        mk_orient(lane_name, "facing_towards", 0.0),
    )

    if is_static:
        vis_dur = scenario_duration
        veh["timeline"].append(
            mk_maneuver(
                f"{veh['name']} parked", "stop",
                mk_action("forward", start_pos, 0.0, 0.0, 0.0),
                mk_transition(lane_name, "simulation_time", vis_dur),
                veh_uid,
                "Parked / static vehicle",
            )
        )
    else:
        speed_end = numeric(traj[-1].get("speed_mps"))
        speed_end = speed0 if speed_end is None else speed_end
        end_pos = traj[-1].get("position_ego_m") or {}
        x_end = numeric(end_pos.get("x"))
        dist = abs(x_end - x0) if x_end is not None else None
        if not dist:
            dist = max((speed0 + speed_end) / 2.0 * scenario_duration, 0.1)
        vel_s = mps_to_kmh(speed0)
        vel_e = mps_to_kmh(speed_end)
        if abs(speed_end - speed0) < 0.5:
            m_type = "drive_straight"
            accel = 0.0
        else:
            m_type = "accelerate" if speed_end > speed0 else "decelerate"
            dv = speed_end - speed0
            dt = safe_number(
                traj[-1].get("time_s", traj[-1].get("timestamp_s")),
                scenario_duration,
            ) - safe_number(t0.get("time_s", t0.get("timestamp_s")), 0.0)
            accel = round(dv / dt, 4) if dt > 0 else 1.0
        veh["timeline"].append(
            mk_maneuver(
                f"{veh['name']} drive straight", m_type,
                mk_action("forward", start_pos, vel_s, vel_e, accel),
                mk_transition(lane_name, "traveled_distance", dist),
                veh_uid,
                "Moving vehicle through intersection",
            )
        )
    vehicles.append(veh)

# --- Pedestrians ---
pedestrians = []
for actor in src["actors"]:
    if actor["actor_type"] != "pedestrian":
        continue
    ped_uid = uid()
    aid = actor["actor_id"]
    if included_actor_ids and aid not in included_actor_ids:
        continue
    traj = actor.get("trajectory") or []
    condition = initial_conditions.get(aid) or {}
    t0 = traj[0]
    pos0 = t0.get("position_ego_m") or condition.get("position_ego_m") or {}
    y0 = require(numeric(pos0.get("y")), f"actors.{aid}.position_ego_m.y", default=5.0)
    x0 = require(numeric(pos0.get("x")), f"actors.{aid}.position_ego_m.x", default=25.0)
    speed0 = numeric(t0.get("speed_mps"))
    if speed0 is None:
        speed0 = speed_mps_from(condition)
    speed0 = require(speed0, f"actors.{aid}.speed_mps", default=1.2)
    is_static = bool(actor.get("is_static"))

    if y0 >= 0:
        ref_lane = "Road_1_Seg_1_Lane_5"
        lat_spec, lat_val = "left_of", 1.0
    else:
        ref_lane = "Road_1_Seg_1_Lane_1"
        lat_spec, lat_val = "right_of", 1.0

    start_pos = mk_oriented_point(
        mk_pos(ref_lane, "ahead_of", float(x0), lat_spec, lat_val, "lane"),
        mk_orient(ref_lane, "facing_towards", 0.0),
    )

    ped = {
        "id": ped_uid,
        "name": actor["actor_id"].replace("pedestrian_", "Pedestrian_"),
        "description": actor.get("note", "Pedestrian on sidewalk"),
        "is_ego_actor": False,
        "parent_id": scenario_id,
        "tracking_id": None,
        "gender": "male",
        "age_group": "adult",
        "top_wear": "jacket" if "jacket" in str(actor.get("color", ""))
                    else "t_shirt",
        "bottom_wear": "jeans",
        "timeline": [],
    }

    if is_static or speed0 == 0:
        ped["timeline"].append(
            mk_maneuver(
                f"{ped['name']} wait", "wait",
                mk_action("forward", start_pos, 0.0, 0.0, 0.0),
                mk_transition(ref_lane, "simulation_time", 2.0),
                ped_uid,
                "Standing pedestrian",
            )
        )
    else:
        end_pos = traj[-1].get("position_ego_m") or {}
        x_end = numeric(end_pos.get("x"))
        dist = abs(x_end - x0) if x_end is not None else None
        if not dist:
            dist = max(speed0 * scenario_duration, 0.1)
        vel = mps_to_kmh(speed0)
        ped["timeline"].append(
            mk_maneuver(
                f"{ped['name']} walk", "walk",
                mk_action("forward", start_pos, vel, vel, 0.0),
                mk_transition(ref_lane, "traveled_distance", dist),
                ped_uid,
                "Walking pedestrian on sidewalk",
            )
        )
    pedestrians.append(ped)

scenario = {
    "id": scenario_id,
    "name": primary.get("name", "Intersection Straight-Through Scenario"),
    "description": first_scenario_description("Ego straight through signalized intersection"),
    "scene_id": scene_id,
    "vehicles": vehicles,
    "pedestrians": pedestrians,
    "animals": [],
}

# ---- Environment ----
src_env = src.get("environment", {})
weather_raw = normalized_text(src_env.get("target_weather_compatibility") if WORKFLOW_V3 else src_env.get("weather"), "clear")
weather_map = {"sunny":"clear","clear":"clear","cloudy":"overcast","overcast":"overcast","rain":"rain","fog":"fog","snow":"snow","thunderstorm":"thunderstorm"}
weather = weather_map.get(weather_raw,"clear")
visibility=evidence_value(src_env.get("visibility_m"), None)
daytime_raw=normalized_text(
    src_env.get("daytime", src_env.get("daylight")), "afternoon")
daytime_map={"day":"afternoon","daytime":"afternoon","morning":"morning","afternoon":"afternoon","evening":"evening","night":"night"}

environment = {
    "id": env_id,
    "name": "Urban Intersection Environment",
    "description": f"weather={weather_raw}, "
                   f"lighting={evidence_value(src_env.get('lighting'), 'unknown')}, "
                   f"road_surface={evidence_value(src_env.get('road_surface'), 'unknown')}, "
                   f"visibility={visibility if visibility is not None else 'unknown'} m",
    "scene_id": scene_id,
    "daytime": daytime_map.get(daytime_raw, "afternoon"),
    "sun_position": None,
    "weather": weather,
    **environment_season_topology(src_env),
}

# ---- 组装最终 JSON ----
target = {
    "scene": scene,
    "entities": entities,
    "scenario": scenario,
    "environment": environment,
}

# ============================================================================
# 写入文件
# ============================================================================
from motion_timeline import apply_motion_timelines, apply_standard_timelines
if WORKFLOW_V3:
    apply_standard_timelines(src, target, fallbacks=conversion_fallbacks)
apply_motion_timelines(src, target)
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(target, f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    f.write('\n')

print(f"[OK] 目标 JSON 已写入: {OUTPUT_PATH}")

# ---- Write conversion fallbacks ----
fallbacks_path = os.environ.get("SCENE_FALLBACKS_OUTPUT")
if fallbacks_path:
    os.makedirs(os.path.dirname(fallbacks_path), exist_ok=True)
    with open(fallbacks_path, "w", encoding="utf-8") as f:
        json.dump(conversion_fallbacks, f, ensure_ascii=False, indent=2)

# ============================================================================
# 校验
# ============================================================================

print("\n" + "=" * 60)
print("Pydantic 四部分校验")
print("=" * 60)
result = validate_data(target)
print(json.dumps(result, ensure_ascii=False, indent=2))

print("\n" + "=" * 60)
print("下游 OpenX middleware 校验")
print("=" * 60)
ds_errors = check_downstream_road_network(target)
if ds_errors:
    print(f"❌ {len(ds_errors)} 个下游约束违反:")
    for e in ds_errors:
        print(f"  - {e}")
else:
    print("✅ 下游 OpenX middleware 校验通过")

if result["valid"]:
    print("\n✅ Mela/Pydantic 权威格式校验通过！")
    if ds_errors:
        print("⚠️ Scene 脚本发现 OpenX 静态错误；正式 v3 Wrapper 将阻止提交 target。")
else:
    print("\n❌ Mela/Pydantic 格式校验未通过，请检查上方错误信息。")
    raise SystemExit(2)
