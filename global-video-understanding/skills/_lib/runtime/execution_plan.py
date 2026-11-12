"""Versioned, deterministic planning between observations and executable scenes."""
import copy
from pathlib import Path
from _lib.runtime.case_core import read_json, atomic_json, sha256, now
from _lib.behavior.compile import preview_digest
from _lib.contracts.v3 import ContractError

REVISION = 'ego_scene_approximation.v1'
SOURCES = ('state/world_context.json','state/behavior.json','state/actor_registry.json')


def enabled(case):
    path=Path(case)/'state/execution_policy.json'
    return path.exists() and read_json(path).get('execution_planning') == REVISION


def evidence(case):
    path=Path(case)/'state/execution_evidence.json'
    if not path.exists(): return {}
    value=read_json(path)
    if value['input_hashes'] != {p:sha256(Path(case)/p) for p in SOURCES}:
        raise ContractError('Execution planning evidence changed; scoped re-review required')
    return value


def activate(case, revision, evidence_path=None):
    case=Path(case)
    if revision != REVISION: raise ContractError('Unsupported execution planning revision')
    progress=read_json(case/'state/progress.json')
    if progress.get('phase')=='done' or progress.get('status') in {'done','done_degraded'}:
        raise ContractError('Completed Case planning policy is immutable')
    if any(s.get('status') in {'submitted','pending','running'} or
           (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and not s.get('deadline_task_resolved'))
           for s in progress.get('sessions',[])):
        raise ContractError('Reconcile active model tasks before planning migration')
    policy_path=case/'state/execution_policy.json'
    if not policy_path.exists(): raise ContractError('Execution planning requires standard v3')
    policy=read_json(policy_path); supplied=read_json(Path(evidence_path)) if evidence_path else None
    if supplied:
        from _lib.contracts.v3 import _schema
        _schema('execution_evidence.schema.json',supplied)
        if supplied['input_hashes'] != {p:sha256(case/p) for p in SOURCES}:
            raise ContractError('Execution planning evidence input mismatch')
    target=case/'state/execution_evidence.json'
    if policy.get('execution_planning')==revision and (supplied is None or (target.exists() and read_json(target)==supplied)):
        return
    # Only derived execution certificates are invalidated; accepted observations and budgets remain.
    from _lib.behavior.compile import preview_digest
    archive=case/'diagnostics/superseded_execution_planning'/preview_digest(dict(policy=policy,evidence=supplied))
    for name in ('diagnostics/intersection_review.json','diagnostics/intersection_execution_projection.json',
                 'state/execution_route.json','state/execution_plan.json','diagnostics/preview_relations.json',
                 'state/preview_review.request.json','state/preview_review.candidate.json',
                 'diagnostics/preview_review.prepared.json'):
        old=case/name
        if old.exists():
            dest=archive/old.name; dest.parent.mkdir(parents=True,exist_ok=True); old.replace(dest)
    diagnostics=progress.setdefault('diagnostics',{})
    for key in ('preview_review','preview_separation','preview_localization'):
        if key in diagnostics:
            diagnostics.setdefault('superseded_execution_reviews',[]).append({key:diagnostics.pop(key)})
    if supplied: atomic_json(target,supplied)
    atomic_json(policy_path,{**policy,'execution_planning':revision})
    progress.setdefault('diagnostics',{}).setdefault('execution_planning_migrations',[]).append(
        dict(at=now(),before=policy,after=read_json(policy_path),evidence=supplied))
    progress.update(phase='aggregating',status='needs_review',substage='behavior',error=None,artifacts={},generation_status='not_generated')
    atomic_json(case/'state/progress.json',progress)


def _claim_conflict(path):
    error = ContractError('Execution evidence conflicts with review: ' + path,
                          rule_id='execution.review.fact_conflict')
    error.diagnostics = dict(path=path, retry_kind='fact_conflict')
    raise error


def _merge_frames(left, right, path):
    if any(not isinstance(ids, list) or any(not isinstance(v, str) for v in ids)
           for ids in (left, right)):
        _claim_conflict(path)
    return sorted(set(left) | set(right))


def _anchor_index(anchors, path):
    if not isinstance(anchors, list):
        _claim_conflict(path)
    result = {}
    for anchor in anchors:
        if (not isinstance(anchor, dict) or not isinstance(anchor.get('lane_id'), str)
                or anchor['lane_id'] in result):
            _claim_conflict(path)
        result[anchor['lane_id']] = anchor
    return result


def _merge_claim(collection, left, right, path):
    """Only these planning claims may merge evidence, never fact or scope changes."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        _claim_conflict(path)
    result = copy.deepcopy(left)
    missing = object()
    for field in sorted(set(left) | set(right)):
        field_path = path + '/' + field
        a, b = left.get(field, missing), right.get(field, missing)
        if field == 'evidence_frame_ids' and collection != 'limitation_bindings':
            result[field] = _merge_frames(left.get(field, []), right.get(field, []), field_path)
        elif field == 'anchors' and collection == 'lane_knowledge':
            a, b = _anchor_index(a, field_path), _anchor_index(b, field_path)
            if set(a) != set(b):
                _claim_conflict(field_path)
            indices = {anchor['lane_id']: index for index, anchor in enumerate(left[field])}
            result[field] = [_merge_claim('anchor', a[lid], b[lid], field_path + '/' + str(indices[lid]))
                             for lid in sorted(a)]
        elif field in ('actor_ids', 'world_issue_ids') and collection == 'limitation_bindings':
            if any(not isinstance(ids, list) or any(not isinstance(v, str) for v in ids)
                   for ids in (a, b)) or sorted(a) != sorted(b):
                _claim_conflict(field_path)
            result[field] = sorted(a)
        elif a != b:
            # Missing/null/unknown declarations cannot replace confirmed facts.
            _claim_conflict(field_path)
    return result


def prepare_review(case, world, review):
    if not enabled(case): return copy.deepcopy(review)
    result=copy.deepcopy(review); supplement=evidence(case)
    result['planning_revision']=REVISION
    # Fill absent claims; overlapping claims must agree on facts and scope.
    # Only evidence sets are combined. Neither input is modified.
    for key in ('lane_knowledge','limitation_bindings','dependency_bindings'):
        source=supplement.get(key,{})
        if key=='lane_knowledge' and not source:
            source={r['road_id']:r['lane_knowledge'] for r in world['world']['road']['roads'] if 'lane_knowledge' in r}
        existing=result.setdefault(key,{})
        for k,v in source.items():
            path='/' + key + '/' + k.replace('~', '~0').replace('/', '~1')
            existing[k]=_merge_claim(key, existing.get(k, v), v, path)
    return result


def classify(item):
    rule=item.get('rule_id','')
    retry=item.get('retry_kind')
    if retry=='unsupported_capability' or rule.startswith('capability.'): return 'unsupported_capability'
    if retry=='content_repair': return 'contract_repair'
    if retry=='fact_conflict' or 'conflict' in rule: return 'fact_conflict'
    if item.get('kind')=='execution_gap': return 'policy_completion'
    return 'new_evidence'


def record(case, world, behavior, route, *, review=None, findings=None):
    if not enabled(case): return None
    case=Path(case)
    # The execution plan is a derived delivery artifact.  Keep the committed
    # Behavior untouched, but carry its structured limitation reason into
    # repeated unknown speed constraints so the final analysis is self-auditing.
    from _lib.behavior.compile import materialize_inherited_unknown_reasons
    plan_behavior, _ = materialize_inherited_unknown_reasons(behavior)
    actors=plan_behavior.get('actors',[])
    source_hashes={p:sha256(case/p) for p in SOURCES}
    rows=[]
    for collection,key in (('questions_to_verify','question_id'),('ambiguities','ambiguity_id')):
        for item in world.get(collection,[]):
            if item.get('resolution')!='unresolved': continue
            rows.append(dict(issue_id=item[key],actor_ids=item.get('actor_ids',(item.get('identity_binding') or {}).get('actor_ids',[])),
                world_paths=item.get('world_paths',[]),decision='review_required'))
    constraints=[]
    for actor in actors:
        for point in actor.get('trajectory',[]):
            for field in ('lane_id','speed_mps','position_ego_m'):
                value=point.get(field)
                if value is None: continue
                estimated=isinstance(value,dict) and value.get('status')=='estimated'
                constraints.append(dict(actor_id=actor['actor_id'],frame_id=point.get('frame_id'),field=field,
                    value=copy.deepcopy(value),strength='estimate' if estimated else 'source_fact'))
    from _lib.behavior.intersection import POLICY
    diagnostics=[dict(i,classification=classify(i)) for i in (findings if findings is not None else route.get('issues',[]))]
    plan=dict(revision=REVISION,input_hashes=source_hashes,scope_revision=read_json(case/'state/execution_policy.json').get('execution_scope'),
        source_view_sha256=preview_digest(dict(world=world,behavior=behavior)),
        execution_actor_ids=([r['actor_id'] for r in review['actors'] if r['execution']=='include'] if review
                             else [actor['actor_id'] for actor in actors]),
        time_range_s=[min(p['time_s'] for a in actors for p in a['trajectory']),max(p['time_s'] for a in actors for p in a['trajectory'])],
        route=route['route'],constraints=constraints,events=copy.deepcopy(plan_behavior.get('events',[])),
        open_dependencies=rows,findings=diagnostics,
        defaults=dict(intersection=POLICY,straight=read_json(case/'case.json')['parameters']['fallback_policy'].get('straight_preview',{})),
        review_sha256=preview_digest(review) if review else None,
        execution_evidence_sha256=preview_digest(evidence(case)))
    plan['sha256']=preview_digest(plan)
    atomic_json(case/'diagnostics/execution_plans'/f"{plan['sha256']}.json",plan)
    atomic_json(case/'state/execution_plan.json',plan)
    return plan


def preflight(world, behavior, review, frames, parameters=None):
    """Collect independent execution prerequisites before spending on another review."""
    from _lib.contracts.fidelity import frame_index
    frames=frame_index(frames)
    from _lib.runtime.routing import capability_issues
    from _lib.behavior.intersection import solve_motion, IntersectionBlocked
    from _lib.behavior.review_contract import issues as reference_issues
    issues=list(capability_issues(world,behavior))
    issues.extend(reference_issues(world,behavior,review))
    # Reference diagnostics must not hide independent physical contradictions.
    # This read-only solve never admits a candidate; project still validates all references.
    try: solve_motion(world,behavior,review,frames,observation_contract=True,check_references=False)
    except IntersectionBlocked as error: issues.extend(error.diagnostics.get('issues',[error.diagnostics]))
    roads={r['road_id']:r for r in world['world']['road']['roads']}
    rows={a['actor_id']:a for a in review.get('actors',[])}
    def add(rule,message,**fields):
        issues.append(dict(rule_id=rule,message=message,owner='behavior',**{'retry_kind':'new_evidence',**fields}))
    for a in behavior['actors']:
        if rows.get(a['actor_id'],{}).get('execution')!='include': continue
        points=a['trajectory']
        if len(points)<2 or points[0]['time_s']!=0: add('intersection.temporal_coverage','Participant lacks complete initial coverage',actor_id=a['actor_id'])
        values=[]
        for point in points:
            speed=point.get('speed_mps')
            if type(speed) in (int,float): values.append(speed)
            elif isinstance(speed,dict) and speed.get('status')=='observed' and type(speed.get('value')) in (int,float): values.append(speed['value'])
        from _lib.behavior.longitudinal import events as longitudinal_events
        continuous_model=bool(behavior.get('video_fidelity') or longitudinal_events(behavior))
        if not continuous_model and values and (min(values)<=0 or max(values)-min(values)>1e-7):
            add('intersection.motion_capability','Observed stop/variable speed requires an independently supported motion model',actor_id=a['actor_id'],actual=values,retry_kind='unsupported_capability')
    parking={r['area_id'] for r in review.get('parking',[])}
    for a in world['world']['road'].get('roadside_areas',[]):
        if (a.get('area_type')=='curbside_parking' or a.get('type') in {'parking','parallel_parking','curbside_parking','parking_band','parking_strip'}) and a['area_id'] not in parking: add('intersection.parking_coverage','Parking omitted from review',field_path=a['area_id'])
    for collection,key,review_key in [('crosswalks','crosswalk_id','crosswalks'),('traffic_signals','signal_id','signals')]:
        reviewed={r[key] for r in review.get(review_key,[])}
        for j in world['world'].get('junctions',[]):
            for item in j.get(collection,[]):
                if item[key] not in reviewed: add('intersection.facility_coverage','Observed facility omitted',field_path=item[key])
    if not issues and parameters is not None:
        from _lib.behavior.intersection import project
        try: project(world,behavior,review,frames,parameters,observation_contract=True)
        except IntersectionBlocked as error: issues.extend(error.diagnostics.get('issues',[error.diagnostics]))
    unique={preview_digest(i):i for i in issues}
    return list(unique.values())


def finalize(case, analysis, *, persist=True):
    """Publish the effective plan alongside its observations and execution certificate."""
    if not enabled(case): return
    path=Path(case)/'state/execution_plan.json'
    if not path.exists(): return
    plan=read_json(path); plan.pop('sha256',None)
    q=analysis['quality_report']; certificate=q.get('intersection_preview',{})
    # Planning hands off the accepted actor roster. Only an explicitly reviewed
    # preview narrows it; Converter reports actual target exclusions in its receipt.
    reviewed = q.get('straight_preview') or certificate
    plan['execution_actor_ids'] = (
        list(analysis['simulation_scenarios'][0]['involved_actor_ids']) if reviewed else
        [actor['actor_id'] for actor in [analysis['ego'], *analysis.get('actors', [])]])
    plan['projection_provenance']=copy.deepcopy(certificate.get('provenance',[]))
    plan['omitted_actors']=copy.deepcopy(q.get('execution_actor_scope',{}).get('excluded_actors',[]))+copy.deepcopy(certificate.get('background_exclusions',[]))
    plan['solver']=copy.deepcopy(certificate.get('solver'))
    plan['state']='projected'
    discharged={p['field_path'].rsplit('/',1)[-1] for p in plan['projection_provenance'] if p['field_path'].startswith(('/execution_scope/','/execution_dependencies/','/execution_limitations/'))}
    for item in plan['open_dependencies']:
        if item['issue_id'] in discharged: item['decision']='covered_by_execution_projection'
    plan['sha256']=preview_digest(plan)
    if persist:
        atomic_json(path,plan); atomic_json(Path(case)/'diagnostics/execution_plans'/f"{plan['sha256']}.json",plan)
    q['execution_plan']=copy.deepcopy(plan)
