#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高速公路场景转换脚本

读取 media/input/crash_scene_analysis.json，按 references/mapping-rules.md 通用规则
转换为 Mela pydantic 格式 JSON，执行权威四部分模型校验并输出 OpenX 兼容诊断。

共享代码（路径设置 / 枚举映射 / 构造器 / 校验）全部从本 Skill 的 scripts/lib/ 导入。
车辆的车道、位置、速度和 maneuver 均来自结构化 analysis；关键业务字段缺失时失败。
"""
import os
import sys
import json
import math

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
from execution_fallbacks import VEHICLE_CLASSES            # noqa: E402
from builders import (configure_ids, uid, mps_to_kmh, mk_orient, mk_pos,  # noqa: E402
                      mk_oriented_point, mk_action, mk_transition,
                      mk_maneuver)
from validators import validate_data, check_downstream_road_network  # noqa: E402
from normalization import (evidence_value, normalized_text,         # noqa: E402
                           lane_boundary_markings, environment_season_topology)
from motion_timeline import apply_highway_lane_change                # noqa: E402

# ============================================================================
# 读取源 JSON
# ============================================================================

INPUT_FILE = "crash_scene_analysis.json"
OUTPUT_FILE = "highway_scene_target.json"

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

# A divided highway's opposite carriageway must not be rendered as adjacent
# lanes on the ego road when no physical median object can be represented.
# Keep only the ego-direction carriageway in the executable Mela scene; the
# complete divided-road topology remains available in analysis.json.
source_road_for_lane = {
    lane_id: road for road in src_roads for lane_id in road.get("lane_ids", [])
}
mela_source_roads = [
    road for road in src_roads
    if road.get("traffic_direction") not in
    ("opposite_to_ego", "opposite", "backward")
] or src_roads[:1]


def safe_number(value, default=0.0):
    return float(value) if isinstance(value, (int, float)) else float(default)


def width_value(lane):
    value=lane.get("width_m")
    return safe_number(value.get("value"),3.5) if isinstance(value,dict) else safe_number(value,3.5)

# World lane_ids run from the outer right edge toward the centre. MELA's
# zero-based lane index runs from the centre line outward on this right-hand
# traffic road, so reverse each forward carriageway before assigning names.
lane_name_map = {}
for road_idx, road in enumerate(mela_source_roads):
    road_name = f"Road_{road_idx + 1}"
    for lane_idx, lane_id in enumerate(reversed(road["lane_ids"])):
        lane_name_map[lane_id] = f"{road_name}_Seg_1_Lane_{lane_idx + 1}"


def roadside_widths_by_road():
    result={}; default_ids={road["road_id"] for road in mela_source_roads}
    for area in src.get("road",{}).get("roadside_areas",[]):
        if roadside_execution_type(area)!="paved_shoulder": continue
        side=normalized_text(area.get("side"),""); width=evidence_value(area.get("width_m"),None)
        if side not in {"left","right"}: continue
        if (not isinstance(width,(int,float)) or width<=0) and not WORKFLOW_V3: width=2.5
        if not isinstance(width,(int,float)) or width<=0: raise ValueError(f"curbside parking width missing: {side}")
        for road_id in ({area["road_id"]} if area.get("road_id") else default_ids): result.setdefault(road_id,{})[side]=float(width)
    return result


roadside_widths=roadside_widths_by_road()


def parking_shoulder(seg_uid,road_name,side,width):
    return {"id":uid(),"parent_id":seg_uid,"name":f"{road_name}_Seg_1_{side.title()}PavedShoulder",
            "description":f"Execution roadside surface represented as paved shoulder on the {side}; see conversion receipt for provenance","width":width,"type":"paved"}


# ============================================================================
# 辅助函数
# ============================================================================

def map_color(raw_color):
    """源颜色 -> Mela Color 枚举值。"""
    if not raw_color:
        return "silver"
    c = COLOR_MAP.get(raw_color)
    if c:
        return c
    # 处理组合色（如 white/silver_two-tone）
    lower = raw_color.lower()
    if "white" in lower and "silver" in lower:
        return "silver"
    if "white" in lower:
        return "white"
    if "silver" in lower:
        return "silver"
    if "grey" in lower or "gray" in lower:
        return "grey"
    if "black" in lower or "dark" in lower:
        return "black"
    return "silver"


def map_vclass(actor_type, subtype):
    """源 actor_type/subtype -> Mela VehicleClass 枚举值。"""
    # The execution projection has already canonicalized an explicit class.
    # Do not let a broad subtype keyword (or the sedan fallback) override it.
    if isinstance(actor_type, str) and actor_type.lower() in VEHICLE_CLASSES:
        return actor_type.lower()
    # 先查 VCLASS_MAP（基于 subtype 关键词）
    if subtype:
        sub_lower = subtype.lower()
        for key, val in VCLASS_MAP.items():
            if key in sub_lower:
                return val
    # 再基于 actor_type
    type_map = {
        "van": "van", "car": "sedan", "truck": "truck",
        "bus": "bus", "motorcycle": "motorcycle",
    }
    return type_map.get(actor_type, "sedan")


def scenario_description(default):
    scenarios = src.get("simulation_scenarios") or []
    if scenarios and isinstance(scenarios[0], dict):
        return scenarios[0].get("description") or default
    events = src.get("events") or []
    if events and isinstance(events[0], dict):
        return events[0].get("description") or default
    return default


def primary_scenario():
    """Return the primary simulation scenario when the analysis provides one."""
    scenarios = src.get("simulation_scenarios") or []
    return scenarios[0] if scenarios and isinstance(scenarios[0], dict) else {}


def speed_mps_from(condition):
    """Read an optional initial-condition speed expressed in km/h."""
    speed_kmh = evidence_value(condition.get("speed_kmh"), None)
    return float(speed_kmh) / 3.6 if isinstance(speed_kmh, (int, float)) else None


def numeric(value):
    return float(value) if isinstance(value, (int, float)) else None


_SENTINEL = object()
conversion_fallbacks = []


def require(value, path, default=_SENTINEL):
    if value is None:
        if WORKFLOW_V3 and default is not _SENTINEL:
            conversion_fallbacks.append({"field": path, "value": default, "reason": "executable_field_missing"})
            return default
        raise ValueError(f"executable field missing: {path}")
    return value


def trigger_for(actor_id, *types):
    for trigger in primary.get("trigger_events") or []:
        if trigger.get("actor_id") == actor_id and trigger.get("type") in types:
            return trigger
    return None


primary = primary_scenario()
preview_mode = bool(src.get('quality_report', {}).get('straight_preview'))
initial_conditions = primary.get("initial_conditions") or {}
involved_actor_ids = primary.get("involved_actor_ids") or []
actors_by_id = {actor['actor_id']: actor for actor in src['actors']}
loss_actor_id = next((actor_id for actor_id in involved_actor_ids
                      if actor_id != 'ego' and trigger_for(actor_id, 'loss_of_control_onset')), None)
lead_actor_id = next(
    (actor_id for actor_id in involved_actor_ids if actor_id != "ego"
     and actors_by_id.get(actor_id, {}).get('simulation_role') != 'background'
     and not actors_by_id.get(actor_id, {}).get('is_static')), None)
lead_actor_id = loss_actor_id or lead_actor_id


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
for road_idx, road in enumerate(mela_source_roads):
    road_name = f"Road_{road_idx + 1}"
    road_uid = uid()
    seg_uid = uid()
    parking_sides=roadside_widths.get(road.get("road_id"),{})
    road_length = safe_number(road.get("road_geometry", {}).get("visible_length_m"),300.0)

    mela_lanes = []
    if not road["lane_ids"]:
        # 空车道兜底（skill.md 规则：segment 至少 1 条 lane）
        mela_lanes.append({
            "id": uid(),
            "parent_id": seg_uid,
            "name": f"{road_name}_Seg_1_Lane_1",
            "description": "Default lane for road with unobserved lanes",
            "index": 0,
            "width": 3.5,
            "type": "driving",
            "direction": "forward",
            "allowed_maneuvers": ["straight"],
            "left_lane_marking_type": "solid",
            "left_lane_marking_color": "white",
            "right_lane_marking_type": "solid",
            "right_lane_marking_color": "white",
        })
    else:
        for lane_idx, lane_id in enumerate(reversed(road["lane_ids"])):
            lane = src_lanes[lane_id]
            source_road = source_road_for_lane.get(lane_id, road)
            source_direction = source_road.get("traffic_direction", lane.get("travel_direction", "same_as_ego"))
            mela_direction = "backward" if source_direction in ("opposite_to_ego", "opposite", "backward") else "forward"
            lane_uid = uid()
            lane_markings = src_markings[lane_id]
            left_type = lane_markings["left"]
            right_type = lane_markings["right"]
            mela_lanes.append({
                "id": lane_uid,
                "parent_id": seg_uid,
                "name": lane_name_map[lane_id],
                "description": (
                    f"Lane {lane_idx + 1} of {road_name}, "
                    f"{lane.get('lane_type', 'driving')} lane, "
                    f"travel_direction={lane.get('travel_direction', 'same_as_ego')}"),
                "index": lane_idx,
                "width": width_value(lane),
                "type": "driving",
                "direction": mela_direction,
                "allowed_maneuvers": lane.get(
                    "maneuver_permissions", ["straight"]),
                "left_lane_marking_type": left_type,
                "left_lane_marking_color": "white",
                "right_lane_marking_type": right_type,
                "right_lane_marking_color": "white",
            })

    # ---- 对向车道（bidirectional_separated 高速公路）----
    # 对向车辆不加 actor，但道路网络保留对向车道
    src_traffic_dir = src["road"].get("traffic_direction", "")
    n_opposite = 0
    if len(src_roads) == 1 and src_traffic_dir in ("bidirectional_separated", "bidirectional"):
        n_opposite = src["road"].get("lane_count_opposite_direction", 1)
        n_forward = len(mela_lanes)
        for i in range(n_opposite):
            lane_idx = n_forward + i
            is_first = (i == 0)
            is_last = (i == n_opposite - 1)
            # right = 靠中央隔离带（median）侧
            right_m = "solid" if is_first else "dashed"
            # left = 靠路肩（outer edge）侧
            left_m = "solid" if is_last else "dashed"
            if n_opposite == 1:
                right_m = "solid"
                left_m = "solid"
            mela_lanes.append({
                "id": uid(),
                "parent_id": seg_uid,
                "name": f"{road_name}_Seg_1_Lane_{lane_idx + 1}",
                "description": (
                    f"Oncoming lane {i + 1} of {road_name}, "
                    f"backward direction (opposite carriageway)"),
                "index": lane_idx,
                "width": 3.5,
                "type": "driving",
                "direction": "backward",
                "allowed_maneuvers": ["straight"],
                "left_lane_marking_type": left_m,
                "left_lane_marking_color": "white",
                "right_lane_marking_type": right_m,
                "right_lane_marking_color": "white",
            })

    mela_roads.append({
        "id": road_uid,
        "name": road_name,
        "description": (
            f"{road.get('road_role', 'ego_carriageway')} road, "
            f"{road.get('road_type', 'highway')}, "
            f"{'bidirectional' if n_opposite else 'one-way'}, "
            f"heading {road.get('heading_rad', 0.0)} rad"),
        "parent_id": rn_id,
        "connection_points": {},
        "segments": [{
            "id": seg_uid,
            "parent_id": road_uid,
            "name": f"{road_name}_Seg_1",
            "description": (
                f"First segment of {road_name}, straight highway, "
                f"{len(mela_lanes)} lanes"
                + (f" ({n_forward} forward + {n_opposite} backward)"
                   if n_opposite else " same direction")),
            "order_index": 0,
            "length": float(road_length),
            "shape_type": "straight",
            "road_radius": None,
            "lanes": mela_lanes,
            "left_shoulder": parking_shoulder(seg_uid,road_name,"left",parking_sides["left"]) if "left" in parking_sides else None,
            "right_shoulder": parking_shoulder(seg_uid,road_name,"right",parking_sides["right"]) if "right" in parking_sides else None,
            "left_sidewalk": None,
            "right_sidewalk": None,
        }],
        "length": float(road_length),
    })

road_network = {
    "id": rn_id,
    "parent_id": scene_id,
    "name": "Highway Road Network",
    "description": (
        f"Highway road network derived from {len(src_roads)} source "
        f"carriageway(s) and {len(src_lane_list)} source lane(s)"),
    "traffic_type": "right_hand_traffic",
    "country": "unknown",
    "roads": mela_roads,
    "junctions": [],  # 高速公路无路口
}

scene = {
    "id": scene_id,
    "name": primary.get("name") or "Highway Scene",
    "type": "highway",
    "description": scenario_description("Highway scenario"),
    "road_network": road_network,
    "off_road_surface": None,
}

# ---- Entities (highway: no traffic lights/signs/crosswalks) ----

entities = {
    "id": entities_id,
    "scene_id": scene_id,
    "assets": [],
    "traffic_lights": [],
    "traffic_signs": [],
    "crosswalks": [],
}

# ---- Scenario (vehicles) ----

vehicles = []

# --- Ego ---
ego = src["ego"]
ego_initial = initial_conditions.get("ego") or {}
ego_t0 = ego["trajectory"][0]
ego_lane_id = (
    (ego_initial.get('lane_id') if preview_mode else ego_t0.get("lane_id"))
    or ego_initial.get("lane_id")
)
if ego_lane_id is None or ego_lane_id not in lane_name_map:
    first_available = next(iter(lane_name_map), None)
    ego_lane_id = require(first_available, "ego.trajectory[0].lane_id or initial_conditions.ego.lane_id",
                          default=first_available)
ego_lane_name = lane_name_map[ego_lane_id]
ego_speed_mps = numeric(ego_t0.get("speed_mps"))
if preview_mode:
    ego_speed_mps = speed_mps_from(ego_initial)
if ego_speed_mps is None:
    ego_speed_mps = speed_mps_from(ego_initial)
ego_speed_mps = require(ego_speed_mps, "ego.trajectory[0].speed_mps or initial_conditions.ego.speed_kmh",
                        default=80/3.6)
ego_vel = mps_to_kmh(ego_speed_mps)
scenario_duration = numeric(primary.get("duration_s"))
if scenario_duration is None:
    metadata = src.get("metadata") or {}
    scenario_duration = numeric(metadata.get("duration_s"))
    if scenario_duration is None:
        scenario_duration = numeric(metadata.get("source_duration_s"))
    if scenario_duration is None:
        scenario_duration = numeric(metadata.get("video_duration_s"))
following_gap_m = numeric(evidence_value(ego_initial.get("following_gap_m"), None))
collision_trigger = trigger_for("ego", "rear_end_collision")
ego_trigger = trigger_for("ego", "evasive_steering")
collision_trigger_time = numeric((collision_trigger or {}).get("time_s"))
ego_trigger_time = numeric((ego_trigger or {}).get("time_s"))
first_phase_s = (
    collision_trigger_time
    if collision_trigger_time is not None
    else ego_trigger_time if ego_trigger_time is not None else scenario_duration
)

ego_uid = uid()
ego_start_pos = mk_oriented_point(
    mk_pos(ego_lane_name, "ahead_of", 0.0, "right_of", 0.0, "lane"),
    mk_orient(ego_lane_name, "facing_towards", 0.0),
)
if preview_mode:
    offset=ego_initial['lane_center_offset_m']
    ego_start_pos=mk_oriented_point(
        mk_pos(ego_lane_name,'ahead_of',ego_initial['position_ego_m']['x'],
               'left_of' if offset>=0 else 'right_of',abs(offset),'m'),
        mk_orient(ego_lane_name,'facing_towards',0.0))

# First phase follows the observed/estimated initial state until a trigger.
if first_phase_s is None:
    ego_approach_dist = safe_number(
        src_roads[0].get("road_geometry", {}).get("visible_length_m"), 0.0)
    ego_approach_dist = require(ego_approach_dist or None, "road.roads[0].road_geometry.visible_length_m",
                                default=300.0)
else:
    ego_approach_dist = max(ego_speed_mps * first_phase_s, 0.001)
ego_m1 = mk_maneuver(
    ("Ego rear-ends the lead vehicle"
     if collision_trigger else "Ego drives until the first scenario trigger"),
    "drive_straight",
    mk_action("forward", ego_start_pos, ego_vel, ego_vel, 0.0),
    mk_transition(ego_lane_name, "traveled_distance", ego_approach_dist),
    ego_uid,
    (collision_trigger.get("description")
     if collision_trigger else
     f"Ego drives at {ego_vel:g} km/h for {first_phase_s:g} s"
     if first_phase_s is not None
     else f"Ego drives at {ego_vel:g} km/h over the visible road"),
)
ego_timeline = [ego_m1]
if ego_trigger:
    parameters = ego_trigger.get("parameters") or {}
    evasive_duration = numeric(parameters.get("duration_s"))
    lateral_offset = numeric(evidence_value(parameters.get("lateral_offset_m"), None))
    evasive_duration = require(evasive_duration, "primary trigger evasive_steering.parameters.duration_s",
                               default=2.0)
    lateral_offset = require(lateral_offset, "primary trigger evasive_steering.parameters.lateral_offset_m",
                             default=3.5)
    longitudinal = ego_speed_mps * evasive_duration
    distance = math.hypot(longitudinal, lateral_offset)
    angle = math.degrees(math.atan2(abs(lateral_offset), longitudinal))
    direction = "left" if parameters.get("steering_direction") == "left" else "right"
    ego_timeline.append(mk_maneuver(
        "Ego evasive steering",
        "turn",
        mk_action(direction, None, ego_vel, ego_vel, 0.0, turning=angle),
        mk_transition(ego_lane_name, "traveled_distance", distance),
        ego_uid,
        ego_trigger.get("description"),
    ))

vehicles.append({
    "id": ego_uid,
    "name": "Ego",
    "description": f"Ego vehicle on {ego_lane_id}",
    "is_ego_actor": True,
    "parent_id": scenario_id,
    "tracking_id": None,
    "vehicle_class": map_vclass("vehicle", ego.get("vehicle_class") or ego.get("subtype")) if WORKFLOW_V3 else "sedan",
    "color": map_color(ego.get("color")) if WORKFLOW_V3 else "silver",
    "timeline": ego_timeline,
})

# --- Actor vehicles ---
for actor in src["actors"]:
    aid = actor["actor_id"]
    if aid == ego.get("actor_id"):
        continue
    if actor.get("actor_type") == "vehicle_group":
        continue
    if preview_mode and actor.get('simulation_role') == 'background' and aid not in initial_conditions:
        # Builder's reviewed execution scope excludes separated opposing traffic.
        # Its partial observations remain in analysis, with no simulated state.
        continue
    actor_initial = initial_conditions.get(aid) or {}
    is_lead_actor = aid == lead_actor_id
    motion_state=normalized_text(actor_initial.get("motion_state"),"")
    is_static=bool(actor.get("is_static")) or motion_state in {"parked","stationary","stopped"}
    color = map_color(actor.get("color", "silver"))
    vclass = map_vclass(actor.get("vehicle_class") or actor.get("actor_type", "car"),
                        actor.get("subtype", ""))
    traj = actor["trajectory"]
    t0 = traj[0]
    lane_id = t0.get("lane_id") or actor_initial.get("lane_id")
    if preview_mode: lane_id = actor_initial['lane_id']
    if is_lead_actor:
        if lane_id is None or lane_id not in lane_name_map:
            lane_id = require(next(iter(lane_name_map), None), f"initial_conditions.{aid}.lane_id",
                              default=next(iter(lane_name_map), None))
    position = t0.get("position_ego_m") or {}
    x_value = position.get("x") if isinstance(position, dict) else None
    if x_value is None:
        initial_position = actor_initial.get("position_ego_m") or {}
        if isinstance(initial_position, dict):
            x_value = initial_position.get("x")
    if x_value is None and is_lead_actor:
        x_value = following_gap_m
    speed0 = 0.0 if is_static else numeric(t0.get("speed_mps"))
    if speed0 is None:
        speed0 = speed_mps_from(actor_initial)
    if preview_mode:
        x_value=actor_initial['position_ego_m']['x']
        speed0=speed_mps_from(actor_initial)
    if is_lead_actor:
        x_value = require(x_value, f"actors[{aid}].trajectory[0].position_ego_m.x or initial_conditions.ego.following_gap_m",
                          default=25.0)
        speed0 = require(speed0, f"actors[{aid}].trajectory[0].speed_mps or initial_conditions.{aid}.speed_kmh",
                         default=80/3.6)
    # Ordinary v3 conversion has supplied an audited state for every vehicle.
    # Reviewed previews keep their explicitly bound initial conditions.
    parking_side=normalized_text(actor.get("parking_side"),"")
    lateral_specifier="right_of"; lateral_value=0.0; lateral_unit="lane"
    if preview_mode:
        offset=actor_initial['lane_center_offset_m']
        lateral_specifier='left_of' if offset>=0 else 'right_of'
        lateral_value=abs(offset); lateral_unit='m'
    parking_layout = actor.get('_execution_parking')
    if is_static and parking_layout:
        lane_id = parking_layout['reference_lane_id']
        x_value = parking_layout['s_m']
        lateral_specifier = parking_layout['side'] + '_of'
        lateral_value = parking_layout['offset_m']
        lateral_unit = 'm'
    elif is_static and lane_id not in lane_name_map:
        road=mela_source_roads[0]; source_ids=road.get("lane_ids") or []
        if parking_side not in {"left","right"}:
            parking_side = "right"
            conversion_fallbacks.append({"field": f"actors.{aid}.parking_side", "value": "right", "reason": "executable_field_missing"})
        lane_id=source_ids[-1] if parking_side=="left" else (source_ids[0] if source_ids else None)
        if lane_id is None:
            continue
        width=roadside_widths.get(road["road_id"],{}).get(parking_side)
        if width is None:
            width = 2.5
            conversion_fallbacks.append({"field": f"actors.{aid}.parking_width", "value": 2.5, "reason": "executable_field_missing"})
        lateral_specifier="left_of" if parking_side=="left" else "right_of"; lateral_value=round(width_value(src_lanes[lane_id])/2+width/2,3); lateral_unit="m"
    if lane_id not in lane_name_map or x_value is None or speed0 is None:
        continue
    lane_name = lane_name_map[lane_id]
    x0 = float(x_value)

    veh_uid = uid()
    veh_name = aid.replace("vehicle_", "Vehicle_")

    start_pos = mk_oriented_point(
        mk_pos(lane_name, "ahead_of", float(x0),
               lateral_specifier, lateral_value, lateral_unit),
        mk_orient(lane_name, "facing_towards", 0.0),
    )

    if is_lead_actor and not is_static:
        van_vel = mps_to_kmh(speed0)
        loss_trigger = trigger_for(aid, "loss_of_control_onset")
        loss_time = numeric((loss_trigger or {}).get("time_s"))
        van_first_phase_s = loss_time if loss_time is not None else scenario_duration
        if van_first_phase_s is None:
            van_first_phase_s = 5.0
            conversion_fallbacks.append({"field": f"actors.{aid}.duration_s", "value": 5.0, "reason": "executable_field_missing"})
        van_pre_dist = max(speed0 * van_first_phase_s, 0.001)
        van_m1 = mk_maneuver(
            f"{veh_name} drives until the first scenario trigger",
            "drive_straight",
            mk_action("forward", start_pos, van_vel, van_vel, 0.0),
            mk_transition(lane_name, "traveled_distance", van_pre_dist),
            veh_uid,
            f"Lead vehicle drives at {van_vel:g} km/h for {van_first_phase_s:g} s",
        )
        van_timeline = [van_m1]
        if loss_trigger:
            parameters = loss_trigger.get("parameters") or {}
            rotation_rate = numeric(evidence_value(
                parameters.get("rotation_rate_deg_per_s"), None))
            rotation_rate = require(rotation_rate, f"trigger {loss_trigger.get('trigger_id')}.parameters.rotation_rate_deg_per_s",
                                    default=15.0)
            scenario_duration = require(scenario_duration, "simulation_scenarios[0].duration_s or metadata.video_duration_s",
                                        default=5.0)
            turn_duration = scenario_duration - van_first_phase_s
            if turn_duration <= 0:
                turn_duration = 1.0
                conversion_fallbacks.append({"field": f"actors.{aid}.turn_duration", "value": 1.0, "reason": "executable_field_missing"})
            rotation_angle = rotation_rate * turn_duration
            deceleration = parameters.get("deceleration") or {}
            end_speed_kmh = numeric(evidence_value(
                deceleration.get("end_speed_kmh"), van_vel))
            if end_speed_kmh is None or end_speed_kmh < 0:
                end_speed_kmh = van_vel
                conversion_fallbacks.append({"field": f"actors.{aid}.end_speed_kmh", "value": van_vel, "reason": "executable_field_missing"})
            end_speed_mps = end_speed_kmh / 3.6
            longitudinal_acceleration = (
                end_speed_mps - speed0) / turn_duration
            turn_distance = max(
                (speed0 + end_speed_mps) * 0.5 * turn_duration, 0.001)
            rotation_direction = parameters.get("rotation_direction")
            direction = "right" if rotation_direction == "clockwise" else "left"
            van_timeline.append(mk_maneuver(
                f"{veh_name} loss of control",
                "turn",
                mk_action(direction, None, van_vel, end_speed_kmh,
                          longitudinal_acceleration,
                          turning=rotation_angle),
                mk_transition(lane_name, "traveled_distance", turn_distance),
                veh_uid,
                loss_trigger.get("description"),
            ))

        vehicles.append({
            "id": veh_uid,
            "name": veh_name,
            "description": actor.get("identity_description"),
            "is_ego_actor": False,
            "parent_id": scenario_id,
            "tracking_id": None,
            "vehicle_class": vclass,
            "color": color,
            "timeline": van_timeline,
        })
    elif is_static:
        duration=scenario_duration or 5.0
        veh_m=mk_maneuver(f"{veh_name} parked","stop",mk_action("forward",start_pos,0.0,0.0,0.0),mk_transition(lane_name,"simulation_time",duration),veh_uid,"Parked / static vehicle")
        vehicles.append({"id":veh_uid,"name":veh_name,"description":actor.get("identity_description"),"is_ego_actor":False,"parent_id":scenario_id,"tracking_id":None,"vehicle_class":vclass,"color":color,"timeline":[veh_m]})
    else:
        speed_end = numeric(traj[-1].get("speed_mps"))
        if preview_mode: speed_end = speed0
        if speed_end is None:
            speed_end = speed0
        duration = numeric(traj[-1].get("time_s", traj[-1].get("timestamp_s")))
        if preview_mode: duration = scenario_duration
        if duration is None or duration <= 0:
            duration = scenario_duration
        if duration is None:
            continue
        dist = max((speed0 + speed_end) * 0.5 * duration, 0.001)
        vel_s = mps_to_kmh(speed0)
        vel_e = mps_to_kmh(speed_end)
        if abs(speed_end - speed0) < 0.5:
            m_type = "drive_straight"
            accel = 0.0
        else:
            m_type = "decelerate" if speed_end < speed0 else "accelerate"
            dv = speed_end - speed0
            dt = duration - safe_number(
                t0.get("time_s", t0.get("timestamp_s")), 0.0)
            accel = round(dv / dt, 4) if dt > 0 else 0.0

        veh_m = mk_maneuver(
            f"{veh_name} drive straight", m_type,
            mk_action("forward", start_pos, vel_s, vel_e, accel),
            mk_transition(lane_name, "traveled_distance", dist),
            veh_uid,
            actor.get("identity_description"),
        )
        vehicles.append({
            "id": veh_uid,
            "name": veh_name,
            "description": (
                f"{actor.get('subtype', 'vehicle')}, "
                f"color={actor.get('color')}"),
            "is_ego_actor": False,
            "parent_id": scenario_id,
            "tracking_id": None,
            "vehicle_class": vclass,
            "color": color,
            "timeline": [veh_m],
        })

scenario = {
    "id": scenario_id,
    "name": primary.get("name") or "Highway Scenario",
    "description": scenario_description("Highway scenario"),
    "scene_id": scene_id,
    "vehicles": vehicles,
    "pedestrians": [],
    "animals": [],
}

# ---- Environment ----
src_env = src.get("environment", {})
visibility=evidence_value(src_env.get("visibility_m"), None)
weather_raw = normalized_text(src_env.get("target_weather_compatibility") if WORKFLOW_V3 else src_env.get("weather"), "clear")
weather_map = {
    "sunny": "clear", "clear": "clear", "cloudy": "overcast",
    "overcast": "overcast", "rain": "rain", "fog": "fog",
    "snow": "snow", "thunderstorm": "thunderstorm",
}
weather = weather_map.get(weather_raw, "clear")

daytime_raw = normalized_text(
    src_env.get("daytime", src_env.get("daylight")), "day")
# 源 "day" + lighting "上午 08:45" -> morning
daytime_map = {
    "day": "morning", "daytime": "morning", "morning": "morning",
    "afternoon": "afternoon", "evening": "evening", "night": "night",
}
daytime = daytime_map.get(daytime_raw, "morning")

environment = {
    "id": env_id,
    "name": "Highway Environment",
    "description": (
        f"weather={weather_raw}, "
        f"daytime={daytime_raw}, "
        f"lighting={evidence_value(src_env.get('lighting'), 'unknown')}, "
        f"road_surface={evidence_value(src_env.get('road_surface'), 'unknown')}, "
        f"visibility={visibility if visibility is not None else 'unknown'} m"),
    "scene_id": scene_id,
    "daytime": daytime,
    "sun_position": "in_front_of_ego",
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

# Replace only the one changing actor's base straight timeline.  The shared
# interpreter re-derives the spec from analysis; no plan is persisted or read.
from motion_timeline import apply_semantic_motion
apply_semantic_motion(src, target)
apply_highway_lane_change(
    src, target, lane_name_map,
    {lane_id: width_value(src_lanes[lane_id]) for lane_id in lane_name_map},
)

# ============================================================================
# 写入文件
# ============================================================================
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
