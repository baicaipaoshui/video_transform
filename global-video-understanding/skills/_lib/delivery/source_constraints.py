"""Independent checks of available observations, including incomplete analysis.

No execution defaults are inferred here and no model or lifecycle is invoked.
"""
from _lib.contracts.entry import ego_entry_intervals
from _lib.contracts.parking import actor_parking_binding, parking_area_type
from _lib.delivery.motion_check import decode, at


def check(analysis, target):
    if analysis.get('scene_type') != 'intersection':
        return {'status': 'not_applicable'}
    source_roads = analysis.get('road', {}).get('roads', [])
    target_roads = target['scene']['road_network']['roads']
    road_indices = {r['road_id']: i for i, r in enumerate(source_roads)}
    areas = analysis.get('road', {}).get('roadside_areas', [])
    for index, road in enumerate(source_roads):
        segment = target_roads[index]['segments'][0]
        knowledge = road.get('lane_knowledge') or {}
        kind, count = knowledge.get('kind'), knowledge.get('count')
        actual = len(segment['lanes'])
        if kind in {'exact', 'minimum'} and type(count) is int:
            if actual < count or kind == 'exact' and actual != count:
                raise ValueError('source.lane_count: ' + road['road_id'])
        for area in areas:
            if parking_area_type(area) != 'curbside_parking' or area.get('road_id') != road['road_id']:
                continue
            side = area.get('side')
            if side in {'left', 'right'}:
                shoulder = segment.get(side + '_shoulder')
                if not shoulder or shoulder.get('type') != 'paved':
                    raise ValueError('source.parking_shoulder: ' + road['road_id'] + ':' + side)
    vehicles = {v['name']: v for v in target['scenario']['vehicles']}
    for actor in analysis.get('actors', []):
        name = actor['actor_id'].replace('vehicle_', 'Vehicle_', 1)
        if not actor.get('is_static') or name not in vehicles:
            continue
        road, side = actor_parking_binding(actor, areas)
        road = road or (actor.get('trajectory') or [{}])[0].get('road_id')
        if side is None:
            continue
        pos = vehicles[name]['timeline'][0]['action']['start_position']['position']
        if pos.get('lateral_specifier') != side + '_of' or pos.get('lateral_unit') != 'm':
            raise ValueError('source.parking_side: ' + actor['actor_id'])
        if road in road_indices:
            segment = target_roads[road_indices[road]]['segments'][0]
            lane = segment['lanes'][-1 if side == 'left' else 0]
            if pos['reference_name'] != lane['name']:
                raise ValueError('source.parking_road: ' + actor['actor_id'])
    # Reviewed motion has its own stronger timing/boundary contract.
    quality = analysis.get('quality_report') or {}
    if quality.get('motion_contract') or quality.get('intersection_preview') or quality.get('straight_preview'):
        return {'status': 'passed', 'timing': 'dedicated_motion_contract'}
    duration = analysis.get('metadata', {}).get('duration_s')
    if type(duration) not in (int, float) or duration <= 0:
        return {'status': 'passed', 'timing': 'source_duration_unknown'}
    decoded = {name: decode(vehicle, duration) for name, vehicle in vehicles.items()}
    events = []
    for event_id, lo, hi in ego_entry_intervals(analysis):
        if not 0 <= lo <= hi <= duration:
            raise ValueError('source.entry_interval_outside_video: ' + str(event_id))
        ego = vehicles['Ego']
        reference = ego['timeline'][0]['action']['start_position']['position']['reference_name']
        segments = [s for r in target_roads for s in r['segments']
                    if any(l['name'] == reference for l in s['lanes'])]
        if len(segments) != 1:
            raise ValueError('source.entry_road_reference')
        boundary = segments[0]['length']
        approach_lanes = {lane['name'] for lane in segments[0]['lanes']}
        for name, vehicle in vehicles.items():
            initial = vehicle['timeline'][0]['action']['start_position']['position']
            if initial['reference_name'] in approach_lanes:
                if not 0 <= at(decoded[name], 0) <= boundary:
                    raise ValueError('source.initial_position_outside_road: ' + name)
        motion = decoded['Ego']
        if at(motion, 0) >= boundary or at(motion, duration) < boundary-1e-6:
            raise ValueError('source.entry_unreachable: ' + str(event_id))
        left, right = 0.0, float(duration)
        for _ in range(50):
            mid = (left+right)/2
            if at(motion, mid) < boundary:
                left = mid
            else:
                right = mid
        if not lo-1e-6 <= right <= hi+1e-6:
            raise ValueError('source.entry_time: ' + str(event_id))
        events.append(dict(event_id=event_id, interval_s=[lo, hi], predicted_s=right))
    return dict(status='passed', duration_s=duration, entry_events=events,
                verification='local_prediction_not_rendered')
