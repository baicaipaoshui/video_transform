"""One immutable intersection fact review; Python owns projection and lifecycle."""
from pathlib import Path

from _lib.behavior.intersection import (POLICY, binding, validate_review, project,
                                      validate_world_challenges, IntersectionBlocked)
from _lib.behavior.compile import evidence_frames, preview_digest
from _lib.contracts.v3 import _schema, ContractError
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.paths import SKILLS
from _lib.world.gaps import enabled as new_facts, REVISION


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('intersection_approximation') == POLICY


def path_contract_enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('intersection_path_contract') == 'intersection_path.v1'


def review_intersection(runner, case):
    case = Path(case)
    if not enabled(case):
        raise ContractError('Frozen Case does not authorize intersection approximation')
    from _lib.runtime.execution_scope import inputs
    world, behavior, scope_audit = inputs(case)
    all_frames = evidence_frames(case)
    if path_contract_enabled(case):
        from _lib.behavior.intersection_evidence import freeze as freeze_evidence, review_frames
        evidence_catalog = freeze_evidence(case, world, behavior, all_frames)
        frames = review_frames(evidence_catalog)
    else:
        evidence_catalog = None
        frames = all_frames
    from _lib.runtime.execution_plan import enabled as planned, prepare_review, evidence as planning_evidence, record as record_plan, preflight
    input_hash = binding(world, behavior, evidence_catalog['catalog_sha256'] if evidence_catalog else None)
    validation_hash=preview_digest(dict(inputs=input_hash,frames=frames,evidence_catalog=evidence_catalog['catalog_sha256'] if evidence_catalog else None,execution_scope=scope_audit,
        planning_evidence=planning_evidence(case) if planned(case) else None,
        observation_contract=new_facts(case),
        planning_policy=read_json(case/'state/execution_policy.json').get('execution_planning'),
        planning_implementation={p:sha256(SKILLS/p) for p in ('_lib/runtime/execution_plan.py','_lib/behavior/lane_completion.py','_lib/experimental/constraint_solver.py')} if planned(case) else None,
        motion_contract=sha256(SKILLS/'schemas/longitudinal_motion.schema.json'),
        motion_implementation=sha256(SKILLS/'_lib/behavior/longitudinal.py'),
        motion_solver=sha256(SKILLS/'_lib/behavior/fidelity_motion.py'),
        review_contract=sha256(SKILLS/'_lib/behavior/review_contract.py'),
        scope_schema=sha256(SKILLS/'schemas/limitation_binding.schema.json'),
        repair_schema=sha256(SKILLS/'schemas/intersection_review_repair.schema.json'),
        review_stage=sha256(SKILLS/'_lib/stages/intersection.py'),
        validator=sha256(SKILLS/'_lib/behavior/intersection.py'),schema=sha256(SKILLS/'schemas/intersection_review.schema.json')))
    root = case/'diagnostics/intersection'/validation_hash
    request, candidate, accepted = root/'request.json', root/'candidate.json', root/'review.json'
    state = runner.progress(case)
    state['substage'] = 'intersection_review'; runner.save_progress(case,state)
    if not request.exists():
        payload = dict(input_sha256=input_hash, world=world, behavior=behavior,
            frames=frames, required_actor_evidence={a['actor_id']:sorted({p['frame_id'] for p in a['trajectory'] if p.get('frame_id')})
                    for a in behavior['actors']})
        if evidence_catalog:
            payload.update(evidence_catalog_sha256=evidence_catalog['catalog_sha256'],
                           path_contract_revision='intersection_path.v1')
            payload['path_review_contract']={
                'max_frame_count':12,
                'actor_fields':['path_evidence_frame_ids','entry_ordinal_from_right','exit_ordinal_from_right',
                                'lateral_motion','lateral_transition_interval_s','path_witnesses'],
                'lateral_motion_values':['keeping','changing_left','changing_right','unknown'],
                'connection_witnesses':['incoming','junction','outgoing'],
                'world_challenges':('Use only when a World-owned fact is contradicted, unsupported, or an '
                                    'evidence-supported roadside parking area is missing; status must be needs_evidence. '
                                    'A missing parking area uses exactly world.road.roadside_areas[-].')}
        from _lib.behavior.review_contract import issue_index
        payload['reference_catalog']={
            'world_issue_ids':sorted(issue_index(world)),
            'limitation_ids':sorted(v['limitation_id'] for v in behavior.get('semantic_limitations',[])),
            'limitation_binding_schema':read_json(SKILLS/'schemas/limitation_binding.schema.json')}
        if new_facts(case): payload['fact_contract_revision']=REVISION
        if scope_audit: payload['execution_scope']=scope_audit
        from _lib.behavior.longitudinal import events, schema
        if events(behavior):
            payload['longitudinal_event_review_schema']=schema()
            payload['required_longitudinal_events']=events(behavior)
        if planned(case): payload.update(prepare_review(case,world,{}))
        atomic_json(request,payload)
    if not accepted.exists():
        terminal = root/'stopped.json'
        reservations=state.get('diagnostics',{}).get('model_task_reservations',[])
        last=next((r for r in reversed(reservations) if r.get('stage')=='intersection_review'),{})
        unsubmitted=(last.get('status')=='abandoned_before_task_id' and last.get('reason')=='backend_unavailable')
        # A worker may finish writing its immutable candidate after the task
        # transport has already reported cancellation/failure.  Recovery must
        # revalidate that late candidate before treating the prior stop record
        # as authoritative; otherwise a valid, hash-bound result is stranded.
        if terminal.exists() and not unsubmitted and not candidate.exists():
            detail = read_json(terminal)
            raise IntersectionBlocked('intersection.unchanged_failure',
                'Unchanged review inputs already stopped; new evidence or validator required', actual=detail)
        template=(SKILLS/'behavior-understanding/references/intersection-review.md').read_text()
        prompt=template.replace('$request_path',str(request)).replace('$candidate_path',str(candidate)).replace(
            '$schema_path',str(SKILLS/'schemas/intersection_review.schema.json'))
        from _lib.contracts.fidelity import prompt as fidelity_prompt
        prompt += fidelity_prompt(case)
        def adopt(_session=None):
            response=read_json(candidate)
            active_repair=case/'state/intersection_repair.active.json'
            if active_repair.exists() and read_json(active_repair).get('input_sha256')==input_hash and 'base_sha256' not in response:
                raise ContractError('Active local repair must return a scoped patch, not a full candidate')
            response=materialize_repair(case,response)
            response=prepare_review(case,world,response)
            fingerprint=preview_digest(dict(response=response,validation=validation_hash))
            rejected=root/'rejected'/f'{fingerprint}.json'
            if rejected.exists():
                raise IntersectionBlocked('intersection.unchanged_candidate','Identical rejected candidate; no further model retry')
            try:
                from _lib.behavior.longitudinal import events as longitudinal_events
                if longitudinal_events(behavior) and not behavior.get('video_fidelity') and 'longitudinal_events' not in response:
                    raise ContractError('intersection review missing required longitudinal_events')
                _schema('intersection_review.schema.json',response)
                if response['input_sha256'] != input_hash:
                    raise ContractError('intersection review input hash mismatch')
                if evidence_catalog:
                    if (response.get('path_contract_revision')!='intersection_path.v1' or
                            response.get('evidence_catalog_sha256')!=evidence_catalog['catalog_sha256']):
                        raise ContractError('intersection review path/evidence contract mismatch')
                    actor_ids={a['actor_id'] for a in behavior['actors']}
                    validate_world_challenges(response,actor_ids,{f['frame_id']:f for f in frames})
                # A well-formed negative answer is valid model output, not invalid_output.
                if response['status']=='accepted':
                    from _lib.contracts.fidelity import enabled, validate_evidence, compare_reviews
                    if enabled(case):
                        validate_evidence(response.get('video_fidelity'), world, behavior, frames,
                                          read_json(case/'state/probe.json')['probe']['duration_s'])
                        compare_reviews(behavior['video_fidelity'],response['video_fidelity'])
                    if planned(case):
                        findings=preflight(world,behavior,response,frames,read_json(case/'case.json')['parameters'])
                        record_plan(case,world,behavior,{'route':'intersection_preview','issues':findings},review=response,findings=findings)
                        if findings:
                            primary=next((f for f in findings if f.get('retry_kind')!='content_repair'),findings[0])
                            error=IntersectionBlocked(primary['rule_id'],primary['message'],retry_kind=primary.get('retry_kind','new_evidence'))
                            error.diagnostics.update(primary,issues=findings)
                            raise error
                    validate_review(world,behavior,response,frames,observation_contract=new_facts(case))
            except Exception as error:
                atomic_json(rejected,dict(response=response,error=str(error)))
                raise
            atomic_json(accepted,response)
            migration=root/'candidate_revalidation.json'
            if migration.exists():
                note=read_json(migration)
                if note.get('system_contract_repair'):
                    getattr(runner,'credit_model_reservation',lambda *args:False)(case,'intersection_review',
                        note.get('source_attempt'), 'Local intersection repair wire-contract mismatch',note.get('source_sha256'))
        # Revalidate an immutable candidate after a deterministic validator migration.
        # A previously rejected scoped patch may become valid after fixing the local
        # patch contract; reuse it before considering another model task.
        if planned(case) and not candidate.exists():
            sessions=runner.progress(case).get('sessions',[])
            for session in reversed(sessions):
                archive=session.get('invalid_candidate_path')
                if session.get('stage')!='intersection_review' or not archive: continue
                path=Path(archive)
                if not path.is_file() or not path.resolve().is_relative_to(case.resolve()): continue
                raw=read_json(path)
                accepted_raw=(raw.get('status')=='accepted' and raw.get('input_sha256')==input_hash)
                repair_raw=(raw.get('base_sha256') and raw.get('input_sha256')==input_hash)
                if accepted_raw or repair_raw:
                    try:
                        materialized=materialize_repair(case,raw) if repair_raw else raw
                        _schema('intersection_review.schema.json',prepare_review(case,world,materialized))
                    except Exception:
                        continue
                    atomic_json(candidate,raw)
                    atomic_json(root/'candidate_revalidation.json',dict(source_path=str(path),source_sha256=sha256(path),
                        source_attempt=session.get('attempt'),reason='Deterministic validator or execution planning migration',
                        system_contract_repair=bool(repair_raw)))
                    break
        try:
            if planned(case) and candidate.exists():
                try: adopt()
                except IntersectionBlocked as error:
                    if getattr(error,'diagnostics',{}).get('retry_kind')!='content_repair': raise
                    # Pass the same failure to the budget-owning runner. Re-reading
                    # the rejected candidate would lose its scoped repair details.
                    def failed_precheck(saved=error): raise saved
                    runner.model_stage(case,'intersection_review','global-video-understanding',prompt,adopt,
                        precheck=failed_precheck,candidate_path=candidate,max_attempts=3)
            else:
                runner.model_stage(case,'intersection_review','global-video-understanding',prompt,adopt,
                precheck=adopt if candidate.exists() else None,candidate_path=candidate,max_attempts=3)
        except Exception as error:
            current=runner.progress(case)
            active=any(s.get('status') in {'submitted','pending','running'} for s in current.get('sessions',[]))
            code=getattr(error,'code',None) or runner.candidate_error_code(error)
            if not active and code not in {'backend_unavailable','submission_unresolved','service_access_denied','local_dependency_missing','local_io_error','agent_lease_timeout'}:
                atomic_json(terminal,dict(input_sha256=input_hash,error=str(error),code=code))
            raise
    review=read_json(accepted)
    challenges=validate_world_challenges(review,{a['actor_id'] for a in behavior['actors']},
                                         {f['frame_id']:f for f in frames}) if evidence_catalog else []
    if challenges:
        from _lib.world.post_behavior import run as readjudicate_world
        readjudicate_world(runner,case,accepted,review,evidence_catalog)
        return None
    validate_review(world,behavior,review,frames,observation_contract=new_facts(case))
    # No state/world_context.json or state/behavior.json write is permitted here.
    atomic_json(case/'diagnostics/intersection_review.json',dict(
        input_sha256=input_hash,review_path=str(accepted),review_sha256=sha256(accepted),
        frames_sha256=preview_digest(frames)))
    current=runner.progress(case)
    if current.get('diagnostics',{}).get('intersection_structured_closure_active'):
        current['diagnostics']['intersection_structured_closure_active']=False
        runner.save_progress(case,current)
    return review


def repair_prompt(case, candidate, archived, error):
    """Repair evidence or reference metadata without reopening accepted facts."""
    rule=getattr(error,'rule_id',None)
    # Plain ContractError gates (e.g. review/evidence conflicts) carry no
    # diagnostics payload; treat their message as the sole finding instead of
    # crashing the generic retry path.
    diagnostics=getattr(error,'diagnostics',None)
    if not isinstance(diagnostics,dict):
        diagnostics={'rule_id':rule,'message':str(error)}
    findings=diagnostics.get('issues',[diagnostics])
    crossing_ids=sorted({f['actual']['exit_crosswalk_id'] for f in findings
                         if f['rule_id']=='intersection.crosswalk_motion_conflict'})
    metadata=rule in {'intersection.review_contract','intersection.review_reference','intersection.limitation_scope','intersection.crosswalk_motion_conflict'}
    if rule != 'intersection.actor_evidence' and not metadata:
        return None
    aid=error.actor_id; base=read_json(archived)
    if 'base_sha256' in base:
        base=materialize_repair(case,base)
    from _lib.behavior.compile import preview_digest
    base_path=Path(case)/'diagnostics/intersection_repair'/f'{preview_digest(base)}.json'
    atomic_json(base_path,base)
    allowed=(['covered_world_ids','covered_limitation_ids','limitation_bindings','dependency_bindings','status','reason'] if metadata
             else ['evidence_frame_ids','context_frame_ids','reason'])
    if crossing_ids:allowed.append('crosswalks')
    payload=dict(base_path=str(base_path),base_sha256=sha256(base_path),actor_id=aid,
                 input_sha256=base['input_sha256'],allowed_fields=allowed,
                 expected=diagnostics.get('expected'),candidate_path=str(candidate),scope='review_metadata' if metadata else 'actor_evidence')
    path=Path(case)/'state/intersection_repair.active.json'
    prompt=(f'只读 {path} 和 base_path。输出 {candidate}，严格遵循 intersection_review_repair_schema。'
            'replacements是非空数组，每项仅含path和value；path必须为允许字段对应的顶层JSON Pointer。'
            '仅含允许的证据字段及理由；evidence_frame_ids必须等于expected。禁止改身份、车道、关系或其他actor。')
    payload['intersection_review_repair_schema']=read_json(SKILLS/'schemas/intersection_review_repair.schema.json')
    if metadata:
        from _lib.runtime.execution_scope import inputs
        from _lib.behavior.review_contract import issue_index
        world,behavior,_=inputs(case)
        payload.update(world_issues=issue_index(world),semantic_limitations=behavior.get('semantic_limitations',[]),
            findings=findings,
            limitation_binding_schema=read_json(SKILLS/'schemas/limitation_binding.schema.json'))
        prompt=(f'只读 {path} 与 base_path，输出 {candidate}，严格遵循 intersection_review_repair_schema。'
                'replacements必须是非空数组，每项仅含path和value；path是allowed_fields对应的顶层JSON Pointer。'
                '仅修复 allowed_fields 中的引用和执行范围关联：ID必须来自所给world_issues/semantic_limitations，不得用字段路径或自造标签当ID。'
                '使用所给权威Schema；限制项只能关联其真实影响范围，不得扩大豁免。不能通过删除限制或覆盖声明逃避门禁；'
                '无法支持关联时保留为未覆盖。禁止改原观察、身份结论、actors、lane_knowledge、车道、轨迹、动作、时间或模型预算。')
        if crossing_ids:
            payload.update(crosswalk_repair_ids=crossing_ids,frames=evidence_frames(case),
                source_events=behavior['events'],
                crosswalk_schema=read_json(SKILLS/'schemas/intersection_review.schema.json')['properties']['crosswalks'])
            prompt += (' 本次另允许crosswalk_repair_ids对应横道的ego_crossing_interval_s、evidence_frame_ids、reason；'
                       '必须独立查看frames允许的原图，重新确认是否真的跨越出口横道，不能把接近横道当作跨越。'
                       '可输出完整crosswalks数组，但其他行及其他字段必须原样保留。未观测跨越用null并解释证据；'
                       '时间只能来自所给帧。不得为了满足默认几何反推视频时间；矛盾无法解除时status=needs_evidence并给出具体reason。')
    payload['prompt']=prompt; atomic_json(path,payload)
    return prompt


def materialize_repair(case, response):
    if 'base_sha256' not in response:
        return response
    from _lib.behavior.intersection import require
    request=read_json(Path(case)/'state/intersection_repair.active.json')
    try:_schema('intersection_review_repair.schema.json',response)
    except ContractError as error:
        require(False,'intersection.repair_scope','Repair does not match the authoritative patch contract',actual=str(error))
    path=Path(request['base_path'])
    require(path.resolve().is_relative_to((Path(case)/'diagnostics/intersection_repair').resolve())
            and sha256(path)==request['base_sha256'], 'intersection.repair_base', 'Repair base changed')
    require(set(response)=={'base_sha256','input_sha256','actor_id','replacements'} and
            all(response[k]==request[k] for k in ('base_sha256','input_sha256','actor_id')),
            'intersection.repair_binding', 'Repair binding differs')
    operations=response['replacements']
    paths=[op['path'] for op in operations]
    require(len(paths)==len(set(paths)), 'intersection.repair_scope', 'Repair repeats a field path')
    require(all(path.startswith('/') and path.count('/')==1 and path[1:] in request['allowed_fields'] for path in paths),
            'intersection.repair_scope', 'Repair modifies unauthorized fields')
    replacements={op['path'][1:]:op['value'] for op in operations}
    base=read_json(path)
    if request.get('scope')=='review_metadata':
        if 'crosswalks' in replacements:
            before=base['crosswalks'];after=replacements['crosswalks']
            require(isinstance(after,list) and len(before)==len(after),'intersection.repair_scope','Crosswalk repair cannot add/remove facilities')
            allowed={'ego_crossing_interval_s','evidence_frame_ids','reason'}
            for old,new in zip(before,after):
                mutable=allowed if old['crosswalk_id'] in request.get('crosswalk_repair_ids',[]) else set()
                require(isinstance(new,dict) and set(new)==set(old) and
                    all(new[k]==v for k,v in old.items() if k not in mutable),
                    'intersection.repair_scope','Crosswalk repair changes an unauthorized fact')
        base.update(replacements)
    else:next(a for a in base['actors'] if a['actor_id']==request['actor_id']).update(replacements)
    return base


def build_intersection(case, world, behavior, parameters):
    case=Path(case); frames=evidence_frames(case)
    record=read_json(case/'diagnostics/intersection_review.json')
    if record.get('frames_sha256') != preview_digest(frames):
        raise ContractError('Intersection evidence frames changed; new review required')
    path=Path(record['review_path'])
    if not path.resolve().is_relative_to((case/'diagnostics/intersection').resolve()) or sha256(path)!=record['review_sha256']:
        raise ContractError('Intersection review certificate changed')
    analysis,status=project(world,behavior,read_json(path),frames,parameters,observation_contract=new_facts(case))
    atomic_json(case/'diagnostics/intersection_execution_projection.json',analysis)
    return analysis,status
