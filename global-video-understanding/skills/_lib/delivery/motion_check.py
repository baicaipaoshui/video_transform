"""Independent Preview JSON time audit. Does not import solver or converter.

Supports absolute-time phases and a single constant-speed distance phase.
This is a source-contract prediction, NOT an esmini/renderer implementation.
"""
import math


def scalar(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError("target motion contains nonfinite value")
    return float(value)


def decode(actor, duration):
    timeline=actor.get("timeline") or []
    if not timeline: raise ValueError("target timeline is empty")
    output=[];x=None;previous=None;start=0.0
    for i,event in enumerate(timeline):
        action=event["action"];trigger=event["transition"]
        # The authoritative Maneuver contract says the transition terminates
        # its associated action.  Therefore each absolute simulation-time
        # value is this segment's end, not the next segment's start.
        position=action.get("start_position")
        if i==0:
            p=position["position"]
            if p["longitudinal_unit"]!="m" or p["longitudinal_specifier"]!="ahead_of": raise ValueError("unaudited position")
            x=scalar(p["longitudinal_value"])
        elif position is not None: raise ValueError("unexpected target teleport")
        v0=scalar(action["start_longitudinal_velocity"])/3.6
        v1=scalar(action["end_longitudinal_velocity"])/3.6
        acceleration=scalar(action["acceleration"])
        if trigger['type'] == 'simulation_time':
            end = scalar(trigger['value'])
        elif trigger['type'] == 'traveled_distance' and len(timeline) == 1 and v0 > 0:
            # A single constant-acceleration distance action is compiled to
            # cover the declared source duration.  Verify both its terminal
            # velocity and integrated distance before accepting that timing.
            end = duration
            dt = end - start
            if (abs(v0 + acceleration*dt - v1) > 0.01
                    or abs(v0*dt + 0.5*acceleration*dt*dt
                           - scalar(trigger['value'])) > 1e-4):
                raise ValueError('distance action does not cover source time')
        else:
            raise ValueError('unaudited target transition')
        if end <= start or end > duration + 1e-7:
            raise ValueError('target timeline does not cover source time')
        if min(v0,v1)<0 or (previous is not None and abs(v0-previous)>0.01): raise ValueError("target velocity discontinuity")
        if abs(v0+acceleration*(end-start)-v1)>0.01: raise ValueError("target acceleration/duration mismatch")
        if event["type"]=="lane_change":
            if action["direction"] not in {"left","right"} or abs(scalar(action.get("start_lateral_velocity",0)))<1e-9:
                raise ValueError("unaudited target lane change")
        else:
            expected="stop" if max(v0,v1)<1e-8 else "accelerate" if v1>v0+1e-8 else "decelerate" if v1<v0-1e-8 else "drive_straight"
            if event["type"]!=expected or action["direction"]!="forward" or abs(scalar(action.get("start_lateral_velocity",0)))>1e-9:
                raise ValueError("unaudited target action")
        if action.get("turning_angle",0)!=0:
            raise ValueError("unaudited target action")
        output.append((start,end,x,v0,acceleration))
        x+=v0*(end-start)+0.5*acceleration*(end-start)**2
        previous=v1
        start=end
    if abs(start-duration)>1e-7:
        raise ValueError("target timeline does not cover source time")
    return output


def at(segments,t):
    for start,end,x,v,a in segments:
        if start-1e-9<=t<=end+1e-9:
            dt=t-start;return x+v*dt+0.5*a*dt*dt
    raise ValueError("time outside target motion")


def compare(analysis,target):
    motion=analysis["quality_report"]["motion_contract"]
    duration=motion["duration_s"]
    actors={v["name"]:v for v in target["scenario"]["vehicles"]}
    expected=motion["target_names"]
    if set(actors)!=set(expected.values()) or target["scenario"].get("pedestrians"):
        raise ValueError("target motion actor set changed")
    decoded={key:decode(actors[name],duration) for key,name in expected.items()}
    # Full profile state checks, not merely the first action.
    for key,plan in motion["plans"].items():
        actual=decoded[key]
        if len(actual)!=len(plan["segments"]): raise ValueError("target motion segment lost")
        for segment,(start,end,x,v,a) in zip(plan["segments"],actual):
            for observed,expected_value in zip((start,end,x,v,a),(segment["start_s"],segment["end_s"],segment["start_m"],segment["start_mps"],segment["acceleration_mps2"])):
                if abs(observed-expected_value)>1e-4: raise ValueError("target motion parameter changed")
    rows=[]
    for event in analysis["events"]:
        key=event["involved_actor_ids"][0];segments=decoded[key]
        boundary=motion["boundaries"][event["boundary_id"]]
        destination=scalar(boundary["s_m"]["value"])
        if event["visibility"]=="not_reached":
            if at(segments,duration)>=destination: raise ValueError("target reaches unobserved boundary")
            rows.append({"event_id":event["event_id"],"predicted_s":None,"visibility":"not_reached","passed":True});continue
        lo,hi=0.0,duration
        if at(segments,lo)>=destination or at(segments,hi)<destination: raise ValueError("target crossing is unreachable")
        for _ in range(50):
            middle=(lo+hi)/2
            if at(segments,middle)<destination:lo=middle
            else:hi=middle
        left,right=event["time_interval_s"]
        if not left-0.1<=hi<=right+0.1: raise ValueError("target crossing time diverges from evidence")
        rows.append({"event_id":event["event_id"],"evidence_interval_s":[left,right],"predicted_s":hi,"outside_interval_s":max(left-hi,hi-right,0),"passed":True})
    # Preserve same-lane front/back order at 0.05-second resolution.
    keys=list(decoded)
    for i,key in enumerate(keys):
        for other in keys[i+1:]:
            lane=motion["plans"][key]["lane_id"]
            if lane is None or lane!=motion["plans"][other]["lane_id"]:continue
            initial=at(decoded[key],0)-at(decoded[other],0)
            if abs(initial)<0.1: raise ValueError("same-lane actors start overlapped")
            for step in range(math.ceil(duration/0.05)+1):
                t=min(duration,step*0.05)
                if initial*(at(decoded[key],t)-at(decoded[other],t))<=0:
                    raise ValueError("same-lane actor order changed")
    return {"passed":True,"verification":"local_motion_predicted","render_verification":"not_executed","events":rows,"source_duration_s":duration,
            "preview_encoding":{"status":"not_executed","audited_step_s":0.033,"audited_fps":30,"nominal_playback_to_simulation_ratio":1/(30*0.033)}}
