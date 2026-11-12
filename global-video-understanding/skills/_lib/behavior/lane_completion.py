"""Complete partially observed exit lanes without rewriting observations."""
import copy
from _lib.runtime.execution_plan import REVISION


def active(review): return review.get('planning_revision')==REVISION


def complete(world, behavior, review):
    from _lib.behavior.intersection import require, POLICY, project_connections
    w,b,r=copy.deepcopy(world),copy.deepcopy(behavior),copy.deepcopy(review)
    roads=w['world']['road']['roads']; by_id={x['road_id']:x for x in roads}
    incoming=by_id[r['arms']['S']]; outgoing=by_id[r['arms']['N']]
    knowledge=r.get('lane_knowledge',{}).get(outgoing['road_id'])
    require(knowledge is not None,'intersection.lane_knowledge','Exit count must distinguish exact/minimum/unknown',owner='world')
    source_incoming_ids=list(incoming['lane_ids'])
    n=len(source_incoming_ids); old_ids=outgoing['lane_ids']
    require(r.get('exit_lane_continuity')=='same_order','intersection.exit_continuity','Same-order continuation required')
    require(knowledge['kind']!='exact' or knowledge['count']==n,'intersection.exact_exit_conflict','Confirmed exit count differs from approach; merge/diverge cannot be invented',owner='world',retry_kind='fact_conflict')
    require(knowledge['kind']=='unknown' or knowledge['count']<=n,'intersection.minimum_exit_conflict','Known lane count exceeds proposed exit',owner='world',retry_kind='fact_conflict')
    index=next(i for i,road in enumerate(roads) if road is outgoing)
    path=f'world.road.roads[{index}].lane_ids'
    anchors=knowledge['anchors']; mapped={a['lane_id']:a['ordinal_from_right'] for a in anchors}
    require(len(mapped)==len(anchors) and len(set(mapped.values()))==len(mapped) and
            set(mapped)<=set(old_ids) and all(0<=v<n for v in mapped.values()),
            'intersection.anchor_conflict','Invalid or overlapping exit lane anchors',owner='world',retry_kind='fact_conflict')
    field_evidence=w.get('field_evidence',{}).get(path,[])
    legacy_placeholder=bool(field_evidence) and all(e.get('status')=='unknown' for e in field_evidence)
    require(set(mapped)==set(old_ids) or (knowledge['kind']=='unknown' and legacy_placeholder and len(old_ids)==1 and not mapped),
            'intersection.anchor_coverage','Every identified exit lane needs a positional anchor; observed lanes cannot be discarded',owner='world')
    if not mapped and old_ids: mapped[old_ids[0]]=0
    # Explicit exact-count declarations in World cannot be weakened by the review.
    source_knowledge=outgoing.get('lane_knowledge')
    require(not source_knowledge or source_knowledge.get('kind')!='exact' or
            (knowledge['kind']=='exact' and knowledge['count']==source_knowledge['count']),
            'intersection.count_conflict','Review weakens confirmed lane count',owner='world',retry_kind='fact_conflict')
    lane_by_id={x['lane_id']:x for x in w['world']['road']['lanes']}
    old_to_new={}; memberships={}; rebuilt=[]; counter=1
    before=copy.deepcopy(w['world']['road'])
    for road in roads:
        slots=range(n) if road is outgoing else range(len(road['lane_ids']))
        ids=[]
        for ordinal in slots:
            source=(next((k for k,v in mapped.items() if v==ordinal),None) if road is outgoing else road['lane_ids'][ordinal])
            lid=f'lane_{counter:03d}';counter+=1
            lane=copy.deepcopy(lane_by_id[source]) if source else dict(lane_type='driving',width_m=POLICY['lane_width_m'])
            lane.update(lane_id=lid,road_id=road['road_id'])
            if source: old_to_new[source]=lid
            ids.append(lid);rebuilt.append(lane)
        memberships[road['road_id']]=ids
    def remap(node):
        if isinstance(node,list):
            for value in node: remap(value)
        elif isinstance(node,dict):
            for key,value in list(node.items()):
                if key in {'lane_id','incoming_lane_id','outgoing_lane_id','hypothesized_lane'} and isinstance(value,str): node[key]=old_to_new.get(value,value)
                elif key=='lane_ids' and node.get('road_id') in memberships: node[key]=memberships[node['road_id']]
                else: remap(value)
    remap(w);remap(b);remap(r)
    w['world']['road']['lanes']=rebuilt
    exit_map={old_to_new[lid]:memberships[outgoing['road_id']][i] for i,lid in enumerate(source_incoming_ids)}
    for row in r['actors']:
        if row['execution']!='include': continue
        expected=exit_map.get(row['incoming_lane_id'])
        require(expected is not None,'intersection.approach','Unknown incoming lane',actor_id=row['actor_id'])
        if knowledge['kind']=='unknown' and legacy_placeholder and len(old_ids)==1:
            placeholder_id=old_to_new[old_ids[0]]
            if row['outgoing_lane_id']==placeholder_id: row['outgoing_lane_id']=None
            actor=next(a for a in b['actors'] if a['actor_id']==row['actor_id'])
            for point in actor['trajectory']:
                if point.get('lane_id')==placeholder_id: point['lane_id']=expected
        require(row['outgoing_lane_id'] in {None,expected},'intersection.anchor_conflict','Observed exit assignment contradicts same-order projection',actor_id=row['actor_id'],retry_kind='fact_conflict')
        row['outgoing_lane_id']=expected
    for junction in w['world']['junctions']:
        for edge in junction.get('lane_connections',[]):
            if edge['incoming_lane_id'] in exit_map and edge.get('outgoing_road_id')==outgoing['road_id']:
                require(edge['maneuver']=='straight' and edge['outgoing_lane_id']==exit_map[edge['incoming_lane_id']],
                        'intersection.connection_conflict','Known connection contradicts completion',owner='world',retry_kind='fact_conflict')
    audit=[dict(field_path='/road',observed_value=before,value=copy.deepcopy(w['world']['road']),source='simulation_policy',
        policy_id=REVISION,degraded=True,input_sha256=review['input_sha256'],evidence_frame_ids=knowledge['evidence_frame_ids'],
        reason='Retain identified exit lane anchors and complete same-order execution lanes',source_lane_map=old_to_new,exit_for_incoming=exit_map)]
    return project_connections(w,b,r,audit,observation_contract=True)


def scope_dependencies(world, behavior, review, provenance):
    """Remove only future-window or explicitly unused roadside dependencies in the copy."""
    from _lib.behavior.intersection import require
    from _lib.runtime.execution_plan import REVISION
    from _lib.runtime.execution_scope import references
    rows={a['actor_id']:a for a in review['actors']}
    end=max(p['time_s'] for a in behavior['actors'] if rows[a['actor_id']]['execution']=='include' for p in a['trajectory'])
    removed=set()
    for collection,key in [('questions_to_verify','question_id'),('ambiguities','ambiguity_id')]:
        kept=[]
        for item in world.get(collection,[]):
            loc=item.get('localization') or {}; claim=review.get('dependency_bindings',{}).get(item[key])
            future=(not references(item) and bool(item.get('world_paths')) and
                    set(item.get('impact_domains',[])) <= {'lane_count','road_topology'} and loc.get('temporal_scope')=='after_observed_window' and
                    len((loc.get('observed_window') or {}).get('time_range_s',[]))==2 and
                    loc['observed_window']['time_range_s'][1]>=end)
            unused=False
            if claim:
                rid=claim['road_id']; side=claim['side'];roads=world['world']['road']['roads']
                index=next((i for i,r in enumerate(roads) if r['road_id']==rid),None)
                expected=f'world.road.roads[{index}].roadside_areas'
                require(index is not None and item.get('world_paths')==[expected] and not references(item) and
                        set(item.get('impact_domains',[])) <= {'road_topology'} and claim['kind']=='unused_roadside_side',
                    'intersection.dependency_binding','Roadside claim changes issue scope',owner='world')
                areas=world['world']['road'].get('roadside_areas',[])
                require(not any(a.get('road_id') in {None,rid} and a.get('side') in {None,side} for a in areas) and not roads[index].get('roadside_areas'),
                    'intersection.roadside_dependency','Declared roadside geometry cannot be omitted',owner='world')
                for actor in behavior['actors']:
                    if rows[actor['actor_id']]['execution']!='include': continue
                    require(not actor.get('roadside_area_id') and not any(p.get('roadside_area_id') for p in actor['trajectory']),
                        'intersection.roadside_dependency','Executable actor references roadside area')
                require(not any(e.get('roadside_area_id') for e in behavior.get('events',[])) and
                        not any(p.get('roadside_area_id') for p in behavior.get('executor_primitives',[])),
                        'intersection.roadside_dependency','Event/action consumes roadside geometry')
                unused=True
            if future or unused:
                removed.add(item[key])
                provenance.append(dict(field_path='/execution_dependencies/'+item[key],observed_value=copy.deepcopy(item),
                    value={'decision':'excluded','execution_end_s':end},source='simulation_policy',policy_id=REVISION,degraded=True,
                    input_sha256=review['input_sha256'],evidence_frame_ids=(claim or loc).get('evidence_frame_ids',[]),
                    reason='Dependency lies after execution window' if future else 'No declared roadside entity on the scoped side or executable roadside reference'))
            else: kept.append(item)
        world[collection]=kept
    for interval in world.get('critical_intervals',[]):
        for key in ('question_ids','ambiguity_ids'):
            if key in interval: interval[key]=[v for v in interval[key] if v not in removed]
    return removed
