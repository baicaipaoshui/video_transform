"""构建辅助函数 —— 所有场景共用的 OrientedPoint / Maneuver / Action 构造器。"""
import uuid
import math
import hashlib
import itertools
import json

_namespace = uuid.NAMESPACE_URL
_sequence = itertools.count()


def configure_ids(analysis):
    """Each scene CLI starts a deterministic ID stream from input content."""
    global _namespace, _sequence
    canonical = json.dumps(analysis, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False)
    _namespace = uuid.uuid5(uuid.NAMESPACE_URL, 'mela-preview:' + hashlib.sha256(canonical.encode()).hexdigest())
    _sequence = itertools.count()


def uid():
    """Unique within this conversion; reproducible for fixed input and code."""
    return str(uuid.uuid5(_namespace, str(next(_sequence))))


def mps_to_kmh(v):
    """m/s -> km/h，保留 4 位小数。"""
    return round(v * 3.6, 4)


def mk_orient(ref_name, spec="facing_towards", angle=0.0):
    """构造 orientation 子对象。"""
    return {
        "reference_name": ref_name,
        "specifier": spec,
        "offset_angle": angle,
    }


def mk_pos(ref_name, lon_spec, lon_val, lat_spec, lat_val, lat_unit="lane"):
    """构造 position 子对象。"""
    return {
        "reference_name": ref_name,
        "longitudinal_specifier": lon_spec,
        "longitudinal_value": lon_val,
        "longitudinal_unit": "m",
        "lateral_specifier": lat_spec,
        "lateral_value": lat_val,
        "lateral_unit": lat_unit,
    }


def mk_oriented_point(pos, orient):
    """构造完整 OrientedPoint。"""
    return {"position": pos, "orientation": orient}


def mk_action(direction, start_pos, s_vel, e_vel, accel=0.0,
              turning=0.0, lat_vel=0.0):
    """构造 action 子对象。"""
    return {
        "id": uid(),
        "direction": direction,
        "start_position": start_pos,
        "start_longitudinal_velocity": s_vel,
        "start_lateral_velocity": lat_vel,
        "end_longitudinal_velocity": e_vel,
        "turning_angle": turning,
        "acceleration": accel,
    }


def mk_transition(ref_name, t_type, value):
    """构造 transition 子对象。"""
    return {
        "id": uid(),
        "reference_name": ref_name,
        "type": t_type,
        "value": value,
    }


def mk_maneuver(name, m_type, action, transition, parent_id, desc=None):
    """构造完整 maneuver 对象。"""
    return {
        "id": uid(),
        "name": name,
        "description": desc,
        "parent_id": parent_id,
        "type": m_type,
        "action": action,
        "transition": transition,
    }


def decide_direction(role, heading):
    """
    根据 road_role + heading_rad 判断 lane direction。
    通用规则（skill.md 4.4 节）：
      - 主路（heading ≈ 0）: forward
      - 横向（heading ≈ π/2）:
          outgoing  -> backward  (cross_street_outgoing)
          incoming  -> forward   (cross_street_incoming)
    """
    is_cross = abs(heading - math.pi / 2) < 0.1
    if not is_cross:
        return "forward"
    if role == "outgoing":
        return "backward"
    return "forward"
