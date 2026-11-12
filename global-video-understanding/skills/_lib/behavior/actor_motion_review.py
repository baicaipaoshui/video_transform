"""Explicit, budgeted actor observation review; never bypass execution gates."""
import copy
from pathlib import Path
from _lib.runtime.case_core import read_json, atomic_json, sha256
from _lib.contracts.v3 import ContractError
from _lib.behavior.compile import evidence_frames, validate_compiled_behavior
from _lib.contracts import census_scope
from _lib.contracts.conversion_policy import digest
from _lib.behavior.input_binding import InputChanged


def _check_inputs(packet, *, target=None, result_sha256=None):
    for name, expected in packet['input_hashes'].items():
        allowed = {expected}
        if target is not None and Path(name) == target:
            allowed.add(result_sha256)
        if not Path(name).is_file() or sha256(name) not in allowed:
            raise InputChanged('Actor review inputs changed: ' + name)
    if sha256(packet['base_path']) != packet['base_sha256']:
        raise InputChanged('Actor review base changed')


def _validate_case(case, result, frames):
    """Use the same observation owners as the normal Behavior checkpoint."""
    from _lib.runtime.identity_revision import validate_behavior
    from _lib.runtime.execution_scope import validate_behavior_scope
    from _lib.contracts.fidelity import enabled, validate_evidence, FidelityEvidenceError
    from _lib.runtime.census_scope_recovery import validate_patch
    world = read_json(case/'state/world_context.json')
    validate_compiled_behavior(result, world, read_json(case/'state/actor_registry.json'), frames, execution=False)
    from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
    validate_motion_obligations(case, result)
    validate_behavior(case, result)
    validate_behavior_scope(case, world, result)
    if enabled(case):
        if 'video_fidelity' not in result:
            raise FidelityEvidenceError('video_fidelity observations required by frozen policy', '/video_fidelity')
        validate_evidence(result['video_fidelity'], world, result, frames,
                          read_json(case/'state/probe.json')['probe']['duration_s'])
    validate_patch(case, result)


def materialize(packet, response):
    if set(response) != {'base_sha256','reason','replacements'} or not response['reason'].strip():
        raise ContractError('Actor review requires hash, evidence reason and replacements')
    _check_inputs(packet)
    base=read_json(packet['base_path'])
    if response['base_sha256'] != packet['base_sha256'] or sha256(packet['base_path']) != packet['base_sha256']:
        raise ContractError('Actor review base changed')
    ops=response['replacements']
    if not isinstance(ops,list) or not 1<=len(ops)<=3:
        raise ContractError('Actor review requires 1..3 field replacements')
    result=copy.deepcopy(base); seen=set(); index=packet['actor_index']
    for op in ops:
        if set(op)!={'path','value'} or op['path'] not in packet['allowed_paths'] or op['path'] in seen:
            raise ContractError('Actor review replacement outside assigned scope')
        seen.add(op['path']); result['actors'][index][op['path'].rsplit('/',1)[1]]=copy.deepcopy(op['value'])
    frames={f['frame_id']:f for f in packet['frames']}
    actor=result['actors'][index]
    for point in actor['trajectory']:
        if point.get('frame_id') not in frames or point.get('time_s')!=frames[point['frame_id']]['time_s']:
            raise ContractError('Actor review frame/time mismatch')
    if actor['simulation_role'] != base['actors'][index]['simulation_role'] or actor['is_static'] != base['actors'][index]['is_static']:
        if len({p['frame_id'] for p in actor['trajectory']})<2:
            raise ContractError('Motion/membership correction requires multiple source frames')
        if any(actor['actor_id'] in e.get('involved_actor_ids',[]) for e in base['events']):
            raise ContractError('Event participant requires event-owner adjudication')
    validate_compiled_behavior(result,read_json(packet['world_path']),read_json(packet['registry_path']),
                               packet['frames'],execution=False)
    return result


def run(runner, case, actor_id, *, mode='motion'):
    if mode == 'identity_link':
        from _lib.runtime.identity_revision import run as review_identity
        return review_identity(runner, case, actor_id)
    if mode == 'identity_split':
        from _lib.runtime.identity_revision import run as review_identity
        return review_identity(runner, case, actor_id, mode=mode)
    if mode != 'motion':
        raise ContractError('Unknown Actor review mode')
    case=Path(case).resolve(); state=runner.progress(case)
    if state.get('status') in {'done','done_degraded'}:
        raise ContractError('Completed observations are immutable')
    if read_json(case/'case.json')['parameters'].get('workflow_version')!='v3':
        raise ContractError('Actor motion review requires v3')
    stage='actor_motion_review_'+actor_id
    root=case/'diagnostics'/stage; request=root/'request.json'; candidate=root/'candidate.json'
    accepted=root/'accepted.json'; target=case/'state/behavior.json'
    prepared=root/'prepared.json'; journal=root/'commit.json'
    if accepted.exists():
        saved=read_json(accepted)
        if sha256(target)!=saved['result_sha256']: raise ContractError('Reviewed behavior changed')
        from _lib.contracts.core_lane_continuity import validate as validate_motion_obligations
        validate_motion_obligations(case, read_json(target))
        census_scope.verified_review(case, read_json(target))
        return saved
    if not request.exists():
        if state.get('diagnostics', {}).get('unresolved_submissions') or any(
                s.get('status') in {'submitted','pending','running'} or
                s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and
                not s.get('deadline_task_resolved') for s in state.get('sessions',[])):
            raise ContractError('Reconcile active model tasks first')
        base=read_json(target); index=next(i for i,a in enumerate(base['actors']) if a['actor_id']==actor_id)
        archive=root/'before.json'; atomic_json(archive,base)
        inputs=[target,case/'state/world_context.json',case/'state/actor_registry.json',case/'state/behavior_frames.json']
        packet=dict(base_path=str(archive),base_sha256=sha256(archive),actor_index=index,
            actor=base['actors'][index],actor_id=actor_id,
            world_path=str(inputs[1]),registry_path=str(inputs[2]),
            input_hashes={str(p):sha256(p) for p in inputs},frames=evidence_frames(case),
            allowed_paths=[f'/actors/{index}/{field}' for field in ('trajectory','is_static','simulation_role')])
        census_scope.bind_request(case, packet)
        packet['scope_review_sha256'] = sha256(case/census_scope.REVIEW) if (case/census_scope.REVIEW).is_file() else None
        atomic_json(request,packet)
    packet=read_json(request)
    request_sha256=sha256(request)
    census_scope.verify_request(case, packet)
    prompt=(f'使用 behavior-understanding 的图像取证规则，只读 {request} 和其中 frames 全部图像。'
        '这是独立的车辆运动事实复核：原描述、首次可见时间和单帧运动判断均可能错误。'
        '按图片确认同一车辆在各帧是否可见、相对路缘/建筑位置是否变化；区分自车视差与目标运动。'
        '不要因旧文字说横穿而认定横穿，也不要因为执行器不支持它就改成背景。'
        '若有跨帧证据纠正其静止/运动及参与范围，只修改 allowed_paths；轨迹保留真实可见帧，未知位置和尺度保持未知。'
        '未确定同一身份时不得合并早期车辆，结果保持原事实并说明证据不足。'
        '不得改原身份描述、World、registry、其他Actor、事件或局限；旧文字由审计保留，新的逐帧事实写入trajectory.detail。'
        'simulation_role=background仅在图像证明不影响自车及其他执行车辆路径时成立；reason说明证据。'
        f'只写 {candidate}，JSON严格为 {{"base_sha256":"请求中的哈希","reason":"含帧ID的理由",'
        '"replacements":[{"path":"允许的路径","value":"对应JSON值"}]}}。最多3项，每项必须是实际修正。')
    if 'scope_context' in packet:
        prompt += (f'\n范围复查还允许只读完整基线 {packet["base_path"]}，结合本轮补丁复查全部观察项；'
                   '只读权限不扩大 allowed_paths。以下私有字段是上述三字段响应的唯一例外。'
                   + census_scope.prompt(case, frozen_context=packet['scope_context']))

    def commit():
        bundle=read_json(journal); body=dict(bundle); signature=body.pop('sha256', None)
        if (signature != digest(body) or bundle.get('revision') != 'actor-motion-commit.v1'
                or bundle['request_sha256'] != request_sha256 or request_sha256 != sha256(request)
                or bundle['accepted']['result_sha256'] != sha256(prepared)):
            raise InputChanged('Actor review prepared commit changed')
        result=read_json(prepared); saved=bundle['accepted']; review=bundle['scope_review']
        _check_inputs(packet, target=target, result_sha256=saved['result_sha256'])
        census_scope.verify_request(case, packet)
        current_review=case/census_scope.REVIEW
        current_hash=sha256(current_review) if current_review.is_file() else None
        if current_hash != bundle['old_scope_review_sha256'] and not (
                review is not None and current_review.is_file() and read_json(current_review) == review):
            raise InputChanged('Actor review scope checkpoint changed')
        _validate_case(case, result, packet['frames'])
        checked=census_scope.validate_review(case, result, review['review'] if review else None)
        if checked != review:
            raise InputChanged('Actor review prepared scope proof changed')
        from _lib.behavior.input_binding import write_behavior
        write_behavior(case, result)
        census_scope.commit_review(case,review)
        census_scope.verified_review(case,result)
        atomic_json(accepted,saved)

    def adopt(_session=None):
        if journal.exists():
            commit(); return
        if sha256(request) != request_sha256:
            raise InputChanged('Actor review request changed during its task')
        _check_inputs(packet)
        census_scope.verify_request(case, packet)
        response, review=census_scope.split(read_json(candidate))
        result=materialize(packet,response)
        patched=result
        from _lib.behavior.compile import prepare_behavior_candidate
        result, _ = prepare_behavior_candidate(result, packet['frames'],
            read_json(packet['registry_path']), read_json(packet['world_path']), case=case)
        def unassigned(data):
            data=copy.deepcopy(data)
            for field in ('trajectory','is_static','simulation_role'):
                data['actors'][packet['actor_index']].pop(field,None)
            data['executor_primitives']=[p for p in data['executor_primitives']
                if not (p.get('actor_id')==actor_id and p.get('type')=='initial_state')]
            return data
        if unassigned(result) != unassigned(patched):
            raise ContractError('Actor review normalization changes unassigned facts')
        _validate_case(case,result,packet['frames'])
        try:
            checked=census_scope.validate_review(case,result,review)
        except census_scope.ScopeReviewBlocked as error:
            from _lib.runtime.census_scope_recovery import begin
            begin(runner,case,error,producer_stage=stage,producer_session=_session)
            raise
        old_review=sha256(case/census_scope.REVIEW) if (case/census_scope.REVIEW).is_file() else None
        if old_review != packet.get('scope_review_sha256'):
            raise InputChanged('Actor review scope checkpoint changed')
        atomic_json(prepared,result)
        saved=dict(actor_id=actor_id,source_sha256=packet['base_sha256'],result_sha256=sha256(prepared),
                   reason=response['reason'],candidate_path=str(candidate),original_path=packet['base_path'])
        body=dict(revision='actor-motion-commit.v1', request_sha256=request_sha256,
                  candidate_sha256=sha256(candidate), accepted=saved, scope_review=checked,
                  old_scope_review_sha256=old_review)
        atomic_json(journal,dict(body,sha256=digest(body)))
        commit()
    if not journal.exists() and prepared.exists() and sha256(target)==sha256(prepared):
        # Historical partial commits without scope authority retain their old recovery.
        census_scope.verified_review(case,read_json(target))
        if 'scope_context' in packet:
            raise InputChanged('Scoped Actor commit is missing its prepared journal')
        _check_inputs(packet,target=target,result_sha256=sha256(prepared))
        response=read_json(candidate)
        saved=dict(actor_id=actor_id,source_sha256=packet['base_sha256'],result_sha256=sha256(target),
                   reason=response['reason'],candidate_path=str(candidate),original_path=packet['base_path'])
        atomic_json(accepted,saved); return saved
    if not journal.exists():
        _check_inputs(packet)
    runner.model_stage(case,stage,'global-video-understanding',prompt,adopt,
                       precheck=adopt if candidate.exists() or journal.exists() else None,candidate_path=candidate,max_attempts=2)
    return read_json(accepted)
