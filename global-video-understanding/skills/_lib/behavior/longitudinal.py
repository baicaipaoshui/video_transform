"""Reviewed longitudinal events on an existing intersection execution policy."""
from _lib.paths import SKILLS
from _lib.runtime.case_core import read_json


def schema():
    return read_json(SKILLS/'schemas/longitudinal_motion.schema.json')


def kinds():
    return schema()['x-event-motion-states']


def events(behavior):
    return [e for e in behavior.get('events',[]) if e.get('event_type') in kinds()]


def validate(behavior, review, frames):
    from _lib.behavior.intersection import require
    from _lib.contracts.fidelity import validator
    source=events(behavior)
    if not source: return
    rows=review.get('longitudinal_events',[])
    validator('longitudinal_motion.schema.json').validate(rows)
    indexed={r['event_id']:r for r in rows}
    require(len(indexed)==len(rows) and set(indexed)=={e['event_id'] for e in source},
            'intersection.motion_review', 'Review must cover every longitudinal event')
    actors={a['actor_id'] for a in behavior['actors']}
    for event in source:
        row=indexed[event['event_id']];interval=event.get('time_range_s')
        require(isinstance(interval,list) and len(interval)==2 and all(type(t) in (int,float) for t in interval)
                and 0<=interval[0]<interval[1], 'intersection.motion_interval', 'Motion event needs a positive bounded interval',actual=event)
        ids=event.get('involved_actor_ids',[])
        require(bool(ids) and set(ids)<=actors,'intersection.motion_actor','Motion event actor must resolve',actual=event)
        refs={e['frame_id'] for e in event.get('evidence',[]) if e.get('frame_id')}
        require(len(refs)>=2 and refs<=set(frames) and set(row['evidence_frame_ids'])<=set(frames)
                and refs<=set(row['evidence_frame_ids']), 'intersection.motion_evidence', 'Review must retain event evidence frames')
        if isinstance(frames,dict):
            times={frames[f]['time_s'] for f in refs}
            require(len(times)>=2 and min(times)<=interval[0] and max(times)>=interval[1],
                    'intersection.motion_evidence','Event evidence must bracket the reviewed interval')
        require(row['status']=='confirmed','intersection.motion_review','Longitudinal motion was not confirmed',actual=row)


def sampling_facts(behavior, frames):
    """Internal sample grid only: no inferred junction state or video certification."""
    return dict(source_duration_s=max(f['time_s'] for f in frames.values()), observations=[
        dict(actor_id=a['actor_id'],frame_id=p['frame_id'],junction_state='unknown',motion_state='unknown')
        for a in behavior['actors'] for p in a['trajectory'] if p.get('frame_id')])


def audit_target(analysis, target):
    from _lib.delivery.motion_check import decode
    certificate=analysis['quality_report'].get('intersection_preview')
    if not certificate: return
    source=events(certificate['source_behavior'])
    if not source: return
    vehicles={a['name']:a for a in target['scenario']['vehicles']}
    duration=analysis['metadata']['duration_s']
    for event in source:
        start,end=event['time_range_s'];trend=kinds()[event['event_type']]
        for aid in event['involved_actor_ids']:
            name='Ego' if aid=='ego' else ('Vehicle_'+aid[8:] if aid.startswith('vehicle_') else aid)
            if name not in vehicles: raise ValueError('Target omits longitudinal event actor: '+aid)
            segments=decode(vehicles[name],duration)
            def velocity(t):
                for left,right,x,v,acc in segments:
                    if left-1e-8<=t<=right+1e-8:return v+acc*(t-left)
                raise ValueError('Target does not cover motion event')
            delta=velocity(end)-velocity(start)
            if ((trend=='decelerating' and delta>=-1e-7) or
                (trend=='accelerating' and delta<=1e-7) or
                (trend=='stopped' and abs(velocity(end))>1e-5)):
                raise ValueError('Target lost reviewed longitudinal event: '+event['event_id'])
            for left,right,x,v,acc in segments:
                if max(left,start)<min(right,end)-1e-8 and (
                    (trend=='decelerating' and acc>1e-7) or (trend=='accelerating' and acc< -1e-7)):
                    raise ValueError('Target reverses reviewed speed trend: '+event['event_id'])
