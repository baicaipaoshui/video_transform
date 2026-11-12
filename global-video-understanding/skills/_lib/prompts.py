"""Render each worker prompt from its owning Skill; no lifecycle mutations."""
import json
from pathlib import Path
from string import Template
from .paths import SKILLS as HERE

def render(skill, name, **values):
    path = HERE / skill / "references" / name
    return Template(path.read_text(encoding="utf-8")).substitute(values)


def actor_schema_text():
    """Inline the shared field contract without expanding worker read permissions."""
    return (HERE / 'schemas/actor_fields.schema.json').read_text(encoding='utf-8').strip()


def output_fields(schema_name, *, injected=()):
    """Describe root ownership from the wire schema, including runtime-only keys."""
    schema = json.loads((HERE / 'schemas' / schema_name).read_text(encoding='utf-8'))
    required = schema['required']
    nested = {}
    if schema_name == 'behavior.schema.json':
        actor = schema['properties']['actors']['items']
        trajectory = actor['properties']['trajectory']
        nested = {
            'actors.items': {key: actor[key] for key in ('type', 'required')},
            'actors[].simulation_role': actor['properties']['simulation_role'],
            'actors[].is_static': actor['properties']['is_static'],
            'actors[].trajectory': {
                key: trajectory[key] for key in ('type', 'minItems')},
        }
        nested['actors[].trajectory']['items'] = {
            key: trajectory['items'][key] for key in ('type', 'minProperties')}
    elif schema_name == 'batch_v3.schema.json':
        observation = schema['$defs']['observation']['properties']
        conflict = schema['properties']['semantic_conflicts']['items']
        nested = {
            'observations[].status': observation['status'],
            'observations[].answer_resolution': observation['answer_resolution'],
            'semantic_conflicts.items': {
                key: conflict[key] for key in ('type', 'required', 'additionalProperties')},
        }
        nested['semantic_conflicts.items']['property_names'] = list(conflict['properties'])
    return json.dumps({
        'model_required': [key for key in required if key not in injected],
        'model_optional': [key for key in schema['properties'] if key not in required and key not in injected],
        'orchestrator_injected': list(injected),
        'nested_contracts': nested,
    }, ensure_ascii=False)


def batch_response_schema(*, layout_review=False, vehicle_census=False, ego_lane_observations=False):
    """Private worker view; the persisted Batch schema remains authoritative."""
    if layout_review:
        from .world.single_world import layout_schema
        schema = json.loads((HERE/'schemas/batch_v3.schema.json').read_text(encoding='utf-8'))
        fields = {key: schema['properties'][key] for key in
                  ('contract_version', 'case_id', 'batch_id', 'world_evidence_epoch')}
        fields.update(request_sha256={'type': 'string', 'minLength': 1},
                      layout_observation=layout_schema())
        return dict(type='object', additionalProperties=False, required=list(fields), properties=fields)
    schema = json.loads((HERE/'schemas/batch_v3.schema.json').read_text(encoding='utf-8'))
    schema['required'].remove('session_id')
    schema['properties'].pop('session_id')
    if vehicle_census:
        schema['required'].append('vehicle_census')
        schema['properties']['vehicle_census'] = json.loads(
            (HERE/'schemas/vehicle_census.schema.json').read_text(encoding='utf-8'))
    if ego_lane_observations:
        from .world.actor_facts import extend_ego_lane_observation_schema
        extend_ego_lane_observation_schema(schema)
    return schema


def _ego_lane_observation_instructions(location):
    return (f'\n在已提供的原帧中发现与核心运动有关的自车走廊观察时，在 {location} 保留单帧候选。'
            '使用 actor_id=ego、实际 frame_id 和 time_s、status 和 reason。可辨认时填写 '
            'status=observed 和 lane_id 或道路内 ordinal_from_right，说明相机走廊与道路分隔线的关系。'
            '两个值均提供时必须一致。看不清则填 status=unknown、lane_id=null 并说明原因。'
            '保留能说明走廊变化的关键帧，省略普通同车道重复观察。后续候选由定点复核裁决，'
            '不得覆盖初始车道、将自车加入车辆登记或直接裁定跨帧事件。')


def world_request_text(case):
    case = Path(case).resolve()
    from .world.single_world import enabled as single_enabled, REQUEST
    if single_enabled(case):
        from .world.gaps import enabled as observation_enabled
        from .contracts.vehicle_census import temporal_admission
        contract = render('world-understanding', 'single-world-prompt.md', request_path=case/REQUEST)
        from .runtime.case_core import read_json
        from .world.actor_facts import EGO_LANE_OBSERVATIONS, EGO_LANE_OBSERVATIONS_KEY
        if ((case/REQUEST).is_file()
                and read_json(case/REQUEST).get(EGO_LANE_OBSERVATIONS_KEY) == EGO_LANE_OBSERVATIONS):
            contract += _ego_lane_observation_instructions('field_evidence.ego_lane_observations[]')
            contract += '\n自车候选只写入这个证据数组，其他字段的原始证据照常保留。'
        from .world.actor_facts import strict_enabled as strict_actor_facts
        if strict_actor_facts(case):
            contract += ('\n新 Case 问题绑定规则：已知 Actor 的单帧 actor_lane question 必须填写唯一现有 '
                         'actor_ids、实际未决事实的 world_paths、对应证据帧和零长度 verification_window。'
                         '对象身份尚未建立时，使用 owner=world、subject_field=actor_identity 或 actor_set '
                         '的 ambiguity 保留观察及证据，先交上游复核；不要提出无 Actor 绑定的 actor_lane '
                         'question，也不要猜测身份。连续时段命题不能由若干离散单帧答案结案。')
        if temporal_admission(case):
            contract += ('\n新 Case 身份验收规则：actor_hypotheses.identity_description 只描述首次出现帧'
                         '实际可见的外观和位置，不在这个不可变字段中断言后续帧仍为同一辆车。'
                         '后续身份关联尚未逐帧核实时，用现有 actor_identity 问题和证据窗口记录待决；'
                         '不要为了完成描述提前把远端车头写成已确认的旧编号。')
        return _world_prompt(case,
            task_contract=contract,
            observation_rules=(render('world-understanding', 'observation-prompt.md')
                               if observation_enabled(case) else ''))
    return _legacy_world_request_text(case)


def _world_prompt(case, *, task_contract, observation_rules=''):
    """Shared visual rules with one selected access/output contract, no overrides."""
    from .world.topology import coordinate_convention
    return render('world-understanding', 'prompt.md', case_id=case.name,
        actor_schema=actor_schema_text(),
        coordinate_convention=json.dumps(coordinate_convention(), ensure_ascii=False),
        candidate_path=case/'state/world_context.candidate.json',
        task_contract=task_contract, observation_rules=observation_rules)


def _legacy_world_request_text(case):
    """Retain pre-v2 prompt composition for frozen legacy observation modes."""
    from .world.topology import coordinate_convention
    from .world.semantic import load_compiled
    compiled = load_compiled(case)
    if compiled is not None:
        from .runtime.case_core import read_json
        return render('world-understanding', 'world-parts-prompt.md',
            case_id=case.name, case_json=case/'case.json',
            frames_path=case/'state/global_frames.json',
            candidate_path=case/'state/world_context.candidate.json',
            parts_schema=json.dumps(read_json(case/'state/topology_answers.request.json')['world_parts_schema'], ensure_ascii=False),
            actor_schema=actor_schema_text(),
            coordinate_convention=json.dumps(coordinate_convention(), ensure_ascii=False),
            compiled=json.dumps(compiled, ensure_ascii=False))
    prompt = _world_prompt(case, task_contract=render('world-understanding',
        'legacy-world-task-prompt.md', case_json=case/'case.json',
        frames_path=case/'state/global_frames.json', schema_path=HERE/'schemas/world_context.schema.json'))
    from .world.gaps import enabled
    if enabled(case):
        prompt += '\n' + render('world-understanding', 'observation-prompt.md')
    from .world.admission import enabled as admission_enabled, required_outline
    if admission_enabled(case):
        prompt += ('\n以下必填键由本次 Schema 生成：' + required_outline() +
                   '\n本次冻结的观察边界优先于上述完整拓扑要求：只检查已声明事实自洽，'
                   '不要求出口数量、连接、路臂数量或几何达到执行完整程度。'
                   '缺失关系不等于禁止通行，未知按现有问题/证据结构保留。'
                   '不能把路口前后两个物理路臂合并为同一 bidirectional 路臂。')
    from .contracts.fidelity import enabled, inventory
    if enabled(case):
        prompt += '\nSchema 权威接口（自动派生，禁止使用其他枚举）：' + json.dumps(inventory()['contracts']['world_context.schema.json']['schema'], ensure_ascii=False)
        prompt += '\n道路数量区分可见下界与确认总数。停止线、近横道、路口入口、远横道和出口是不同物理边界。'
    return prompt


def behavior_request_text(case):
    case = Path(case).resolve()
    import json
    from .runtime.case_core import read_json
    manifest = read_json(case / 'manifest.json')
    batches = []
    for batch in manifest.get('batches', []):
        path = case / 'batches' / (batch['batch_id'] + '.json')
        if not path.is_file():
            raise ValueError(f'accepted Behavior batch input missing: {path}')
        batches.append(str(path))
    from .runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    world_path = view.world_path if view else case/'state/world_context.json'
    registry_path = view.registry_path if view else case/'state/actor_registry.json'
    from .behavior import input_binding
    timeline_instruction = ''
    census_view_path = None
    batch_instruction = '有效 Batch 文件列表 ' + json.dumps(batches, ensure_ascii=False)
    if input_binding.timeline_enabled(case):
        input_binding.current_evidence_timeline(case)
        census_view_path = input_binding.current_census_read_view(case)
        frozen = input_binding.active_request(case)
        index_path = input_binding.request_path(case, frozen)
        batch_instruction = ('按 Actor/时间整理的来源证据索引 '
                             + str(index_path) + ' 中的 evidence_timeline；完整 Batch 文件列表 '
                             + json.dumps(batches, ensure_ascii=False) + ' 仅供按 source_id 定向核查')
        timeline_instruction = (
            '\n先读 evidence_timeline.groups 的原文、来源与 accepted_claims，再核对有效 World/Registry。'
            '原文和 unresolved 的 source_binding 是历史来源主张，不自动等于当前已接纳事实；'
            '当前身份以有效 Census/Registry 为准，不得自行合并。'
            '需要核查冲突、身份或物理边界时，按 source_id 读取原 Batch 段落和原帧，不重复通读全部 Batch。'
            '对执行相关的 Actor/帧组，用现有 trajectory 点、event.evidence、semantic_limitations'
            ' 或经现有规则准许的范围排除承接；一个点可承接同帧多条来源，'
            '不得为凑覆盖增加点、事件或精确动作时间。'
            '在承接节点添加 source_observation_ids，填写组的 source_observation_id；'
            '同时在已有 detail/description 中解释采用、冲突或未决原因。'
            '仅引用 ID 不代表理解正确；车道、道路、身份及事件必须与有效事实和原帧一致。')
    prompt = render('behavior-understanding', 'prompt.md', case_id=case.name, world_path=world_path, registry_path=registry_path, schema_path=HERE / 'schemas/behavior.schema.json', actor_schema=actor_schema_text(), output_fields=output_fields('behavior.schema.json'), frames_path=case / 'state/behavior_frames.json', candidate_path=case / 'state/behavior.candidate.json', batch_instruction=batch_instruction, batch_paths=json.dumps(batches, ensure_ascii=False))
    record = case / 'case.json'
    assignments = read_json(record).get('parameters', {}).get('confirmed_lane_assignments', {}) if record.exists() else {}
    if assignments:
        prompt += '\n用户已确认的整个观察窗口车道约束（优先于模型历史标签；证据冲突必须报告，不能静默覆盖）：' + json.dumps(assignments, ensure_ascii=False)
    from .world.semantic import enabled as semantic_enabled
    if semantic_enabled(case):
        prompt += '\n' + render('behavior-understanding', 'semantic-fields-prompt.md')
    from .contracts.fidelity import prompt as fidelity_prompt
    from .runtime.identity_revision import behavior_prompt
    from .contracts.census_adjudication import behavior_prompt as census_prompt
    from .contracts.census_scope import prompt as scope_prompt
    from .contracts.execution_gaps import prompt as gaps_prompt
    from .contracts.behavior_handoff import prompt as handoff_prompt
    from .behavior.compile import evidence_frames
    from .contracts.core_lane_continuity import obligations
    motion_packet = obligations(case)
    motion = motion_packet['obligations']
    if motion:
        prompt += ('\n以下 motion-obligations.v1 是由已冻结 Batch、World 和 Registry 派生的待解释观察差异，'
                   '不是预先确认的变道。逐项核对物理边界、道路、身份和车道映射；不得把 lane_id 不同直接写成 lane_change。'
                   '物理跨界需在现有 events/trajectory 中保留两端证据；道路切换需道路证据和带 '
                   'from_road_id/to_road_id 的 road_transition 事件。'
                   '车道重映射、身份争议或证据不足，用现有 semantic_limitations 明确记录 '
                   'motion_obligation_id、source_observation_id、target_observation_id、'
                   'motion_explanation（lane_remap / identity_dispute / insufficient_evidence）、'
                   'actor_ids、impact_domains、execution_effect 和证据原因。'
                   '未知若影响执行须标 blocks_execution；仅经现有执行依赖规则许可时才可有界降级。'
                   '\n义务集 SHA256：' + motion_packet['sha256'] + '\n待解释义务：' + json.dumps([{
                       key: row[key] for key in ('obligation_id', 'actor_id', 'source_observation_id',
                                                  'target_observation_id', 'source_frame_id',
                                                  'target_frame_id', 'source_lane_id_effective',
                                                  'target_lane_id_effective', 'source_quote', 'target_quote')}
                       for row in motion], ensure_ascii=False))
    return (prompt + timeline_instruction + behavior_prompt(case) + census_prompt(case, read_view=census_view_path) + fidelity_prompt(case)
            + scope_prompt(case) + gaps_prompt(case) + handoff_prompt(case, evidence_frames(case)))


def batch_request_text_v3(case, batch_id):
    case = Path(case).resolve(); candidate = case / "batches" / f".{batch_id}.candidate.json"
    request = case / "state" / "requests" / f"{batch_id}.json"
    from _lib.runtime.case_core import read_json
    from _lib.world.single_world import LAYOUT_REVISION
    from _lib.world.actor_facts import EGO_LANE_OBSERVATIONS, EGO_LANE_OBSERVATIONS_KEY
    frozen = read_json(request) if request.is_file() else {}
    if frozen.get('mode') == LAYOUT_REVISION:
        return render('batch-lifecycle', 'independent-layout-prompt.md', case_id=case.name,
                      batch_id=batch_id, request_path=request, candidate_path=candidate)
    prompt = render('batch-lifecycle', 'worker-prompt.md', case_id=case.name, batch_id=batch_id, request_path=request, forbidden_path=case / 'state' / f'{batch_id}.json', actor_schema=actor_schema_text(), output_fields=output_fields('batch_v3.schema.json', injected=('session_id',)), candidate_path=candidate)
    if frozen.get(EGO_LANE_OBSERVATIONS_KEY) == EGO_LANE_OBSERVATIONS:
        prompt += _ego_lane_observation_instructions('observations[].evidence[]')
    return prompt


def readjudication_request_text(case, conflict_ids, next_epoch):
    case=Path(case).resolve()
    from .world.topology import coordinate_convention
    return render('world-understanding', 'readjudication-prompt.md', case_id=case.name, reconciliation_path=case / 'state/reconciliation.json', conflict_ids=conflict_ids, next_epoch=next_epoch, coordinate_convention=json.dumps(coordinate_convention(), ensure_ascii=False), candidate_path=case / 'state/world_context.readjudicated.candidate.json')
