"""Continuous longitudinal motion constrained by independently reviewed video facts.

Connector geometry is an explicit source-profile estimate, never a video measurement
or a claim that a deployed renderer has been verified.
"""
from _lib.contracts.entry import ENTRY_TYPES

import copy
from _lib.experimental.constraint_solver import solve, REVISION
from _lib.contracts.fidelity import compare_reviews

# The available OpenX source attaches opposite roads at radius 20 m. For aligned
# straight lanes the centreline connection is 40 m. Runtime version is unverified.
CONNECTOR_M = 40.0


def compile_motion(world, behavior, review, frames, policy, *, motion_facts=None):
    from _lib.behavior.intersection import require, number
    if motion_facts is None:
        compare_reviews(behavior['video_fidelity'], review['video_fidelity'])
    facts = behavior['video_fidelity'] if motion_facts is None else motion_facts
    duration = facts['source_duration_s']
    roads = {r['road_id']: r for r in world['world']['road']['roads']}
    length = number(roads[review['arms']['S']].get('road_geometry', {}).get('visible_length_m')) or policy['road_length_m']
    rows = {r['actor_id']: r for r in review['actors'] if r['execution'] == 'include'}
    actors = {a['actor_id']: a for a in behavior['actors'] if a['actor_id'] in rows}
    for e in behavior['events']:
        if e.get('event_type') not in ENTRY_TYPES or e.get('involved_actor_ids')!=['ego']:continue
        entry=e.get('time_range_s',e.get('time_interval_s'))
        if isinstance(entry,dict):entry=[entry['start'],entry['end']]
        for c in review.get('crosswalks',[]):
            crossing=c.get('ego_crossing_interval_s')
            if c['road_id']!=review['arms']['N'] or crossing is None:continue
            available=crossing[1]-entry[0]
            required=CONNECTOR_M/(policy['maximum_speed_kmh']/3.6)
            require(available>=required-1e-7,'intersection.crosswalk_motion_conflict',
                'Reviewed exit crosswalk time cannot follow entry within the supported connector geometry and speed bound',
                path='/crosswalks/'+c['crosswalk_id']+'/ego_crossing_interval_s',
                actual=dict(entry_event_id=e['event_id'],entry_interval_s=entry,exit_crosswalk_id=c['crosswalk_id'],
                    exit_interval_s=crossing,available_traversal_s=available,minimum_traversal_s=required,
                    connector_m=CONNECTOR_M,maximum_speed_kmh=policy['maximum_speed_kmh'],
                    geometry_verification='source_profile_estimate_not_runtime_verified'),
                retry_kind='content_repair')
    times = sorted({0., duration, *(frames[o['frame_id']]['time_s'] for o in facts['observations']),
                    *(p['time_s'] for a in actors.values() for p in a['trajectory']),
                    *(t for e in behavior['events'] for t in e.get('time_range_s',[]) if type(t) in (int,float))})
    variables, constraints, preferences = [], [], []
    def vk(a, i): return 'v'+a if i == 0 else f'v{a}@{i}'
    def position(a, t):
        result = {'x'+a: 1.}
        for i, (left, right) in enumerate(zip(times, times[1:])):
            dt = min(t, right)-left
            if dt <= 0: break
            ratio = dt/(right-left)
            result[vk(a,i)] = result.get(vk(a,i),0)+dt*(1-ratio/2)
            result[vk(a,i+1)] = result.get(vk(a,i+1),0)+dt*ratio/2
        return result
    def add(key, coefficients, refs, lo=None, hi=None):
        row = dict(id=key, coefficients=coefficients, evidence_frame_ids=refs or sorted(frames),
                   origin='video_observation' if refs else 'execution_policy_bound')
        if lo is not None: row['lower']=lo
        if hi is not None: row['upper']=hi
        constraints.append(row)
    for aid, a in sorted(actors.items()):
        variables.append(dict(id='x'+aid,lower=0,upper=length,hint=policy['initial_x_m'],weight=1,unit='m'))
        for i,t in enumerate(times):
            variables.append(dict(id=vk(aid,i),lower=0,upper=policy['maximum_speed_kmh']/3.6,
                                  hint=policy['speed_kmh']/3.6,weight=10,unit='m/s'))
            if i:
                preferences.append(dict(id=f'{aid}.simple.{i}',coefficients={vk(aid,i):1,vk(aid,i-1):-1},value=0,weight=100,
                                        evidence_frame_ids=sorted(frames),origin='execution_simplicity_preference'))
                add(f'{aid}.acceleration.{i}',{vk(aid,i):1,vk(aid,i-1):-1},[],
                    -4*(t-times[i-1]),3*(t-times[i-1]))
        for i,p in enumerate(a['trajectory']):
            t=p['time_s']; refs=[p['frame_id']] if p.get('frame_id') else []
            raw=p.get('speed_mps'); value=number(raw)
            if value is not None:
                co={vk(aid,times.index(t)):1}
                if isinstance(raw,dict) and raw.get('status')=='estimated':
                    preferences.append(dict(id=f'{aid}.speed.{i}',coefficients=co,value=value,weight=20,evidence_frame_ids=refs))
                    interval=raw.get('range')
                    if interval: add(f'{aid}.speed_range.{i}',co,refs,*interval)
                else: add(f'{aid}.speed.{i}',co,refs,value,value)
            raw_position=p.get('position_ego_m',{});x=number(raw_position.get('x'))
            if x is not None:
                require(a.get('trajectory_coordinate_system')!='road_local','fidelity.coordinate_system',
                        'Road-local metric observations require explicit route coordinate conversion')
                co=position(aid,t);co['xego']=co.get('xego',0)-1
                if raw_position.get('status')=='estimated':
                    preferences.append(dict(id=f'{aid}.position.{i}',coefficients=co,value=x,weight=5,evidence_frame_ids=refs))
                else:add(f'{aid}.position.{i}',co,refs,x,x)
        for i,o in enumerate(facts['observations']):
            if o['actor_id']!=aid: continue
            t=frames[o['frame_id']]['time_s']; state=o['junction_state']; refs=[o['frame_id']]
            co=position(aid,t)
            if state=='before': add(f'{aid}.before.{i}',co,refs,hi=length)
            if state=='inside': add(f'{aid}.inside.{i}',co,refs,length,length+CONNECTOR_M-0.01)
            if state=='after': add(f'{aid}.after.{i}',co,refs,lo=length+CONNECTOR_M)
            if o['motion_state']=='stopped': add(f'{aid}.stopped.{i}',{vk(aid,times.index(t)):1},refs,0,0)
            if o['motion_state'] in {'accelerating','decelerating'}:
                k=times.index(t);left,right=(k-1,k) if k else (0,1)
                co={vk(aid,right):1,vk(aid,left):-1}
                # A strict qualitative trend, not an invented measured acceleration.
                eps=0.001*(times[right]-times[left])
                add(f'{aid}.speed_trend.{i}',co,refs,eps if o['motion_state']=='accelerating' else None,
                    -eps if o['motion_state']=='decelerating' else None)
        terminal=max((o for o in facts['observations'] if o['actor_id']==aid),key=lambda o:frames[o['frame_id']]['time_s'])
        if terminal['junction_state']=='inside':
            add(f'{aid}.terminal_inside',position(aid,duration),[terminal['frame_id']],length,length+CONNECTOR_M-0.01)
        add(f'{aid}.extent',position(aid,duration),[],hi=length+CONNECTOR_M+policy['road_length_m'])
    for event in behavior['events']:
        kind=event.get('event_type')
        if kind not in ENTRY_TYPES:
            # Longitudinal events may be represented by the continuous profile
            # only when the independently reviewed observations express them.
            from _lib.behavior.longitudinal import kinds
            expected=kinds().get(kind)
            interval=event.get('time_range_s',event.get('time_interval_s'))
            if isinstance(interval,dict):interval=[interval.get('start'),interval.get('end')]
            ids=event.get('involved_actor_ids',[])
            require(expected is not None and ids and isinstance(interval,list) and len(interval)==2,
                    'fidelity.motion_event', 'Longitudinal event needs a bounded, reviewed motion representation',
                    retry_kind='unsupported_capability',actual=event)
            for aid in ids:
                require(aid in actors and (motion_facts is not None or any(o['actor_id']==aid and o['motion_state']==expected
                    and interval[0]<=frames[o['frame_id']]['time_s']<=interval[1]
                    for o in facts['observations'])), 'fidelity.motion_event',
                    'Motion event lacks independently reviewed speed-trend or stop evidence',actor_id=aid)
                require(0<=interval[0]<interval[1]<=duration,'fidelity.motion_event','Motion event lies outside execution interval')
                left,right=times.index(interval[0]),times.index(interval[1])
                refs=[e['frame_id'] for e in event.get('evidence',[]) if e.get('frame_id')]
                if expected=='stopped':
                    add(event['event_id']+'.stop.'+aid,{vk(aid,right):1},refs,0,0)
                else:
                    eps=0.001*(interval[1]-interval[0])
                    add(event['event_id']+'.trend.'+aid,{vk(aid,right):1,vk(aid,left):-1},refs,
                        eps if expected=='accelerating' else None,-eps if expected=='decelerating' else None)
                    for k in range(left,right):
                        add(event['event_id']+f'.monotone.{aid}.{k}',{vk(aid,k+1):1,vk(aid,k):-1},refs,
                            0 if expected=='accelerating' else None,0 if expected=='decelerating' else None)
            continue
        ids=event.get('involved_actor_ids',[])
        require(len(ids)==1 and ids[0] in actors,'fidelity.event_actor','Entry must identify one included vehicle')
        interval=event.get('time_range_s',event.get('time_interval_s'))
        if isinstance(interval,dict): interval=[interval['start'],interval['end']]
        refs=[e['frame_id'] for e in event.get('evidence',[]) if e.get('frame_id')]
        add(event['event_id']+'.before',position(ids[0],interval[0]),refs,hi=length)
        add(event['event_id']+'.after',position(ids[0],interval[1]),refs,lo=length)
    for i,r in enumerate(review['orders']):
        a,b=r['behind'],r['ahead']
        if a not in actors or b not in actors: continue
        for t in times:
            co=position(b,t)
            for k,v in position(a,t).items(): co[k]=co.get(k,0)-v
            add(f'order.{i}.{t}',co,r['evidence_frame_ids'],lo=policy['minimum_gap_m'])
    # Solve facility timing jointly with motion. A post-solve clamp cannot
    # recover crossing evidence omitted from the optimization problem.
    originals={c['crosswalk_id']:c for j in world['world'].get('junctions',[]) for c in j.get('crosswalks',[])}
    for c in review.get('crosswalks',[]):
        rid=c['road_id']; interval=c.get('ego_crossing_interval_s')
        if rid not in {review['arms']['S'],review['arms']['N']}:continue
        if interval is None and rid!=review['arms']['S']:continue
        extent=number(roads[rid].get('road_geometry',{}).get('visible_length_m')) or policy['road_length_m']
        shift=0 if rid==review['arms']['S'] else length+CONNECTOR_M
        key='crosswalk.'+c['crosswalk_id']; refs=c['evidence_frame_ids']
        raw=originals.get(c['crosswalk_id'],{}).get('position_ego_m',{})
        known=number(raw.get('x'))
        hint=extent-policy['crosswalk_main_offset_m'] if rid==review['arms']['S'] else policy['crosswalk_main_offset_m']
        variables.append(dict(id=key,lower=shift,upper=shift+extent,hint=shift+hint,weight=1,unit='m'))
        if known is not None:add(key+'.known',{key:1},refs,shift+known,shift+known)
        if interval is not None:
            require(len(interval)==2 and 0<=interval[0]<interval[1]<=duration,
                    'intersection.crosswalk_interval','Crosswalk interval outside execution time',actual=c)
            for t,side in zip(interval,('before','after')):
                co=position('ego',t);co[key]=-1
                add(key+'.'+side,co,refs,hi=0 if side=='before' else None,lo=0 if side=='after' else None)
        relation=c.get('ego_relation_at_start')
        if rid==review['arms']['S'] and relation in {'ahead','behind'}:
            add(key+'.initial',{key:1,'xego':-1},refs,lo=1 if relation=='ahead' else None,hi=-1 if relation=='behind' else None)
    problem=dict(revision=REVISION,variables=variables,constraints=constraints,preferences=preferences,predicates=[],
                 coverage=dict.fromkeys(('evidence','paths','critical_predicates','target_capability'),True))
    result=solve(problem,frames)
    require(result['status']=='feasible_stable','fidelity.motion_constraints',
            'Video boundaries, lane order and speed observations cannot be jointly executed',actual=result)
    result['profiles']={}
    for aid in actors:
        result['profiles'][aid]=[dict(time_s=t,s_m=sum(result['values'][k]*v for k,v in position(aid,t).items()),
                                     speed_mps=result['values'][vk(aid,i)]) for i,t in enumerate(times)]
    result['path_geometry']=dict(entry_s_m=length,exit_s_m=length+CONNECTOR_M,connector_length_m=CONNECTOR_M,
        source='OpenX source attachment radius 20 m, aligned straight connection',verification='source_profile_estimate_not_runtime_verified')
    return result,problem


def state(solution, aid, t):
    if 'profiles' not in solution:
        v=solution['values']['v'+aid];return solution['values']['x'+aid]+v*t,v
    points=solution['profiles'][aid]
    for a,b in zip(points,points[1:]):
        if a['time_s']-1e-8<=t<=b['time_s']+1e-8:
            dt=t-a['time_s']; acc=(b['speed_mps']-a['speed_mps'])/(b['time_s']-a['time_s'])
            return a['s_m']+a['speed_mps']*dt+acc*dt*dt/2,a['speed_mps']+acc*dt
    raise ValueError('motion time outside source duration')


def complete_trajectory(actor, solution, incoming, outgoing, y):
    """Keep physical lane unknown inside the junction; route remains explicit."""
    source={p['time_s']:p for p in actor['trajectory']}
    geometry=solution['path_geometry']; points=[]
    for p in solution['profiles'][actor['actor_id']]:
        t=p['time_s'];old=copy.deepcopy(source.get(t,{}));x=p['s_m']
        old.update(time_s=t,position_ego_m={'x':x,'y':y},speed_mps=p['speed_mps'],
                   lane_id=incoming if x<geometry['entry_s_m'] else outgoing if x>=geometry['exit_s_m'] else None)
        if t not in source:
            old.update(execution_origin='interpolated_or_terminal_forecast',execution_extension=True)
        points.append(old)
    actor['trajectory']=points


def audit_target(analysis,target):
    """Decode final target independently of the solver; do not certify a renderer."""
    from _lib.delivery.motion_check import decode, at
    q=analysis['quality_report']['video_fidelity'];facts=q['facts'];geom=q.get('path_geometry')
    actors={a['name']:a for a in target['scenario']['vehicles']}; results=[]
    excluded={r['actor_id'] for r in analysis['quality_report'].get('intersection_preview',{}).get('background_exclusions',[])}
    lanes={lane['name']:(i,j) for i,road in enumerate(target['scene']['road_network']['roads'])
           for segment in road['segments'] for j,lane in enumerate(segment['lanes'])}
    ego_position=actors['Ego']['timeline'][0]['action']['start_position']['position']
    ego_lane=lanes[ego_position['reference_name']]
    visible={}
    for o in facts['observations']:
        aid=o['actor_id'];name='Ego' if aid=='ego' else ('Vehicle_'+aid[8:] if aid.startswith('vehicle_') else aid)
        if aid in excluded:continue
        if name not in actors: raise ValueError(f'fidelity target omitted observed vehicle: {aid}')
        actor=actors[name];timeline=actor['timeline']
        if len(timeline)==1 and timeline[0]['transition']['type']=='traveled_distance':
            action=timeline[0]['action'];v=action['start_longitudinal_velocity']/3.6
            if abs(v-action['end_longitudinal_velocity']/3.6)>1e-8 or v<=0:raise ValueError('unaudited distance-trigger motion')
            if abs(timeline[0]['transition']['value']/v-facts['source_duration_s'])>1e-5:raise ValueError('target distance does not cover source duration')
            points=[(0.,facts['source_duration_s'],action['start_position']['position']['longitudinal_value'],v,0.)]
        else:points=decode(actor,facts['source_duration_s'])
        t=q['frames'][o['frame_id']]['time_s'];x=at(points,t)
        position=timeline[0]['action']['start_position']['position'];lane=lanes[position['reference_name']]
        if o['corridor'] in {'ego','left_adjacent','right_adjacent'}:
            expected={'ego':0,'left_adjacent':1,'right_adjacent':-1}[o['corridor']]
            if lane[0]!=ego_lane[0] or lane[1]-ego_lane[1]!=expected:
                raise ValueError('fidelity target violates observed lane/corridor relation')
        visible.setdefault(o['frame_id'],[]).append((aid,lane,x,o))
        if geom:
            lo,hi=geom['entry_s_m'],geom['exit_s_m']
            state=o['junction_state']
            if (state=='before' and x>lo+0.05) or (state=='inside' and not lo-0.05<=x<hi) or (state=='after' and x<hi-0.05):
                raise ValueError('fidelity target violates independently reviewed junction state')
            if aid=='ego' and state=='inside' and t==max(q['frames'][p['frame_id']]['time_s'] for p in facts['observations'] if p['actor_id']=='ego'):
                if at(points,facts['source_duration_s'])>=hi: raise ValueError('fidelity target exits before video end')
        if o['motion_state']=='stopped':
            segment=next(p for p in points if p[0]-1e-8<=t<=p[1]+1e-8)
            if abs(segment[3]+segment[4]*(t-segment[0]))>0.01: raise ValueError('fidelity target lost observed stop')
        if o['motion_state'] in {'accelerating','decelerating'}:
            segment=next(p for p in points if p[0]-1e-8<t<=p[1]+1e-8) if t else points[0]
            if (o['motion_state']=='decelerating' and segment[4]>=0) or (o['motion_state']=='accelerating' and segment[4]<=0):
                raise ValueError('fidelity target lost observed speed trend')
        results.append({'actor_id':aid,'frame_id':o['frame_id'],'passed':True})
    for group in visible.values():
        ego=next((v for v in group if v[0]=='ego'),None)
        if ego and ego[3]['ego_lane_ahead']=='clear':
            if any(aid!='ego' and lane==ego[1] and x>ego[2]+0.1 for aid,lane,x,_ in group):
                raise ValueError('fidelity target invents a visible same-lane lead vehicle')
    return dict(status='local_prediction_passed',observations=results,render_verification='not_executed',
                termination='not_guaranteed_by_target_schema',source_duration_s=facts['source_duration_s'])
