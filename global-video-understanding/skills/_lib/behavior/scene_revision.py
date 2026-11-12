"""Explicit user corrections, isolated from immutable observed and delivered facts."""
import copy
import math
from _lib.behavior.compile import preview_digest
from _lib.behavior.intersection import project, binding, require

REVISION='user_scene_revision.v1'
HIGHWAY_SOURCE='highway_user_layout.v1'


class RevisionBlocked(ValueError):
    code='scene_revision_blocked'

    def __init__(self, rule_id, message):
        super().__init__(message)
        self.rule_id=rule_id
        self.diagnostics={'rule_id':rule_id,'owner':'delivery','retry_kind':'user_correction'}


def highway_require(condition, rule, message):
    if not condition:raise RevisionBlocked(rule,message)


def build_highway(source, corrections):
    """User-confirmed layout overlay; original observation and timing stay in source."""
    original=source['source_analysis']
    highway_require(source.get('analysis_digest')==preview_digest(original)
        and source.get('parent_analysis_sha256')==corrections['parent_analysis_sha256'],
        'revision.source_binding','Highway correction source/hash differs')
    highway_require(original.get('scene_type')=='highway' and not original.get('junctions')
        and len(original['road']['roads'])==1,
        'revision.highway_scope','Highway layout revision requires one junction-free road')
    highway_require(not original['quality_report'].get('straight_preview')
        and not original['quality_report'].get('scene_revision'),
        'revision.highway_scope','Revise an ordinary highway parent, not a reviewed or already revised projection')
    highway_require(set(corrections)<={'revision','parent_analysis_sha256','reason','highway_layout','actor_lanes'},
        'revision.highway_scope','Highway layout correction cannot change timing or add scenery vehicles')
    layout=corrections.get('highway_layout')
    highway_require(isinstance(layout,dict),'revision.highway_layout','Highway layout is required')
    analysis=copy.deepcopy(original);road=analysis['road']['roads'][0]
    old=list(road['lane_ids']);mapping=layout['lane_remap']
    ids=[f'lane_{i+1:03d}' for i in range(layout['driving_lane_count'])]
    highway_require(layout['road_id']==road['road_id'] and set(mapping)==set(old)
        and len(set(mapping.values()))==len(old) and set(mapping.values())<=set(ids),
        'revision.lane_remap','Remap must uniquely cover every source lane on the existing road')
    shifts={ids.index(mapping[lid])-i for i,lid in enumerate(old)}
    highway_require(len(shifts)==1 and len(ids)>=len(old),
        'revision.lane_order','Layout revision must preserve adjacency, direction and the relative lane order')
    lanes={lane['lane_id']:lane for lane in analysis['road']['lanes']}
    highway_require(set(lanes)==set(old),'revision.lane_ownership','Unexpected lanes outside the highway road')

    def remap(node):
        if isinstance(node,list):
            for item in node:remap(item)
        elif isinstance(node,dict):
            for key,value in list(node.items()):
                if key in {'lane_id','source_lane_id','target_lane_id','incoming_lane_id','outgoing_lane_id'} and isinstance(value,str):
                    node[key]=mapping.get(value,value)
                elif key in {'lane_ids','between'} and isinstance(value,list):
                    node[key]=[mapping.get(lid,lid) for lid in value]
                else:remap(value)
            if node.get('lane_id') in ids and 'ordinal_from_right' in node:
                node['ordinal_from_right']=ids.index(node['lane_id'])
    for key in ('ego','actors','events','simulation_scenarios','interactions'):
        remap(analysis.get(key,[]))
    remap(analysis['road'].get('lane_markings',[]))
    inverse={target:source_id for source_id,target in mapping.items()}
    analysis['road']['lanes']=[dict(copy.deepcopy(lanes[inverse[lid]]) if lid in inverse else {},
        lane_id=lid,road_id=road['road_id'],lane_type='driving',travel_direction='forward') for lid in ids]
    road['lane_ids']=ids
    knowledge=road.get('lane_knowledge')
    if knowledge:
        for anchor in knowledge['anchors']:
            anchor['lane_id']=mapping[anchor['lane_id']]
            anchor['ordinal_from_right']=ids.index(anchor['lane_id'])
        knowledge.update(kind='exact',count=len(ids))
    actors={a['actor_id']:a for a in [analysis['ego'],*analysis['actors']]}
    for aid,lid in corrections.get('actor_lanes',{}).items():
        highway_require(aid in actors and lid in ids,'revision.actor_lane','Unknown vehicle or corrected lane')
        actor=actors[aid]
        highway_require(actor.get('actor_type')=='vehicle'
            and len({p['lane_id'] for p in actor.get('trajectory',[]) if p.get('lane_id')})<=1
            and not any(aid in e.get('involved_actor_ids',[]) for e in analysis.get('events',[])),
            'revision.actor_lane','A changing/event participant must use the global lane remap')
        highway_require(all('y' not in p.get('position_ego_m',{})
            or aid=='ego' and p['position_ego_m']['y']==0 for p in actor.get('trajectory',[])),
            'revision.position_conflict','Lane override conflicts with an existing lateral coordinate')
        for point in actor['trajectory']:
            point.update(lane_id=lid,road_id=road['road_id'],ordinal_from_right=ids.index(lid))
    for actor in actors.values():
        for point in actor.get('trajectory',[]):
            point['detail']='User-confirmed scene lane mapping; original visual explanation retained in parent source.'
    for event in analysis.get('events',[]):
        event['detail']='Original maneuver, actor identity and frame/time evidence retained; lane IDs corrected by user-confirmed layout.'
        for evidence in event.get('evidence',[]):
            if 'detail' in evidence:
                evidence['detail']='Original boundary witness retained; lane ID follows user-confirmed layout.'
    areas=analysis['road'].setdefault('roadside_areas',[])
    if layout['right_parking_area']:
        right=[area for area in areas if area.get('road_id')==road['road_id'] and area.get('side')=='right']
        highway_require(not right or all(area.get('area_type')=='curbside_parking' for area in right),
            'revision.parking_conflict','Existing right-side facilities require their own correction')
        if not right:
            areas.append(dict(area_id='user_right_parking_001',road_id=road['road_id'],
                              area_type='curbside_parking',side='right'))
    q=analysis['quality_report']
    q['scene_revision']={'revision':REVISION,'source':copy.deepcopy(source),'corrections':copy.deepcopy(corrections)}
    q['scene_revision']['sha256']=preview_digest(q['scene_revision'])
    q['execution_plan']={'revision':REVISION,'sha256':q['scene_revision']['sha256'],
        'execution_actor_ids':list(actors),'time_range_s':[0,analysis['metadata']['duration_s']],
        'user_corrections':copy.deepcopy(corrections),'authority':'user_confirmation_not_independent_visual_review'}
    if layout['right_parking_area']:
        for key in ('allowed_degradations','required_degradations','applied_degradations'):
            q[key]=sorted(set(q.get(key,[]))|{'parking_as_paved_shoulder'})
    return analysis,'done_degraded'


def build_highway_gaps(source, corrections):
    """Bind user-selected execution positions without changing observed tracks."""
    original = source['source_analysis']
    highway_require(source.get('analysis_digest') == preview_digest(original)
        and source.get('parent_analysis_sha256') == corrections['parent_analysis_sha256'],
        'revision.source_binding', 'Highway correction source/hash differs')
    highway_require(original.get('scene_type') == 'highway' and not original.get('junctions')
        and len(original['road']['roads']) == 1,
        'revision.highway_scope', 'Initial-gap revision requires one junction-free highway road')
    highway_require(not original['quality_report'].get('straight_preview')
        and not original['quality_report'].get('scene_revision')
        and not original.get('simulation_scenarios'),
        'revision.highway_scope', 'Initial-gap revision requires an ordinary, unrevised highway parent')
    highway_require(set(corrections) == {
        'revision', 'parent_analysis_sha256', 'reason', 'actor_initial_gaps_m'},
        'revision.highway_scope', 'Initial-gap revision cannot change other scene fields')
    actors = {actor['actor_id']: actor for actor in original['actors']}
    excluded = {row['actor_id'] for row in original['quality_report']
                .get('execution_actor_scope', {}).get('excluded_actors', [])}
    ego_position = (original['ego'].get('trajectory') or [{}])[0].get('position_ego_m') or {}
    highway_require(ego_position.get('x') == 0,
        'revision.ego_origin', 'Initial gaps require the frozen ego origin at zero metres')
    parameters = source.get('parameters') or {}
    envelope_length = (parameters.get('fallback_policy') or {}).get('straight_preview', {}).get('envelope_length_m')
    highway_require(type(envelope_length) in (int, float) and math.isfinite(envelope_length)
        and envelope_length > 0,
        'revision.gap_envelope', 'Initial-gap revision requires the frozen vehicle envelope')
    def known_length(actor):
        value = actor.get('length_m')
        if isinstance(value, dict):
            value = value.get('value')
        return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else 0
    road_length = original['road']['roads'][0].get('road_geometry', {}).get('visible_length_m')
    if isinstance(road_length, dict):
        road_length = road_length.get('value')
    if type(road_length) not in (int, float) or not math.isfinite(road_length):
        road_length = (parameters.get('fallback_policy') or {}).get('highway_length_m')
    highway_require(type(road_length) in (int, float) and math.isfinite(road_length)
        and road_length > 0,
        'revision.road_extent', 'Initial-gap revision requires a finite frozen road extent')
    conditions = {}
    for actor_id, gap in corrections['actor_initial_gaps_m'].items():
        actor = actors.get(actor_id)
        highway_require(actor is not None and actor_id not in excluded
            and actor.get('actor_type') == 'vehicle'
            and actor.get('simulation_role') == 'participant'
            and actor.get('is_static') is False
            and bool(actor.get('trajectory')),
            'revision.actor_gap_scope', 'Initial gap must name a retained moving vehicle')
        first = actor['trajectory'][0]
        def known_x(point):
            position = point.get('position_ego_m') or {}
            value = position.get('x') if isinstance(position, dict) else None
            if isinstance(value, dict):
                value = value.get('value') if value.get('status') != 'unknown' else None
            return value
        highway_require(all(known_x(point) is None for point in actor['trajectory'])
            and first.get('time_s') == 0
            and first.get('lane_id') in original['road']['roads'][0]['lane_ids'],
            'revision.observed_gap_conflict', 'Cannot replace measured positions or an unbound starting lane')
        highway_require(type(gap) in (int, float) and math.isfinite(gap)
            and gap > max(envelope_length, (known_length(actor) + known_length(original['ego'])) / 2)
            and gap < road_length,
            'revision.actor_gap_range', 'Initial gap must fit the supported road')
        conditions[actor_id] = {
            'lane_id': first['lane_id'], 'position_ego_m': {'x': gap}}
    analysis = copy.deepcopy(original)
    analysis['simulation_scenarios'] = [{
        'scenario_id': 'user_gap_revision_001',
        'description': 'User-selected execution initial gaps; not video measurements.',
        'initial_conditions': conditions,
    }]
    quality = analysis['quality_report']
    quality['scene_revision'] = {
        'revision': REVISION, 'source': copy.deepcopy(source),
        'corrections': copy.deepcopy(corrections)}
    quality['scene_revision']['sha256'] = preview_digest(quality['scene_revision'])
    quality['execution_plan'] = {
        'revision': REVISION, 'sha256': quality['scene_revision']['sha256'],
        'execution_actor_ids': [analysis['ego']['actor_id'], *actors],
        'time_range_s': [0, analysis['metadata']['duration_s']],
        'user_corrections': copy.deepcopy(corrections),
        'authority': 'user_selected_execution_approximation',
    }
    return analysis, 'done_degraded'


def build(source, corrections):
    from _lib.contracts.v3 import _schema
    _schema('scene_corrections.schema.json',corrections)
    if source.get('kind')==HIGHWAY_SOURCE:
        if 'actor_initial_gaps_m' in corrections:
            return build_highway_gaps(source, corrections)
        return build_highway(source,corrections)
    require('highway_layout' not in corrections,'revision.highway_scope','Highway layout cannot revise an intersection')
    w,b,r=copy.deepcopy((source['source_world'],source['source_behavior'],source['review']))
    actors={a['actor_id']:a for a in b['actors']}; rows={a['actor_id']:a for a in r['actors']}
    roads=w['world']['road']['roads']; approach=roads[0]['lane_ids']
    for aid,lid in corrections.get('actor_lanes',{}).items():
        require(aid in actors and actors[aid]['actor_type']=='vehicle' and lid in approach,'revision.actor_lane','Correction must bind an existing vehicle and approach lane')
        actor=actors[aid];actor['simulation_role']='participant'
        rows[aid].update(execution='include',incoming_lane_id=lid,outgoing_lane_id=None,non_interacting=False,
            reason='User corrected lane assignment, corroborated by source frames; prior observation retained in revision certificate.')
        for point in actor['trajectory']:
            if point.get('lane_id') in approach:point['lane_id']=lid
            elif point.get('lane_id'):point['lane_id']=None
            if 'y' in point.get('position_ego_m',{}):
                require(point['position_ego_m'].get('status')=='estimated','revision.position_conflict','Lane correction requires review of an observed lateral coordinate')
                point['position_ego_m'].pop('y')
            point['detail']='Execution correction: '+aid+' follows '+lid+' on approach; original detail preserved in parent source.'
        for p in b.get('executor_primitives',[]):
            if p.get('actor_id')==aid and p.get('lane_id') is not None:p['lane_id']=lid
        for a in w.get('actor_hypotheses',[]):
            if a.get('actor_id')==aid:a['hypothesized_lane']=lid
    for order in r['orders']:
        if {order['behind'],order['ahead']} & set(corrections.get('actor_lanes',{})):
            order['reason']='Rechecked longitudinal order after user lane correction; this does not imply a shared lane.'
    entry=corrections.get('ego_entry_time_s')
    if entry is not None:
        require(type(entry) in (int,float),'revision.entry','Entry time must be numeric')
        for event in b['events']:
            if event.get('event_type')=='ego_intersection_entry':
                interval=event['time_range_s']
                require(interval[0]<=entry<=interval[1],'revision.entry','Requested time conflicts with original entry evidence interval')
                r['execution_entry_time_s']=entry
    r['input_sha256']=binding(w,b)
    a,status=project(w,b,r,source['frames'],source['parameters'],observation_contract=source.get('observation_contract',False))
    # Side-arm geometry is a reviewed scene approximation, never new observation IDs.
    expand_side_roads(a,corrections.get('side_road_lane_counts',{}))
    add_parking(a,corrections.get('parked_vehicles',[]))
    extend_duration(a,corrections.get('execution_duration_s'),source.get('media_duration_s'))
    q=a['quality_report']
    q['scene_revision']={'revision':REVISION,'source':copy.deepcopy(source),'corrections':copy.deepcopy(corrections)}
    q['scene_revision']['sha256']=preview_digest(q['scene_revision'])
    q['execution_plan']={'revision':REVISION,'sha256':q['scene_revision']['sha256'],
        'execution_actor_ids':a['simulation_scenarios'][0]['involved_actor_ids'],
        'time_range_s':[0,a['metadata']['duration_s']], 'user_corrections':copy.deepcopy(corrections)}
    return a,status


def extend_duration(analysis,target,media_duration):
    if target is None:return
    current=analysis['metadata']['duration_s']
    require(type(media_duration) in (int,float) and abs(target-media_duration)<=1e-6,
            'revision.duration','Execution duration must equal the media probe duration')
    require(current<=target<=current+1.0,'revision.duration','Only the unobserved media tail may be extrapolated')
    if target==current:return
    certificate=analysis['quality_report']['intersection_preview'];geometry=certificate['solver'].get('path_geometry')
    if geometry is None:
        from _lib.behavior.fidelity_motion import CONNECTOR_M
        entry=analysis['road']['roads'][0]['road_geometry']['visible_length_m']
        geometry={'entry_s_m':entry,'exit_s_m':entry+CONNECTOR_M,'connector_length_m':CONNECTOR_M,
                  'source':'execution_policy','verification':'source_profile_estimate_not_runtime_verified'}
    rows={r['actor_id']:r for r in certificate['review']['actors']}
    included=set(analysis['simulation_scenarios'][0]['involved_actor_ids'])
    for actor in [analysis['ego'],*analysis['actors']]:
        aid=actor['actor_id']
        if aid not in included or actor.get('is_static'):continue
        trajectory=actor['trajectory'];last=trajectory[-1]
        require(abs(last['time_s']-current)<=1e-7 and type(last.get('speed_mps')) in (int,float),
                'revision.duration','Dynamic trajectory does not end at the source observation boundary',actor_id=aid)
        position=last.get('position_ego_m') or {};x,y=position.get('x'),position.get('y')
        require(type(x) in (int,float) and type(y) in (int,float),'revision.duration','Dynamic trajectory lacks executable terminal position',actor_id=aid)
        end=x+last['speed_mps']*(target-current)
        require(end<=geometry['exit_s_m']+analysis['road']['roads'][2]['road_geometry']['visible_length_m'],
                'revision.duration','Tail extrapolation exceeds the supported route extent',actor_id=aid)
        row=rows[aid]
        lane=row['incoming_lane_id'] if end<geometry['entry_s_m'] else row['outgoing_lane_id'] if end>=geometry['exit_s_m'] else None
        trajectory.append(dict(time_s=target,lane_id=lane,position_ego_m={'x':end,'y':y},speed_mps=last['speed_mps'],
            execution_origin='media_tail_extrapolation',execution_extension=True))
    analysis['metadata']['duration_s']=target
    q=analysis['quality_report'];q['duration_extension']={'from_s':current,'to_s':target,'media_probe_duration_s':media_duration,
        'method':'constant_terminal_speed','observed':False,'reason':'Last decodable sample precedes the media endpoint.'}
    for key in ('allowed_degradations','required_degradations','applied_degradations'):
        q[key]=sorted(set(q.get(key,[]))|{'media_tail_motion_extrapolation'})


def expand_side_roads(analysis,claims):
    roads=analysis['road']['roads'];lanes={l['lane_id']:l for l in analysis['road']['lanes']}
    mapping={};memberships={};rebuilt=[];counter=1
    for road in roads:
        claim=claims.get(road['road_id']); old=road['lane_ids']
        if claim:
            require(road['road_id'] in {'road_002','road_004'} and set(claim)=={'count','status','confidence','reason'},'revision.side_scope','Only side-arm count may be revised here')
            require(claim['status']=='estimated' and type(claim['confidence']) in (int,float) and 0<=claim['confidence']<=1 and type(claim['count']) is int and len(old)<=claim['count']<=4 and bool(claim['reason']), 'revision.side_count','Side count must be an explicit bounded estimate, not a confirmed observation')
        n=claim['count'] if claim else len(old); ids=[]
        for i in range(n):
            lid=f'lane_{counter:03d}';counter+=1;ids.append(lid)
            lane=copy.deepcopy(lanes[old[i]]) if i<len(old) else dict(lane_type='driving',width_m=3.5,road_id=road['road_id'])
            if i<len(old):mapping[old[i]]=lid
            lane['lane_id']=lid;rebuilt.append(lane)
        memberships[road['road_id']]=ids
    def remap(node):
        if isinstance(node,list):
            for v in node:remap(v)
        elif isinstance(node,dict):
            for k,v in list(node.items()):
                if k in {'lane_id','incoming_lane_id','outgoing_lane_id'} and isinstance(v,str):node[k]=mapping.get(v,v)
                elif k=='lane_ids' and node.get('road_id') in memberships:node[k]=memberships[node['road_id']]
                elif k=='between' and isinstance(v,list):node[k]=[mapping.get(lane,lane) for lane in v]
                else:remap(v)
    # Source certificates remain in their original coordinate/ID space.
    for key in ('road','junctions','ego','actors','events','simulation_scenarios'):remap(analysis[key])
    analysis['road']['lanes']=rebuilt


def add_parking(analysis,vehicles):
    require(isinstance(vehicles,list) and len(vehicles)<=12,'revision.parking','At most twelve scenery vehicles')
    road=analysis['road']['roads'][0];areas=analysis['road']['roadside_areas']
    width={a['side']:a['width_m'] for a in areas if a.get('road_id')==road['road_id'] and a.get('area_type')=='curbside_parking'}
    occupied=[];existing={a['actor_id'] for a in analysis['actors']}
    for i,item in enumerate(vehicles):
        require(set(item)=={'side','x_m','color','vehicle_class'},'revision.parking','Invalid parking specification')
        side=item['side'];x=item['x_m'];aid=f'scenery_vehicle_{i+1:03d}'
        require(aid not in existing and side in width and type(x) in (int,float) and 5<=x<=road['road_geometry']['visible_length_m']-12,'revision.parking','Parking outside supported approach shoulder')
        require(all(s!=side or abs(xx-x)>=7 for s,xx in occupied),'revision.parking_overlap','Parked vehicles overlap')
        occupied.append((side,x));existing.add(aid)
        # Lane-centre convention: ego is in the middle of three 3.5 m lanes.
        ids=road['lane_ids'];lane_widths={l['lane_id']:l['width_m'] for l in analysis['road']['lanes']}
        ego_lane=analysis['ego']['trajectory'][0]['lane_id'];offset=sum(lane_widths[l] for l in ids[:ids.index(ego_lane)])+lane_widths[ego_lane]/2
        y=(sum(lane_widths[l] for l in ids)+width[side]/2-offset) if side=='left' else (-width[side]/2-offset)
        analysis['actors'].append(dict(actor_id=aid,actor_type='vehicle',simulation_role='background',is_static=True,
            identity_description='User-requested synthetic parked scenery; not a tracked video identity.',
            subtype=item['vehicle_class'],vehicle_class=item['vehicle_class'],color=item['color'],parking_side=side,
            trajectory=[dict(time_s=0.0,lane_id=None,position_ego_m={'x':x,'y':y},speed_mps=0.0)],
            provenance={'source':'user_requested_scenery','observed':False}))


def validate(analysis):
    c=analysis['quality_report']['scene_revision']
    require(c.get('revision')==REVISION and c.get('sha256')==preview_digest({k:v for k,v in c.items() if k!='sha256'}),'revision.binding','Revision certificate changed')
    rebuilt,_=build(c['source'],c['corrections'])
    require(analysis['quality_report'].get('intersection_preview')==rebuilt['quality_report'].get('intersection_preview'),'revision.audit','Underlying projection certificate changed')
    for key in ('road','junctions','ego','actors','events','environment','simulation_scenarios'):
        require(analysis.get(key)==rebuilt.get(key),'revision.projection','Corrected projection differs from reconstruction',path='/'+key)
    if c['source'].get('kind')==HIGHWAY_SOURCE:
        highway_require(analysis.get('metadata')==rebuilt.get('metadata'),
            'revision.timing','Highway revision changed the original timing')
