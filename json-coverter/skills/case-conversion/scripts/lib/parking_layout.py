"""Deterministic parking scenery in the Converter's private execution copy."""
from __future__ import annotations

import copy
import math
from _lib.contracts.actors import vehicle_class_hint
from _lib.contracts.parking import actor_parking_binding, parking_area_type

POLICY = "parking_scenery_layout.v1"
MARGIN = 6.0
GAP = 2.0


def number(value):
    if isinstance(value, dict):
        value = None if value.get('status') == 'unknown' else value.get('value')
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def allocate(projection, actors, conditions):
    """Allocate only roadside static actors; a known in-lane stop stays in lane.

    No observation schema fields are added. Actor IDs, observed road/side and
    usable longitudinal positions take precedence over deterministic slots.
    """
    data = projection.data
    roads = {r['road_id']: r for r in data['road']['roads']}
    lanes = {l['lane_id']: l for l in data['road']['lanes']}
    candidates = []
    for actor in actors:
        first = (actor.get('trajectory') or [{}])[0]
        initial = conditions.get(actor['actor_id']) or {}
        if not actor.get('is_static'):
            continue
        _, side = actor_parking_binding(actor, data['road']['roadside_areas'])
        lane = first.get('lane_id') or initial.get('lane_id')
        if lane in lanes and side not in {'left', 'right'}:
            continue
        candidates.append(actor)
    occupied = {}
    for actor in sorted(candidates, key=lambda a: a['actor_id']):
        aid = actor['actor_id']; initial = conditions.get(aid) or {}
        first = (actor.get('trajectory') or [{}])[0]
        bound_road, bound_side = actor_parking_binding(actor, data['road']['roadside_areas'])
        road_id = bound_road or first.get('road_id') or initial.get('road_id')
        if road_id not in roads:
            road_id = next(iter(roads))
        road = roads[road_id]
        side = bound_side
        areas = [a for a in data['road']['roadside_areas']
                 if a.get('road_id') == road_id and parking_area_type(a) == 'curbside_parking']
        surfaces = {a['side']: a for a in areas}
        choices = [side] if side in {'left', 'right'} else sorted(surfaces) or ['left', 'right']
        # Conservative envelopes cover large vehicles as well as passenger cars.
        kind = vehicle_class_hint(actor) or actor.get('actor_type')
        length = 12.0 if kind in {'bus', 'truck', 'box_truck', 'tractor'} else 6.0 if kind in {'van', 'minivan'} else 5.0
        # A shared surface width must be stable before assigning any actor.
        width = 3.0 if any((vehicle_class_hint(a) or a.get('actor_type'))
                          in {'bus', 'truck', 'box_truck', 'tractor'} for a in candidates) else 2.5
        if projection.policy is not None:
            configured = projection.defaults['parking_width_m']
            if configured < width:
                from _lib.contracts.conversion_policy import PolicyError
                raise PolicyError('Configured parking width is smaller than the execution vehicle clearance',
                                  'conversion.parking_clearance', configured_width_m=configured, required_width_m=width)
            width = configured
        lo = MARGIN + length / 2
        road_length = number(road['road_geometry']['visible_length_m'])
        preferred = number((first.get('position_ego_m') or {}).get('x'))
        if preferred is None:
            preferred = number((initial.get('position_ego_m') or {}).get('x'))
        forbidden = []
        for junction in data.get('junctions', []):
            for feature in junction.get('crosswalks', []):
                if feature.get('related_road_id', feature.get('road_id')) == road_id:
                    x = number((feature.get('position_ego_m') or {}).get('x'))
                    if x is not None:
                        forbidden.append(x)

        def free(s, chosen):
            return (all(abs(s-x) >= length/2 + other/2 + GAP - 1e-8
                        for x, other in occupied.get((road_id, chosen), []))
                    and all(abs(s-x) >= length/2 + 4.0 for x in forbidden))

        slots = []
        for chosen in choices:
            hi = road_length - MARGIN - length/2
            if preferred is not None and lo <= preferred <= hi and free(preferred, chosen):
                slots.append((0, preferred, chosen))
            s = lo
            while s <= hi + 1e-8:
                if free(s, chosen):
                    slots.append((1, s, chosen)); break
                s += 1.0
        if slots:
            _, s, side = min(slots)
        else:
            # Retain every actor; expand execution capacity explicitly instead
            # of creating an out-of-road placement or dropping the vehicle.
            side = min(choices, key=lambda c: (len(occupied.get((road_id, c), [])), c))
            s = max(lo, road_length + length/2 + GAP)
            while not free(s, side):
                s += 1.0
            projection.set(road['road_geometry'], 'visible_length_m', s + length/2 + MARGIN,
                           f'road.roads[{road_id}].road_geometry.visible_length_m',
                           'parking_execution_capacity_extension')
        if bound_side is None:
            projection.set(actor, 'parking_side', side, f'actors[{aid}].parking_side',
                           'parking_side_policy_default')
        if side not in surfaces:
            area = {'area_type': 'curbside_parking', 'road_id': road_id,
                    'side': side, 'width_m': width}
            projection.set(data['road'], 'roadside_areas', data['road']['roadside_areas'] + [area],
                           'road.roadside_areas', 'parking_execution_shoulder_default')
        else:
            area = surfaces[side]
            if number(area['width_m']) < width:
                if projection.policy is not None:
                    from _lib.contracts.conversion_policy import PolicyError
                    raise PolicyError('Existing parking width conflicts with execution vehicle clearance',
                                      'conversion.parking_clearance', path=f'road.roadside_areas[{road_id}:{side}].width_m')
                projection.set(area, 'width_m', width, f'road.roadside_areas[{road_id}:{side}].width_m',
                               'parking_execution_vehicle_clearance')
        lane_id = road['lane_ids'][-1 if side == 'left' else 0]
        offset = number(lanes[lane_id]['width_m']) / 2 + number(area['width_m']) / 2
        layout = dict(policy=POLICY, road_id=road_id, side=side, reference_lane_id=lane_id,
                      s_m=s, offset_m=offset, vehicle_length_m=length, gap_m=GAP)
        projection.set(actor, '_execution_parking', layout, f'actors[{aid}]._execution_parking',
                       'static_vehicle_parking_layout')
        # Normalize from allocated state before the generic moving-actor path.
        track = copy.deepcopy(actor.get('trajectory') or [{'time_s': 0.0}])
        for point in track:
            point.update(road_id=road_id, lane_id=lane_id,
                         ordinal_from_right=road['lane_ids'].index(lane_id),
                         position_ego_m={'x': s, 'y': offset if side == 'left' else -offset}, speed_mps=0.0)
        projection.set(actor, 'trajectory', track, f'actors[{aid}].trajectory',
                       'static_vehicle_parking_layout')
        occupied.setdefault((road_id, side), []).append((s, length))
