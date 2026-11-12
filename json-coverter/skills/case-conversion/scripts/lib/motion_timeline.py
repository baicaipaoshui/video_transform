"""Compile audited longitudinal plans to Preview absolute-time triggers."""
from builders import mk_action, mk_transition, mk_maneuver
import math
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[5] / 'global-video-understanding' / 'skills'
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


def compact_rows(source, actor, rows):
    """Remove redundant samples only within the same straight motion phase.

    A straight junction connection (including unknown lane while inside it)
    continues the same action. Explicit lane changes and event boundaries
    remain separate; no speed approximation tolerance is used.
    """
    from _lib.contracts.entry import ENTRY_TYPES
    aid = actor['actor_id']
    protected = set()
    for event in source.get('events', []):
        if aid not in event.get('involved_actor_ids', []) or event.get('event_type') in ENTRY_TYPES:
            continue
        interval = event.get('time_range_s', event.get('time_interval_s', []))
        if isinstance(interval, dict):
            interval = [interval.get('start'), interval.get('end')]
        protected.update(t for t in interval if type(t) in (int, float))
    connections = {(c.get('incoming_lane_id'), c.get('outgoing_lane_id'))
                   for j in source.get('junctions', []) for c in j.get('lane_connections', [])
                   if c.get('maneuver') == 'straight'}
    previous_lane = None
    for point in actor.get('trajectory', []):
        lane = point.get('lane_id')
        if lane and previous_lane and lane != previous_lane and (previous_lane, lane) not in connections:
            return rows
        if lane:
            previous_lane = lane
    result = [rows[0]]
    for index in range(1, len(rows)-1):
        t0, v0 = result[-1]; t, v = rows[index]; t1, v1 = rows[index+1]
        a0, a1 = (v-v0)/(t-t0), (v1-v)/(t1-t)
        if abs(a0-a1) > 1e-10 or any(t0 < edge < t1 for edge in protected):
            result.append((t, v))
    result.append(rows[-1])
    return result


def single_distance_action_allowed(source, actor, rows):
    """Use the proven one-action MELA shape only for estimated straight motion."""
    if not actor.get('_execution_single_action_motion') or len(rows) < 2:
        return False
    aid = actor['actor_id']
    from _lib.contracts.entry import ENTRY_TYPES
    if any(aid in event.get('involved_actor_ids', [])
           and event.get('event_type') not in ENTRY_TYPES
           for event in source.get('events', [])):
        return False
    straight = {(c.get('incoming_lane_id'), c.get('outgoing_lane_id'))
                for junction in source.get('junctions', [])
                for c in junction.get('lane_connections', [])
                if c.get('maneuver') == 'straight'}
    roads = {road.get('road_id'): road for road in source.get('road', {}).get('roads', [])}
    owners = {lane_id: road_id for road_id, road in roads.items()
              for lane_id in road.get('lane_ids', [])}
    previous = None
    crossed_junction = False
    for point in actor.get('trajectory', []):
        lane = point.get('lane_id')
        if lane is None and previous:
            crossed_junction = True
            continue
        if lane and previous and lane != previous and (previous, lane) not in straight:
            before, after = roads.get(owners.get(previous), {}), roads.get(owners.get(lane), {})
            straight_across_gap = (crossed_junction and before.get('road_role') == 'incoming'
                                   and after.get('road_role') == 'outgoing'
                                   and before.get('heading_rad') == after.get('heading_rad'))
            if not straight_across_gap:
                return False
        if lane:
            previous = lane
            crossed_junction = False
    return True


def integrated_distance(rows):
    return sum((v0 + v1) * (t1 - t0) / 2
               for (t0, v0), (t1, v1) in zip(rows, rows[1:]))


def apply_semantic_motion(source, target):
    """Compile every accepted knot; lane-change overlay retains its proven shape."""
    decision = source.get('_semantic_motion')
    if decision is None:
        return
    from _lib.contracts.semantic_motion import segments
    from _lib.behavior.lane_change import actor_name
    vehicles = {v['name']: v for v in target['scenario']['vehicles']}
    for plan in decision['plans']:
        actor = vehicles[actor_name(plan['actor_id'])]
        position = actor['timeline'][0]['action']['start_position']
        position['position']['longitudinal_value'] = plan['initial_x_m']
        position['position']['longitudinal_unit'] = 'm'
        position['position']['longitudinal_specifier'] = 'ahead_of'
        lane = position['position']['reference_name']
        timeline = []
        for index, (start, end, _x, v, a) in enumerate(segments(plan)):
            w = v + a*(end-start)
            kind = ('stop' if max(v, w) < 1e-8 else 'accelerate' if a > 1e-8 else
                    'decelerate' if a < -1e-8 else 'drive_straight')
            timeline.append(mk_maneuver(
                f"{plan['actor_id']} semantic phase {index+1}", kind,
                mk_action('forward', position if index == 0 else None, v*3.6, w*3.6, a),
                mk_transition(lane, 'simulation_time', end), actor['id'],
                'Text-grounded execution approximation: ' + plan['assumption']))
        actor['timeline'] = timeline


def apply_highway_lane_change(source, target, lane_name_map, lane_widths):
    """Compile the shared evidence interpretation into exactly three actions."""
    from _lib.behavior.lane_change import actor_name, from_analysis
    spec = from_analysis(source)
    if spec is None:
        return None
    if spec.source_lane_id not in lane_name_map or spec.target_lane_id not in lane_name_map:
        raise ValueError("lane-change source or target lane is absent from executable highway")
    source_width = lane_widths.get(spec.source_lane_id)
    target_width = lane_widths.get(spec.target_lane_id)
    if (type(source_width) not in (int, float) or not math.isfinite(source_width) or source_width <= 0
            or type(target_width) not in (int, float) or not math.isfinite(target_width) or target_width <= 0):
        raise ValueError("lane-change source and target require positive metric widths")
    source_actor = source["ego"] if spec.actor_id == "ego" else next(
        (row for row in source.get("actors", []) if row.get("actor_id") == spec.actor_id), None)
    if source_actor is None:
        raise ValueError("lane-change source actor missing")
    speeds = []
    for point in source_actor.get("trajectory", []):
        speed = point.get("speed_mps")
        speed = speed.get("value") if isinstance(speed, dict) else speed
        if type(speed) not in (int, float) or not math.isfinite(speed) or speed < 0:
            raise ValueError("lane-change execution requires projected finite speeds")
        speeds.append(float(speed))
    if not speeds or max(speeds) - min(speeds) > 1e-6:
        raise ValueError("lane-change execution requires one constant longitudinal speed")
    vehicles = {row["name"]: row for row in target["scenario"]["vehicles"]}
    name = actor_name(spec.actor_id)
    if name not in vehicles:
        raise ValueError("lane-change target actor missing")
    vehicle = vehicles[name]
    old_timeline = vehicle.get("timeline") or []
    if not old_timeline:
        raise ValueError("lane-change target actor has no initial timeline")
    position = old_timeline[0]["action"].get("start_position")
    if position is None:
        raise ValueError("lane-change target actor has no initial position")
    source_reference = lane_name_map[spec.source_lane_id]
    target_reference = lane_name_map[spec.target_lane_id]
    if position["position"].get("reference_name") != source_reference:
        raise ValueError("lane-change initial position does not bind the source lane")
    speed_kmh = speeds[0] * 3.6
    lateral_speed = round(((source_width + target_width) / 2) /
                          (spec.end_s - spec.start_s) * 3.6, 4)
    vehicle["timeline"] = [
        mk_maneuver(
            f"{spec.actor_id} pre-change straight", "drive_straight",
            mk_action("forward", position, speed_kmh, speed_kmh, 0.0),
            mk_transition(source_reference, "simulation_time", spec.start_s), vehicle["id"],
            "Straight segment before the evidence-bound adjacent lane change.",
        ),
        mk_maneuver(
            f"{spec.actor_id} adjacent lane change", "lane_change",
            mk_action(spec.direction, None, speed_kmh, speed_kmh, 0.0, lat_vel=lateral_speed),
            mk_transition(target_reference, "simulation_time", spec.end_s), vehicle["id"],
            "Single adjacent lane change derived from existing Behavior evidence.",
        ),
        mk_maneuver(
            f"{spec.actor_id} post-change straight", "drive_straight",
            mk_action("forward", None, speed_kmh, speed_kmh, 0.0),
            mk_transition(target_reference, "simulation_time", spec.duration_s), vehicle["id"],
            "Straight segment after reaching the evidence-bound target lane.",
        ),
    ]
    return spec


def apply_standard_timelines(source, target, fallbacks=None):
    """Use source time/speed samples, never subtract road-local end positions."""
    if source.get('scene_type') != 'intersection':
        return
    if source.get('quality_report', {}).get('motion_contract') is not None:
        return
    by_name = {v['name']: v for v in target['scenario']['vehicles']}
    lane_names={lane_id:f"Road_{road_index+1}_Seg_1_Lane_{lane_index+1}"
                for road_index,road in enumerate(source.get('road',{}).get('roads',[]))
                for lane_index,lane_id in enumerate(road.get('lane_ids',[]))}
    actor_paths=source.get('quality_report',{}).get('intersection_preview',{}).get('actor_paths',{})
    duration = source.get('metadata', {}).get('duration_s')
    default_speed_mps = 30 / 3.6
    for actor in [source['ego'], *source.get('actors', [])]:
        aid = actor['actor_id']
        name = 'Ego' if aid == 'ego' else aid.replace('vehicle_', 'Vehicle_')
        if name not in by_name or actor.get('actor_type') != 'vehicle' or actor.get('is_static'):
            continue
        points = actor.get('trajectory', [])
        rows = []
        has_speed_error = False
        for point in points:
            t = point.get('time_s', point.get('timestamp_s'))
            v = point.get('speed_mps')
            v = v.get('value') if isinstance(v, dict) else v
            if any(type(n) not in (int, float) or not math.isfinite(n) for n in (t, v)) or v < 0:
                has_speed_error = True
                break
            rows.append((t, v))
        if has_speed_error or not rows:
            # Fall back to constant-speed straight drive
            v = default_speed_mps
            rows = [(0, v), (duration or 5.0, v)]
            if fallbacks is not None:
                fallbacks.append({'field': f'{aid}.trajectory.speed_samples',
                                  'value': 'constant_default',
                                  'reason': 'missing_or_invalid_speed_samples'})
        if (len(rows) < 2 or rows[0][0] != 0 or rows[-1][0] != duration or
                any(b[0] <= a[0] for a, b in zip(rows, rows[1:]))):
            # Pad to cover [0, duration]
            if rows and rows[0][0] != 0:
                rows.insert(0, (0, rows[0][1]))
            if rows and duration is not None and rows[-1][0] != duration:
                rows.append((duration, rows[-1][1]))
            # Re-check after padding; if still bad, fall back to constant speed
            if (len(rows) < 2 or any(b[0] <= a[0] for a, b in zip(rows, rows[1:]))):
                v = rows[0][1] if rows else default_speed_mps
                rows = [(0, v), (duration or 5.0, v)]
            if fallbacks is not None:
                fallbacks.append({'field': f'{aid}.trajectory.time_coverage',
                                  'value': 'padded_or_rebuilt',
                                  'reason': 'trajectory_does_not_cover_duration'})
        output = by_name[name]
        position = output['timeline'][0]['action']['start_position']
        reference = position['position']['reference_name']
        lane_change=actor_paths.get(aid)
        if lane_change:
            try:
                if lane_change.get('contract')!='intersection_lane_change.v1':
                    raise ValueError(f'{aid}: unsupported lane-change contract')
                start,end=lane_change['transition_interval_s']
                if not 0 < start < end < duration:
                    raise ValueError(f'{aid}: lane-change interval must leave three nonempty segments')
                if max(v for _,v in rows)-min(v for _,v in rows)>1e-6:
                    raise ValueError(f'{aid}: lane-change v1 requires constant longitudinal speed')
                target_reference=lane_names.get(lane_change['target_lane_id'])
                if target_reference is None:
                    raise ValueError(f'{aid}: lane-change target lane missing')
                speed=rows[0][1]*3.6
                lateral=lane_change['lateral_speed_kmh']
                direction='left' if lane_change['lateral_motion']=='changing_left' else 'right'
                output['timeline']=[
                    mk_maneuver(f'{aid} pre-change straight','drive_straight',
                        mk_action('forward',position,speed,speed,0.0),
                        mk_transition(reference,'simulation_time',start),output['id'],
                        'Straight segment before the evidence-bound adjacent lane change.'),
                    mk_maneuver(f'{aid} adjacent lane change','lane_change',
                        mk_action(direction,None,speed,speed,0.0,lat_vel=lateral),
                        mk_transition(target_reference,'simulation_time',end),output['id'],
                        'Single adjacent lane change from intersection_lane_change.v1.'),
                    mk_maneuver(f'{aid} post-change straight','drive_straight',
                        mk_action('forward',None,speed,speed,0.0),
                        mk_transition(target_reference,'simulation_time',duration),output['id'],
                        'Straight segment after reaching the reviewed target lane.'),
                ]
                continue
            except (ValueError, KeyError, TypeError):
                # Fall back to straight drive timeline on any lane-change failure
                if fallbacks is not None:
                    fallbacks.append({'field': f'{aid}.lane_change',
                                      'value': 'straight_drive_fallback',
                                      'reason': 'lane_change_construction_failed'})
        rows = compact_rows(source, actor, rows)
        if single_distance_action_allowed(source, actor, rows):
            elapsed = rows[-1][0] - rows[0][0]
            distance = integrated_distance(rows)
            if elapsed > 0 and distance > 0:
                start_speed = rows[0][1]
                end_speed = 2 * distance / elapsed - start_speed
                acceleration = (end_speed - start_speed) / elapsed
                if end_speed < 0:
                    end_speed = None
            else:
                end_speed = None
            if end_speed is not None:
                kind = ('accelerate' if end_speed > start_speed + 1e-8 else
                        'decelerate' if end_speed < start_speed - 1e-8 else 'drive_straight')
                output['timeline'] = [mk_maneuver(
                    f'{aid} estimated straight motion', kind,
                    mk_action('forward', position, start_speed*3.6, end_speed*3.6, acceleration),
                    mk_transition(reference, 'traveled_distance', distance), output['id'],
                    'Estimated source speed samples integrated into one MELA-compatible action.')]
                if fallbacks is not None:
                    fallbacks.append({
                        'field': f'{aid}.timeline',
                        'value': {'actions': 1, 'transition': 'traveled_distance',
                                  'duration_s': elapsed, 'distance_m': distance},
                        'reason': 'estimated_motion_single_action_mela_compatibility',
                    })
                continue
        timeline = []
        for index, ((t0, v0), (t1, v1)) in enumerate(zip(rows, rows[1:])):
            kind = 'stop' if max(v0, v1) < 1e-8 else 'accelerate' if v1 > v0+1e-8 else 'decelerate' if v1 < v0-1e-8 else 'drive_straight'
            # The reference MELA scene uses a single distance-bounded cruise.
            # Keep absolute-time boundaries for multi-phase/accelerating motion.
            distance_bounded = len(rows) == 2 and kind == 'drive_straight'
            timeline.append(mk_maneuver(f'{aid} source segment {index+1}', kind,
                mk_action('forward', position if index == 0 else None, v0*3.6, v1*3.6, (v1-v0)/(t1-t0)),
                mk_transition(reference, 'traveled_distance' if distance_bounded else 'simulation_time',
                              v0*(t1-t0) if distance_bounded else t1), output['id'],
                'Compiled source speed/time profile; redundant samples merged without changing motion.'))
        output['timeline'] = timeline


def apply_motion_timelines(source, target):
    contract=source.get("quality_report",{}).get("motion_contract")
    if contract is None:return
    by_name={v["name"]:v for v in target["scenario"]["vehicles"]}
    for actor_id,plan in contract["plans"].items():
        actor=by_name[contract["target_names"][actor_id]]
        position=actor["timeline"][0]["action"]["start_position"]
        reference=position["position"]["reference_name"]
        timeline=[]
        for index,segment in enumerate(plan["segments"]):
            v0,v1=segment["start_mps"],segment["end_mps"]
            kind="stop" if max(v0,v1)<1e-8 else "accelerate" if v1>v0+1e-8 else "decelerate" if v1<v0-1e-8 else "drive_straight"
            timeline.append(mk_maneuver(f"{actor_id} segment {index+1}",kind,
                mk_action("forward",position if index==0 else None,v0*3.6,v1*3.6,segment["acceleration_mps2"]),
                mk_transition(reference,"simulation_time",segment["end_s"]),actor["id"],
                "Absolute source-time segment end; finite segment verified by local motion gate."))
        actor["timeline"]=timeline
