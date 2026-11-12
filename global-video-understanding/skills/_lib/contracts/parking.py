"""Canonical roadside-parking vocabulary shared by Builder and Converter."""

from __future__ import annotations

import copy


PARKING_AREA_TYPE_ALIASES = frozenset({
    "curbside_parking",
    "roadside_parking",
    "parallel_parking",
    "parallel_parking_strip",
    "parking",
    "parking_band",
    "parking_strip",
})


def canonical_parking_area_type(value) -> str | None:
    """Return the execution-facing parking type without mutating observations."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return "curbside_parking" if normalized in PARKING_AREA_TYPE_ALIASES else None


def parking_area_type(area: dict) -> str | None:
    """Read either the current ``area_type`` field or its legacy ``type`` alias."""
    if not isinstance(area, dict):
        return None
    return canonical_parking_area_type(
        area.get("area_type") if area.get("area_type") is not None else area.get("type")
    )


def sidewalk_area_type(area: dict) -> str | None:
    """Recognize only an explicit observed sidewalk, never roadside prose."""
    if not isinstance(area, dict):
        return None
    value = area.get("area_type") if area.get("area_type") is not None else area.get("type")
    return "sidewalk" if value == "sidewalk" else None


def validate_sidewalk_area(area: dict, road_ids: set[str], road: dict | None = None) -> None:
    """Keep the new sidewalk approximation tied to a particular evidenced band."""
    if (area.get("type") == "sidewalk" or area.get("area_type") == "sidewalk") and (
            area.get("type") not in (None, "sidewalk")
            or area.get("area_type") not in (None, "sidewalk")):
        raise ValueError("Sidewalk type and area_type declarations disagree")
    if sidewalk_area_type(area) != "sidewalk":
        return
    left, right = area.get("boundary_left"), area.get("boundary_right")
    frames = area.get("evidence_frame_ids")
    if (area.get("road_id") not in road_ids or area.get("side") != "right"
            or not isinstance(left, str) or not left
            or not isinstance(right, str) or not right or left == right
            or not isinstance(frames, list)
            or not all(isinstance(frame, str) and frame for frame in frames)
            or len(set(frames)) < 2):
        raise ValueError("Right sidewalk needs road, side, distinct boundaries and two evidence frames")
    if road is not None:
        lanes = [lane for lane in road.get("lanes", []) if lane.get("road_id") == area["road_id"]
                 and lane.get("lane_type") == "driving"
                 and lane.get("travel_direction") in (None, "forward", "same_as_ego")
                 and lane.get("ordinal_from_right") == 0]
        markings = {item.get("marking_id") for item in road.get("lane_markings", [])}
        reachable = set()
        if len(lanes) == 1:
            edge = lanes[0].get("right_boundary")
            while edge and edge not in reachable:
                reachable.add(edge)
                adjacent = [other for other in road.get("roadside_areas", [])
                            if other is not area and other.get("road_id") == area["road_id"]
                            and other.get("side") == "right"
                            and other.get("boundary_left") == edge
                            and other.get("boundary_right")]
                if len(adjacent) != 1:
                    break
                edge = adjacent[0]["boundary_right"]
        if (len(lanes) != 1 or left not in reachable
                or left not in markings or right not in markings
                or any(right in (lane.get("right_boundary"), lane.get("left_boundary"))
                       for lane in road.get("lanes", []) if lane.get("lane_type") == "driving")):
            raise ValueError("Right sidewalk must connect to the rightmost driving lane through registered roadside boundaries")


def roadside_execution_type(area: dict) -> str | None:
    """Return the private target surface without relabeling the observation."""
    if not isinstance(area, dict):
        return None
    if parking_area_type(area) == "curbside_parking":
        return "paved_shoulder"
    if sidewalk_area_type(area) == "sidewalk":
        return "paved_shoulder"
    value = area.get("area_type") if area.get("area_type") is not None else area.get("type")
    return "paved_shoulder" if value in {"paved_strip", "paved_shoulder",
                                         "continuous_paved_shoulder"} else None


def normalize_unconfirmed_area_references(behavior: dict, areas: list[dict]):
    """Drop only structurally invalid, unconfirmed parking-area references.

    ``roadside_area_id`` is the execution-facing parking binding.  A model may
    correctly observe a static vehicle in a World roadside strip while
    incorrectly using that field for a strip whose parking designation remains
    unknown.  When the referenced World area is unique, unoccupied, explicitly
    unconfirmed for parking, and its road/side do not contradict the actor, the
    area reference can be removed without changing the underlying observation.
    The original candidate remains available in the candidate snapshot and the
    returned repair list provides the commit audit.
    """
    result = copy.deepcopy(behavior)
    repairs = []
    by_id = {}
    duplicates = set()
    for area in areas if isinstance(areas, list) else []:
        area_id = area.get('area_id') if isinstance(area, dict) else None
        if not isinstance(area_id, str):
            continue
        if area_id in by_id:
            duplicates.add(area_id)
        by_id[area_id] = area
    for actor_index, actor in enumerate(result.get('actors', [])):
        if (not isinstance(actor, dict) or actor.get('actor_type') != 'vehicle'
                or actor.get('is_static') is not True):
            continue
        points = actor.get('trajectory') if isinstance(actor.get('trajectory'), list) else []
        first = points[0] if points and isinstance(points[0], dict) else {}
        area_id = actor.get('roadside_area_id') or first.get('roadside_area_id')
        area = by_id.get(area_id)
        if (not isinstance(area_id, str) or area_id in duplicates or not isinstance(area, dict)
                or parking_area_type(area) is not None
                or area.get('parking_designation') not in (None, 'unknown')
                or area.get('occupied_by')):
            continue
        side, road = actor.get('parking_side'), first.get('road_id')
        if side is not None and side != area.get('side'):
            continue
        if road is not None and road != area.get('road_id'):
            continue
        paths = []
        if actor.get('roadside_area_id') == area_id:
            actor['roadside_area_id'] = None
            paths.append(f'actors.{actor_index}.roadside_area_id')
        if first.get('roadside_area_id') == area_id:
            first['roadside_area_id'] = None
            paths.append(f'actors.{actor_index}.trajectory.0.roadside_area_id')
        if paths:
            repairs.append({
                'paths': paths,
                'action': 'drop_unconfirmed_parking_area_reference',
                'before': area_id,
                'after': None,
                'world_area_type': area.get('area_type', area.get('type')),
                'parking_designation': area.get('parking_designation'),
            })
    return result, repairs


def actor_parking_binding(actor, areas):
    """Resolve structured parking evidence; never infer a side from prose."""
    aid = actor.get('actor_id')
    first = (actor.get('trajectory') or [{}])[0]
    area_id = actor.get('roadside_area_id') or first.get('roadside_area_id')
    parking = [a for a in areas if parking_area_type(a) == 'curbside_parking']
    matches = [a for a in parking if (area_id and a.get('area_id') == area_id)
               or aid in (a.get('occupied_by') or [])]
    if area_id and not any(a.get('area_id') == area_id for a in parking):
        raise ValueError(f'parking.area_reference: {aid}: {area_id}')
    bindings = {(a.get('road_id'), a.get('side')) for a in matches}
    if len(bindings) > 1:
        raise ValueError(f'parking.binding_conflict: {aid}')
    road, side = next(iter(bindings), (None, None))
    explicit_side = actor.get('parking_side')
    if explicit_side not in {None, 'left', 'right'}:
        raise ValueError(f'parking.side_invalid: {aid}')
    if side and explicit_side and side != explicit_side:
        raise ValueError(f'parking.side_conflict: {aid}')
    if road and first.get('road_id') and road != first['road_id']:
        raise ValueError(f'parking.road_conflict: {aid}')
    return road, side or explicit_side
