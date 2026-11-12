"""Standard v3 straight-motion checks, in video seconds and metres.

Checks numerical consistency, not visual truth. Turns and lane changes need a
different executable mapping and must not silently become straight actions.
"""
from _lib.contracts.entry import ENTRY_TYPES
from _lib.world.topology import finite, topology_index, validate_actor_routes


class UnsupportedMotion(ValueError):
    # Retrying the model cannot add a missing Converter capability.
    code = 'unsupported_scene_type'


def samples(actor):
    points = actor.get('trajectory', [])
    if len(points) < 2:
        if actor.get('is_static'):
            return []
        raise ValueError(f"actor {actor['actor_id']} needs at least two timed motion samples")
    result = []
    for point in points:
        t = finite(point.get('time_s', point.get('timestamp_s')))
        v = finite(point.get('speed_mps'))
        if t < 0 or v < 0 or (result and t <= result[-1][0]):
            raise ValueError(f"actor {actor['actor_id']} needs finite increasing times and nonnegative speeds")
        result.append((t, v, point))
    return result


def check_samples(scene, actor):
    points = samples(actor)
    if not points:
        return
    if actor.get('is_static') and any(v != 0 for _, v, _ in points):
        raise ValueError(f"actor {actor['actor_id']}: static actor has nonzero speed")
    roads, lanes, owners = topology_index(scene)
    validate_actor_routes(scene, [actor])
    previous_lane = None
    offset = 0.0
    positions = []
    for t, v, point in points:
        lane = point.get('lane_id')
        if lane is not None and previous_lane is not None and lane != previous_lane:
            if owners[lane] == owners[previous_lane]:
                raise UnsupportedMotion(f"actor {actor['actor_id']}: intersection lane-change mapping not supported")
            connection = next(c for j in scene['junctions'] for c in j['lane_connections']
                              if c['incoming_lane_id'] == previous_lane and c['outgoing_lane_id'] == lane)
            if connection.get('maneuver') != 'straight':
                raise UnsupportedMotion(f"actor {actor['actor_id']}: intersection turn mapping not supported")
            if actor.get('trajectory_coordinate_system') == 'road_local':
                offset += finite(roads[owners[previous_lane]]['road_geometry']['visible_length_m'])
        if lane is not None:
            previous_lane = lane
        pos = point.get('position_ego_m', {})
        positions.append((finite(pos.get('x')) + offset, finite(pos.get('y'))))
    for index, ((t0, v0, _), (t1, v1, _)) in enumerate(zip(points, points[1:])):
        x0, y0 = positions[index]; x1, y1 = positions[index+1]
        distance = (v0 + v1) * (t1-t0) / 2
        # Explicit numerical consistency tolerance, not an uncertainty bound.
        if abs((x1-x0)-distance) > max(0.1, distance*0.02):
            from _lib.contracts.v3 import ContractError
            raise ContractError(
                f"actor {actor['actor_id']}: speed/time/position disagree at {t0}..{t1}s",
                rule_id='motion.speed_time_position', actor_id=actor['actor_id'])
        lane_change=(scene.get('quality_report',{}).get('intersection_preview',{})
                     .get('actor_paths',{}).get(actor['actor_id']))
        if abs(y1-y0) > 0.1 and not lane_change:
            raise UnsupportedMotion(f"actor {actor['actor_id']}: lateral motion cannot be encoded as straight")


def bind_entry_geometry(analysis, primitives):
    """Bind approach length to evidenced entry time; never leave a 200m default
    in front of an ego that is evidenced entering the junction after 40m.
    The inferred metric is explicitly an estimate derived from motion, not an
    independent visual scale measurement.
    """
    if analysis.get('scene_type') != 'intersection':
        return []
    ego = analysis['ego']
    rows = samples(ego) if any(p.get('actor_id') == 'ego' and p.get('type') in ENTRY_TYPES
                              for p in primitives) else []
    if not rows:
        return []
    roads, _, owners = topology_index(analysis)
    lane = rows[0][2].get('lane_id')
    road = roads[owners[lane]]
    if road.get('road_role') != 'incoming':
        raise ValueError('ego intersection entry must begin on an incoming road')
    x0 = finite(rows[0][2]['position_ego_m']['x'])

    def distance(t):
        if not rows[0][0] <= t <= rows[-1][0]:
            raise ValueError('ego intersection entry time lies outside trajectory coverage')
        x = x0
        for (start, v0, _), (end, v1, _) in zip(rows, rows[1:]):
            dt = max(0, min(t,end)-start)
            x += v0*dt + (v1-v0)/(end-start)*dt*dt/2
        return x

    repairs = []
    events = {e['event_id']:e for e in analysis.get('events', [])}
    for primitive in primitives:
        if primitive.get('actor_id') != 'ego' or primitive.get('type') not in ENTRY_TYPES:
            continue
        event = events[primitive['event_id']]
        interval = event.get('time_range_s',event.get('time_interval_s'))
        if isinstance(interval,dict): interval = [interval['start'],interval['end']]
        lo, hi = map(distance, interval)
        estimate = distance(finite(primitive['trigger_time_s']))
        if estimate <= x0:
            raise ValueError('ego stationary trajectory cannot prove intersection entry')
        geometry = road.setdefault('road_geometry', {})
        length = geometry.get('visible_length_m')
        if length is None:
            geometry['visible_length_m'] = {'value':estimate,'status':'estimated','confidence':0.0,
                'reason':'Derived from estimated ego motion and actor-bound entry frames; not calibrated absolute scale.',
                'event_id':event['event_id']}
            repairs.append({'road_id':road['road_id'],'event_id':event['event_id'],
                            'entry_interval_s':interval,'estimated_length_m':estimate})
        elif not lo-0.1 <= finite(length) <= hi+0.1:
            raise ValueError('ego entry time and incoming road length disagree; repair only road geometry or event/motion evidence')
    return repairs


def compare_target(analysis, target):
    """Independently decode output actions; check every speed sample and time."""
    from _lib.delivery.motion_check import decode, at
    if analysis.get('scene_type') != 'intersection':
        return {'status': 'not_executed', 'reason': 'standard straight-intersection mapping only'}
    duration = finite(analysis['metadata']['duration_s'])
    targets = {a['name']: a for a in target['scenario']['vehicles']}
    involved = set(analysis['simulation_scenarios'][0]['involved_actor_ids'])
    checked = []
    moving = []
    for actor in [analysis['ego'], *analysis.get('actors', [])]:
        aid = actor['actor_id']
        if aid not in involved or actor.get('actor_type') != 'vehicle' or actor.get('is_static'):
            continue
        check_samples(analysis, actor)
        points = samples(actor)
        name = 'Ego' if aid == 'ego' else aid.replace('vehicle_', 'Vehicle_')
        segments = decode(targets[name], duration)
        if abs(points[0][0]) > 1e-8 or abs(points[-1][0]-duration) > 1e-6:
            raise ValueError(f'{aid}: target horizon does not match source samples')
        lane_change=(analysis.get('quality_report',{}).get('intersection_preview',{})
                     .get('actor_paths',{}).get(aid))
        if lane_change:
            timeline=targets[name]['timeline']; start,end=lane_change['transition_interval_s']
            if ([row['type'] for row in timeline] != ['drive_straight','lane_change','drive_straight'] or
                    [row['transition']['value'] for row in timeline] != [start,end,duration] or
                    timeline[1]['action']['direction'] != ('left' if lane_change['lateral_motion']=='changing_left' else 'right') or
                    abs(timeline[1]['action']['start_lateral_velocity']-lane_change['lateral_speed_kmh'])>1e-4):
                raise ValueError(f'{aid}: target lane-change contract changed')
            lane_names={lane_id:f"Road_{ri+1}_Seg_1_Lane_{li+1}"
                        for ri,road in enumerate(analysis['road']['roads'])
                        for li,lane_id in enumerate(road['lane_ids'])}
            if timeline[1]['transition']['reference_name']!=lane_names[lane_change['target_lane_id']]:
                raise ValueError(f'{aid}: target lane-change reference changed')
            for index,row in enumerate(timeline):
                if (index==0) != (row['action']['start_position'] is not None):
                    raise ValueError(f'{aid}: lane-change timeline contains a teleport')
            checked.append(aid);moving.append((actor,segments));continue
        if actor.get('_execution_single_action_motion'):
            timeline = targets[name]['timeline']
            if (len(timeline) != 1 or timeline[0]['type'] not in {'drive_straight','accelerate','decelerate'}
                    or timeline[0]['transition']['type'] != 'traveled_distance'):
                raise ValueError(f'{aid}: estimated motion compatibility action changed')
            expected_distance = sum((v0+v1)*(t1-t0)/2
                                    for (t0,v0,_),(t1,v1,_) in zip(points,points[1:]))
            action = timeline[0]['action']
            start_speed = action['start_longitudinal_velocity']/3.6
            end_speed = action['end_longitudinal_velocity']/3.6
            expected_end = 2*expected_distance/duration-points[0][1]
            if (abs(start_speed-points[0][1])>1e-7
                    or abs(end_speed-expected_end)>1e-7
                    or abs(action['acceleration']-(expected_end-start_speed)/duration)>1e-7
                    or abs(timeline[0]['transition']['value']-expected_distance)>1e-5
                    or abs((start_speed+end_speed)*duration/2-expected_distance)>1e-5):
                raise ValueError(f'{aid}: estimated motion duration or distance changed')
            if aid == 'ego':
                roads, _, owners = topology_index(analysis)
                approach = roads[owners[points[0][2]['lane_id']]]
                boundary = finite(approach['road_geometry']['visible_length_m'])
                for event in analysis.get('events', []):
                    if (event.get('event_type') not in {'intersection_entry','ego_intersection_entry',
                            'vehicle_intersection_entry','ego_intersection_entry_and_crossing'}
                            or 'ego' not in event['involved_actor_ids']):
                        continue
                    interval = event.get('time_range_s',event.get('time_interval_s'))
                    if isinstance(interval,dict): interval=[interval['start'],interval['end']]
                    if not at(segments,interval[0])-0.1 <= boundary <= at(segments,interval[1])+0.1:
                        raise ValueError('target ego does not reach junction within observed entry interval')
            checked.append(aid);moving.append((actor,segments));continue
        cumulative = segments[0][2]
        for (t0, v0, _), (t1, v1, _) in zip(points, points[1:]):
            # Compare the entire piecewise profile on the union of boundaries;
            # equal acceleration phases may span multiple observation samples.
            for start, end, x, speed, acceleration in segments:
                lo, hi = max(t0, start), min(t1, end)
                if hi <= lo + 1e-9:
                    continue
                expected_a = (v1-v0)/(t1-t0)
                if max(abs(speed+acceleration*(lo-start)-(v0+expected_a*(lo-t0))),
                       abs(acceleration-expected_a)) > 1e-4:
                    raise ValueError(f'{aid}: target changed source motion')
            cumulative += (v0+v1)*(t1-t0)/2
            if abs(at(segments, t1)-cumulative) > 1e-4:
                raise ValueError(f'{aid}: target travel distance changed')
        if aid == 'ego':
            roads, _, owners = topology_index(analysis)
            approach = roads[owners[points[0][2]['lane_id']]]
            for event in analysis.get('events', []):
                if event.get('event_type') not in {'intersection_entry','ego_intersection_entry',
                        'vehicle_intersection_entry','ego_intersection_entry_and_crossing'} or 'ego' not in event['involved_actor_ids']:
                    continue
                interval = event.get('time_range_s',event.get('time_interval_s'))
                if isinstance(interval,dict): interval = [interval['start'],interval['end']]
                boundary = finite(approach['road_geometry']['visible_length_m'])
                if not at(segments,interval[0])-0.1 <= boundary <= at(segments,interval[1])+0.1:
                    raise ValueError('target ego does not reach junction within observed entry interval')
        checked.append(aid)
        moving.append((actor, segments))
    # For paths that remain in the same lane, test quadratic extrema as well
    # as sample endpoints: a brief order reversal between samples must fail.
    for i, (left, a) in enumerate(moving):
        left_lanes = {p.get('lane_id') for p in left['trajectory']}
        if len(left_lanes) != 1 or None in left_lanes:
            continue
        for right, b in moving[i+1:]:
            if {p.get('lane_id') for p in right['trajectory']} != left_lanes:
                continue
            initial = at(a, 0)-at(b, 0)
            for sa in a:
                for sb in b:
                    lo, hi = max(sa[0], sb[0]), min(sa[1], sb[1])
                    if hi < lo:
                        continue
                    times = [lo, hi]
                    relative_accel = sa[4]-sb[4]
                    relative_speed = sa[3]+sa[4]*(lo-sa[0])-sb[3]-sb[4]*(lo-sb[0])
                    if abs(relative_accel) > 1e-10:
                        turning = lo-relative_speed/relative_accel
                        if lo < turning < hi:
                            times.append(turning)
                    if any(initial*(at(a,t)-at(b,t)) <= 0 for t in times):
                        raise ValueError('same-lane actor order changed without an executable passing path')
    return {'status': 'passed', 'actors': checked, 'verification': 'local_motion_predicted',
            'render_verification': 'not_executed'}
