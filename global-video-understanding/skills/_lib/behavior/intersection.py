"""Evidence-bound intersection execution projection. Never mutate observations.

The certificate is a fact review, not permission to relax arbitrary failures.
All generated numbers belong to the separately hashed execution projection.
"""
from _lib.contracts.entry import ENTRY_TYPES

import copy
import math
import json

from _lib.behavior.compile import preview_digest
from _lib.contracts.v3 import ContractError
from _lib.paths import SKILLS

POLICY = {
    'policy_id': 'intersection_approximation.v1', 'road_length_m': 150.0,
    'lane_width_m': 3.5, 'parking_width_m': 2.5, 'speed_kmh': 30.0,
    'initial_x_m': 45.0, 'minimum_gap_m': 6.0,
    'maximum_speed_kmh': 80.0, 'crosswalk_main_offset_m': 30.0,
    'crosswalk_side_offset_m': 45.0,
}


class IntersectionBlocked(ContractError):
    code = 'intersection_execution_blocked'

    def __init__(self, rule, message, path=None, actual=None, expected=None,
                 owner='behavior', actor_id=None, retry_kind='new_evidence'):
        super().__init__(message, rule_id=rule, actor_id=actor_id)
        if retry_kind == 'content_repair':
            self.code = 'schema_validation_failed'
        self.diagnostics = dict(code=self.code, rule_id=rule, message=message,
            owner=owner, actor_id=actor_id, field_path=path, actual=actual,
            expected=expected, retry_kind=retry_kind)


def require(condition, rule, message, **details):
    if not condition:
        raise IntersectionBlocked(rule, message, **details)


def scalar(value):
    return value.get('value') if isinstance(value, dict) else value


def number(value):
    value = scalar(value)
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _linear_overlap_interval(p, q, limit, start, end):
    """Closed interval where abs(p + q*t) <= limit, or None."""
    if abs(q) < 1e-12:
        return (start, end) if abs(p + q*start) <= limit else None
    a, b = (-limit-p)/q, (limit-p)/q
    lo, hi = sorted((a,b))
    lo, hi = max(start,lo), min(end,hi)
    return (lo,hi) if lo <= hi else None


def validate_lateral_collision_envelopes(solution, rows, duration, lateral_state):
    """Continuous piecewise-linear rectangle-envelope check."""
    ids=sorted(rows)
    for i, aid in enumerate(ids):
        for bid in ids[i+1:]:
            breaks={0.,float(duration)}
            for key in (aid,bid):
                interval=rows[key].get('lateral_transition_interval_s')
                if interval: breaks.update(map(float,interval))
            points=sorted(breaks)
            for start,end in zip(points,points[1:]):
                ax0,ax1=solution['values']['x'+aid],solution['values']['x'+aid]+solution['values']['v'+aid]
                bx0,bx1=solution['values']['x'+bid],solution['values']['x'+bid]+solution['values']['v'+bid]
                ay0,ay1=lateral_state(aid,start),lateral_state(aid,end)
                by0,by1=lateral_state(bid,start),lateral_state(bid,end)
                x_interval=_linear_overlap_interval(ax0-bx0,ax1-bx1,5.0,start,end)
                if x_interval is None: continue
                slope=((ay1-by1)-(ay0-by0))/(end-start) if end>start else 0.
                intercept=(ay0-by0)-slope*start
                y_interval=_linear_overlap_interval(intercept,slope,2.2,start,end)
                if y_interval is not None and max(x_interval[0],y_interval[0]) <= min(x_interval[1],y_interval[1]):
                    raise IntersectionBlocked('intersection.lane_change_collision',
                        'Lane-change collision envelopes overlap continuously',actual=[aid,bid],
                        retry_kind='unsupported_capability')
    return True


def binding(world, behavior, evidence_catalog_sha256=None, policy=POLICY):
    value = dict(world=world, behavior=behavior, policy=policy)
    if evidence_catalog_sha256 is not None:
        value['evidence_catalog_sha256'] = evidence_catalog_sha256
    return preview_digest(value)


def path_contract(review):
    return review.get('path_contract_revision') == 'intersection_path.v1'


def validate_world_challenges(review, actors, frame_ids):
    """Validate read-only review challenges before handing them to the World owner."""
    challenges = review.get('world_challenges', [])
    ids = [item['challenge_id'] for item in challenges]
    require(len(ids) == len(set(ids)), 'intersection.world_challenge_id',
            'World challenge IDs must be unique', retry_kind='content_repair')
    for item in challenges:
        require(set(item['actor_ids']) <= set(actors), 'intersection.world_challenge_actor',
                'World challenge refers to an unknown actor', retry_kind='content_repair')
        require(set(item['evidence_frame_ids']) <= set(frame_ids), 'intersection.world_challenge_evidence',
                'World challenge refers to unavailable evidence', retry_kind='content_repair')
        append_path = 'world.road.roadside_areas[-]'
        if item.get('finding') == 'missing':
            require(item['world_paths'] == [append_path] and item['actor_ids'],
                    'intersection.world_challenge_missing_scope',
                    'A missing World fact may only propose one actor-bound roadside parking append',
                    retry_kind='content_repair')
        else:
            require(append_path not in item['world_paths'],
                    'intersection.world_challenge_append_scope',
                    'Only a missing roadside parking finding may request an append',
                    retry_kind='content_repair')
    require(not challenges or review.get('status') == 'needs_evidence',
            'intersection.world_challenge_status',
            'A review with World challenges must stop for owner re-adjudication', retry_kind='content_repair')
    return challenges


def project_topology(world, behavior, review, *, observation_contract=False):
    """Replace a wholly unknown exit placeholder only after continuity review.

    IDs are renamed in the private execution graph, never in observed files.
    An observed one-lane exit is not a placeholder and cannot be expanded.
    """
    from _lib.behavior.lane_completion import active, complete
    from _lib.behavior.review_contract import execution_view
    behavior = execution_view(behavior, review)
    if active(review): return complete(world, behavior, review)
    w,b,r=copy.deepcopy(world),copy.deepcopy(behavior),copy.deepcopy(review)
    roads=w['world']['road']['roads']; by_road={road['road_id']:road for road in roads}
    incoming=by_road[r['arms']['S']]; outgoing=by_road[r['arms']['N']]
    if len(outgoing['lane_ids']) == len(incoming['lane_ids']):
        return project_connections(w,b,r,[],observation_contract=observation_contract)
    index=next(i for i,road in enumerate(roads) if road is outgoing)
    path=f'world.road.roads[{index}].lane_ids'
    evidence=w.get('field_evidence',{}).get(path,[])
    unknown=bool(evidence) and all(e.get('status')=='unknown' for e in evidence)
    issues=[i for collection in ('questions_to_verify','ambiguities') for i in w.get(collection,[])
            if path in i.get('world_paths',[]) and i.get('resolution')=='unresolved']
    covered=set(r['covered_world_ids'])
    require(unknown and len(outgoing['lane_ids'])==1 and issues and
            all(i.get('question_id',i.get('ambiguity_id')) in covered for i in issues),
            'intersection.exit_topology', 'A differing observed or unreviewed exit count cannot be replaced', owner='world')
    if path_contract(r):
        included=[row for row in r['actors'] if row['execution']=='include']
        require(included and all(row.get('lateral_motion')!='unknown' and
                isinstance(row.get('exit_ordinal_from_right'),int) for row in included),
                'intersection.exit_continuity',
                'Unknown exit placeholder needs a resolved per-actor path', owner='behavior')
        require(max(row['exit_ordinal_from_right'] for row in included) < len(incoming['lane_ids']),
                'intersection.exit_topology', 'Reviewed exit ordinal exceeds bounded execution lanes', owner='behavior')
    else:
        require(r.get('exit_lane_continuity')=='same_order', 'intersection.exit_continuity',
                'Unknown exit placeholder needs evidenced same-order lane continuity', owner='behavior')
    old_lanes={lane['lane_id']:lane for lane in w['world']['road']['lanes']}
    old_exit=outgoing['lane_ids'][0]
    original=copy.deepcopy(w['world']['road'])
    old_to_new={}; rebuilt=[]; memberships={}; next_id=1
    for road in roads:
        old_ids=list(road['lane_ids']); ids=[]
        source_ids=incoming['lane_ids'] if road is outgoing else old_ids
        for old_id in source_ids:
            lid=f'lane_{next_id:03d}'; next_id+=1
            lane=copy.deepcopy(old_lanes[old_id])
            lane.update(lane_id=lid,road_id=road['road_id'])
            if road is outgoing:
                # Width and ordinal continuity are execution assumptions, not observations.
                lane={'lane_id':lid,'road_id':road['road_id'],'lane_type':'driving',
                      'width_m':number(old_lanes[old_id].get('width_m')) or POLICY['lane_width_m']}
            else:
                old_to_new[old_id]=lid
            ids.append(lid); rebuilt.append(lane)
        memberships[road['road_id']]=ids
    old_to_new[old_exit]=memberships[outgoing['road_id']][0]
    exit_for_incoming={old_to_new[lid]:memberships[outgoing['road_id']][i]
                       for i,lid in enumerate(incoming['lane_ids'])}
    def remap(node):
        if isinstance(node,list):
            for item in node: remap(item)
        elif isinstance(node,dict):
            for key,value in list(node.items()):
                if key in {'lane_id','incoming_lane_id','outgoing_lane_id','hypothesized_lane'} and isinstance(value,str):
                    node[key]=old_to_new.get(value,value)
                elif key=='lane_ids' and node.get('road_id') in memberships:
                    node[key]=list(memberships[node['road_id']])
                else: remap(value)
    remap(w); remap(b); remap(r)
    w['world']['road']['lanes']=rebuilt
    for junction in w['world']['junctions']:
        for connection in junction.get('lane_connections',[]):
            if (connection.get('incoming_road_id')==incoming['road_id'] and
                    connection.get('outgoing_road_id')==outgoing['road_id']):
                require(connection.get('maneuver')=='straight','intersection.exit_connection',
                        'Exit placeholder includes a non-straight connection',owner='world')
                connection['outgoing_lane_id']=exit_for_incoming[connection['incoming_lane_id']]
    actor_rows={row['actor_id']:row for row in r['actors']}
    source_actors={a['actor_id']:a for a in behavior['actors']}
    for actor in b['actors']:
        row=actor_rows[actor['actor_id']]
        if row['execution']!='include': continue
        row['outgoing_lane_id']=(memberships[outgoing['road_id']][row['exit_ordinal_from_right']]
            if path_contract(r) else exit_for_incoming[row['incoming_lane_id']])
        for source,point in zip(source_actors[actor['actor_id']]['trajectory'],actor['trajectory']):
            if source.get('lane_id')==old_exit:
                point['lane_id']=row['outgoing_lane_id']
    audit=[dict(field_path='/road',value=copy.deepcopy(w['world']['road']),observed_value=original,
                source='simulation_policy',policy_id=POLICY['policy_id'],degraded=True,
                input_sha256=review['input_sha256'],evidence_frame_ids=sorted({f for row in review['actors']
                    if row['execution']=='include' for f in row.get('path_evidence_frame_ids',row['evidence_frame_ids'])}),
                reason='Unknown exit placeholder expanded by reviewed per-actor continuity; execution-only ID remapping',
                source_lane_map=old_to_new,exit_for_incoming=exit_for_incoming)]
    return project_connections(w,b,r,audit,observation_contract=observation_contract)


def project_connections(w,b,r,audit,*,observation_contract=False):
    if not observation_contract: return w,b,r,audit
    junction=w['world']['junctions'][0]
    original=copy.deepcopy(junction.get('lane_connections',[]))
    connections=junction.setdefault('lane_connections',[])
    pairs={(c['incoming_lane_id'],c['outgoing_lane_id']) for c in connections}
    for row in sorted(r['actors'],key=lambda a:a['actor_id']):
        if row['execution']!='include': continue
        pair=(row['incoming_lane_id'],row['outgoing_lane_id'])
        if pair in pairs: continue
        connection=dict(incoming_road_id=r['arms']['S'],outgoing_road_id=r['arms']['N'],
            incoming_lane_id=pair[0],outgoing_lane_id=pair[1],maneuver='straight')
        connections.append(connection); pairs.add(pair)
        audit.append(dict(field_path=f'/junctions/0/lane_connections/{len(connections)-1}',
            value=copy.deepcopy(connection),observed_value=None,source='simulation_policy',
            policy_id=POLICY['policy_id'],degraded=True,input_sha256=r['input_sha256'],
            evidence_frame_ids=row.get('path_evidence_frame_ids',row['evidence_frame_ids']),reason='Reviewed straight execution edge; unobserved turns remain unknown'))
    from _lib.world.topology import validate_topology
    validate_topology(w['world'])
    return w,b,r,audit


def prerequisites(world, behavior):
    from _lib.runtime.routing import capability_issues
    scene = world['world']; roads = scene.get('road', {}).get('roads', [])
    require(scene.get('scene_type') == 'intersection' and len(roads) == 4
            and len(scene.get('junctions', [])) == 1,
            'intersection.four_arms', 'Exactly one four-arm junction is required', owner='world')
    require(not capability_issues(world, behavior), 'intersection.capability',
            'Unsupported road shape, signal change or vehicle maneuver', retry_kind='unsupported_capability')
    require(all(r.get('road_role') in {'incoming', 'outgoing', 'bidirectional'} for r in roads),
            'intersection.direction', 'Road direction must be evidenced', owner='world')
    from _lib.behavior.longitudinal import kinds
    longitudinal=set(kinds())
    require(all(e.get('event_type') in ENTRY_TYPES | longitudinal
                for e in behavior.get('events', [])),
            'intersection.event', 'Unsupported intersection event; supported motion is straight entry and reviewed longitudinal speed changes',
            retry_kind='unsupported_capability')
    require(all(p.get('type') in {'initial_state'} | ENTRY_TYPES | longitudinal
                for p in behavior.get('executor_primitives', [])),
            'intersection.primitive', 'Unsupported event execution', retry_kind='unsupported_capability')
    for actor in behavior.get('actors',[]):
        if actor.get('simulation_role')!='participant': continue
        points=actor.get('trajectory',[])
        require(len(points)>=2 and points[0].get('time_s')==0,
                'intersection.temporal_coverage','A participant needs coverage from the video start and at least two frames',
                actor_id=actor['actor_id'],retry_kind='unsupported_capability')
    return True


def validate_review(world, behavior, review, frame_ids, *, observation_contract=False, check_references=True):
    """Exact actor/frame coverage; known facts cannot be repaired by this review."""
    from _lib.contracts.v3 import _schema
    if not isinstance(frame_ids,dict):
        frame_ids={f['frame_id'] if isinstance(f,dict) else f:
                   (f if isinstance(f,dict) else {'frame_id':f}) for f in frame_ids}
    _schema('intersection_review.schema.json', review)
    require(review['input_sha256'] == binding(world, behavior, review.get('evidence_catalog_sha256')),
            'intersection.stale_review', 'Review input hash changed', retry_kind='content_repair')
    from _lib.behavior.review_contract import validate as validate_references, execution_view
    if check_references:validate_references(world, behavior, review)
    behavior = execution_view(behavior, review)
    actors = {a['actor_id']: a for a in behavior['actors']}
    validate_world_challenges(review, actors, frame_ids)
    require(review['status'] == 'accepted', 'intersection.evidence',
            review['reason'], retry_kind='new_evidence')
    require(not review.get('world_challenges'), 'intersection.world_challenge_unresolved',
            'Accepted review cannot retain unresolved World challenges', owner='world')
    prerequisites(world, behavior)
    if not behavior.get('video_fidelity'):
        from _lib.behavior.longitudinal import validate as validate_longitudinal
        validate_longitudinal(behavior,review,frame_ids)
    require(review['single_four_way'] and review['straight_only'],
            'intersection.scene', 'Review does not establish four-way straight execution')
    roads = {r['road_id']: r for r in world['world']['road']['roads']}
    require(set(review['arms'].values()) == set(roads) and len(set(review['arms'].values())) == 4,
            'intersection.arms', 'Arm mapping must cover each road exactly once', owner='world')
    require(review['arms'] == {'S':'road_001','W':'road_002','N':'road_003','E':'road_004'},
            'intersection.arm_numbering', 'Current Converter requires the accepted clockwise ego-anchored road numbering', owner='world')
    actors = {a['actor_id']: a for a in behavior['actors']}
    rows = {a['actor_id']: a for a in review['actors']}
    require(len(rows) == len(review['actors']) and set(rows) == set(actors),
            'intersection.actor_coverage', 'Review must include every observed actor')
    from _lib.behavior.lane_completion import active
    if active(review):
        for claim in review.get('dependency_bindings',{}).values():
            require(set(claim['evidence_frame_ids'])<=set(frame_ids),'intersection.dependency_evidence','Dependency evidence frame unavailable')
        for knowledge in review.get('lane_knowledge',{}).values():
            refs=knowledge['evidence_frame_ids'] + [f for a in knowledge['anchors'] for f in a['evidence_frame_ids']]
            require(set(refs)<=set(frame_ids),'intersection.lane_evidence','Lane knowledge refers to unavailable frames',owner='world')
        for aid,actor in actors.items():
            require(rows[aid]['evidence_frame_ids']==sorted({p['frame_id'] for p in actor['trajectory'] if p.get('frame_id')}),
                'intersection.actor_evidence','Actor evidence coverage differs before completion',actor_id=aid)
        world,behavior,review,_=project_topology(world,behavior,review,observation_contract=observation_contract)
        roads={r['road_id']:r for r in world['world']['road']['roads']}
        actors={a['actor_id']:a for a in behavior['actors']}
        rows={a['actor_id']:a for a in review['actors']}
    links = {(c['incoming_lane_id'], c['outgoing_lane_id']) for j in world['world']['junctions']
             for c in j.get('lane_connections', []) if c.get('maneuver') == 'straight'}
    for aid, actor in actors.items():
        row = rows[aid]
        required = sorted({p['frame_id'] for p in actor['trajectory'] if p.get('frame_id')})
        require(row['evidence_frame_ids'] == required and bool(required),
                'intersection.actor_evidence', 'Actor evidence frames differ', actor_id=aid,
                path=f'/actors/{aid}/evidence_frame_ids', expected=required,
                actual=row['evidence_frame_ids'], retry_kind='content_repair')
        require(set(required + row.get('context_frame_ids', [])) <= set(frame_ids),
                'intersection.context_frames', 'Review refers to an unavailable frame', actor_id=aid)
        if path_contract(review):
            path_frames=row.get('path_evidence_frame_ids',[])
            witnesses=row.get('path_witnesses') or {}
            require(bool(path_frames) and len(path_frames)<=12 and set(path_frames)<=set(frame_ids),
                    'intersection.path_evidence','Actor path evidence is missing or unavailable',actor_id=aid)
            require(set(f for values in witnesses.values() for f in values)<=set(path_frames),
                    'intersection.path_witness','Path witnesses must be contained in path evidence',actor_id=aid)
        if row['execution'] == 'exclude':
            require(actor.get('simulation_role') == 'background' and row['non_interacting'] is True,
                    'intersection.background_conflict', 'Only proven non-interacting background may be excluded', actor_id=aid)
            require(not any(aid in e.get('involved_actor_ids', []) for e in behavior['events']),
                    'intersection.background_event', 'An event participant cannot be excluded', actor_id=aid)
            continue
        require(actor.get('simulation_role') == 'participant' and not actor.get('is_static'),
                'intersection.actor_capability', 'This projection supports moving vehicle participants only', actor_id=aid,
                retry_kind='unsupported_capability')
        require(actor.get('actor_type') in {'vehicle', 'car', 'van', 'truck', 'suv', 'sedan', 'vehicle_car', 'vehicle_van'},
                'intersection.actor_type', 'Unsupported executable actor type', actor_id=aid)
        require(observation_contract or (row['incoming_lane_id'], row['outgoing_lane_id']) in links,
                'intersection.connection', 'Reviewed straight connection is absent from accepted World', actor_id=aid, owner='world')
        if observation_contract:
            if not path_contract(review):
                require(review.get('exit_lane_continuity')=='same_order','intersection.exit_continuity',
                        'New connection projection requires evidenced same-order continuation',owner='behavior')
            for connection in world['world']['junctions'][0].get('lane_connections',[]):
                if (connection['incoming_lane_id']==row['incoming_lane_id'] and connection.get('maneuver')=='straight'
                        and connection['outgoing_lane_id']!=row['outgoing_lane_id']
                        and (not path_contract(review) or row.get('lateral_motion')=='keeping')):
                    require(False,'intersection.connection_conflict','Review contradicts an existing straight connection',actor_id=aid)
        require(row['incoming_lane_id'] in roads[review['arms']['S']]['lane_ids'] and
                row['outgoing_lane_id'] in roads[review['arms']['N']]['lane_ids'],
                'intersection.path', 'Only the ego approach/straight exit corridor is executable', actor_id=aid)
        if observation_contract:
            incoming=roads[review['arms']['S']]['lane_ids']; outgoing=roads[review['arms']['N']]['lane_ids']
            if path_contract(review):
                entry= row.get('entry_ordinal_from_right'); exit_=row.get('exit_ordinal_from_right')
                require(entry==incoming.index(row['incoming_lane_id']) and exit_==outgoing.index(row['outgoing_lane_id']),
                        'intersection.path_ordinal','Reviewed lane ordinal disagrees with execution topology',actor_id=aid)
                motion=row.get('lateral_motion'); interval=row.get('lateral_transition_interval_s')
                require(all(witnesses.get(part) for part in ('incoming','junction','outgoing')),
                        'intersection.connection_witness','Observed execution connection needs incoming, junction and outgoing witnesses',actor_id=aid)
                if motion=='keeping':
                    require(entry==exit_ and interval is None,'intersection.path_motion','Keeping path changed ordinal or has a transition interval',actor_id=aid)
                elif motion in {'changing_left','changing_right'}:
                    delta=1 if motion=='changing_left' else -1
                    require(exit_==entry+delta and isinstance(interval,list) and interval[0]<interval[1],
                            'intersection.path_motion','Only one evidenced adjacent lane change is supported',actor_id=aid,
                            retry_kind='unsupported_capability')
                    times={frame_ids[f]['time_s'] if isinstance(frame_ids,dict) else None for f in path_frames}
                    require(set(interval)<=times,'intersection.path_transition_evidence',
                            'Lane-change interval endpoints must be supplied path-evidence frames',actor_id=aid)
                else:
                    require(False,'intersection.path_motion','Executable actor lateral motion remains unknown',actor_id=aid)
            elif len(incoming)==len(outgoing):
                require(incoming.index(row['incoming_lane_id'])==outgoing.index(row['outgoing_lane_id']),
                        'intersection.order_conflict','Same-order continuation cannot swap exit lanes',actor_id=aid)
            for c in world['world']['junctions'][0].get('lane_connections',[]):
                if (c['incoming_lane_id'],c['outgoing_lane_id'])==(row['incoming_lane_id'],row['outgoing_lane_id']):
                    require(c.get('maneuver')=='straight','intersection.connection_conflict',
                            'A known turning edge cannot become straight',actor_id=aid)
        require(all(p.get('lane_id') in {None, row['incoming_lane_id'], row['outgoing_lane_id']}
                    for p in actor['trajectory']),
                'intersection.lane_conflict', 'Review conflicts with an observed lane', actor_id=aid)
        require(len(actor['trajectory']) >= 2, 'intersection.temporal_coverage',
                'A moving participant needs two or more evidence frames', actor_id=aid)
    require(rows.get('ego', {}).get('execution') == 'include', 'intersection.ego', 'Ego must execute')
    if path_contract(review):
        from _lib.behavior.longitudinal import events as longitudinal_events
        motions=[row.get('lateral_motion') for row in rows.values() if row['execution']=='include']
        require(sum(value in {'changing_left','changing_right'} for value in motions)<=1,
                'intersection.multiple_lane_changes','At most one participant may change lane in the bounded preview',
                retry_kind='unsupported_capability')
        require(not any(value in {'changing_left','changing_right'} for value in motions) or
                (not behavior.get('video_fidelity') and not longitudinal_events(behavior)),
                'intersection.lane_change_motion_scope',
                'Lane change cannot be combined with longitudinal or video-fidelity motion in v1',
                retry_kind='unsupported_capability')
        expected='same_order' if set(motions)<={'keeping'} else 'unknown'
        require(review.get('exit_lane_continuity')==expected,'intersection.exit_continuity',
                'Global continuity summary disagrees with per-actor paths',owner='behavior')
    for relation in review['orders']:
        require(relation['behind'] in rows and relation['ahead'] in rows and relation['behind'] != relation['ahead'],
                'intersection.order', 'Invalid actor order')
        require(bool(relation['evidence_frame_ids']) and set(relation['evidence_frame_ids']) <= set(frame_ids),
                'intersection.order_evidence', 'Order evidence unavailable')
    for item in review['parking'] + review['crosswalks'] + review['signals']:
        require(item['road_id'] in roads and bool(item['evidence_frame_ids']) and
                set(item['evidence_frame_ids']) <= set(frame_ids),
                'intersection.facility_evidence', 'Facility needs a known road and supplied evidence', owner='world')
    return rows


def solve_motion(world, behavior, review, frames, *, observation_contract=False, check_references=True):
    """Compile straight constant-speed tracks into evidence-bound linear constraints."""
    from _lib.experimental.constraint_solver import solve, REVISION
    rows = validate_review(world, behavior, review, frames, observation_contract=observation_contract,check_references=check_references)
    world,behavior,review,_=project_topology(world,behavior,review, observation_contract=observation_contract)
    rows={row['actor_id']:row for row in review['actors']}
    from _lib.behavior.longitudinal import events, validate, sampling_facts
    if behavior.get('video_fidelity') or events(behavior):
        from _lib.behavior.fidelity_motion import compile_motion
        if not behavior.get('video_fidelity'):
            validate(behavior,review,frames)
        return compile_motion(world,behavior,review,frames,POLICY,
            motion_facts=None if behavior.get('video_fidelity') else sampling_facts(behavior,frames))
    actors = {a['actor_id']: a for a in behavior['actors'] if rows[a['actor_id']]['execution'] == 'include'}
    roads = {r['road_id']: r for r in world['world']['road']['roads']}
    length = number(roads[review['arms']['S']].get('road_geometry', {}).get('visible_length_m')) or POLICY['road_length_m']
    variables, constraints, preferences = [], [], []
    def constraint(key, coef, evidence, lower=None, upper=None):
        row = dict(id=key, coefficients=coef, evidence_frame_ids=evidence)
        if lower is not None: row['lower'] = lower
        if upper is not None: row['upper'] = upper
        constraints.append(row)
    def numeric(key, coefficients, raw, value, refs):
        from _lib.behavior.lane_completion import active
        if active(review) and isinstance(raw,dict) and raw.get('status')=='estimated':
            interval=raw.get('range')
            if interval is not None:
                require(isinstance(interval,list) and len(interval)==2 and all(number(x) is not None for x in interval) and interval[0]<=interval[1],
                        'intersection.estimate_range','Invalid estimate interval')
                constraint(key,coefficients,refs,interval[0],interval[1])
            else:
                preferences.append(dict(id=key,coefficients=coefficients,value=value,weight=20.0,evidence_frame_ids=refs))
        else: constraint(key,coefficients,refs,value,value)
    for aid in sorted(actors):
        actor = actors[aid]; trajectory = actor['trajectory']; facts = rows[aid]
        for prefix, lo, hi, hint, unit in [('x', 0, length, POLICY['initial_x_m'], 'm'),
                                         ('v', 0.1, POLICY['maximum_speed_kmh']/3.6, POLICY['speed_kmh']/3.6, 'm/s')]:
            variables.append(dict(id=prefix+aid, lower=lo, upper=hi, hint=hint, weight=10 if prefix=='v' else 1, unit=unit))
        for i, point in enumerate(trajectory):
            t = number(point.get('time_s')); require(t is not None, 'intersection.time', 'Sample time missing', actor_id=aid)
            evidence = [point['frame_id']]
            speed = number(point.get('speed_mps'))
            if speed is not None:
                numeric(f'{aid}.v.{i}', {'v'+aid: 1}, point.get('speed_mps'), speed, evidence)
            lane = point.get('lane_id')
            if lane is not None:
                constraint(f'{aid}.lane.{i}', {'x'+aid: 1, 'v'+aid: t}, evidence,
                           length if lane == facts['outgoing_lane_id'] else None,
                           length if lane == facts['incoming_lane_id'] else None)
            x = number(point.get('position_ego_m', {}).get('x'))
            # Frozen ego origin is translated into the simulation road coordinate system.
            if x is not None:
                coefficients = {'x'+aid: 1, 'v'+aid: t}
                coefficients['xego'] = coefficients.get('xego', 0)-1
                numeric(f'{aid}.x.{i}', coefficients, point.get('position_ego_m'), x, evidence)
        require(trajectory[0]['time_s'] == 0, 'intersection.late_actor',
                'Late-appearing participant spawn is not yet supported', actor_id=aid, retry_kind='unsupported_capability')
        exit_length = number(roads[review['arms']['N']].get('road_geometry',{}).get('visible_length_m')) or POLICY['road_length_m']
        constraint(aid+'.road_extent', {'x'+aid:1,'v'+aid:trajectory[-1]['time_s']},
                   facts['evidence_frame_ids'], upper=length+exit_length)
    for event in behavior['events']:
        ids = event.get('involved_actor_ids', [])
        require(len(ids) == 1 and ids[0] in actors, 'intersection.event_actor', 'Entry event must bind one participant')
        aid = ids[0]; interval = event.get('time_range_s', event.get('time_interval_s'))
        if isinstance(interval, dict): interval = [interval['start'], interval['end']]
        require(isinstance(interval, list) and len(interval) == 2, 'intersection.entry_interval', 'Entry interval missing')
        evidence = [e['frame_id'] for e in event['evidence'] if e.get('frame_id')]
        selected=review.get('execution_entry_time_s') if aid=='ego' else None
        if selected is not None:
            require(interval[0]<=selected<=interval[1],'intersection.entry_choice','Selected entry time exceeds observed interval')
            constraint(event['event_id']+'.selected_time', {'x'+aid:1,'v'+aid:selected},evidence,lower=length,upper=length)
        constraint(event['event_id']+'.before', {'x'+aid: 1, 'v'+aid: interval[0]}, evidence, upper=length)
        constraint(event['event_id']+'.after', {'x'+aid: 1, 'v'+aid: interval[1]}, evidence, lower=length)
    pairs = set()
    for i, relation in enumerate(review['orders']):
        a, b = relation['behind'], relation['ahead']
        from _lib.behavior.lane_completion import active
        if active(review) and (a not in actors or b not in actors): continue
        require(a in actors and b in actors, 'intersection.order_scope', 'Order references excluded actor')
        pairs.add(frozenset((a,b)))
        end = min(actors[a]['trajectory'][-1]['time_s'], actors[b]['trajectory'][-1]['time_s'])
        for t in (0, end):
            constraint(f'order.{i}.{t}', {'x'+b: 1, 'v'+b: t, 'x'+a: -1, 'v'+a: -t},
                       relation['evidence_frame_ids'], lower=POLICY['minimum_gap_m'])
        trend = relation['trend']
        if trend != 'unknown':
            constraint(f'trend.{i}', {'v'+b: 1, 'v'+a: -1}, relation['evidence_frame_ids'],
                       lower=(1/3.6 if trend=='receding' else 0) if trend in {'steady', 'receding'} else None,
                       upper=(-1/3.6 if trend=='approaching' else 0) if trend in {'steady', 'approaching'} else None)
    ids = sorted(actors)
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            if rows[a]['incoming_lane_id'] == rows[b]['incoming_lane_id']:
                require(frozenset((a,b)) in pairs, 'intersection.order_coverage',
                        'Every same-lane pair needs reviewed order', actual=[a,b])
    problem = dict(revision=REVISION, variables=variables, constraints=constraints, preferences=preferences, predicates=[],
                   coverage=dict.fromkeys(('evidence','paths','critical_predicates','target_capability'), True))
    result = solve(problem, frames)
    require(result['status'] == 'feasible_stable', 'intersection.motion_constraints',
            'Motion projection cannot satisfy its hard constraints', actual=result,
            retry_kind='unsupported_capability' if result['status']=='unsupported' else 'new_evidence')
    return result, problem


def project(world, behavior, review, frames, parameters, *, observation_contract=False):
    """Build a private execution copy and retain every original observation."""
    from _lib.behavior.compile import derive_initial_states
    from _lib.contracts.v3 import build_analysis
    if not isinstance(frames,dict): frames={f['frame_id']:f for f in frames}
    solution, problem = solve_motion(world, behavior, review, frames, observation_contract=observation_contract)
    w, b, execution_review, topology_provenance = project_topology(world,behavior,review, observation_contract=observation_contract)
    scene = w['world']; roads = {r['road_id']: r for r in scene['road']['roads']}
    lanes = {l['lane_id']: l for l in scene['road']['lanes']}
    rows = {r['actor_id']: r for r in execution_review['actors']}
    provenance = topology_provenance
    def record(path, value, original, evidence, reason):
        provenance.append(dict(field_path=path, value=copy.deepcopy(value), source='simulation_policy',
            policy_id=POLICY['policy_id'], observed_value=copy.deepcopy(original),
            evidence_frame_ids=evidence, reason=reason, degraded=True,
            input_sha256=review['input_sha256']))
    original_roles={a['actor_id']:a.get('simulation_role') for a in behavior['actors']}
    for actor in b['actors']:
        aid=actor['actor_id']
        if original_roles[aid]!=actor.get('simulation_role'):
            record('/execution_membership/'+aid,actor['simulation_role'],original_roles[aid],
                   rows[aid]['evidence_frame_ids'],'Reviewed moving vehicle included in private execution view')
    for rid, road in roads.items():
        geometry = road.setdefault('road_geometry', {})
        if number(geometry.get('visible_length_m')) is None:
            record(f'/road/roads/{rid}/road_geometry/visible_length_m', POLICY['road_length_m'],
                   geometry.get('visible_length_m'), [], 'Uncalibrated road extent')
            geometry['visible_length_m'] = POLICY['road_length_m']
    for lid, lane in lanes.items():
        if number(lane.get('width_m')) is None:
            record(f'/road/lanes/{lid}/width_m', POLICY['lane_width_m'], lane.get('width_m'), [], 'Uncalibrated lane width')
            lane['width_m'] = POLICY['lane_width_m']
    # Only explicit lane-count uncertainty is covered; never clear arbitrary questions.
    covered = set(review['covered_world_ids']); found = set()
    for collection, id_key in [('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')]:
        kept = []
        for item in w.get(collection, []):
            if item[id_key] not in covered:
                kept.append(item); continue
            paths = item.get('world_paths', [])
            from _lib.behavior.review_contract import lane_count_paths
            allowed = lane_count_paths(w, review['arms']['S'])
            if observation_contract: allowed.add('world.junctions[0].lane_connections')
            require(paths and set(paths) <= allowed and item.get('owner', 'world') == 'world',
                    'intersection.uncovered_world', 'Only non-approach lane-count uncertainty may be projected', actual=item, owner='world')
            for i, road in enumerate(scene['road']['roads']):
                if not any(path.startswith(f'world.road.roads[{i}].') for path in paths):
                    continue
                for knowledge in (road.get('lane_knowledge'), review.get('lane_knowledge',{}).get(road['road_id'])):
                    if not knowledge or knowledge['kind']=='unknown':
                        continue
                    count=len(road['lane_ids'])
                    require((knowledge['kind']=='exact' and knowledge['count']==count) or
                            (knowledge['kind']=='minimum' and knowledge['count']<=count),
                            'intersection.count_conflict', 'Execution lane count contradicts declared exact count or minimum',
                            owner='world', retry_kind='fact_conflict', actual=knowledge, expected=count)
            found.add(item[id_key])
            record('/execution_scope/'+item[id_key], paths, item, [], 'Estimated topology retained only in execution copy')
            item['resolution'] = 'resolved'
            kept.append(item)
        w[collection] = kept
    require(found == covered, 'intersection.unknown_world_id', 'Unknown covered World issue')
    # Limitations remain visible; only explicit scale and excluded background impacts are scoped out.
    from _lib.behavior.lane_completion import active, scope_dependencies
    if active(review): scope_dependencies(w,b,review,provenance)
    from _lib.behavior.compile import preview_scale_limitation
    excluded = {aid for aid,row in rows.items() if row['execution'] == 'exclude'}
    kept = []
    for item in b['semantic_limitations']:
        from _lib.behavior.lane_completion import active
        claim=review.get('limitation_bindings',{}).get(item['limitation_id']) if active(review) else None
        if claim:
            if claim['kind']=='non_approach_lane_count':
                from _lib.behavior.review_contract import lane_limit_covered
                require(lane_limit_covered(item,claim,world,behavior,review),
                    'intersection.uncovered_limitation','Lane-count scope does not match covered World issues',actual=item)
                record('/execution_limitations/'+item['limitation_id'],claim,item,[],
                       'Non-approach lane count explicitly bound to projected World uncertainty')
                continue
            ids=set(claim['actor_ids'])
            from _lib.runtime.execution_scope import references
            affected=references(item)
            event_ids=set(item.get('affected_event_ids',[])) | set((item.get('scope_binding') or {}).get('affected_event_ids',[]))
            events={e['event_id']:e for e in b.get('events',[])}
            require(event_ids<=set(events),'intersection.limitation_binding','Limitation references unavailable event',actual=item)
            for event_id in event_ids: affected.update(events[event_id].get('involved_actor_ids',[]))
            require(affected<=ids and not item.get('world_paths') and not item.get('related_world_paths'),
                'intersection.limitation_binding','Scope claim omits affected actor or world dependency',actual=item)
            covered_exit=(claim['kind']=='exit_lane_assignment' and claim['road_id']==review['arms']['N']
                and set(item.get('impact_domains',[]))<={'lane','path'} and ids<=set(rows)
                and all(rows[aid]['execution']=='exclude' or rows[aid]['outgoing_lane_id'] in roads[review['arms']['N']]['lane_ids'] for aid in ids))
            covered_background=claim['kind']=='excluded_background' and ids<=excluded
            require(covered_exit or covered_background,'intersection.uncovered_limitation','Bound limitation is not resolved by projection',actual=item)
            record('/execution_limitations/'+item['limitation_id'],claim,item,[], 'Structured dependency discharged by execution projection')
            continue
        if item['limitation_id'] not in review['covered_limitation_ids']:
            kept.append(item); continue
        from _lib.behavior.review_contract import excluded_limitation
        require(preview_scale_limitation(item) or excluded_limitation(item, b, excluded),
                'intersection.uncovered_limitation', 'This limitation is not an approved scale/background approximation', actual=item)
    b['semantic_limitations'] = kept
    areas = scene['road'].setdefault('roadside_areas', [])
    known = {a['area_id']:a for a in areas}
    parking = {a['area_id']:a for a in review['parking']}
    for area in areas:
        if area.get('type') in {'parking','parallel_parking','curbside_parking','parking_band','parking_strip'} or area.get('area_type') == 'curbside_parking':
            require(area['area_id'] in parking, 'intersection.parking_coverage', 'Observed parking area missing from review', owner='world')
    for item in review['parking']:
        area = known.get(item['area_id'])
        require(area is not None, 'intersection.parking_fact', 'Parking must originate in accepted World', owner='world')
        require(area.get('road_id') in {None, item['road_id']} and area.get('side') == item['side'],
                'intersection.parking_conflict', 'Parking review changes known road/side', owner='world')
        area.update(road_id=item['road_id'], area_type='curbside_parking')
        if number(area.get('width_m')) is None:
            record('/road/roadside_areas/'+item['area_id']+'/width_m', POLICY['parking_width_m'],
                   area.get('width_m'), item['evidence_frame_ids'], 'Parking width approximation')
            area['width_m'] = POLICY['parking_width_m']
    junction = scene['junctions'][0]
    for collection, id_key in [('crosswalks','crosswalk_id'), ('traffic_signals','signal_id')]:
        facts = review['crosswalks' if collection=='crosswalks' else 'signals']
        originals = {i[id_key]:i for i in junction.get(collection, [])}
        require(set(originals) <= {i[id_key] for i in facts}, 'intersection.facility_coverage',
                'Structured observed facility omitted from review', owner='world')
        result = []
        for fact in facts:
            item = copy.deepcopy(originals.get(fact[id_key], {})); rid = fact['road_id']
            require(item.get('road_id') in {None,rid} and item.get('related_road_id') in {None,rid},
                    'intersection.facility_road', 'Facility road conflict', owner='world')
            item.update({id_key:fact[id_key], 'road_id':rid})
            if collection == 'crosswalks':
                item['related_road_id'] = rid
            length = number(roads[rid]['road_geometry']['visible_length_m'])
            offset = POLICY['crosswalk_main_offset_m'] if rid in {review['arms']['S'],review['arms']['N']} else POLICY['crosswalk_side_offset_m']
            require(length >= offset, 'intersection.facility_bounds', 'Road too short for approved facility layout', owner='world')
            x = length-offset if roads[rid]['road_role']=='incoming' else offset
            key='crosswalk.'+fact[id_key]
            if collection=='crosswalks' and key in solution['values']:
                shift=0 if rid==review['arms']['S'] else solution['path_geometry']['exit_s_m']
                x=solution['values'][key]-shift
            if collection=='traffic_signals':
                x=length if roads[rid]['road_role']=='incoming' else 0.
            interval=fact.get('ego_crossing_interval_s') if collection=='crosswalks' else None
            if interval is not None:
                require(rid in {review['arms']['S'],review['arms']['N']} and interval[0]<interval[1],
                        'intersection.crosswalk_interval','Crosswalk timing must belong to the ego corridor',owner='behavior')
                evidence_times={frames[f]['time_s'] for f in fact['evidence_frame_ids']}
                require(set(interval)<=evidence_times and interval[-1]<=behavior['actors'][next(i for i,a in enumerate(behavior['actors']) if a['actor_id']=='ego')]['trajectory'][-1]['time_s'],
                        'intersection.crosswalk_evidence','Crosswalk crossing needs both supplied boundary frames',owner='behavior')
                shift=0 if rid==review['arms']['S'] else number(roads[review['arms']['S']]['road_geometry']['visible_length_m'])
                from _lib.behavior.fidelity_motion import state as motion_state
                if 'profiles' in solution and rid==review['arms']['N']:
                    shift+=solution['path_geometry']['connector_length_m']
                lo,hi=[motion_state(solution,'ego',t)[0]-shift for t in interval]
                require(max(0,lo)<=min(length,hi)+1e-7,'intersection.crosswalk_geometry','Crosswalk event conflicts with road bounds',owner='behavior',actual=dict(crosswalk_id=fact[id_key],interval_s=interval,position_interval_m=[lo,hi],road_bounds_m=[0,length]))
                x=min(max(x,max(0,lo)),min(length,hi))
            if collection=='crosswalks' and rid==review['arms']['S']:
                relation=fact.get('ego_relation_at_start')
                require(relation in {'ahead','behind'} or interval is not None,
                        'intersection.crosswalk_relation','Approach crosswalk needs its initial ego relation or crossing interval',owner='behavior')
                if relation=='ahead': x=max(x,solution['values']['xego']+1.)
                if relation=='behind': x=min(x,solution['values']['xego']-1.)
                require(0<=x<=length and (interval is None or lo-1e-7<=x<=hi+1e-7),
                        'intersection.crosswalk_order','Crosswalk relation conflicts with crossing time',owner='behavior')
            if not item.get('position_ego_m'):
                item['position_ego_m'] = {'x':x, 'y':0., 'z':5. if collection=='traffic_signals' else 0.}
                record('/junctions/'+collection+'/'+fact[id_key]+'/position_ego_m', item['position_ego_m'], None,
                       fact['evidence_frame_ids'], 'Junction-relative execution layout; crosswalk reference constrained by reviewed timing and initial relation')
            if collection=='crosswalks' and (interval is not None or rid==review['arms']['S']):
                actual=number(item['position_ego_m'].get('x'))
                require(actual is not None and (interval is None or lo-1e-7<=actual<=hi+1e-7),
                        'intersection.crosswalk_position','Known crosswalk position conflicts with timing',owner='world')
                if rid==review['arms']['S'] and fact.get('ego_relation_at_start') in {'ahead','behind'}:
                    ahead=actual>solution['values']['xego']
                    require(ahead==(fact['ego_relation_at_start']=='ahead'),'intersection.crosswalk_position',
                            'Known crosswalk position conflicts with initial relation',owner='world')
            if collection == 'traffic_signals':
                require(fact['control'] in {'ego','conflicting'}, 'intersection.signal_control',
                        'Unknown signal control relation',owner='world')
                observed_timeline = copy.deepcopy(item.get('state_timeline', []))
                states = {r.get('state') for r in observed_timeline} - {None,'unknown'}
                changing_signal = len(states) > 1
                require(changing_signal or not states or fact['state'] in states,
                        'intersection.signal_conflict', 'Signal review conflicts with known state/timeline', owner='world')
                state = 'green' if changing_signal else fact['state']
                if changing_signal:
                    record('/junctions/traffic_signals/'+fact[id_key]+'/state_timeline',
                           [{'time_s':0., 'state':'green'}], observed_timeline,
                           fact['evidence_frame_ids'], 'Authorized time-varying signal replacement with all-green execution; observed timeline retained')
                if state == 'unknown':
                    require(fact['control'] != 'unknown', 'intersection.signal_control', 'Unknown signal control relation', owner='world')
                    state = 'green' if fact['control']=='ego' else 'red'
                    record('/junctions/traffic_signals/'+fact[id_key]+'/state', state, None,
                           fact['evidence_frame_ids'], 'Static non-conflicting signal approximation')
                duration=max(p['time_s'] for a in b['actors'] if a['actor_id']=='ego' for p in a['trajectory'])
                approach_length=number(roads[review['arms']['S']]['road_geometry']['visible_length_m'])
                from _lib.behavior.fidelity_motion import state as motion_state
                enters=motion_state(solution,'ego',duration)[0]>=approach_length
                require(not (fact['control']=='ego' and state!='green' and enters),
                        'intersection.red_entry', 'Observed entry conflicts with non-green ego signal', owner='world')
                require(changing_signal or not (fact['control']=='conflicting' and state=='green'),
                        'intersection.conflicting_green', 'Conflicting movement cannot receive green during ego traversal', owner='world')
                item['state_timeline'] = [{'time_s':0., 'state':state}]
            result.append(item)
        junction[collection] = result
    env = scene.setdefault('environment', {})
    for key, value in [('season','summer'), ('topology','flat')]:
        original = env.get(key, env.get('grade') if key=='topology' else None)
        if key not in env or (key=='topology' and env[key]!='flat'):
            record('/environment/'+key, value, original, [], 'Authorized environment execution approximation')
            env[key] = value
    length = number(roads[review['arms']['S']]['road_geometry']['visible_length_m'])
    incoming = roads[review['arms']['S']]['lane_ids']
    outgoing = roads[review['arms']['N']]['lane_ids']
    def lane_centers(ids):
        result={}; edge=0.
        for lid in ids:
            width=number(lanes[lid]['width_m']); result[lid]=edge+width/2; edge+=width
        return result
    centers = lane_centers(incoming); exit_centers=lane_centers(outgoing)
    anchor = centers[rows['ego']['incoming_lane_id']]
    exit_anchor = exit_centers[rows['ego']['outgoing_lane_id']]
    actor_paths={}
    def lateral_state(aid,time_s):
        row=rows[aid]
        start_y=centers[row['incoming_lane_id']]-anchor
        end_y=exit_centers[row['outgoing_lane_id']]-exit_anchor
        interval=row.get('lateral_transition_interval_s')
        if row.get('lateral_motion') not in {'changing_left','changing_right'} or not interval:
            return start_y
        if time_s<=interval[0]: return start_y
        if time_s>=interval[1]: return end_y
        return start_y+(end_y-start_y)*(time_s-interval[0])/(interval[1]-interval[0])
    included_rows={aid:rows[aid] for aid in rows if aid not in excluded}
    duration=max(point['time_s'] for actor in b['actors'] if actor['actor_id'] in included_rows for point in actor['trajectory'])
    if path_contract(execution_review):
        validate_lateral_collision_envelopes(solution,included_rows,duration,lateral_state)
    for actor in b['actors']:
        aid = actor['actor_id']; row = rows[aid]
        if aid in excluded:
            continue
        x0, speed = solution['values']['x'+aid], solution['values']['v'+aid]
        start_y=centers[row['incoming_lane_id']]-anchor
        end_y=exit_centers[row['outgoing_lane_id']]-exit_anchor
        interval=row.get('lateral_transition_interval_s')
        if row.get('lateral_motion') in {'changing_left','changing_right'}:
            require(0 < interval[0] < interval[1] < duration,
                    'intersection.path_transition_window','Lane change must leave nonempty straight segments before and after',
                    actor_id=aid,retry_kind='unsupported_capability')
            width=abs(end_y-start_y)
            require(width>0,'intersection.path_transition_width','Lane change has zero lateral displacement',actor_id=aid)
            actor_paths[aid]={
                'contract':'intersection_lane_change.v1','lateral_motion':row['lateral_motion'],
                'source_lane_id':row['incoming_lane_id'],'target_lane_id':row['outgoing_lane_id'],
                'transition_interval_s':copy.deepcopy(interval),'lateral_displacement_m':width,
                'lateral_speed_kmh':round(width/(interval[1]-interval[0])*3.6,4),
                'evidence_frame_ids':copy.deepcopy(row['path_evidence_frame_ids'])}
        for point in actor['trajectory']:
            from _lib.behavior.fidelity_motion import state as motion_state
            old = copy.deepcopy(point); x, point_speed = motion_state(solution,aid,point['time_s'])
            y=lateral_state(aid,point['time_s'])
            observed_y=number(point.get('position_ego_m',{}).get('y'))
            require(observed_y is None or abs(observed_y-y)<=0.1,
                    'intersection.lateral_conflict', 'Observed lateral coordinate conflicts with reviewed path approximation', actor_id=aid)
            point.update(lane_id=row['incoming_lane_id'] if x < length-1e-7 else row['outgoing_lane_id'],
                         position_ego_m={'x':x, 'y':y}, speed_mps=point_speed)
            # Preserve a boundary sample's evidenced incoming lane.
            if abs(x-length)<=1e-7 and old.get('lane_id') == row['incoming_lane_id']:
                point['lane_id'] = old['lane_id']
            record('/actors/'+aid+'/trajectory/'+str(point['time_s']), point, old,
                   [point['frame_id']], 'Joint path-motion projection; translated frozen ego origin')
        if 'profiles' in solution:
            from _lib.behavior.fidelity_motion import complete_trajectory
            complete_trajectory(actor,solution,row['incoming_lane_id'],row['outgoing_lane_id'],y)
    b['executor_primitives'] = [p for p in b['executor_primitives'] if p['type']!='initial_state']
    for primitive in b['executor_primitives']:
        if primitive['type'] not in ENTRY_TYPES:
            continue
        aid = primitive['actor_id']; speed = solution['values']['v'+aid]
        if 'profiles' in solution:
            from _lib.behavior.fidelity_motion import state as motion_state
            lo,hi=0.,solution['profiles'][aid][-1]['time_s']
            for _ in range(40):
                mid=(lo+hi)/2
                if motion_state(solution,aid,mid)[0]<length: lo=mid
                else: hi=mid
            primitive['trigger_time_s']=round(hi,8)
        else:
            primitive['trigger_time_s'] = (length-solution['values']['x'+aid])/speed
    b = derive_initial_states(b)
    params = copy.deepcopy(parameters)
    params['fallback_policy']['straight_preview']['enabled'] = False
    analysis, status = build_analysis(w, b, params, execution_scope_excluded_actor_ids=excluded, observation_contract=observation_contract)
    require(status in {'done','done_degraded'}, 'intersection.remaining_gaps',
            'Projection still has uncovered execution gaps', actual=analysis.get('quality_report',{}).get('execution_issues'))
    quality = analysis['quality_report']
    if behavior.get('video_fidelity'):
        quality['video_fidelity'] = dict(revision='video_fidelity.v1',facts=copy.deepcopy(behavior['video_fidelity']),
            frames=copy.deepcopy(frames),path_geometry=solution['path_geometry'],
            target_semantics_verification='source_contract_prediction_only',
            termination='not_guaranteed_by_target_schema')
    quality['intersection_preview'] = dict(policy=copy.deepcopy(POLICY), review=copy.deepcopy(review),
        observation_contract=observation_contract,
        provenance=provenance, solver=solution, constraints=problem,
        source_world=copy.deepcopy(world), source_behavior=copy.deepcopy(behavior),
        background_exclusions=[rows[aid] for aid in sorted(excluded)],
        relative_arm_mapping=copy.deepcopy(review['arms']), frames=copy.deepcopy(frames),
        parameters=copy.deepcopy(parameters),actor_paths=actor_paths,
        collision_envelope={'verification':'continuous_piecewise_linear','longitudinal_half_extent_m':5.0,
                            'lateral_half_extent_m':2.2})
    quality['execution_ready'] = True
    if behavior.get('video_fidelity'):
        from _lib.contracts.fidelity import decorate
        decorate(analysis,parameters.get('parked_scenery_per_side',0))
    quality['world_uncertainties'] = dict(questions_to_verify=world.get('questions_to_verify',[]), ambiguities=world.get('ambiguities',[]))
    analysis['limitations'] = copy.deepcopy(behavior['semantic_limitations'])
    for key in ('required_degradations','applied_degradations','allowed_degradations'):
        quality[key] = sorted(set(quality[key]) | {'intersection_approximation'})
    return analysis, 'done_degraded'


def validate_projection(analysis):
    if analysis.get('quality_report',{}).get('scene_revision'):
        from _lib.behavior.scene_revision import validate
        return validate(analysis)
    certificate = analysis.get('quality_report', {}).get('intersection_preview')
    if certificate is None:
        return
    require(certificate.get('policy') == POLICY, 'intersection.policy_binding', 'Execution policy changed')
    rebuilt, _ = project(certificate['source_world'], certificate['source_behavior'],
        certificate['review'], certificate['frames'], certificate['parameters'],
        observation_contract=certificate.get('observation_contract',False))
    for field in ('road','junctions','ego','actors','events','environment','simulation_scenarios'):
        require(analysis.get(field) == rebuilt.get(field), 'intersection.projection_changed',
                'Execution projection differs from deterministic reconstruction', path='/'+field)
    for field in ('provenance','background_exclusions','relative_arm_mapping','actor_paths','collision_envelope'):
        require(certificate.get(field) == rebuilt['quality_report']['intersection_preview'][field],
                'intersection.provenance_changed', 'Projection audit record changed', path='/quality_report/intersection_preview/'+field)
