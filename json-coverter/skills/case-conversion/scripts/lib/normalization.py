"""Normalize analysis fields and supported road-marking representations."""
from __future__ import annotations

import re


def evidence_value(value, default=None):
    """Return the semantic value from a scalar or `{status, value, ...}` object."""
    if isinstance(value, dict):
        if "value" not in value:
            return default
        return evidence_value(value.get("value"), default)
    return default if value is None else value


def normalized_text(value, default):
    """Return a stripped lower-case scalar suitable for enum mapping."""
    value = evidence_value(value, default)
    if not isinstance(value, str):
        return default
    value = value.strip().lower()
    return value or default


def environment_season_topology(source):
    """Preserve supplied environmental facts using the authoritative enums.

    Missing fields retain the existing compatibility defaults. An explicitly
    supplied unsupported value must never silently become summer/flat.
    """
    from mela_business_logic.scenario_preview.pydantic_classes.environment.environment import Season, Topology
    result = {}
    for field, enum, default, aliases in (
        ('season', Season, 'summer', {'fall': 'autumn'}),
        ('topology', Topology, 'flat', {'uphill': 'hilly', 'downhill': 'hilly'}),
    ):
        raw = evidence_value(source.get(field))
        if raw is None:
            value = default
        elif isinstance(raw, str):
            value = raw.strip().lower()
            value = aliases.get(value, value)
        else:
            raise ValueError(f'environment.{field} must be a supported string')
        try:
            result[field] = enum(value).value
        except ValueError as error:
            raise ValueError(f'unsupported environment.{field}: {raw!r}') from error
    return result


def lane_marking_type(marking, default="solid"):
    """Map either ``type`` or legacy ``marking_type`` to a Mela value."""
    raw = normalized_text(
        marking.get("marking_type", marking.get("type")), "")
    if "dash" in raw or "broken" in raw:
        return "dashed"
    return default


def lane_boundary_markings(roads, lanes, markings):
    """Return ``lane_id -> {left, right}`` for both supported source shapes.

    The legacy representation links a lane's ``*_boundary_id`` to a marking
    carrying ``marking_id``.  The analysis contract also permits relational
    markings such as ``between: [lane_001, lane_002]`` and ``side: right``.
    Road lane order is right-to-left, so a marking between consecutive lanes
    is the left boundary of the lower-index lane and the right boundary of the
    higher-index lane.
    """
    lane_by_id = {lane["lane_id"]: lane for lane in lanes}
    result = {
        lane_id: {"left": "solid", "right": "solid"}
        for lane_id in lane_by_id
    }
    road_by_id = {road.get("road_id"): road for road in roads}
    lane_position = {}
    for road in roads:
        for index, lane_id in enumerate(road.get("lane_ids", [])):
            lane_position[lane_id] = (road.get("road_id"), index)

    by_id = {
        marking["marking_id"]: marking
        for marking in markings
        if isinstance(marking, dict) and marking.get("marking_id")
    }
    for lane_id, lane in lane_by_id.items():
        for side in ("left", "right"):
            boundary_id = lane.get(f"{side}_boundary_id")
            if boundary_id in by_id:
                result[lane_id][side] = lane_marking_type(by_id[boundary_id])

    def assign_between(first, second, marking_type):
        if first not in lane_position or second not in lane_position:
            raise ValueError(
                f"lane marking references unknown lanes: {first!r}, {second!r}")
        first_road, first_index = lane_position[first]
        second_road, second_index = lane_position[second]
        if first_road != second_road or abs(first_index - second_index) != 1:
            raise ValueError(
                f"lane marking 'between' requires adjacent lanes on one road: "
                f"{first!r}, {second!r}")
        right_lane, left_lane = (
            (first, second) if first_index < second_index else (second, first)
        )
        result[right_lane]["left"] = marking_type
        result[left_lane]["right"] = marking_type

    def assign_side(side, marking_type, lane_id=None, road_id=None):
        if side not in {"left", "right"}:
            raise ValueError(f"unsupported lane marking side: {side!r}")
        if lane_id:
            if lane_id not in result:
                raise ValueError(f"lane marking references unknown lane: {lane_id!r}")
            result[lane_id][side] = marking_type
            return
        road = road_by_id.get(road_id) if road_id else (roads[0] if roads else None)
        lane_ids = road.get("lane_ids", []) if road else []
        if not lane_ids:
            raise ValueError("lane marking side cannot be resolved to a road lane")
        # Source road lane_ids are ordered right-to-left.
        outer_lane = lane_ids[0] if side == "right" else lane_ids[-1]
        result[outer_lane][side] = marking_type

    between_pattern = re.compile(
        r"between\s+(lane_[0-9]{3})\s+and\s+(lane_[0-9]{3})", re.I)
    edge_pattern = re.compile(
        r"(left|right)\s+edge\s+of\s+(lane_[0-9]{3})", re.I)
    for marking in markings:
        if not isinstance(marking, dict):
            continue
        marking_type = lane_marking_type(marking)
        between = marking.get("between")
        if isinstance(between, list) and len(between) == 2:
            assign_between(between[0], between[1], marking_type)
            continue
        side = marking.get("side")
        if side:
            assign_side(
                str(side).lower(), marking_type,
                lane_id=marking.get("lane_id"), road_id=marking.get("road_id"))
            continue

        # Older ID-style analyses sometimes describe the relation only in a
        # free-text location while lane boundary IDs are absent.
        location = marking.get("location")
        if isinstance(location, str):
            match = between_pattern.search(location)
            if match:
                assign_between(match.group(1), match.group(2), marking_type)
                continue
            match = edge_pattern.search(location)
            if match:
                assign_side(match.group(1).lower(), marking_type,
                            lane_id=match.group(2))

    return result
