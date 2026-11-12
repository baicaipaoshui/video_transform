"""Reuse World closure's task, lease, budget and commit journal for census.

Normal layout closure wraps both decisions in its one existing call. Explicit
continuation appends one task to the completed closure session; it never rebinds
the historical request or resets the Case budget.
"""
import copy
import json
from pathlib import Path

from _lib.contracts import census_adjudication as contract
from _lib.contracts import census_history as history
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError, validate_world
from _lib.runtime.case_core import atomic_json, materialize_pack, now, read_json, raw_read_json, sha256, validate_registry
from _lib.runtime.fact_versions import census_path

ROOT = 'diagnostics/world_census_adjudication'
ACTIVE = 'state/world_census_adjudication.active.json'


def _active(case):
    value=read_json(Path(case)/ACTIVE)
    body=dict(value);signature=body.pop('sha256',None)
    if signature!=digest(body) or body.get('revision') not in contract.REVISIONS:
        raise ContractError('World continuation authorization changed')
    return body


def _save_active(case,value):
    atomic_json(Path(case)/ACTIVE,dict(value,sha256=digest(value)))


def continuation_binding(case, key):
    if key != 'world_closure' or not (Path(case)/ACTIVE).is_file():
        return None
    active = _active(case)
    return {name:active[name] for name in ('request_sha256', 'session_id', 'prior_task_id')}


def _reservations(progress, active):
    name = ('world_contract_repair_reservations' if active.get('kind') == 'contract_repair'
            else 'model_task_reservations')
    return progress.setdefault('diagnostics', {}).setdefault(name, [])


def reusable_reservation(case, progress, binding):
    """Only an authorized, request-bound pre-dispatch reservation can be reused."""
    if not binding:
        return False
    active = _active(case)
    if active['status'] != 'authorized' or progress.get('diagnostics', {}).get('unresolved_submissions'):
        raise ContractError('World continuation submission must be reconciled before reservation')
    rows = [r for r in _reservations(progress, active)
            if r.get('world_continuation_binding') == binding
            and r.get('status') != 'abandoned_before_task_id']
    if len(rows) > 1 or any(r.get('status') or r.get('task_id') for r in rows):
        raise ContractError('World continuation already has a submitted reservation')
    return bool(rows)


def abandon_continuation_reservation(case, progress, key, reason):
    binding = continuation_binding(case, key)
    if not binding:
        return False
    active = _active(case)
    # An exception from a consumed/unknown grant must never refund a task.
    if active['status'] == 'authorized' and not progress.get('diagnostics', {}).get('unresolved_submissions'):
        for row in _reservations(progress, active):
            if row.get('world_continuation_binding') == binding and not row.get('status') and not row.get('task_id'):
                row.update(status='abandoned_before_task_id', reason=str(reason)[:200], at_abandoned=now())
        if active.get('kind') != 'contract_repair':
            used = sum(r.get('additional', False) for r in _reservations(progress, active)
                       if r.get('status') not in {'abandoned_before_task_id', 'credited_pipeline_contract_fault'}
                       and r.get('budget_scope') != 'authorized_transport_recovery')
            progress['diagnostics'].setdefault('model_recovery_budget', {}).update(used=used, remaining=max(0, 2-used))
    return True  # Never fall through to an unrelated historical reservation.


def bind_continuation_receipt(case, progress, key, receipt):
    binding = continuation_binding(case, key)
    if not binding:
        return
    rows = [r for r in _reservations(progress, _active(case))
            if r.get('world_continuation_binding') == binding and not r.get('status')]
    if len(rows) != 1 or receipt['session_id'] != binding['session_id']:
        raise ContractError('World continuation task receipt lost its reservation binding')
    rows[0].update(status='submitted', task_id=receipt['task_id'], session_id=receipt['session_id'])


def _recorded_task(case, progress, active):
    binding = continuation_binding(case, 'world_closure')
    ids = {r['task_id'] for r in _reservations(progress, active)
           if r.get('world_continuation_binding') == binding and r.get('task_id')}
    return next((s for s in reversed(progress.get('sessions', []))
                 if s.get('task_id') in ids and s.get('stage') == 'world_closure'
                 and s.get('session_id') == active['session_id']), None)


def _validate_continuation(runner, case, active):
    if any((Path(case)/name).exists() for name in ('state/behavior.json', 'state/behavior.candidate.json',
            'state/behavior_input.request.json', 'final/analysis.json')):
        raise ContractError('World closure continuation is only before first Behavior')
    state = runner.progress(case)
    if (state.get('diagnostics', {}).get('unresolved_submissions') or any(
            s.get('status') in {'submitted', 'pending', 'running'} or
            (s.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
             or s.get('recovered_from_poll_failure')) and not s.get('deadline_task_resolved')
            for s in state.get('sessions', []))):
        raise ContractError('Reconcile all remote tasks before World continuation')
    if active.get('kind') == 'scope_recovery':
        from _lib.runtime.census_scope_recovery import KEY
        record = state.get('diagnostics', {}).get(KEY)
        if not record or record['trigger_sha256'] != active.get('scope_trigger_sha256'):
            raise ContractError('Scope continuation lost its Behavior conflict binding')
        latest = next((s for s in reversed(state['sessions']) if s.get('task_id') == active['prior_task_id']), {})
    else:
        latest = state['sessions'][-1]
    if (latest.get('task_id') != active['prior_task_id'] or latest.get('session_id') != active['session_id']
            or latest.get('status') != ('invalid_output' if active.get('kind') == 'contract_repair' else 'completed')
            or latest.get('recovered_from_poll_failure') and not latest.get('deadline_task_resolved')):
        raise ContractError('World continuation no longer matches its terminal task')
    packet = read_json(active['request_path']); contract.verify_packet(packet)
    if packet.get('parent_certificate') and (
            packet['parent_certificate'] != active.get('parent_certificate')
            or not census_path(case).is_file()
            or sha256(census_path(case)) != packet['parent_certificate']['sha256']):
        raise ContractError('World continuation accepted parent changed')
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    view = current_facts(case) if (Path(case)/FACT_ROOT/'active.json').is_file() else None
    world_path = view.world_path if view else Path(case)/'state/world_context.json'
    registry_path = view.registry_path if view else Path(case)/'state/actor_registry.json'
    if (packet['request_sha256'] != active['request_sha256']
            or active.get('fact_version', view.version_id if view else None) != (view.version_id if view else None)
            or sha256(world_path) != active['world_sha256']
            or sha256(registry_path) != active['registry_sha256']):
        raise ContractError('World continuation live input changed')


def _immutable(path, value):
    if path.is_file() and read_json(path) != value:
        raise ContractError('World adjudication immutable snapshot changed')
    if not path.is_file():
        atomic_json(path, value)


def _validate_layout_source(case, layout):
    """Reject an invalid inherited World before spending a model attempt."""
    if layout is None:
        return
    base_value = layout.get('base_path') if isinstance(layout, dict) else None
    if not isinstance(base_value, str):
        # Evidence-planning callers may supply only focused layout frames.
        # A real frozen closure request always carries lane_layout_contract.
        if isinstance(layout, dict) and layout.get('lane_layout_contract'):
            raise ContractError('Combined layout request is missing its World base')
        return
    base = Path(base_value)
    if (not base.is_file() or layout.get('base_sha256') != sha256(base)):
        raise ContractError('Combined layout World base changed')
    probe = read_json(Path(case)/'state/probe.json')
    from _lib.world.gaps import enabled
    validate_world(read_json(base), probe['probe']['duration_s'],
                   observation_contract=enabled(case))


def _layout_request_without_bindings(value):
    result = copy.deepcopy(value)
    for key in ('request_sha256', 'base_path', 'base_sha256', 'input_hashes',
                'validator_revision', 'output_path'):
        result.pop(key, None)
    # This is only a local pointer to the same hash-bound frame index.  It can
    # differ when a Case is replayed in a temporary checkout.
    result.get('focused_context', {}).pop('frame_index_path', None)
    return result


def _opposing_unknown_binding_repair(case, parent, layout):
    """Prove that a new layout request only repairs the known null binding bug."""
    old_layout = parent.get('layout_request')
    if not isinstance(old_layout, dict) or not isinstance(layout, dict):
        raise ContractError('Pipeline repair requires old and new layout requests')
    try:
        _validate_layout_source(case, old_layout)
    except ContractError as error:
        if getattr(error, 'rule_id', None) != 'world.opposing_count_unknown_binding':
            raise
    else:
        raise ContractError('Pipeline repair source is already valid')
    _validate_layout_source(case, layout)
    if _layout_request_without_bindings(old_layout) != _layout_request_without_bindings(layout):
        raise ContractError('Pipeline repair changed the layout task')
    old_world = read_json(old_layout['base_path'])
    new_world = read_json(layout['base_path'])
    path = ['world.road.opposite_direction_lanes']
    def item(document):
        rows = [row for row in document.get('questions_to_verify', [])
                if row.get('world_paths') == path]
        if len(rows) != 1:
            raise ContractError('Pipeline repair requires one opposing-count question')
        return rows[0]
    old_item, new_item = item(old_world), item(new_world)
    if (old_world['world']['road'].get('opposite_direction_lanes') is not None
            or new_world['world']['road'].get('opposite_direction_lanes') is not None
            or old_item.get('resolution') != 'resolved'
            or new_item.get('resolution') != 'unresolved'):
        raise ContractError('Pipeline repair is not the opposing-count resolution defect')
    expected = copy.deepcopy(old_world)
    index = expected['questions_to_verify'].index(old_item)
    expected['questions_to_verify'][index] = copy.deepcopy(new_item)
    if expected != new_world:
        raise ContractError('Pipeline repair changed facts beyond the opposing-count binding')
    progress = read_json(Path(case)/'state/progress.json')
    archive = archived_candidate(case, progress.get('sessions', []),
                                 request_sha256=parent['request_sha256'])
    if archive is None:
        raise ContractError('Pipeline repair requires the bound archived candidate')
    response = read_json(archive)
    try:
        compile_response(parent, response)
    except ContractError as error:
        if getattr(error, 'rule_id', None) != 'world.opposing_count_unknown_binding':
            raise ContractError('Archived candidate failed for a different reason') from error
    else:
        raise ContractError('Archived candidate already validates without pipeline repair')
    session = next((row for row in reversed(progress.get('sessions', []))
                    if row.get('stage') == 'world_closure'
                    and row.get('invalid_candidate_path') == str(archive)), None)
    if not session or type(session.get('attempt')) is not int:
        raise ContractError('Pipeline repair lost the archived attempt binding')
    return dict(revision='world-pipeline-contract-repair.v1',
        rule_id='world.opposing_count_unknown_binding',
        parent_request_path=str((Path(case)/ROOT/'initial/request.json').resolve()),
        parent_request_sha256=parent['request_sha256'],
        candidate_path=str(archive.resolve()), candidate_sha256=sha256(archive),
        source_attempt=session['attempt'],
        evidence_plan_revision=parent.get('evidence_plan', {}).get('revision'))


def _identity_selection_prompt(registry, plan):
    """Describe frozen Actor types without changing candidate/evidence obligations."""
    groups = sorted(a['actor_id'] for a in registry if a['actor_type'] == 'vehicle_group')
    if not groups:
        return ''
    allowed = set(plan['review_actor_ids']) if plan else {a['actor_id'] for a in registry}
    individuals = sorted(a['actor_id'] for a in registry
                         if a['actor_type'] == 'vehicle' and a['actor_id'] != 'ego'
                         and a['actor_id'] in allowed)
    selection = dict(existing_individual_actor_ids=individuals, vehicle_group_context_ids=groups)
    return ('\nRegistry 身份类型选择范围：' + json.dumps(selection, ensure_ascii=False)
            + '\nexisting 个体身份只能从 existing_individual_actor_ids 中选择，仍须满足全部冻结 links 和反证要求。'
              'vehicle_group_context_ids 仅作群组上下文；属于群组不等于属于同一物理车辆。'
              'candidate_actor_ids 或 evidence_plan.review_actor_ids/links 中出现群组 ID，'
              '不授权将其用作 existing 个体身份、comparison 个体见证或 identity_groups 同车合并。'
              '禁止把多个同帧独立观察通过 existing 绑定到同一个 vehicle_group；'
              '此规则同时适用于首见帧和后续帧，群组多帧 sightings 不证明成员跨帧连续身份。'
              '只能确认群组成员、不能确认个体时，使用 outcome=unresolved、vehicle_key=null、'
              'separation=null；仅在响应 Schema 包含 scope_claim 时将其设为 null。'
              '保留原 issue 及证据，reason 说明群组关系和个体未决，不输出 Schema 未授权字段。'
              '不要为完成任务猜测或新建 Actor；new 仍须满足原独立新车证据要求。'
              'background_candidate 不等于身份确认或已获准排除，excluded_opposing 仍遵守原准入条件。\n')


def prepare(case, layout_path=None, *, root=None, revision=None, lineage=None, parent_certificate=None,
            scope_revocations=None, scope_recovery=None, scope_coverage_enabled=True):
    case = Path(case).resolve(); root = Path(root).resolve() if root else case/ROOT/'initial'
    request_path = root/'request.json'
    pipeline_repair = None
    parent_packet = None
    if request_path.is_file():
        packet = read_json(request_path); contract.verify_packet(packet)
        try:
            _validate_layout_source(case, packet.get('layout_request'))
        except ContractError:
            if layout_path is None or root != case/ROOT/'initial':
                raise
            layout = read_json(layout_path)
            pipeline_repair = _opposing_unknown_binding_repair(case, packet, layout)
            parent_packet = packet
            root = case/ROOT/'pipeline_contract_repair'/layout['request_sha256'][:16]
            request_path = root/'request.json'
            if request_path.is_file():
                repaired = read_json(request_path); contract.verify_packet(repaired)
                _validate_layout_source(case, repaired.get('layout_request'))
                return repaired
        else:
            return packet
    if (case/'state/behavior.json').exists() or (case/'final/analysis.json').exists():
        raise ContractError('Combined census adjudication is only before the first Behavior')
    if (case/'state/identity_link.accepted.json').exists():
        raise ContractError('Existing identity revision requires separate owner reconciliation')
    prior = contract.proof(case) if parent_certificate else None
    pending = history.remaining_records(case, prior, reopen=scope_revocations or ())
    if scope_recovery:
        from _lib.runtime.census_scope_recovery import selected_records
        pending = selected_records(pending, scope_recovery)
    if not pending or len(pending)>256:
        raise ContractError('Combined census adjudication requires 1..256 unresolved records')
    world = read_json(case/'state/world_context.json')
    registry = read_json(case/'state/actor_registry.json')
    policy = read_json(case/'state/execution_policy.json')
    revision = revision or policy.get('world_census_adjudication', contract.REVISION)
    layout = read_json(layout_path) if layout_path else None
    _validate_layout_source(case, layout)
    inherited_sightings, inherited_objects = history.confirmed(case, prior) if prior else ({}, [])
    from _lib.world.actor_facts import frames_for
    decoded = sorted(frames_for(case), key=lambda f:f['time_s'])
    by_id = {f['frame_id']:f for f in decoded}
    required = {r['frame_id'] for r in pending}
    required.update(a['first_seen_frame_id'] for a in registry)
    required.update(f['frame_id'] for f in decoded[:4])
    from _lib.behavior.compile import evidence_frames
    try:
        available = evidence_frames(case)
    except ContractError:
        evidence = read_json(case/'state/global_frames.json')['frames'][:]
        for spec in read_json(case/'manifest.json').get('batches', []):
            if (case/'batches'/f"{spec['batch_id']}.json").is_file():
                evidence.extend(spec.get('frames', []))
        missing = sorted({f['frame_id'] for f in evidence if not Path(f['path']).is_file()})
        if not missing:
            raise
        from _lib.world.closure import ClosureStopped
        raise ClosureStopped('world_evidence_insufficient', 'Required census image is missing', dict(
            stage='world_closure', automatic_retry=False, retry_kind='new_evidence',
            rule_id='identity.census.evidence_missing', frame_ids=missing))
    required.update(f['frame_id'] for f in available)
    upgrade = None
    if revision == contract.REVISION and len(required) > 12 and root == case/ROOT/'initial':
        state = read_json(case/'state/progress.json')
        # Only an unsubmitted outer request may adopt the new private contract.
        # Existing requests return above; sessions, candidates and budgets stay frozen.
        if (not state.get('attempts', {}).get('world_closure', 0)
                and not any(s.get('stage') == 'world_closure' for s in state.get('sessions', []))
                and not (case/'state/world_closure.candidate.json').exists()
                and not (root/'candidate.json').exists()
                and not state.get('diagnostics', {}).get('unresolved_submissions')):
            upgrade = dict(policy_revision=revision, reason='pre_submission_evidence_budget')
            revision = contract.FOCUSED_REVISION
    plan = None
    evidence_upgrade_state = None
    if revision in contract.FOCUSED_REVISIONS:
        from _lib.contracts import census_evidence
        from _lib.contracts.census_evidence import build, EvidencePlanError
        evidence_revision = policy.get(census_evidence.POLICY_KEY, census_evidence.REVISION)
        if pipeline_repair:
            evidence_revision = pipeline_repair['evidence_plan_revision']
        if prior:
            evidence_revision = prior['request'].get('evidence_plan', {}).get('revision', evidence_revision)
        try:
            inherited = dict(confirmed_sightings=inherited_sightings) if prior else {}
            options = dict(scope_coverage=revision in contract.SCOPE_REVISIONS and scope_coverage_enabled, **inherited)
            from _lib.runtime.census_scope_recovery import alternative_actors
            options['alternative_actor_ids'] = alternative_actors(case, scope_recovery)
            try:
                plan = build(case, registry, pending, layout, revision=evidence_revision, **options)
            except EvidencePlanError as error:
                state = read_json(case/'state/progress.json')
                if (error.details['reason'] != 'evidence_budget_exceeded'
                        or error.details.get('required_count', 0) <= census_evidence.MAX_FRAMES
                        or not census_evidence.upgrades(evidence_revision)
                        or root != case/ROOT/'initial' or prior or lineage or scope_recovery or scope_revocations
                        or not census_evidence.unsubmitted(state)
                        or (case/'state/world_closure.candidate.json').exists()
                        or (root/'candidate.json').exists() or (case/ACTIVE).exists()
                        or any((case/ROOT).rglob('request.json'))):
                    raise
                for replacement in census_evidence.upgrades(evidence_revision):
                    try:
                        plan = build(case, registry, pending, layout, revision=replacement, **options)
                        break
                    except EvidencePlanError as replacement_error:
                        if (replacement_error.details['reason'] != 'evidence_budget_exceeded'
                                or replacement == census_evidence.OBLIGATION_REVISION):
                            raise
                evidence_upgrade_state = state
        except EvidencePlanError as error:
            from _lib.world.closure import ClosureStopped
            raise ClosureStopped('world_evidence_insufficient', str(error), dict(
                stage='world_closure', automatic_retry=False,
                retry_kind='new_evidence' if error.details['reason']=='evidence_missing' else 'replan_evidence',
                rule_id=error.rule_id, **error.details)) from error
        required = set(plan['selected_frame_ids'])
    if not required <= set(by_id) or not 2<=len(required)<=12:
        from _lib.world.closure import ClosureStopped
        raise ClosureStopped('world_evidence_insufficient',
            'Census comparison needs 2..12 existing frames; no silent thinning',
            dict(stage='world_closure',automatic_retry=False,retry_kind='new_evidence'))
    selected = [f for f in decoded if f['frame_id'] in required]
    sources = [Path(f.get('source_path') or f['path']).resolve() for f in selected]
    for frame, source in zip(selected, sources):
        if not source.is_file():
            from _lib.world.closure import ClosureStopped
            raise ClosureStopped('world_evidence_insufficient', 'Required census image is missing', dict(
                stage='world_closure', automatic_retry=False, retry_kind='new_evidence',
                rule_id='identity.census.evidence_missing', frame_id=frame['frame_id']))
        if (source==Path(frame['path']).resolve()
                and frame.get('sha256') and sha256(source)!=frame['sha256']):
            raise ContractError('Census comparison source image changed')
    lane_position = policy.get(contract.LANE_POSITION_KEY)
    schema = contract.response_schema(revision, layout=layout, lane_position_evidence=lane_position)
    prompt_name = ('world-census-adjudication-v5-prompt.md' if revision in contract.INFORMATION_REVISIONS else
                   'world-census-adjudication-v3-prompt.md' if revision in contract.MERGE_REVISIONS else
                  'world-census-adjudication-v2-prompt.md' if revision == contract.FOCUSED_REVISION
                  else 'world-census-adjudication-prompt.md')
    template = (Path(__file__).resolve().parents[2]/'world-understanding/references'/prompt_name).read_text()
    template += _identity_selection_prompt(registry, plan)
    if lane_position == contract.LANE_POSITION_REVISION:
        template += ('\n在每辆绑定车辆首次确认车道或改变物理走廊的 sighting 中，'
            '提供 lane_position_evidence 的 right_boundary_id、left_boundary_id，'
            '并在 features 说明车辆轮迹位于这两条实际可见边界之间。'
            '已有初始车道与同一物理走廊的重复观察无需重复证明。'
            '依据该帧道路边界而非画面左右或相对 Ego 的位移判断，证据不足填 lane_id=null。\n')
    if prior:
        template += ('\n本次仅回答 records 中剩余问题。parent_certificate 为已验收历史凭证，'
                     '其身份、目击和排除结论不可撤销；confirmed_objects/confirmed_sightings '
                     '包含历史已确认对象。不得重答已验收 issue 或拆分/替换已验收身份。\n')
    if revision == contract.RELEVANCE_REVISION:
        from _lib.contracts.census_relevance import world_prompt
        template += world_prompt()
    if revision in contract.SCOPE_REVISIONS:
        template += (('\n范围语义沿用 v4：' if revision in contract.INFORMATION_REVISIONS else '\n本请求为 v4：')
            + '每条 binding 必须增加 scope_claim，普通结论填 null。'
            '仅 scope_plan.eligible=true 时可使用 observation_only；完整规则在 response_schema。'
            '唯一新规则 stationary_isolated_offroad.v1：对象在全部指定帧可见、静止、位于车行道外、'
            '被物理屏障隔开且无车行道入口。逐帧给位置、静止及屏障视觉依据；遮挡/未知/移动不允许。'
            '必须 refute 全部已登记非 ego 车辆，复用有锚点的共现区分证据；不能以远、模糊或没事件替代。'
            '这是有限采样证据支持的执行范围决定，不是全视频每一刻无交互的数学证明。'
            'scope_claim.window_s 必须等于 scope_plan.window_s；其他结论不得携带 scope_claim。\n')
    if scope_recovery:
        template += ('\n这是已发生 Behavior 反证的受限 census 恢复。读取 scope_recovery.path 的绑定 trigger，'
            '其中 review 给出相关 issue 的反证和受影响对象。必须针对该反证重新取证，'
            '不能仅重复原 observation_only 理由；确实不足保持 unresolved。'
            'trigger.behavior 仅是只读基线，World Worker 不修改 Behavior、事件或正式文件。\n')
    text = json.dumps(dict(world=world, actors=registry, records=pending, layout=layout,
        evidence_plan=plan, prompt=template), ensure_ascii=False) if plan else ''
    if prior:
        text += json.dumps(dict(parent_certificate=parent_certificate,
            confirmed_sightings=inherited_sightings, confirmed_objects=inherited_objects,
            inherited_actor_aliases=history.aliases(prior)), ensure_ascii=False)
    try:
        packed = materialize_pack(sources, [(12,2560,85),(12,2560,70),(12,1920,80)],
            root/'frames','census',text=text,schema=schema if plan else None,
            protected_paths=sources,max_image_bytes=2*1024*1024)
    except RuntimeError as error:
        if str(error) != 'packing_budget_exceeded':
            raise
        if revision in contract.SCOPE_REVISIONS and scope_coverage_enabled:
            # Optional scope context must not stop an otherwise valid identity call.
            return prepare(case, layout_path, root=root, revision=revision, lineage=lineage,
                parent_certificate=parent_certificate, scope_revocations=scope_revocations,
                scope_recovery=scope_recovery, scope_coverage_enabled=False)
        from _lib.world.closure import ClosureStopped
        raise ClosureStopped('world_evidence_insufficient', 'Required census images exceed payload budget', dict(
            stage='world_closure', automatic_retry=False, retry_kind='replan_evidence',
            rule_id='identity.census.evidence_payload_budget_exceeded', required_count=len(sources))) from error
    if len(packed['frames'])!=len(selected):
        raise ContractError('Census comparison packing omitted required frames')
    frames=[dict(p,frame_id=f['frame_id'],time_s=f['time_s'],source_path=str(source))
            for p,f,source in zip(packed['frames'],selected,sources)]
    snapshots={}
    for key,value in (('world',world),('registry',registry)):
        path=root/'before'/f'{key}.json';_immutable(path,value);snapshots[key+'_path']=str(path)
    if evidence_upgrade_state is not None:
        path = root/'before/evidence_preparation.json'
        if path.is_file():
            # A crash after writing the preparation snapshot must not replace
            # it with a later progress timestamp. Current state passed the
            # same unsubmitted guard above; preserve the original evidence.
            if not census_evidence.unsubmitted(read_json(path)):
                raise ContractError('Census evidence preparation is not unsubmitted')
        else:
            _immutable(path, evidence_upgrade_state)
        snapshots['evidence_preparation_path'] = str(path)
    confirmed={};confirmed_objects=[]
    batches=sorted((case/'batches').glob('batch_*.json'))
    for path in batches:
        for f in read_json(path).get('vehicle_census',[]):
            for row in f['vehicles']:
                if row['binding']!='unresolved':
                    confirmed.setdefault(f['frame_id'],set()).add(row['actor_id'])
                    confirmed_objects.append(dict(batch_id=path.stem,frame_id=f['frame_id'],
                        object_id=row['object_id'],actor_id=row['actor_id']))
    if prior:
        confirmed, confirmed_objects = inherited_sightings, inherited_objects
    if layout_path:
        frozen=root/'layout.request.json';_immutable(frozen,layout)
        snapshots['layout_path']=str(frozen)
    files=[case/'case.json',case/'state/execution_policy.json',case/'state/probe.json',
           case/'state/global_frames.json',case/'manifest.json',*batches]
    files += [Path(p) for p in snapshots.values()]+sources+[Path(f['path']) for f in frames]
    if pipeline_repair:
        files += [Path(pipeline_repair['parent_request_path']),
                  Path(pipeline_repair['candidate_path'])]
        if parent_packet.get('evidence_preparation_path'):
            files.append(case/ROOT/'initial/before/evidence_preparation.json')
    if scope_recovery:
        from _lib.contracts.behavior_census_scope import ADOPTION
        if (case/ADOPTION).is_file():
            files.append(case/ADOPTION)
        trigger = read_json(scope_recovery['path'])
        if trigger.get('pending_census'):
            for frame in trigger['frames']:
                for row in [frame, *frame.get('variants', [])]:
                    files += [Path(row[k]) for k in ('path','source_path') if row.get(k)]
    packet=dict(revision=revision,case_id=case.name,case_dir=str(case),
        mode='layout_and_census' if layout else 'census_after_layout',
        records=pending,actors=registry,confirmed_sightings={k:sorted(v) for k,v in confirmed.items()},
        confirmed_objects=confirmed_objects,
        frames=frames,layout_request=layout,**snapshots,
        response_schema=schema, prompt_template=template,
        input_hashes={str(p.resolve()):sha256(p) for p in files},
        output_path=str(root/'candidate.json'))
    if lane_position is not None:
        packet[contract.LANE_POSITION_KEY] = lane_position
    if plan:
        packet.update(evidence_plan=plan, contract_upgrade=upgrade,
            packing={k:v for k,v in packed.items() if k!='frames'})
        if evidence_upgrade_state is not None:
            packet['evidence_plan_upgrade'] = dict(from_revision=evidence_revision,
                reason='pre_submission_evidence_budget')
    if revision in contract.MERGE_REVISIONS:
        packet['evidence_scopes'] = contract.evidence_scopes(packet)
    if revision in contract.SCOPE_REVISIONS:
        from _lib.contracts.census_scope import plan
        packet['scope_plan'] = plan(case, frames)
        packet['scope_coverage_enabled'] = scope_coverage_enabled
        if not scope_coverage_enabled:
            packet['scope_plan']['eligible'] = False
    if scope_recovery:
        packet['scope_recovery'] = scope_recovery
        packet['input_hashes'][scope_recovery['path']] = scope_recovery['sha256']
    if scope_revocations:
        packet['scope_revocations'] = sorted(scope_revocations)
    if lineage:
        packet['recovery_lineage'] = lineage
        for name in ('parent_request_path', 'candidate_path'):
            path=Path(lineage[name]).resolve()
            packet['input_hashes'][str(path)] = sha256(path)
    if parent_certificate:
        packet['parent_certificate'] = parent_certificate
        packet['inherited_actor_aliases'] = history.aliases(prior)
        packet['input_hashes'][parent_certificate['path']] = parent_certificate['sha256']
    if pipeline_repair:
        packet['pipeline_contract_repair'] = pipeline_repair
        if parent_packet.get('evidence_plan_upgrade') is not None:
            packet['evidence_plan_upgrade'] = copy.deepcopy(parent_packet['evidence_plan_upgrade'])
            packet['evidence_preparation_path'] = str(
                case/ROOT/'initial/before/evidence_preparation.json')
    packet['request_sha256']=digest(packet);_immutable(request_path,packet)
    contract.verify_packet(packet)
    return packet


def compile_response(packet, response):
    contract.verify_packet(packet)
    if packet['revision'] in contract.MERGE_REVISIONS:
        contract.validate_response(packet, response)
    from _lib.world import lane_layout
    world=raw_read_json(packet['world_path']);registry=raw_read_json(packet['registry_path'])
    if packet['mode']=='layout_and_census':
        if not isinstance(response.get('layout'),dict):
            contract.fail('Combined response requires its original layout decision')
        from _lib.world.closure import materialize
        inner=packet['layout_request'];patch=response['layout']
        # A layout answer may echo a Batch-only Actor. The frozen layout lock
        # defines which Actor corridors this decision can revise; discarding an
        # out-of-scope corridor statement does not remove the Actor or its
        # census evidence. Keep the archived model answer intact.
        if (isinstance(patch.get('decision'), dict)
                and isinstance(patch['decision'].get('actor_corridors'), dict)):
            allowed = {row['actor_id'] for row in inner['focused_context']['actor_identity_lock']}
            if set(patch['decision']['actor_corridors']) - allowed:
                patch = copy.deepcopy(patch)
                patch['decision']['actor_corridors'] = {
                    aid: row for aid, row in patch['decision']['actor_corridors'].items()
                    if aid in allowed}
        revised=materialize(Path(packet['case_dir']),patch,request_path=packet['layout_path'])
        source_world=read_json(inner['base_path'])
        mapping=(lane_layout.decision_lane_map(inner,source_world,patch)
                 if inner['lane_layout_contract']['revision'] in lane_layout.DECISION_REVISIONS else patch['lane_id_map'])
        adjudication=dict(source_epoch=source_world['evidence_epoch'],lane_id_map=mapping,
                          request_sha256=inner['request_sha256'])
        revision=inner['lane_layout_contract']['revision']
        if revision==lane_layout.EVIDENCE_DECISION_REVISION:
            adjudication['claim_assessments']=copy.deepcopy(patch['decision']['claim_assessments'])
        if revision in lane_layout.DERIVED_DECISION_REVISIONS:
            adjudication.update(claim_assessments=lane_layout.derived_claim_assessments(inner,patch['decision']),
                claim_disputes=copy.deepcopy(lane_layout.compile_corridor_positions(inner,patch['decision'])['claim_disputes']))
        revised['world']['metadata']['lane_layout_review']['adjudication']=adjudication
        validate_registry(world['actor_hypotheses'],revised['actor_hypotheses'])
        # The census answer was written against the frozen pre-layout Actor
        # anchors. Translate only sightings inside an Actor's already
        # confirmed window, where its old lane is known. Later observations
        # remain the model's separate lane judgments.
        response=contract.normalize_confirmed_layout_sightings(response, registry, mapping)
        registry=lane_layout.remap_registry(registry,mapping,revised)
        world=revised
    else:
        if response.get('layout') is not None:
            contract.fail('Continuation must preserve the already accepted layout')
        world=copy.deepcopy(world)
        if not packet.get('scope_revocations'):
            world['evidence_epoch']+=1
    result=contract.compile_census(packet,response,world,registry)
    history.preserve(history.parent(packet), response, result)
    validate_world(result['world'],read_json(Path(packet['case_dir'])/'state/probe.json')['probe']['duration_s'],
                   observation_contract=True)
    from _lib.world import admission
    if admission.enabled(packet['case_dir']):
        admission.validate(result['world'],admission.duration_for(packet['case_dir']),case=packet['case_dir'])
    return result


def certificate(packet,response,result):
    projected, changes = contract.project_group_memberships(packet,response,raw_read_json(packet['registry_path']))
    if changes and digest(compile_response(packet,projected)) != digest(result):
        raise ContractError('Census group projection changes its reviewed result',
                            rule_id='identity.census.adjudication')
    value=dict(request=packet,response=projected,result=contract.summary(result),result_sha256=digest(result))
    if changes:
        value.update(raw_response=response,response_projection=changes)
        contract.verify_group_projection(value)
    return dict(value,sha256=digest(value))


def validate_journal(journal):
    if journal.get('post_behavior_revision'):
        from _lib.world.post_behavior import validate_commit
        validate_commit(journal)
        return
    saved=journal.get('census_certificate')
    if not saved:
        return
    body=dict(saved);signature=body.pop('sha256',None)
    if signature!=digest(body):
        contract.fail('Prepared census certificate changed')
    contract.verify_group_projection(saved)
    result=compile_response(saved['request'],saved['response'])
    if (digest(result)!=saved['result_sha256'] or result['world']!=journal['world']
            or result['registry']!=journal['registry']
            or saved.get('result')!=contract.summary(result)
            or journal['receipt'].get('census_adjudication')!=saved['request']['request_sha256']):
        contract.fail('Prepared census journal differs from reviewed response')
    if (saved['request'].get('parent_certificate') and
            journal['receipt'].get('remaining_census_issue_ids') != result['remaining_issue_ids']):
        contract.fail('Prepared census journal remaining issues changed')


def _producer_pending(sessions):
    return any(s.get('stage')=='world_closure' and (s.get('status') in {'submitted','pending','running'} or
        s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and not s.get('deadline_task_resolved'))
        for s in sessions)


def _require_live_baseline(case, packet):
    for key, name in (('world_path', 'world_context.json'), ('registry_path', 'actor_registry.json')):
        if read_json(packet[key]) != read_json(case/'state'/name):
            raise ContractError('Census live baseline differs from frozen ' + name,
                                rule_id='identity.census.input_binding')


def pending_reconciliation(case, packet):
    """Recover first-closure inputs, never fabricate a completed reconciliation.

    The original joint closure receives this value in memory. After a restart
    it can be rebuilt only from the complete frozen inputs and must reproduce
    the exact World base already assigned to the layout owner.
    """
    case = Path(case)
    rule = 'identity.census.input_binding'
    layout = packet.get('layout_request')
    if (packet.get('parent_certificate') or packet.get('scope_recovery')
            or packet.get('mode') != 'layout_and_census' or not layout
            or not layout.get('base_path') or (case/contract.ACCEPTED).exists()
            or (case/'state/lane_layout.commit.json').exists()
            or any((case/name).exists() for name in ('state/behavior.json',
                'state/behavior_input.request.json', 'final/analysis.json'))):
        raise ContractError('Missing reconciliation receipt is only recoverable before the initial joint closure',
                            rule_id=rule)
    contract.verify_packet(packet)
    _require_live_baseline(case, packet)
    paths = sorted((case/'batches').glob('batch_*.json'))
    specs = read_json(case/'manifest.json')['batches']
    expected = {spec['batch_id']+'.json' for spec in specs}
    frozen = {Path(p).name for p in packet['input_hashes'] if Path(p).parent == case/'batches'}
    if len(expected) != len(specs) or expected != frozen or expected != {p.name for p in paths}:
        raise ContractError('Pending reconciliation requires the complete frozen Batch chain', rule_id=rule)
    for path, signature in layout['input_hashes'].items():
        if not Path(path).is_file() or sha256(path) != signature:
            raise ContractError('Pending reconciliation layout input changed: ' + path, rule_id=rule)
    from _lib.stages.reconciliation import reconciliation_result
    from _lib.world.lane_layout import handles_conflicts
    result = reconciliation_result(case, paths)
    if result['world'] != read_json(layout['base_path']):
        raise ContractError('Rebuilt reconciliation differs from the frozen layout World base', rule_id=rule)
    if (not any(row['rule_ids'] for row in result.get('layout_reviews', []))
            or result['status'] == 'needs_review' and not handles_conflicts(result, [read_json(p) for p in paths])):
        raise ContractError('Pending reconciliation contains conflicts outside the frozen layout closure', rule_id=rule)
    return result


def run(runner,case,layout_path=None,result_receipt=None,*,packet=None):
    case=Path(case);packet=packet or prepare(case,layout_path)
    _validate_layout_source(case, packet.get('layout_request'))
    progress=runner.progress(case);progress['substage']='world_closure';runner.save_progress(case,progress)
    root=Path(packet['output_path']).parent;candidate=Path(packet['output_path'])
    request=root/'request.json'
    journal_path = case/'state/lane_layout.commit.json'
    if journal_path.is_file() and read_json(journal_path).get('status') != 'committed':
        if not runner.commit_lane_layout(case, {p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')}):
            raise ContractError('Recover existing World commit before census adjudication')
    if (case/ACTIVE).is_file():
        authorization = _active(case)
        if (authorization.get('submission_sequence') and authorization['status'] == 'consumed'
                and authorization['request_sha256'] == packet['request_sha256']
                and _recorded_task(case, progress, authorization) is None):
            from _lib.runtime.orchestrator import BackendError
            raise BackendError('submission_unresolved',
                'World continuation dispatch has no durable task receipt; remote acceptance is unknown')
    receipt_base = None
    def committed():
        saved = contract.proof(case)
        if not saved:
            if packet.get('parent_certificate'):
                raise ContractError('Census continuation lost its accepted parent')
            return False
        if saved['request']['request_sha256'] == packet['request_sha256']:
            return True
        link = packet.get('parent_certificate')
        if not link or sha256(census_path(case)) != link['sha256']:
            raise ContractError('Census result belongs to another request')
        contract.verify_packet(packet)
        return False
    def adopt(_session=None, *, source=None):
        if committed():
            return
        _require_live_baseline(case, packet)
        from _lib.runtime.orchestrator import candidate_snapshot
        response,_=candidate_snapshot(case,'world_closure',Path(source) if source else candidate)
        result=compile_response(packet,response)
        receipt=copy.deepcopy(receipt_base)
        if receipt.get('batch_sha256')!={p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')}:
            raise ContractError('Census commit Batch receipt changed',rule_id='identity.census.input_binding')
        receipt.update(status='reconciled_after_geometry_readjudication',world=result['world'],
            census_adjudication=packet['request_sha256'],
            remaining_census_issue_ids=result['remaining_issue_ids'])
        if packet['mode']=='layout_and_census':
            receipt['adjudicated_conflict_ids']=receipt.get('unresolved_conflict_ids',[])
            receipt['unresolved_conflict_ids']=[]
        journal=dict(world=result['world'],registry=result['registry'],receipt=receipt,
            old_world_sha256=sha256(case/'state/world_context.json'),
            old_registry_sha256=sha256(case/'state/actor_registry.json'),
            input_hashes=packet['input_hashes']|{str(request):sha256(request)},
            census_certificate=certificate(packet,response,result))
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
        if (case/FACT_ROOT/'active.json').is_file():
            parent = current_facts(case)
            journal.update(fact_parent_version=parent.version_id,
                           fact_parent_manifest_sha256=sha256(parent.directory/'manifest.json'),
                           old_world_sha256=sha256(parent.world_path),
                           old_registry_sha256=sha256(parent.registry_path))
        journal['sha256']=digest(journal)
        target=case/'state/lane_layout.commit.json'
        if target.is_file():
            previous=read_json(target)
            if previous.get('status')!='committed':
                raise ContractError('Recover existing World commit before census adjudication')
            _immutable(case/ROOT/'previous_commits'/f'{sha256(target)}.json',previous)
        atomic_json(target,journal)
        runner.commit_lane_layout(case,receipt['batch_sha256'])
        candidate.unlink(missing_ok=True)
    progress = runner.progress(case)
    pending_deadline = next((row for row in reversed(progress.get('sessions', []))
        if row.get('stage') == 'world_closure'
        and row.get('error_code') in {'task_deadline_exceeded', 'task_status_unavailable'}
        and not row.get('deadline_task_resolved')), None)
    if pending_deadline:
        from _lib.runtime.orchestrator import BackendError
        try:
            remote = runner.backend.poll(pending_deadline['task_id'], 'global-video-understanding')
        except Exception as error:
            raise BackendError('task_status_unavailable',
                'World closure producer terminal state is unconfirmed') from error
        if remote.get('status') not in {'completed', 'failed'}:
            raise RuntimeError('task_deadline_exceeded')
        pending_deadline['deadline_task_resolved'] = True
        progress.setdefault('diagnostics', {}).setdefault('world_closure_deadline_reconciliations', []).append(dict(
            task_id=pending_deadline['task_id'], status=remote['status'],
            error=remote.get('error'), at=now()))
        runner.save_progress(case, progress)
        if remote['status'] == 'completed' and not candidate.exists():
            raise BackendError('missing_output',
                'Completed World closure task has not published its candidate')
    sessions=runner.progress(case).get('sessions',[])
    active=_producer_pending(sessions)
    done = committed()
    if not done:
        receipt_path = case/'state/reconciliation.json'
        if result_receipt is None and not receipt_path.is_file():
            result_receipt = pending_reconciliation(case, packet)
        receipt_base = copy.deepcopy(result_receipt if result_receipt is not None else read_json(receipt_path))
        if (not isinstance(receipt_base, dict)
                or receipt_base.get('batch_sha256') != {p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')}):
            raise ContractError('Census commit reconciliation receipt binding changed',
                                rule_id='identity.census.input_binding')
    if done and not active:
        pending = runner.progress(case).get('diagnostics', {}).get('unresolved_submissions', {}).get('world_closure')
        if pending:
            from _lib.runtime.orchestrator import BackendError
            raise BackendError('submission_unresolved', pending['message'])
        return True
    repair = packet.get('pipeline_contract_repair')
    if repair and not active and not done and not candidate.exists():
        parent = read_json(repair['parent_request_path'])
        response = read_json(repair['candidate_path'])
        if (parent['request_sha256'] != repair['parent_request_sha256']
                or sha256(repair['candidate_path']) != repair['candidate_sha256']
                or response.get('request_sha256') != parent['request_sha256']
                or not isinstance(response.get('layout'), dict)
                or response['layout'].get('request_sha256') != parent['layout_request']['request_sha256']
                or response['layout'].get('base_sha256') != parent['layout_request']['base_sha256']):
            raise ContractError('Pipeline repair candidate binding changed')
        migrated = copy.deepcopy(response)
        migrated['request_sha256'] = packet['request_sha256']
        migrated['layout']['request_sha256'] = packet['layout_request']['request_sha256']
        migrated['layout']['base_sha256'] = packet['layout_request']['base_sha256']
        compile_response(packet, migrated)
        migration = root/'candidate_revalidation.json'
        _immutable(migration, migrated)
        credited = getattr(runner, 'credit_model_reservation', lambda *args: False)(
            case, 'world_closure', repair['source_attempt'],
            'Local World reconciliation resolved a required null binding',
            repair['candidate_sha256'])
        if not credited:
            raise ContractError('Pipeline repair could not credit the rejected model attempt')
        progress = runner.progress(case)
        progress.setdefault('diagnostics', {}).setdefault('pipeline_contract_repairs', []).append(dict(
            revision=repair['revision'], rule_id=repair['rule_id'],
            parent_request_sha256=repair['parent_request_sha256'],
            repaired_request_sha256=packet['request_sha256'],
            candidate_sha256=repair['candidate_sha256'], at=now()))
        runner.save_progress(case, progress)
        adopt(source=migration)
        return True
    archive_feedback = ''
    revalidated_content = None
    if not active and not candidate.exists():
        for archive in archived_candidates(case, sessions, request_sha256=packet['request_sha256']):
            try:
                adopt(source=archive)
            except Exception as error:
                from _lib.runtime.orchestrator import validation_feedback
                from _lib.runtime.bounded_recovery import LOCAL_ERRORS
                code = runner.candidate_error_code(error, archive)
                progress = runner.progress(case)
                detail = dict(stage='world_closure', request_sha256=packet['request_sha256'],
                    candidate_sha256=sha256(archive), message=str(error), rule_id=getattr(error,'rule_id',None),
                    validation_details=getattr(error,'diagnostics',{}),
                    error_code=code, exception_type=type(error).__name__)
                failures = progress.setdefault('diagnostics',{}).setdefault('world_closure_revalidation_failures',[])
                if detail not in failures: failures.append(detail)
                runner.save_progress(case,progress)
                if code in LOCAL_ERRORS or not isinstance(error, (ValueError, ContractError)):
                    raise
                archive_feedback = validation_feedback(error)
                revalidated_content = detail
            else:
                progress = runner.progress(case)
                progress.setdefault('diagnostics', {})['world_closure_archive_adoption'] = dict(
                    candidate_path=str(archive), candidate_sha256=sha256(archive),
                    request_sha256=packet['request_sha256'])
                runner.save_progress(case, progress)
                return True
    from string import Template
    prompt=Template(packet['prompt_template']).substitute(request_path=request,candidate_path=candidate)+archive_feedback
    attempts=runner.progress(case).get('attempts',{}).get('world_closure',0)
    done = committed()
    limit = attempts+(2 if packet['revision'] in contract.MERGE_REVISIONS and attempts==0 else 1)
    if (case/ACTIVE).is_file():
        authorization = _active(case)
        if authorization['request_sha256'] == packet['request_sha256']:
            limit = authorization.get('planned_attempt', limit)
            if authorization['status'] == 'consumed':
                limit = attempts  # retry epochs never grant this round another task
    # Existing normal closure owns this call. No dedicated identity stage or
    # task is introduced; later attempts retain the shared recovery budget.
    from _lib.runtime.bounded_recovery import content_revalidation
    with content_revalidation(runner, case, 'world_closure', revalidated_content):
        runner.model_stage(case,'world_closure','global-video-understanding',prompt,adopt,
            precheck=adopt if candidate.exists() or done else None,candidate_path=candidate,
            max_attempts=attempts if done else limit)
    return True


def archived_candidates(case, sessions, *, request_sha256):
    """Hash-bound quarantine snapshots, newest first, for local revalidation."""
    from _lib.contracts.v3 import ContractError
    case=Path(case).resolve()
    for session in reversed(sessions):
        if session.get('stage')!='world_closure' or not session.get('invalid_candidate_path'):
            continue
        archive=Path(session['invalid_candidate_path']).resolve()
        if not archive.is_relative_to(case/'diagnostics/invalid_candidates') or not archive.is_file():
            raise ContractError('Archived closure candidate path binding changed',rule_id='world.closure.archive_binding')
        signature=sha256(archive)
        snapshot=case/'diagnostics/candidate_snapshots'/f'world_closure.{signature}.json'
        if signature[:16] not in archive.name.split('.') or not snapshot.is_file() or sha256(snapshot)!=signature:
            raise ContractError('Archived closure candidate hash binding changed',rule_id='world.closure.archive_binding')
        if read_json(archive).get('request_sha256')==request_sha256:
            yield archive


def archived_candidate(case, sessions, *, request_sha256):
    """Return the latest request-bound snapshot for legacy single-source callers."""
    return next(iter(archived_candidates(case, sessions,
                 request_sha256=request_sha256)), None)


def authorize_continuation(runner,case,session_id):
    case=Path(case);state=runner.progress(case)
    if (case/ACTIVE).is_file():
        active = _active(case)
        if active['session_id'] != session_id:
            raise ContractError('World continuation session choice changed')
        # Reuse a grant after a pre-dispatch crash or a durably recorded rejection,
        # even when the outer Case now reports a transport failure.
        if active['status'] == 'authorized':
            _validate_continuation(runner, case, active)
            remote = runner.backend.poll(active['prior_task_id'], 'global-video-understanding')
            if remote.get('status') != 'completed' or remote.get('session_id') not in {None, session_id}:
                raise ContractError('World closure task is not confirmed completed remotely')
            return active
        task = _recorded_task(case, state, active)
        accepted = census_path(case)
        completed_round = (accepted.is_file() and
                           read_json(accepted)['request']['request_sha256'] == active['request_sha256'])
        if (active.get('kind') == 'contract_repair' and completed_round and task
                and task.get('status') == 'completed' and not _producer_pending([task])
                and not (task.get('recovered_from_poll_failure') and not task.get('deadline_task_resolved'))):
            # Repeating the one-use repair command resumes downstream work; it
            # must not request a second repair or require a new census stop.
            saved = contract.proof(case)
            if not saved or saved['request']['request_sha256'] != active['request_sha256']:
                raise ContractError('World repair completed checkpoint changed')
            return active
        if task and (not completed_round or _producer_pending([task]) or
                     task.get('recovered_from_poll_failure') and not task.get('deadline_task_resolved')):
            # A repeated explicit command may recover this producer, but cannot
            # authorize a replacement for its consumed task.
            if any((case/name).exists() for name in ('state/behavior.json', 'state/behavior.candidate.json',
                    'state/behavior_input.request.json', 'final/analysis.json')):
                raise ContractError('World closure continuation is only before first Behavior')
            packet = read_json(active['request_path']); contract.verify_packet(packet)
            if packet['request_sha256'] != active['request_sha256']:
                raise ContractError('World continuation request changed')
            return active
    if (state.get('status')=='failed' and state.get('sessions')
            and state['sessions'][-1].get('stage')=='world_closure'
            and state['sessions'][-1].get('status')=='invalid_output'):
        return authorize_contract_repair(runner,case,session_id)
    if (state.get('status')!='needs_review' or (state.get('error') or {}).get('code')!='identity_evidence_insufficient'
            or (state.get('error') or {}).get('rule_id')!='identity.census.unresolved'):
        raise ContractError('World closure continuation requires the current census stop')
    if any((case/name).exists() for name in ('state/behavior.json', 'state/behavior.candidate.json',
            'state/behavior_input.request.json', 'final/analysis.json')):
        raise ContractError('World closure continuation is only before first Behavior')
    sessions=state.get('sessions',[])
    if (not sessions or sessions[-1].get('stage')!='world_closure'
            or sessions[-1].get('status')!='completed' or sessions[-1].get('session_id')!=session_id):
        raise ContractError('Continuation must use the latest completed World closure session')
    if state.get('diagnostics',{}).get('unresolved_submissions') or any(
            s.get('status') in {'submitted','pending','running'} or
            s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'} and not s.get('deadline_task_resolved')
            or s.get('recovered_from_poll_failure') and not s.get('deadline_task_resolved')
            for s in sessions):
        raise ContractError('Reconcile all remote tasks before World continuation')
    # Complete a durable local commit before validating live parent state.
    journal = case/'state/lane_layout.commit.json'
    if journal.is_file() and read_json(journal).get('census_certificate'):
        if not runner.commit_lane_layout(case, {p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')}):
            raise ContractError('Census continuation checkpoint is inconsistent')
    saved = contract.proof(case)
    if saved and not history.remaining_records(case, saved):
        raise ContractError('World closure continuation has no remaining census issues')
    marker=case/ACTIVE
    if marker.exists():
        active=_active(case)
        if active['session_id']!=session_id:
            raise ContractError('World continuation session choice changed')
        contract.verify_packet(read_json(active['request_path']))
        completed_round = (saved and active['request_sha256']==saved['request']['request_sha256'])
        if not completed_round:
            return active
    # Poll only the already recorded task, never submit while its terminal
    # record conflicts with current remote state.
    remote=runner.backend.poll(sessions[-1]['task_id'],'global-video-understanding')
    if remote.get('status')!='completed' or remote.get('session_id') not in {None,session_id}:
        raise ContractError('World closure task is not confirmed completed remotely')
    receipt=read_json(case/'state/reconciliation.json')
    if (receipt.get('status') not in {'reconciled_after_geometry_readjudication','reconciled'}
            or receipt.get('world')!=read_json(case/'state/world_context.json')):
        raise ContractError('World continuation requires a completed, bound reconciliation')
    from _lib.runtime.bounded_recovery import available
    if available(case, state)<=0:
        raise ContractError('model_recovery_budget_exhausted')
    parent_certificate = None
    root=case/ROOT/'continuation'
    if saved:
        raw = read_json(census_path(case))
        archive = case/history.ARCHIVE/(raw['sha256']+'.json')
        _immutable(archive, raw)
        parent_certificate = dict(path=str(archive.resolve()), sha256=sha256(archive))
        root=case/ROOT/'continuations'/raw['sha256']
    packet=prepare(case,root=root, parent_certificate=parent_certificate,
                   revision=saved['request']['revision'] if saved else None)
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    active=dict(revision=packet['revision'],request_path=str(root/'request.json'),
        request_sha256=packet['request_sha256'],session_id=session_id,
        prior_task_id=sessions[-1]['task_id'],status='authorized',
        planned_attempt=state.get('attempts',{}).get('world_closure',0)+1,
        world_sha256=sha256(view.world_path if view else case/'state/world_context.json'),
        registry_sha256=sha256(view.registry_path if view else case/'state/actor_registry.json'))
    if view:
        active['fact_version'] = view.version_id
    if parent_certificate:
        active['parent_certificate'] = parent_certificate
    if marker.is_file():
        previous = read_json(marker)
        _immutable(case/ROOT/'authorizations'/f'{previous["sha256"]}.json', previous)
    _save_active(case,active)
    return active


def submit_continuation(runner,case,key,agent,prompt):
    marker=Path(case)/ACTIVE
    if key!='world_closure' or not marker.is_file():
        return None
    active=_active(case)
    from _lib.runtime.orchestrator import BackendError, submission_not_sent, backend_error_code
    try:
        if active['status']!='authorized' or agent!='global-video-understanding':
            raise ContractError('World continuation authorization already consumed or route changed')
        _validate_continuation(runner, case, active)
    except ContractError as error:
        raise BackendError('source_changed',str(error),submission_not_sent=True) from error
    sequence = active.get('submission_sequence', 0) + 1
    active.update(status='consumed', consumed_at=now(), submission_sequence=sequence)
    active.pop('submission_rejection', None)  # Old rejection cannot unlock a later unknown send.
    _save_active(case,active)
    try:
        return runner.backend.submit(agent,prompt,session_id=active['session_id'])
    except Exception as error:
        if submission_not_sent(error):
            active.update(status='authorized', submission_rejection=dict(
                sequence=sequence, request_sha256=active['request_sha256'],
                code=backend_error_code(error), message=str(error)[:500], at=now()))
            _save_active(case,active)
        raise


def authorize_scope_recovery(runner, case, packet, record):
    """Only a v4, locally revoked Behavior scope conflict may use this branch."""
    case = Path(case); state = runner.progress(case)
    if packet['revision'] not in contract.SCOPE_REVISIONS or not packet.get('scope_recovery'):
        raise ContractError('Scope continuation requires its new frozen contract')
    contract.verify_packet(packet)
    if (case/ACTIVE).is_file():
        active = _active(case)
        if active['request_sha256'] == packet['request_sha256']:
            if active.get('kind') != 'scope_recovery':
                raise ContractError('Scope continuation authorization route changed')
            return active
    if state.get('diagnostics', {}).get('unresolved_submissions') or any(
            s.get('status') in {'submitted','pending','running'} or
            (s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
             or s.get('recovered_from_poll_failure')) and not s.get('deadline_task_resolved')
            for s in state.get('sessions', [])):
        raise ContractError('Reconcile all tasks before scope continuation')
    prior = next((s for s in reversed(state.get('sessions', []))
                  if s.get('stage') == 'world_closure' and s.get('status') == 'completed'), None)
    if prior is None:
        raise ContractError('Scope continuation needs its completed World producer')
    remote = runner.backend.poll(prior['task_id'], 'global-video-understanding')
    if remote.get('status') != 'completed' or remote.get('session_id') not in {None, prior['session_id']}:
        raise ContractError('Scope continuation World producer is not terminal')
    from _lib.runtime.bounded_recovery import available
    if available(case, state) <= 0:
        raise ContractError('model_recovery_budget_exhausted')
    active = dict(kind='scope_recovery', revision=packet['revision'],
        request_path=str(Path(packet['output_path']).parent/'request.json'),
        request_sha256=packet['request_sha256'], session_id=prior['session_id'],
        prior_task_id=prior['task_id'], status='authorized', scope_trigger_sha256=record['trigger_sha256'],
        planned_attempt=state.get('attempts', {}).get('world_closure', 0)+1,
        parent_certificate=packet['parent_certificate'],
        world_sha256=sha256(case/'state/world_context.json'),
        registry_sha256=sha256(case/'state/actor_registry.json'))
    if (case/ACTIVE).is_file():
        old = read_json(case/ACTIVE)
        _immutable(case/ROOT/'authorizations'/f"{old['sha256']}.json", old)
    _save_active(case, active)
    return active


def authorize_contract_repair(runner, case, session_id):
    """One explicit old-contract repair; retains the ordinary exhausted ledger."""
    case=Path(case).resolve();state=runner.progress(case)
    sessions=state.get('sessions',[]);last=sessions[-1] if sessions else {}
    if (state.get('status')!='failed' or last.get('stage')!='world_closure'
            or last.get('status')!='invalid_output' or last.get('session_id')!=session_id
            or last.get('error_code')!='schema_validation_failed'):
        raise ContractError('Contract repair requires the latest invalid World closure session')
    if any((case/name).exists() for name in ('state/behavior.json','state/behavior.candidate.json',
            'state/behavior_input.request.json','final/analysis.json',contract.ACCEPTED)):
        raise ContractError('Contract repair is only before first Behavior and census commit')
    if state.get('diagnostics',{}).get('unresolved_submissions') or any(
            s.get('status') in {'submitted','pending','running'} or
            s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
            and not s.get('deadline_task_resolved') for s in sessions):
        raise ContractError('Reconcile remote tasks before contract repair')
    if (case/ACTIVE).exists():
        active=_active(case)
        if active.get('kind')!='contract_repair' or active['session_id']!=session_id:
            raise ContractError('World repair authorization already belongs to another task')
        contract.verify_packet(read_json(active['request_path']))
        return active
    parent_path=case/ROOT/'initial/request.json'
    parent=read_json(parent_path);contract.verify_packet(parent)
    information_repair = parent['revision'] == contract.SCOPE_REVISION
    if parent['revision'] not in {contract.REVISION,contract.FOCUSED_REVISION,contract.SCOPE_REVISION}:
        raise ContractError('One-time contract repair requires an eligible frozen v1/v2/v4 request')
    archive=archived_candidate(case,sessions,request_sha256=parent['request_sha256'])
    if archive is None:
        raise ContractError('Contract repair requires the bound archived candidate')
    try:
        compile_response(parent,read_json(archive))
    except ContractError as error:
        # Only known v1/v2 representation gaps qualify, not arbitrary bad output.
        supported = (getattr(error,'rule_id',None)=='world.layout.decision_shape'
                     and str(error).startswith('Foreign evidence frame in ')) or (
                     getattr(error,'rule_id',None)=='identity.census.adjudication'
                     and str(error)=='Alternative identity needs candidate refutations and duplicate checks')
        if information_repair:
            from _lib.runtime.census_information_adoption import supported as supports_information_repair
            supported = supports_information_repair(error)
        if not supported:
            raise ContractError('Failure is outside the bounded census contract repair') from error
        cause=dict(rule_id=error.rule_id,message=str(error))
    else:
        raise ContractError('Archived output now validates; use deterministic retry without a new task')
    remote=runner.backend.poll(last['task_id'],'global-video-understanding')
    if remote.get('status')!='completed' or remote.get('session_id') not in {None,session_id}:
        raise ContractError('World repair task is not confirmed terminal remotely')
    if information_repair:
        from _lib.runtime.census_relevance_adoption import build
        packet = build(parent_path, archive, cause)
        _immutable(Path(packet['output_path']).with_name('request.json'), packet)
    else:
        packet=prepare(case,parent.get('layout_path'),root=case/ROOT/'contract_repair',
            revision=contract.MERGE_REVISION,lineage=dict(parent_request_path=str(parent_path),
            parent_request_sha256=parent['request_sha256'],candidate_path=str(archive),
            candidate_sha256=sha256(archive),cause=cause))
    active=dict(revision=packet['revision'],kind='contract_repair',
        request_path=str(Path(packet['output_path']).with_name('request.json')),
        request_sha256=packet['request_sha256'],session_id=session_id,prior_task_id=last['task_id'],
        status='authorized',budget_status='authorized',
        world_sha256=sha256(case/'state/world_context.json'),registry_sha256=sha256(case/'state/actor_registry.json'))
    _save_active(case,active)
    return active


def reserve_contract_repair(runner,case,key):
    if key!='world_closure' or not (Path(case)/ACTIVE).exists():
        return False
    active=_active(case)
    if active.get('kind')!='contract_repair':
        return False
    progress=runner.progress(case)
    if active['status']!='authorized':
        raise ContractError('world_contract_repair_budget_exhausted')
    packet=read_json(active['request_path']);contract.verify_packet(packet)
    if packet['request_sha256']!=active['request_sha256']:
        raise ContractError('Contract repair budget request binding changed')
    reservations=progress.setdefault('diagnostics',{}).setdefault('world_contract_repair_reservations',[])
    binding = continuation_binding(case, key)
    pending = [r for r in reservations if r.get('status') != 'abandoned_before_task_id']
    if pending:
        previous=pending[0]
        if (len(pending)==1 and not previous.get('status') and not previous.get('task_id')
                and all(previous.get(name)==value for name,value in binding.items())
                and (previous.get('world_continuation_binding') == binding or
                     progress.get('attempts',{}).get(key,0)<previous['planned_attempt'])):
            previous['world_continuation_binding'] = binding
            runner.save_progress(case, progress)
            return True  # crash after reservation, before attempt/session persistence
        raise ContractError('world_contract_repair_budget_exhausted')
    reservations.append(dict(stage=key,request_sha256=packet['request_sha256'],
        parent_request_sha256=(packet.get('relevance_adoption') or packet.get('information_adoption') or packet['recovery_lineage'])['parent_request_sha256'],
        session_id=active['session_id'],prior_task_id=active['prior_task_id'],
        planned_attempt=progress.get('attempts',{}).get(key,0)+1,at=now(),
        world_continuation_binding=binding))
    runner.save_progress(case,progress)
    return True


def resume(runner,case):
    marker=Path(case)/ACTIVE
    if not marker.exists():
        return False
    active=_active(case);packet=read_json(active['request_path'])
    run(runner,case,packet=packet)
    return True


def recover_checkpoint(runner, case):
    """Recover a census commit and its producer before advancing past Batches."""
    case = Path(case)
    from _lib.world.post_behavior import recover as recover_post_behavior
    recover_post_behavior(runner, case)
    journal = case/'state/lane_layout.commit.json'
    batch_hashes = {p.name:sha256(p) for p in (case/'batches').glob('batch_*.json')}
    census_commit = read_json(journal).get('census_certificate') if journal.is_file() else None
    if census_commit or (case/ACTIVE).exists() or census_path(case).is_file():
        specs = read_json(case/'manifest.json')['batches']
        expected = {spec['batch_id']+'.json' for spec in specs}
        if len(expected) != len(specs) or expected != set(batch_hashes):
            raise ContractError('Census checkpoint requires the complete frozen Batch chain',
                                rule_id='identity.census.input_binding')
    if census_commit:
        if not runner.commit_lane_layout(case, batch_hashes):
            raise ContractError('Census commit checkpoint is inconsistent')
    from _lib.runtime import census_scope_recovery
    scope_record = runner.progress(case).get('diagnostics', {}).get(census_scope_recovery.KEY)
    from _lib.runtime import behavior_scope_review
    scope_supplement = behavior_scope_review.active(case)
    if scope_supplement:
        behavior_scope_review.request(case)  # Verify before deferring an old request.
    from _lib.runtime import downstream_scope_review
    downstream_review = downstream_scope_review.active(case)
    if downstream_review:
        downstream_scope_review.request(case)
    from _lib.runtime import core_scene_scope
    if core_scene_scope.active(case):
        core_scene_scope.request(case)  # Validate the selected mode before deferral.
    if scope_record and scope_record['status'] in {'requested', 'submitted'} and not (
            (scope_supplement or downstream_review) and scope_record['status'] == 'requested'):
        census_scope_recovery.recover(runner, case)
    if (case/ACTIVE).exists():
        resume(runner, case)
    else:
        saved = contract.proof(case)
        if saved:
            # Initial closure has no continuation marker. Its local commit can
            # still precede the persisted remote terminal result.
            state = runner.progress(case)
            if (_producer_pending(state.get('sessions', [])) or
                    state.get('diagnostics', {}).get('unresolved_submissions', {}).get('world_closure')):
                run(runner, case, packet=saved['request'])
    saved = contract.proof(case)
    if not saved:
        return False
    state = runner.progress(case)
    # The generic task runner may accept a durable output when polling fails.
    # A valid local artifact alone cannot certify that its producer has stopped.
    for session in state.get('sessions', []):
        if (session.get('stage') == 'world_closure' and session.get('recovered_from_poll_failure')
                and not session.get('deadline_task_resolved')):
            from _lib.runtime.orchestrator import BackendError
            try:
                remote = runner.backend.poll(session['task_id'], 'global-video-understanding')
            except Exception as error:
                raise BackendError('task_status_unavailable', 'Census producer terminal state is unconfirmed') from error
            if remote.get('status') not in {'completed', 'failed'}:
                raise BackendError('task_status_unavailable', 'Census producer may still be active')
            session['deadline_task_resolved'] = True
            runner.save_progress(case, state)
    if state.get('phase') in {'planning', 'batching'}:
        # Accepted partial results remain subject to the normal pre-Behavior
        # unresolved gate. They must not trigger publication of old registries.
        state.update(phase='aggregating', status='aggregating', substage='reconciling', updated_at=now())
        runner.save_progress(case, state)
    return True
