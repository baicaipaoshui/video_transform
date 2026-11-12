"""Cross-reference checks shared by review admission and execution projection."""
import copy
from _lib.runtime.execution_scope import references


def execution_view(behavior, review):
    """Membership is a reviewed execution decision; source roles remain observations."""
    result=copy.deepcopy(behavior)
    rows={r['actor_id']:r for r in review.get('actors',[])}
    for actor in result.get('actors',[]):
        if rows.get(actor['actor_id'],{}).get('execution')=='include' and actor.get('simulation_role')=='background':
            # Physical type, static state, identity, lane and coverage still pass
            # the same participant checks in validate_review.
            actor['simulation_role']='participant'
    return result


def issue_index(world):
    return {r[k]:r for coll,k in (('questions_to_verify','question_id'),('ambiguities','ambiguity_id'))
            for r in world.get(coll,[])}


def lane_count_paths(world, approach_id=None):
    """Exact quantity leaves shared by gap classification and execution coverage."""
    return {f'world.road.roads[{i}].{leaf}'
            for i, road in enumerate(world['world']['road']['roads'])
            if road['road_id'] != approach_id
            for leaf in ('lane_ids', 'lane_knowledge.count')}


def excluded_limitation(item, behavior, excluded):
    """All affected actors must be excluded; scene dependencies remain live."""
    ids = references(item)
    event_ids = set(item.get('affected_event_ids', [])) | set((item.get('scope_binding') or {}).get('affected_event_ids', []))
    events = {e['event_id']: e for e in behavior.get('events', [])}
    if not event_ids <= set(events): return False
    for eid in event_ids: ids.update(events[eid].get('involved_actor_ids', []))
    return bool(ids) and ids <= excluded and not item.get('world_paths') and not item.get('related_world_paths')


def lane_limit_covered(item, claim, world, behavior, review):
    if claim.get('kind')!='non_approach_lane_count':return False
    if (references(item) or claim.get('actor_ids') or item.get('affected_event_ids') or item.get('scope_binding')
            or set(item.get('impact_domains',[]))!={'lane_count'}):return False
    index=issue_index(world); ids=claim.get('world_issue_ids',[])
    if not ids or not set(ids)<=set(index) or not set(ids)<=set(review['covered_world_ids']):return False
    allowed=lane_count_paths(world, review['arms']['S'])
    paths=set()
    for key in ids:
        source=index[key]; p=set(source.get('world_paths',[]))
        if not p or not p<=allowed or references(source) or source.get('owner','world')!='world':return False
        paths.update(p)
    own=set(item.get('world_paths',[]))|set(item.get('related_world_paths',[]))
    return own<=paths


def issues(world, behavior, review):
    from _lib.behavior.compile import preview_scale_limitation
    result=[]; index=issue_index(world)
    limits={v['limitation_id']:v for v in behavior.get('semantic_limitations',[])}
    def add(rule,path,message,expected=None,actual=None):
        result.append(dict(rule_id=rule,field_path=path,message=message,owner='behavior',
                           retry_kind='content_repair',expected=expected,actual=actual))
    for field,known in (('covered_world_ids',index),('covered_limitation_ids',limits),
                        ('limitation_bindings',limits),('dependency_bindings',index)):
        invalid=set(review.get(field,[]))-set(known)
        if invalid:add('intersection.review_reference','/'+field,'Unknown review reference in '+field,sorted(known),sorted(invalid))
    for key,claim in review.get('limitation_bindings',{}).items():
        if key in limits and claim.get('kind')=='non_approach_lane_count' and not lane_limit_covered(limits[key],claim,world,behavior,review):
            add('intersection.limitation_scope','/limitation_bindings/'+key,'Lane-count coverage must bind non-approach World issues without actor/event dependencies',actual=claim)
    excluded={r['actor_id'] for r in review.get('actors',[]) if r['execution']=='exclude'}
    for key in review.get('covered_limitation_ids',[]):
        if key not in limits or key in review.get('limitation_bindings',{}):continue
        item=limits[key]
        if not preview_scale_limitation(item) and not excluded_limitation(item, behavior, excluded):
            add('intersection.limitation_scope','/limitation_bindings/'+key,
                'Covered limitation needs an explicit supported scope binding',actual=item)
    return result


def validate(world, behavior, review):
    from _lib.behavior.intersection import IntersectionBlocked
    found=issues(world,behavior,review)
    if found:
        error=IntersectionBlocked('intersection.review_contract', 'Review references or scope bindings require repair',
                                  retry_kind='content_repair',actual=found)
        error.diagnostics['issues']=found
        raise error
