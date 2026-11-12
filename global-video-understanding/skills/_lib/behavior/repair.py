"""Bounded, hash-bound Behavior replacements; never infer scene facts."""
import copy
import json
import re
from pathlib import Path

from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.contracts.v3 import ContractError
from _lib.behavior.preview_contract import SCOPE, SCHEMA, output_rule, validate_envelope, review_outcome, PreviewStopped, contract_error


SCOPE_COMPLETION = 'behavior-scope-completion.v1'


def scope_completion_request(case):
    """Resume the frozen review-only request without rebinding changed inputs."""
    from _lib.behavior.input_binding import InputChanged
    from _lib.contracts import census_scope
    from _lib.contracts.conversion_policy import digest
    path = Path(case)/'state/behavior_repair.request.json'
    if not path.is_file():
        return None
    packet = read_json(path)
    body = dict(packet); signature = body.pop('request_sha256', None)
    if (packet.get('repair_contract') == SCOPE_COMPLETION or 'request_sha256' in packet) and signature != digest(body):
        raise InputChanged('Scope completion request changed')
    if packet.get('repair_contract') != SCOPE_COMPLETION:
        return None
    case = Path(case).resolve()
    if (Path(packet['output_path']).resolve() != case/'state/behavior.candidate.json'
            or not Path(packet['base_path']).resolve().is_relative_to(case/'diagnostics')):
        raise InputChanged('Scope completion paths belong to another request')
    for name, expected in packet['input_hashes'].items():
        if not Path(name).is_file() or sha256(name) != expected:
            raise InputChanged('Scope completion input changed: ' + name)
    if not Path(packet['base_path']).is_file() or sha256(packet['base_path']) != packet['base_sha256']:
        raise InputChanged('Scope completion Behavior baseline changed')
    target = case/'state/behavior.json'
    if target.is_file() and read_json(target) != read_json(packet['base_path']):
        raise InputChanged('Scope completion found a different Behavior checkpoint')
    census_scope.verify_request(case, packet)
    return packet


def scope_completion_prompt(case, packet):
    from _lib.contracts import census_scope
    return ('Skill: behavior-understanding\n'
        '本请求为 behavior-scope-completion.v1，仅补答范围复查。只读任务包 '
        + str(Path(case)/'state/behavior_repair.request.json')
        + '、其中 base_path 的完整冻结 Behavior 和 frames 全部图像。'
        '只输出 {base_sha256,census_scope_review}；base_sha256 逐字复制任务包。'
        '针对该完整基线逐项复查全部 scope_context.observations，不继承旧复查。'
        '禁止输出 replacements 或完整 Behavior，禁止修改已有事实、World、Registry、凭证或请求。'
        'Python 将补答合入冻结基线；无法确认必须回答 unknown，不得为放行改成 confirmed。'
        '仅写一次 ' + packet['output_path'] + ' 后停止，不创建 subagent。'
        + census_scope.prompt(case, frozen_context=packet['scope_context'], frames=packet['frames']))


def _prepare_scope_completion(case, candidate, error):
    from _lib.contracts import census_scope
    from _lib.contracts.conversion_policy import digest
    from _lib.behavior import input_binding
    case = Path(case)
    data = error.completion_behavior
    base = case/'diagnostics/candidate_snapshots'/f'behavior.scope_completion.{digest(data)}.json'
    if base.is_file() and read_json(base) != data:
        raise input_binding.InputChanged('Scope completion baseline snapshot changed')
    atomic_json(base, data)
    paths = [case/name for name in ('case.json', 'state/world_context.json',
        'state/actor_registry.json', 'state/behavior_frames.json', 'state/reconciliation.json')]
    paths.extend(sorted((case/'batches').glob('batch_*.json')))
    packet = dict(repair_contract=SCOPE_COMPLETION, base_path=str(base), base_sha256=sha256(base),
        output_path=str(candidate), validation_error=str(error),
        input_hashes={str(p):sha256(p) for p in paths if p.is_file()},
        output_contract=dict(base_sha256=sha256(base), census_scope_review='Complete explicit review of every active issue'))
    bound = input_binding.verify(case)
    if bound:
        packet['input_hashes'].update(bound['input_hashes'])
        packet['input_hashes'][str(case/input_binding.REQUEST)] = sha256(case/input_binding.REQUEST)
    census_scope.bind_request(case, packet)
    packet['request_sha256'] = digest(packet)
    atomic_json(case/'state/behavior_repair.request.json', packet)
    return scope_completion_prompt(case, packet)


def finish_scope_completion(case):
    """Retire only after both Behavior and its ordinary scope proof are committed."""
    packet = scope_completion_request(case)
    if packet is None:
        return
    from _lib.contracts import census_scope
    case = Path(case)
    data = read_json(case/'state/behavior.json')
    if data != read_json(packet['base_path']):
        from _lib.behavior.input_binding import InputChanged
        raise InputChanged('Scope completion changed frozen Behavior')
    census_scope.verified_review(case, data)
    path = case/'state/behavior_repair.request.json'
    archive = case/'diagnostics'/f"behavior_scope_completion.{packet['request_sha256']}.json"
    atomic_json(archive, packet)
    path.unlink()


def write_review_request(case, candidate_path, reason):
    """A durable, case-local evidence packet for semantic adjudication."""
    case=Path(case)
    paths=[case/'state/world_context.json',case/'state/actor_registry.json',case/'state/behavior_frames.json']
    paths.extend(sorted((case/'batches').glob('batch_*.json')))
    if candidate_path: paths.append(Path(candidate_path))
    inputs=[{'path':str(path),'sha256':sha256(path)} for path in paths if path.is_file()]
    limitations=[]
    if candidate_path and Path(candidate_path).is_file():
        try: limitations=read_json(candidate_path).get('semantic_limitations',[])
        except (OSError,ValueError,AttributeError): pass
    request=case/'state/behavior_review.request.json'
    atomic_json(request,{'reason':reason,'inputs':inputs,'limitations':limitations,
        'required_decision_fields':['limitation_id','affected_actor_ids','affected_event_ids',
                                    'evidence','uncertainty_bounds','execution_effect','resolution'],
        'rules':['Preserve original limitations and evidence.',
                 'Do not equate imprecision with event ambiguity.',
                 'World topology must be adjudicated by World owner.',
                 'Unresolved core facts cannot be relabelled as non-core.',
                 'This request is not an approval or an executable checkpoint.']})
    return str(request)


def _source_coverage_prompt(case, candidate, request, packet):
    from _lib.prompts import render
    from _lib.contracts import census_scope
    reading = ('只读任务包、base_path 完整基线及 frames 全部图像；完整基线仅供范围复查，补丁写权限不扩大。'
               if 'scope_context' in packet else
               '只读其中focused_context和指定帧证据；base_path仅供代码哈希合并，不读取完整base。'
               if packet.get('focused_context') else '读取其中base_path。')
    text = render('behavior-understanding', 'repair-prompt.md', request_path=request,
                  reading_rule=reading, candidate_path=candidate)
    text += ('\n只读任务包中的 evidence_timeline_path、指定来源段落和必要原帧；'
             '普通 replacements 仅补 allowed_paths 中的来源承接。')
    if packet.get('source_revision_authorization'):
        text += ('source_revision_authorization 另授权撤出清单中的缺证 Ego 点：'
                 '可提交 source_dispositions=[{point_index,action:"withdraw",reason}]，此时 replacements 必须为空。'
                 '逐点说明缺证理由，不猜车道、位置或运动；Python 只执行已授权撤出、生成未决记录并收窄 Ego 摘要。'
                 '其余轨迹、其他 Actor 来源、变道端点、事件和动作均锁定。')
    return text + (census_scope.prompt(case, frozen_context=packet['scope_context'])
                   if 'scope_context' in packet else '')


def repair_prompt(case, candidate, archived, error, *, scope_review=True):
    """Keep the accepted portions on disk instead of regenerating a document."""
    from _lib.behavior import bounded_repair
    focused = bounded_repair.prepare(case, candidate, error)
    if focused:
        return focused
    request = Path(case)/'state/behavior_repair.request.json'
    if request.is_file() and getattr(error, 'rule_id', None) == 'core_scene.source_coverage':
        packet = read_json(request)
        if packet.get('repair_contract') == 'behavior-source-coverage.v1':
            from _lib.contracts import census_scope
            if (packet.get('base_path') != str(archived)
                    or packet.get('output_path') != str(candidate)
                    or packet.get('actor_id') != getattr(error, 'actor_id', None)
                    or sha256(archived) != packet.get('base_sha256')
                    or any(not Path(path).is_file() or sha256(path) != expected
                           for path, expected in packet.get('input_hashes', {}).items())):
                raise ContractError('Frozen Behavior source repair request changed')
            census_scope.verify_request(case, packet)
            return _source_coverage_prompt(case, candidate, request, packet)
    if Path(candidate).is_file():
        return None  # Deterministic accepted-fact projection; adopt on resume.
    packet = bounded_repair.request(case)
    if packet and packet.get('mode') == 'full_evidence_review':
        return None  # A frozen evidence review cannot fall back to a broader patch.
    from _lib.contracts.fidelity import ContractCompatibilityError, FidelityEvidenceError
    if isinstance(error, ContractCompatibilityError):
        raise error
    if scope_review:
        packet = scope_completion_request(case)
        if packet is not None:
            return scope_completion_prompt(case, packet)
        if (getattr(error, 'code', None) == 'behavior_scope_review_incomplete'
                and hasattr(error, 'completion_behavior')):
            return _prepare_scope_completion(case, candidate, error)
    if (Path(case)/'state/preview_review.request.json').exists():
        return None
    try: base=read_json(archived)
    except (OSError,json.JSONDecodeError): return None
    if not isinstance(base,dict) or 'contract_version' not in base:
        return None
    if request.is_file() and 'lane_review_actor_index' in read_json(request):
        # A failed scoped review must never fall back to an unrestricted repair.
        return None
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    case_path = Path(case)
    view = current_facts(case) if (case_path/FACT_ROOT/'active.json').is_file() else None
    payload={'base_path':str(archived),'base_sha256':sha256(archived),
             'validation_error':str(error), 'output_path':str(candidate),
             'world_path':str(view.world_path if view else case_path/'state/world_context.json'),
             'registry_path':str(view.registry_path if view else case_path/'state/actor_registry.json'),
             'output_contract':{'base_sha256':'copy exact hash',
                 'replacements':[{'path':'/actors/0/trajectory','value':'replacement JSON value'}]}}
    # New execution errors name the actor/event. Include only those entities;
    # the large immutable base stays on disk for the deterministic merger.
    message = str(error)
    actor_id = getattr(error, 'actor_id', None)
    rule_id = getattr(error, 'rule_id', None)
    if rule_id:
        payload['rule_id'] = rule_id
    if actor_id:
        payload['actor_id'] = actor_id
    # Structured errors never route by human wording. Retain the legacy
    # parser for validators that have not yet acquired an actual consumer.
    named = {actor_id} if actor_id else set(re.findall(r'(?:actor|event)\s+([A-Za-z0-9_-]+)', message))
    challenge_ids = set(re.findall(r'(?:ambiguity|question)_[0-9]+', message))
    focused = {}
    for collection, id_field in (('actors','actor_id'), ('events','event_id'), ('executor_primitives','primitive_id')):
        for index, item in enumerate(base.get(collection, [])):
            if item.get(id_field) in named or item.get('actor_id') in named or item.get('event_id') in named:
                focused[f'/{collection}/{index}'] = item
    allowed_paths=[]
    for index, item in enumerate(base.get('challenge_resolutions', [])):
        if item.get('challenge_id') in challenge_ids:
            focused[f'/challenge_resolutions/{index}'] = item
            allowed_paths.extend(f'/challenge_resolutions/{index}/{field}' for field in (
                'resolution','execution_effect','decision','rationale','consistency','evidence_frame_ids'))
    if allowed_paths:
        payload['allowed_paths']=allowed_paths
    if focused:
        payload['focused_context'] = focused
        payload['frame_index_path'] = str(Path(case)/'state/behavior_frames.json')
    payload['input_hashes']={str(path):sha256(path) for path in (
        Path(case)/'state/world_context.json',Path(case)/'state/actor_registry.json',
        Path(case)/'state/behavior_frames.json') if path.is_file()}
    if rule_id == 'core_scene.source_coverage':
        from _lib.behavior import input_binding
        binding = input_binding.active_request(case)
        if (not input_binding.timeline_enabled(case) or binding is None or not actor_id
                or not any(actor.get('actor_id') == actor_id
                           for actor in base.get('actors', []))):
            return None
        index_path = input_binding.request_path(case, binding)
        payload['repair_contract'] = 'behavior-source-coverage.v1'
        payload['evidence_timeline_path'] = str(index_path)
        payload['input_hashes'].update(binding['input_hashes'])
        payload['input_hashes'][str(index_path)] = sha256(index_path)
        paths = []
        for i, actor in enumerate(base.get('actors', [])):
            if actor.get('actor_id') != actor_id:
                continue
            for j, _ in enumerate(actor.get('trajectory', [])):
                paths.extend((f'/actors/{i}/trajectory/{j}/source_observation_ids',
                              f'/actors/{i}/trajectory/{j}/detail'))
        for i, event in enumerate(base.get('events', [])):
            if actor_id in event.get('involved_actor_ids', []):
                paths.append(f'/events/{i}/source_observation_ids')
                paths.extend(f'/events/{i}/evidence/{j}/source_observation_ids'
                             for j, item in enumerate(event.get('evidence', []))
                             if isinstance(item, dict))
        paths.extend(f'/semantic_limitations/{i}/source_observation_ids'
                     for i, item in enumerate(base.get('semantic_limitations', []))
                     if actor_id in item.get('actor_ids', []))
        if not paths:
            return None
        paths.append('/semantic_limitations')  # Only append exact unresolved entries.
        payload['allowed_paths'] = paths
        payload['rules'] = [
            'Only carry the assigned Actor/frame source group into an existing point, event evidence or precise unresolved limitation.',
            'A point citation also needs a source-based detail; citations alone do not prove a physical motion conclusion.',
            'Do not add trajectory points, events, lanes, times, identities or execution claims.',
            'To add an unresolved limitation, replace semantic_limitations with the old list verbatim plus at most four new entries.',
            'Read the evidence_timeline in the frozen Behavior request first; inspect only relevant original Batch sections and frames.']
        payload['output_contract']['replacements'] = [
            {'path': paths[0], 'value': 'source_observation_ids or source-based detail'}]
        from _lib.behavior import source_revision
        authority = source_revision.authorization(base, binding['evidence_timeline'], actor_id)
        if authority:
            payload['source_revision_authorization'] = authority
            payload['output_contract']['source_dispositions'] = [dict(
                point_index='authorized original index', action='withdraw', reason='source-based rationale')]
            payload['rules'].append('With source_dispositions, replacements must be empty; '
                'only authorized points are withdrawn, with an unresolved limitation and a neutral Ego summary. '
                'No other Actor, retained point, event or primitive may change.')
    if isinstance(error, FidelityEvidenceError):
        from _lib.contracts.fidelity import SCHEMAS, enabled
        if not enabled(case):
            return None
        authority = SCHEMAS/'video_fidelity.schema.json'
        document = read_json(authority)
        paths = error.diagnostics['repair_paths']
        value_schemas = {}
        for path in paths:
            if path != '/video_fidelity' and not path.startswith('/video_fidelity/'):
                raise ContractCompatibilityError('fidelity.repair_paths', 'video_fidelity subtree', path)
            node = document
            for part in path.split('/')[2:]:
                node = node['items'] if node.get('type') == 'array' else node['properties'][part]
            value_schemas[path] = node
        payload.update(repair_contract='video-fidelity-repair.v1', allowed_paths=paths,
            value_schemas=value_schemas, authority_path=str(authority), authority_sha256=sha256(authority),
            focused_context={'/video_fidelity':base.get('video_fidelity')},
            frame_index_path=str(Path(case)/'state/behavior_frames.json'),
            rules=['Only replace the assigned paths in allowed_paths; this scope permits an assigned observation collection or missing video_fidelity subtree.',
                   'Schemas in value_schemas are derived from the authoritative schema. Do not change revision or source facts to hide a conflict.',
                   'Preserve every unassigned field and all actor, event and primitive identities. Use only existing actor/lane/frame IDs and supplied evidence.',
                   'ahead_actor_ids always means vehicles ahead of ego, not ahead of the row actor. Unknown evidence remains unknown.'])
        payload['input_hashes'][str(authority)] = sha256(authority)
        payload['output_contract']['replacements'] = [{'path':paths[0], 'value':'evidence-supported value matching value_schemas'}]
        focused = payload['focused_context']
    from _lib.contracts import census_scope
    from _lib.contracts import behavior_handoff
    if (behavior_handoff.enabled(case) and rule_id in {'scope.single_frame_relevance',
            'scope.transient_evidence', 'behavior.handoff.identity_dependencies'}):
        from _lib.paths import SKILLS
        authority = SKILLS/'schemas/behavior.schema.json'
        schema = read_json(authority)['properties']
        paths = {}
        issue_ids = {row['issue_id'] for row in getattr(error, 'diagnostics', {}).get('issues', [])}
        for index, actor in enumerate(base.get('actors', [])):
            if actor['actor_id'] == actor_id:
                paths[f'/actors/{index}/execution_scope_evidence'] = schema['actors']['items']['properties']['execution_scope_evidence']
        for index, item in enumerate(base.get('semantic_limitations', [])):
            if item['limitation_id'] in issue_ids:
                for field in ('census_issue_ids', 'scope_binding'):
                    paths[f'/semantic_limitations/{index}/{field}'] = schema['semantic_limitations']['items']['properties'][field]
        if not paths:
            return None
        payload.update(repair_contract='behavior-handoff-repair.v1', allowed_paths=list(paths),
            value_schemas=paths, frame_index_path=str(Path(case)/'state/behavior_frames.json'),
            rules=['Only add or replace assigned evidence bindings; keep identities, trajectories, roles, limitations and impact domains unchanged.',
                   'Bindings require full frame evidence; unresolved physical identity requires formal adjudication and cannot be hidden.'])
        payload['input_hashes'][str(authority)] = sha256(authority)
        payload.pop('focused_context', None)
        focused = {}
    if scope_review:
        census_scope.bind_request(case, payload)
    from _lib.runtime.bounded_recovery import enabled as bounded_enabled, RecoveryStopped
    if (bounded_enabled(case) and scope_review and payload.get('repair_contract') not in {
            'video-fidelity-repair.v1', 'behavior-handoff-repair.v1',
            'behavior-source-coverage.v1'}):
        raise RecoveryStopped('bounded_recovery_requires_scoped_repair',
            primary_failure=dict(rule_id=rule_id, actor_id=actor_id, message=str(error)))
    if 'scope_context' in payload:
        payload['output_contract']['census_scope_review'] = {
            'certificate_sha256':payload['scope_context']['certificate_sha256'],
            'decisions':'Every active issue, using the scope review fields in the prompt'}
    if payload.get('source_revision_authorization'):
        from _lib.contracts.conversion_policy import digest
        payload['request_sha256'] = digest(payload)
    atomic_json(request,payload)
    if payload.get('repair_contract') == 'behavior-source-coverage.v1':
        return _source_coverage_prompt(case, candidate, request, payload)
    reading = ('只读其中focused_context和指定帧证据；base_path仅供代码哈希合并，不读取完整base。'
               if focused else '读取其中base_path。')
    if 'scope_context' in payload:
        reading = '只读任务包、base_path 完整基线及 frames 全部图像；完整基线仅供范围复查，补丁写权限不扩大。'
    from _lib.prompts import render
    text = render('behavior-understanding', 'repair-prompt.md', request_path=request, reading_rule=reading, candidate_path=candidate)
    if payload.get('repair_contract') == 'behavior-handoff-repair.v1':
        from _lib.behavior.compile import evidence_frames
        text += ('\n本请求允许在 assigned allowed_paths 中补充范围证据字段；读取完整 base 和 frame_index_path 中原帧核对，'
                 '只补证据绑定，不改事实或局限域。' + behavior_handoff.prompt(case, evidence_frames(case)))
    return text + (census_scope.prompt(case, frozen_context=payload['scope_context'])
                   if 'scope_context' in payload else '')


def lane_repair_prompt(case, candidate, archived, actor_id, *, scope='lane_only'):
    """A single-actor image review using the existing replacement protocol."""
    from _lib.contracts.v3 import ContractError
    from _lib.behavior.compile import evidence_frames
    from _lib.prompts import render
    from _lib.contracts import census_scope
    case = Path(case)
    path = case/'state/behavior_repair.request.json'
    def render_request(packet):
        reading = '只读请求包与review_frames列出的原图；不读base、代码、Schema或其他图片。以rules及限定字段为准。'
        if 'scope_context' in packet:
            reading = '只读请求包、base_path完整基线及frames全部图像；完整基线仅用于范围复查，不扩大allowed_paths。'
        text = render('behavior-understanding', 'repair-prompt.md', request_path=path,
                      reading_rule=reading, candidate_path=candidate)
        return text + (census_scope.prompt(case, frozen_context=packet['scope_context'])
                       if 'scope_context' in packet else '')
    if path.is_file() and 'lane_review_actor_index' in read_json(path):
        existing = read_json(path)
        if (existing.get('actor_id') != actor_id or existing.get('base_path') != str(archived)
                or existing.get('output_path') != str(candidate)):
            raise ValueError('Existing lane request must be reconciled before replacement')
        # Frozen old requests retain their scope, payload and hashes verbatim.
        return render_request(existing)
    if scope not in {'lane_only', 'lane_and_position'}:
        raise ValueError('unknown lane repair scope')
    repair_prompt(case, candidate, archived, ContractError(
        'Review lane assignment against original frames; upstream assignments are hypotheses.',
        rule_id='motion.lane_visual_review', actor_id=actor_id), scope_review=False)
    path = case/'state/behavior_repair.request.json'
    packet = read_json(path)
    base = read_json(archived)
    index = next(i for i, a in enumerate(base['actors']) if a['actor_id'] == actor_id)
    packet['lane_review_actor_index'] = index
    current = case/'state/behavior.json'
    if current.is_file(): packet['input_hashes'][str(current)] = sha256(current)
    packet['focused_context'] = {'/actors/' + str(i): a for i, a in enumerate(base['actors'])
                                if a.get('simulation_role') == 'participant'}
    packet['world_lane_definitions'] = read_json(case/'state/world_context.json')['world']['road']
    frames = {f['frame_id']: f for f in evidence_frames(case)}
    packet['review_frames'] = []
    for point in base['actors'][index]['trajectory']:
        frame = frames.get(point.get('frame_id'))
        if frame is None or frame['time_s'] != point.get('time_s', point.get('timestamp_s')):
            raise ValueError('lane review requires matching source frame/time for each point')
        source = Path(frame.get('source_path') or frame['path'])
        if not source.resolve().is_relative_to(case.resolve()) or not source.is_file():
            raise ValueError('lane review source image missing or outside Case')
        packet['review_frames'].append({'frame_id':frame['frame_id'], 'time_s':frame['time_s'],
                                       'path':str(source), 'sha256':sha256(source)})
        packet['input_hashes'][str(source)] = sha256(source)
    prefix = f'/actors/{index}/trajectory'
    location_fields = [field for field in ('road_id', 'ordinal_from_right')
                       if any(field in point for point in base['actors'][index]['trajectory'])]
    packet['lane_location_fields'] = location_fields
    packet['allowed_paths'] = [prefix] + [f'{prefix}/{i}/{field}'
        for i in range(len(base['actors'][index]['trajectory']))
        for field in (*location_fields, 'lane_id', 'position_ego_m', 'detail')]
    packet['output_contract'] = {'base_sha256':packet['base_sha256'], 'replacements':[
        {'path':f'{prefix}/0/lane_id', 'value':'evidence-supported existing lane ID, or null'},
        {'path':f'{prefix}/0/position_ego_m', 'value':
            'copy original x/z exactly; estimated y with status=estimated, confidence and reason'},
        {'path':f'{prefix}/0/detail', 'value':'actual frame-bound lane evidence or uncertainty reason'}]}

    packet['rules'] = [
        'Review ego, the assigned actor, and other participant vehicles against the listed original images only.',
        'Use median, lane boundary markings and vehicle footprint; upstream lane prose is a hypothesis, not evidence.',
        'First identify the road and its travel direction, outer-right and median-side boundaries. Count road-local ordinal_from_right from 0, then map through world_lane_definitions lane_ids/anchors to the existing global lane_id. Other roads do not restart at lane_001; partial lane lists require explicit anchors.',
        'For ego and each key actor, trace both road markings from the near field to the tire/footprint region. Detail must state left boundary, right boundary, ordinal from right, and lane_id. Screen position and vanishing point are not lane-number evidence.',
        'If an actor has the median-side solid boundary on its left and a dashed divider leading to another drivable lane on its right, it cannot be lane_001. Ego and a direct lead vehicle enclosed by the same boundary pair must share lane_id.',
        'No expected lane answer is provided. Do not use vehicle class or color as a lane rule.',
        'Prefer separate lane_id / position_ego_m / detail replacements (at most 12 for four points); do not reproduce speed objects. A whole-trajectory patch is accepted only if every other field is identical.',
        'Preserve trajectory count/order, frame/time, x/z and all identities. Copy speed_mps INCLUDING reason/status/confidence verbatim; put corrected lane reasoning ONLY in detail.',
        'Inside that trajectory change ONLY road_id, ordinal_from_right, lane_id, position_ego_m.y/status/confidence/reason, and detail. Keep declared road/ordinal consistent with each corrected lane; unknown stays null.',
        'Record the actual frame and marking evidence in detail. Uncertain lane stays null with a reason.',
        'Metric y is an estimate, not a measurement. Retain estimate status/confidence/reason; do not force lane-centre y.',
        'If ego or another immutable actor assignment is uncertain or contradictory, set the reviewed lane to null and explain.',
        'If no lateral correction is supported, preserve its value; do not change values merely to pass a check.',
        'Never change World, events, primitives, identities or any unassigned actor. Write only the patch candidate once.'
    ]
    if scope == 'lane_only':
        packet['scope'] = 'lane_only'
        packet['allowed_paths'] = [f'{prefix}/{i}/{field}'
            for i in range(len(base['actors'][index]['trajectory'])) for field in (*location_fields, 'lane_id', 'detail')]
        packet['output_contract'] = {'base_sha256':packet['base_sha256'], 'replacements':[
            {'path':f'{prefix}/0/lane_id', 'value':'existing lane ID supported by this frame, or null'},
            {'path':f'{prefix}/0/detail', 'value':'include exact frame_id and lane-marking evidence or uncertainty reason'}]}
        packet['rules'] = [
            'Review only the assigned actor lane at the listed frame/time; use ego and other actors as context.',
            'Use median, dividing markings and vehicle footprint; upstream lane descriptions are hypotheses.',
            'Identify the road and its travel direction first, then count ordinal_from_right from 0 at the outer-right boundary. Resolve the global lane_id through world_lane_definitions lane_ids/anchors; never restart another road at lane_001.',
            'Trace each boundary from the near field to the actor footprint. Detail must state left boundary, right boundary, ordinal from right and lane_id; screen position is not evidence.',
            'A vehicle with the median-side solid boundary on its left and a dashed divider to another drivable lane on its right cannot have ordinal_from_right=0. Vehicles on the same road enclosed by the same boundary pair must share lane_id.',
            'Return ONLY assigned road_id, ordinal_from_right, lane_id and detail leaf replacements, at most 16 operations. Keep spatial references consistent; unknown stays null.',
            'For every lane_id replacement include a new detail replacement for that SAME point containing its exact frame_id and actual marking evidence (or uncertainty reason).',
            'Unknown lane stays null. Do not infer a lane from vehicle class/color.',
            'Do NOT return a trajectory, position, speed object or speed reason, even unchanged.',
            'The program withdraws old lateral estimates after changed lane assignments; do not estimate y or fill confidence.',
            'Preserve World, other actors, identity, time and events. Only write the requested patch once.'
        ]
    from _lib.contracts.execution_gaps import enabled as gap_recovery, REVISION as gap_revision
    if gap_recovery(case):
        from _lib.contracts.lane_resolution import obligations
        from _lib.behavior.input_binding import closure_request
        from _lib.world.actor_facts import strict_enabled
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
        binding = closure_request(case)
        strict = strict_enabled(case)
        authorization = (binding.get('question_authorization', []) if binding else []
                         ) if strict else (binding.get('question_authorization') if binding else None)
        world = current_facts(case).world if (authorization is not None and
            (case/FACT_ROOT/'active.json').is_file()) else read_json(
            case/'state/world_context.json')
        packet['lane_closure_revision'] = gap_revision
        packet['lane_closure_obligations'] = (obligations(world, base, {actor_id}, authorization)
                                              if binding or not strict else [])
        if authorization is not None:
            packet['question_authorization'] = authorization
        if binding:
            packet['behavior_request_sha256'] = binding['request_sha256']
            packet['fact_version_id'] = binding.get('fact_version_id')
        packet['output_contract']['lane_closures'] = [{'collection':'copy obligation collection',
            'issue_id':'copy obligation issue_id', 'actor_id':actor_id,
            'outcome':'confirmed|unknown', 'evidence_frame_ids':['all reviewed actor trajectory frame IDs'],
            'reason':'frame-bound lane evidence; no claim about identity or motion'}]
        packet['rules'].append('Optionally answer lane_closure_obligations in lane_closures. '
            'Only confirm after all trajectory lanes are known with per-frame boundary evidence. '
            'Do not edit limitations or World directly; Python closes only the lane component, '
            'preserving other uncertainty and the original description. Unknown is a valid result.')
        for name in ('case.json', 'state/execution_policy.json'):
            packet['input_hashes'][str(case/name)] = sha256(case/name)
    census_scope.bind_request(case, packet)
    packet['scope_review_sha256'] = sha256(case/census_scope.REVIEW) if (case/census_scope.REVIEW).is_file() else None
    if 'scope_context' in packet:
        packet['output_contract']['census_scope_review'] = {
            'certificate_sha256':packet['scope_context']['certificate_sha256'],
            'decisions':'Every active issue using the scope review fields in the prompt'}
        packet['rules'].append('Scope review also reads base_path and all frames. Review the patched Behavior; '
                               'only the private census_scope_review field is added to the response. '
                               'The original allowed_paths and lane-only write restrictions still apply.')
    atomic_json(path, packet)
    return render_request(packet)


def _check_lane_patch(original, result, request):
    index = request['lane_review_actor_index']
    old, new = original['actors'][index]['trajectory'], result['actors'][index]['trajectory']
    if not isinstance(new, list) or len(old) != len(new):
        raise ValueError('lane repair cannot change trajectory length')
    if request.get('scope') == 'lane_only':
        known_lanes = {l['lane_id'] for l in request['world_lane_definitions']['lanes']}
        outside = copy.deepcopy(result)
        for i, (before, point) in enumerate(zip(old, new)):
            if not isinstance(point, dict): raise ValueError('lane repair point must be an object')
            frozen = request['review_frames'][i]
            if (point.get('frame_id') != frozen['frame_id'] or
                    point.get('time_s', point.get('timestamp_s')) != frozen['time_s']):
                raise ValueError('lane repair frame/time differs from frozen evidence')
            lane = point.get('lane_id')
            if lane is not None and lane not in known_lanes:
                raise ValueError('lane repair references unknown World lane')
            restored = outside['actors'][index]['trajectory'][i]
            for key in (*request.get('lane_location_fields', []), 'lane_id', 'detail'):
                if key in before: restored[key] = copy.deepcopy(before[key])
                else: restored.pop(key, None)
        if outside != original: raise ValueError('lane repair changed unassigned fields')
        return
    def immutable(point):
        point = copy.deepcopy(point)
        point.pop('lane_id', None); point.pop('detail', None)
        for field in request.get('lane_location_fields', []):
            point.pop(field, None)
        pos = point.get('position_ego_m', {})
        if not isinstance(pos, dict): raise ValueError('lane repair position must be an object')
        for key in ('y', 'status', 'confidence', 'reason'): pos.pop(key, None)
        return point
    if any(not isinstance(p, dict) for p in new) or [immutable(p) for p in old] != [immutable(p) for p in new]:
        raise ValueError('lane repair changed unassigned trajectory fields')
    for before, point in zip(old, new):
        pos = point.get('position_ego_m', {})
        if pos.get('y') != before.get('position_ego_m', {}).get('y'):
            confidence = pos.get('confidence')
            if (pos.get('status') != 'estimated' or type(confidence) not in (int, float) or
                    not 0 <= confidence <= 1 or not isinstance(pos.get('reason'), str) or not pos['reason'].strip()):
                raise ValueError('lane repair y requires estimated status, confidence and reason')
        if not isinstance(point.get('detail'), str) or not point['detail'].strip():
            raise ValueError('lane repair requires frame-grounded detail or uncertainty reason')
    outside = copy.deepcopy(result)
    outside['actors'][index]['trajectory'] = copy.deepcopy(old)
    if outside != original:
        raise ValueError('lane repair changed unassigned fields')


def materialize(case, data):
    from _lib.behavior import bounded_repair
    from _lib.behavior import source_revision
    if isinstance(data, dict) and 'replacements' not in data and source_revision.reserved(data):
        raise ValueError('Source revision records must originate from the assigned patch')
    if bounded_repair.request(case) is not None:
        # The enclosing Behavior producer already materialized the private patch.
        return data
    completion = scope_completion_request(case)
    if completion is not None:
        # The caller has split off census_scope_review. Checkpoints are not
        # model responses; interrupted submissions replay the saved envelope.
        if (not isinstance(data, dict) or set(data) != {'base_sha256'}
                or data['base_sha256'] != completion['base_sha256']):
            raise ContractError('Scope completion only accepts base_sha256 and census_scope_review')
        return read_json(completion['base_path'])
    if isinstance(data, dict) and 'lane_closures' in data and 'replacements' not in data:
        from _lib.contracts.execution_gaps import enabled as gap_recovery
        from _lib.contracts.lane_resolution import apply
        if not gap_recovery(case):
            raise ValueError('Lane closure is not enabled by the frozen policy')
        value = copy.deepcopy(data); closures = value.pop('lane_closures')
        from _lib.behavior.input_binding import closure_request
        from _lib.world.actor_facts import strict_enabled
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
        binding = closure_request(case)
        if strict_enabled(case) and closures and (binding is None or
                'question_authorization' not in binding):
            raise ContractError('model_out_of_scope: lane closure lacks frozen Behavior authority',
                                rule_id='model_out_of_scope')
        authorization = binding.get('question_authorization') if binding else None
        world = current_facts(case).world if (authorization is not None and
            (Path(case)/FACT_ROOT/'active.json').is_file()) else read_json(
            Path(case)/'state/world_context.json')
        return apply(world, value, value, closures,
                     {a['actor_id'] for a in value['actors']}, authorization=authorization)
    if not isinstance(data,dict) or 'replacements' not in data:
        return data
    request=read_json(Path(case)/'state/behavior_repair.request.json')
    closures = None
    if 'lane_closures' in data:
        from _lib.contracts.execution_gaps import enabled as gap_recovery, REVISION as gap_revision
        if not gap_recovery(case) or request.get('lane_closure_revision') != gap_revision:
            raise ValueError('Lane closure is outside the frozen repair contract')
        data = copy.deepcopy(data); closures = data.pop('lane_closures')
    from _lib.contracts import census_scope
    census_scope.verify_request(case, request)
    for path,digest in request.get('input_hashes',{}).items():
        if not Path(path).is_file() or sha256(path)!=digest:
            raise ValueError('Behavior repair input changed; rebuild against current World/registry')
    base=Path(request['base_path'])
    if data.get('base_sha256') != request['base_sha256'] or sha256(base) != request['base_sha256']:
        raise ValueError('Behavior repair base hash mismatch')
    dispositions = data.get('source_dispositions')
    fields = {'base_sha256','replacements'} | ({'source_dispositions'} if dispositions is not None else set())
    operations=data['replacements']
    if (set(data) != fields or not isinstance(operations,list) or len(operations) > 16
            or not operations and not dispositions):
        raise ValueError('Behavior repair requires 1..16 replacements')
    original, _ = census_scope.split(read_json(base)); result=copy.deepcopy(original)
    if dispositions is not None:
        if request.get('repair_contract') != 'behavior-source-coverage.v1' or operations:
            raise ValueError('Source withdrawal cannot combine with other replacements')
        from _lib.behavior.input_binding import current_evidence_timeline
        return source_revision.apply(original, result, request, dispositions,
                                     current_evidence_timeline(case))
    for operation in operations:
        if not isinstance(operation,dict) or set(operation) != {'path','value'}:
            raise ValueError('Behavior repair requires path/value')
        path=operation['path']
        if not isinstance(path,str) or not path.startswith('/'):
            raise ValueError('Behavior repair requires JSON Pointer')
        if request.get('repair_contract') == 'behavior-source-coverage.v1':
            from _lib.behavior.input_binding import current_evidence_timeline
            current_evidence_timeline(case)
            if path not in request['allowed_paths']:
                raise ValueError('Behavior source coverage repair path is not assigned')
            if path == '/semantic_limitations':
                replacement = operation['value']
                previous = result.get('semantic_limitations', [])
                if (not isinstance(replacement, list) or replacement[:len(previous)] != previous
                        or not 1 <= len(replacement)-len(previous) <= 4):
                    raise ValueError('Behavior source coverage may only append limitations')
                result['semantic_limitations'] = copy.deepcopy(replacement)
                continue
            parts = path[1:].split('/')
            node = result
            for part in parts[:-1]:
                node = node[int(part)] if isinstance(node, list) else node[part]
            key = parts[-1]
            if key == 'source_observation_ids':
                if (not isinstance(operation['value'], list)
                        or not all(isinstance(item, str) for item in operation['value'])):
                    raise ValueError('Behavior source citations must be string IDs')
                if (parts[0] == 'actors' and len(parts) == 5
                        and parts[2] == 'trajectory' and not operation['value']):
                    raise ValueError('Behavior source repair cannot erase a trajectory citation')
            elif key == 'detail':
                if not isinstance(operation['value'], str) or not operation['value'].strip():
                    raise ValueError('Behavior source detail must explain the observation')
            else:
                raise ValueError('Behavior source coverage repair cannot change physical facts')
            node[key] = copy.deepcopy(operation['value'])
            continue
        if 'lane_review_actor_index' in request and path not in request['allowed_paths']:
            raise ValueError('lane repair path is not assigned')
        parts=[p.replace('~1','/').replace('~0','~') for p in path[1:].split('/')]
        if request.get('repair_contract') in {'video-fidelity-repair.v1', 'behavior-handoff-repair.v1'}:
            handoff = request['repair_contract'] == 'behavior-handoff-repair.v1'
            if handoff:
                from _lib.contracts.behavior_handoff import enabled
                if not enabled(case): raise ValueError('Handoff repair requires frozen new-Case policy')
                if not re.fullmatch(r'/(actors/\d+/execution_scope_evidence|semantic_limitations/\d+/(census_issue_ids|scope_binding))', path):
                    raise ValueError('Handoff repair cannot change physical observations or limitations')
            if path not in request['allowed_paths'] or not handoff and parts[0] != 'video_fidelity':
                raise ValueError('Fidelity repair path is not assigned')
            from jsonschema import Draft202012Validator
            Draft202012Validator(request['value_schemas'][path]).validate(operation['value'])
            node = result
            for part in parts[:-1]:
                node = node[int(part)] if isinstance(node, list) else node[part]
            key = int(parts[-1]) if isinstance(node, list) else parts[-1]
            node[key] = copy.deepcopy(operation['value'])
            continue
        if parts[0] not in {'actors','events','executor_primitives','challenge_resolutions','semantic_limitations'}:
            raise ValueError('Behavior repair cannot change contract identity')
        if len(parts)<3:
            raise ValueError('Behavior repair cannot replace a collection or an entire entity')
        if parts[0] == 'semantic_limitations':
            raise ValueError('Behavior structural repair cannot change semantic adjudication')
        if parts[0] == 'challenge_resolutions' and path not in set(request.get('allowed_paths', [])):
            raise ValueError('Behavior repair path is not assigned to the failed challenge')
        node=result
        for part in parts[:-1]:
            if isinstance(node,list):
                if not part.isdecimal(): raise ValueError('invalid repair index')
                node=node[int(part)]
            else: node=node[part]
        key=int(parts[-1]) if isinstance(node,list) else parts[-1]
        if isinstance(node,dict) and key not in node:
            if key not in {'detail','trajectory','position_ego_m','x','y','z','speed_mps','speed_kmh','time_s','lane_id',
                           'is_static','simulation_role','vehicle_class','color','confidence',
                           'event_id','time_range_s','trigger_time_s','evidence',
                           'boundary_id','boundary_state','actor_id','frame_id'}:
                if not (parts[0] == 'challenge_resolutions' and key in {
                        'resolution','execution_effect','decision','rationale','consistency','evidence_frame_ids'}):
                    raise ValueError('Behavior repair cannot add this field')
        else: node[key]
        if isinstance(key,int) and key < 0: raise ValueError('negative repair index')
        node[key]=copy.deepcopy(operation['value'])
    if source_revision.reserved(result) != source_revision.reserved(original) or [
            row for row in result.get('semantic_limitations', []) if
            source_revision.KEY in row or source_revision.QUALITATIVE in row] != [
            row for row in original.get('semantic_limitations', []) if
            source_revision.KEY in row or source_revision.QUALITATIVE in row]:
        raise ValueError('Source revision records must originate from the assigned patch')
    def identities(value):
        return [(a.get('actor_id'),a.get('actor_type'),a.get('identity_description')) for a in value['actors']]
    if identities(original) != identities(result):
        raise ValueError('Behavior repair changed immutable actor identity')
    for collection,id_key in (('events','event_id'),('executor_primitives','primitive_id')):
        if [item.get(id_key) for item in original[collection]] != [item.get(id_key) for item in result[collection]]:
            raise ValueError('Behavior repair changed immutable entity identity')
    if 'lane_review_actor_index' in request:
        _check_lane_patch(original, result, request)
    if request.get('scope') == 'lane_only':
        index = request['lane_review_actor_index']
        old = original['actors'][index]['trajectory']
        new = result['actors'][index]['trajectory']
        submitted = {op['path'] for op in operations}
        for i, (before, point) in enumerate(zip(old, new)):
            prefix = f'/actors/{index}/trajectory/{i}'
            if any(prefix + '/' + key in submitted
                   for key in (*request.get('lane_location_fields', []), 'lane_id', 'detail')):
                detail = point.get('detail')
                if (prefix + '/detail' not in submitted or not isinstance(detail, str)
                        or not re.search(r'(?<![A-Za-z0-9_-])' + re.escape(point['frame_id']) + r'(?![A-Za-z0-9_-])', detail) or not detail.strip()):
                    raise ValueError('lane repair requires new detail with matching frame_id')
            if before.get('lane_id') != point.get('lane_id'):
                point.get('position_ego_m', {}).pop('y', None)
                point['detail'] += (' [Program: lane assignment changed; previous lateral estimate withdrawn, '
                                    'not revalidated. Retained speed prose is historical, not lane evidence.]')
    if closures is not None:
        from _lib.contracts.lane_resolution import apply
        from _lib.behavior.input_binding import closure_request
        from _lib.world.actor_facts import strict_enabled
        from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
        binding = closure_request(case)
        if strict_enabled(case) and closures and (binding is None or
                request.get('behavior_request_sha256') != binding['request_sha256'] or
                request.get('question_authorization') != binding.get('question_authorization', [])):
            raise ContractError('model_out_of_scope: lane closure lacks frozen Behavior authority',
                                rule_id='model_out_of_scope')
        if request.get('behavior_request_sha256') and (binding is None or
                request['behavior_request_sha256'] != binding['request_sha256']):
            raise ContractError('stale_version: repair and Behavior requests differ',
                                rule_id='stale_version')
        world = current_facts(case).world if ('question_authorization' in request and
            (Path(case)/FACT_ROOT/'active.json').is_file()) else read_json(
            Path(case)/'state/world_context.json')
        result = apply(world, original, result, closures, {request['actor_id']},
                       authorization=request.get('question_authorization'),
                       frozen_obligations=request.get('lane_closure_obligations'))
    return result


def preview_world(packet):
    """Worker sees a projection; Python reads the complete hash-bound World."""
    path = packet.get('world_snapshot_path')
    if path is None: return packet['world']  # Frozen legacy request.
    if path not in packet['input_hashes'] or sha256(Path(path)) != packet['input_hashes'][path]:
        raise ValueError('preview World snapshot binding changed')
    return read_json(path)


def preview_limitation_contract(limitations):
    from _lib.behavior.compile import preview_scale_limitation
    return [{'limitation_id':item['limitation_id'],
             'absolute_scale_allowed':preview_scale_limitation(item),
             'requires_upstream_adjudication':item.get('execution_effect')=='blocks_execution'
                                             and not preview_scale_limitation(item),
             'handled_by_scope_binding':bool(item.get('scope_binding'))}
            for item in limitations]


def preview_review_prompt(case, candidate, archived):
    """A separate frozen request, so previous lane/structural requests stay intact."""
    from _lib.behavior.compile import evidence_frames
    case=Path(case); request=case/'state/preview_review.request.json'
    if not request.exists():
        base=read_json(archived)
        from _lib.runtime.execution_scope import view_paths
        world_path, behavior_path = view_paths(case)
        record=case/'case.json'
        confirmed=read_json(record).get('parameters',{}).get('confirmed_lane_assignments',{}) if record.exists() else {}
        frames=[]; hashes={str(p):sha256(p) for p in (Path(archived),behavior_path,
            world_path,case/'state/actor_registry.json',case/'state/behavior_frames.json')}
        for f in evidence_frames(case):
            source=Path(f.get('source_path') or f['path'])
            if not source.resolve().is_relative_to(case.resolve()): raise ValueError('preview frame outside Case')
            frames.append({'frame_id':f['frame_id'],'time_s':f['time_s'],'path':str(source),'sha256':sha256(source)})
            hashes[str(source)]=sha256(source)
        allowed=[f'/actors/{i}/trajectory/{j}/{key}' for i,a in enumerate(base['actors'])
                 for j,p in enumerate(a['trajectory'])
                 for key in ('road_id','ordinal_from_right','lane_id','detail')
                 if key in ('lane_id', 'detail') or key in p]
        full_world=read_json(world_path)
        # Keep the full snapshot on disk, not duplicated in the model request.
        visual_world={'world':copy.deepcopy(full_world['world'])}
        visual_world['world'].pop('notes',None)
        actors=[]
        for actor in base['actors']:
            projected={k:copy.deepcopy(v) for k,v in actor.items() if k!='trajectory'}
            projected['trajectory']=[{k:copy.deepcopy(v) for k,v in point.items() if k!='detail'}
                                     for point in actor['trajectory']]
            actors.append(projected)
        from _lib.behavior.preview_contract import actor_evidence_requirements, response_schema
        required_evidence = actor_evidence_requirements(actors, frames)
        packet={'scope':SCOPE,'response_schema':response_schema(),'base_path':str(archived),'base_sha256':sha256(archived),
            'required_actor_evidence':required_evidence,
            'output_path':str(candidate),'input_hashes':hashes,'allowed_paths':allowed,
            'confirmed_lane_assignments':confirmed,
            'actors':actors,'semantic_limitations':base['semantic_limitations'],
            'limitation_contract':preview_limitation_contract(base['semantic_limitations']),
            'events':base['events'],'world':visual_world,
            'world_snapshot_path':str(world_path), 'review_frames':frames,
            'output_contract':{'base_sha256':sha256(archived),'outcome':'confirmed|corrected|inconclusive','replacements':[{'path':'assigned leaf path','value':'lane_id or detail'}],
                'relations':{'shape_type':'straight|curved|unknown','event_state':'none|present|unknown',
                'motion_conflicts':'none|present|unknown','time_range_s':[frames[0]['time_s'],frames[-1]['time_s']],
                'sample_times_s':[f['time_s'] for f in frames], 'evidence_frame_ids':[f['frame_id'] for f in frames],
                'geometry_reason':'Frame-grounded justification, scoped to the observed window.',
                'event_reason':'Frame-grounded event review; do not hide uncertainty.',
                'lane_order_right_to_left':['existing lane IDs or empty if unknown'],
                'lead_order_near_to_far':['all non-ego participant Actor IDs or empty if order unknown; exclude background'],
                'ego_lane_lead_actor_id':'nearest actor visibly ahead in the same boundary pair as ego, or null when none/unknown',
                'non_blocking_limitations':[{'limitation_id':'existing ID','kind':'absolute_scale|acceleration_class|appearance|window_extent|execution_scope',
                                           'affected_actor_ids':['required for execution_scope; otherwise omit'],
                                           'affected_event_ids':['must be empty for execution_scope; otherwise omit'],
                                           'reason':'why a constant-speed preview can retain this unknown without changing observed events'}],
                'actors':[{'actor_id':'existing ID','lane_id':'existing lane ID or null',
                    'relative_position':'ego|ahead|behind|unknown','relative_motion':'ego|receding|approaching|steady|unknown',
                    'direction':'same_direction|opposite|unknown','lane_motion':'keeping|changing|unknown',
                    'evidence_frame_ids':'copy required_actor_evidence[actor_id] exactly',
                    'context_frame_ids':'optional additional review-frame IDs for disappearance/context; not trajectory samples',
                    'reason':'left_boundary=...; right_boundary=...; ordinal_from_right=...; lane_id=...; frame-grounded motion evidence'}]}},
            'rules':['Review every original image listed, only once; do not read code, previous Cases or full schemas.',
                'For each actor, evidence_frame_ids MUST equal required_actor_evidence[actor_id]. Absence in a later frame is context, not an added trajectory sample: put such review frames only in optional context_frame_ids and explain in reason. Never add/delete trajectory samples.',
                'confirmed_lane_assignments are explicit user corrections for this window, not inherited model labels. Preserve them; report evidence conflicts rather than silently overriding them.',
                'A faint road seam, shadow or vehicle wheel track is not a lane divider. Require repeated painted dash segments traceable across frames before adding a lane; do not invent a divider between ego and the direct lead vehicle. Check the right edge solid line separately from the internal dashed divider.',
                'Existing lane/trend prose is a hypothesis. Independently check lane boundaries at the ego hood, median and each vehicle footprint; never pre-assume the previous conclusion.',
                'Before assigning any actor lane, identify its road and travel direction, outer-right and median-side boundaries. Count road-local ordinal_from_right from 0 and resolve the global lane_id through frozen World lane_ids/anchors; only the complete road_001 approach starts at lane_001.',
                'Do not use the visible hood edges as the ego tire footprint: the hood spans image columns and camera mounting may be offset. Infer ego occupancy from the forward travel corridor bounded by near-field markings, their convergence across frames, and alignment with the explicitly identified ego_lane_lead_actor_id.',
                'For ego and every participant, trace the left and right markings from the near-field road surface to the tire/footprint region. Each actor reason must state left boundary, right boundary, ordinal from right and lane_id; screen position, image column and vanishing point are not lane-number evidence.',
                'If the median-side solid boundary is left of a vehicle and a dashed divider to another drivable lane is right of it, that vehicle cannot have ordinal_from_right=0. Ego and a direct lead vehicle on the same road between the same boundary pair must share lane_id; otherwise return unknown/conflict rather than choosing the inherited label.',
                'Compare all supplied review images for approach/recession and near/far order. Do not infer motion from existing text. Unknown is allowed.',
                'Set ego_lane_lead_actor_id independently before lane IDs. Its actor must be ahead and must share ego lane_id after assignment. If that consistency cannot be established, return inconclusive rather than assigning contradictory lanes.',
                'Use steady for a visibly stable gap, receding for an increasing gap, approaching for a decreasing gap, and unknown when undecidable. Same-direction traffic alone does not establish steady gaps. Review visual relations independently of conflicting numeric estimates; keep all metric values unchanged and report their conflict in the reason. Builder alone chooses explicitly marked simulation assumptions.',
                'If events contains exactly one evidence-bound lane_change, set event_state=present. For its actor use lane_motion=changing and set relation lane_id to the source lane occupied at the start of the window; the observed target lane remains owned by the event and trajectory. All other included actors must use lane_motion=keeping. A second or uncertain change requires inconclusive.',
                output_rule(),
                'Each patched point needs a detail replacement naming its exact frame_id and observed lane evidence. Correct associated prose for all assigned actors when necessary.',
                'Never alter identity, frame/time, x/y/z, speed/reason, events, primitives or limitations. Metric values stay unknown.',
                'Geometry is a proposal to the Python World owner, not permission to write World. Scope straightness to evidence range; distant unresolved curvature must not be silently flattened.',
                'Obey limitation_contract: only absolute_scale_allowed IDs may be classified as absolute_scale. Any requires_upstream_adjudication ID requires inconclusive; do not omit it to obtain confirmed/corrected. All limitations must be accounted for, except handled_by_scope_binding which Python verifies.',
                'Classify scale, acceleration subclass, appearance or window-extent limitations with reasons. An absolute position/speed gap may remain blocks_execution in the observations while an authorized simulation supplies policy assumptions; never rewrite the limitation. Never reclassify identity/event/path conflicts.',
                'Use execution_scope only for limitations of physically separated opposing background traffic that has no effect on the ego-carriageway scenario. List every affected background Actor explicitly in affected_actor_ids and use affected_event_ids=[]. Include all actors in relations, but only participant vehicles in lead_order_near_to_far.',
                'A semantic limitation with scope_binding.kind=physically_separated_opposing_observations represents observations that were intentionally not instantiated as Actors. Do not invent affected_actor_ids or repeat that limitation in non_blocking_limitations; Python validates its World and Behavior dependencies.',
                'Write only the candidate once and stop; do not start subagents or the pipeline.']}
        atomic_json(request,packet)
    return (f'Skill: behavior-understanding. Internal scope preview_relations.v1. Read only {request} and its review_frames. '
            f'This request output_contract/rules override the ordinary lane-only output shape. Write candidate {candidate} once.')


def materialize_preview(case, response):
    from _lib.behavior.compile import derive_initial_states, preview_digest, preview_observations
    from _lib.contracts.v3 import validate_world, validate_behavior
    case=Path(case); packet=read_json(case/'state/preview_review.request.json')
    response, legacy_normalizations = validate_envelope(response, packet)
    for path,digest in packet['input_hashes'].items():
        if sha256(Path(path))!=digest: raise ValueError('preview review input hash changed')
    if response['base_sha256']!=packet['base_sha256']: raise ValueError('preview base hash mismatch')
    source_world=preview_world(packet)
    base=read_json(packet['base_path']); result=copy.deepcopy(base); ops=response['replacements']
    submitted=set()
    for op in ops:
        if not isinstance(op,dict) or set(op)!={'path','value'} or op['path'] not in packet['allowed_paths'] or op['path'] in submitted:
            raise ValueError('preview unassigned/duplicate replacement')
        submitted.add(op['path']); _,_,ai,_,pi,key=op['path'].split('/')
        point = result['actors'][int(ai)]['trajectory'][int(pi)]
        if point.get(key) == op['value']:
            raise ValueError('preview same-value replacement: '+op['path'])
        point[key]=copy.deepcopy(op['value'])
    frames={f['frame_id']:f for f in packet['review_frames']}
    lanes={l['lane_id'] for l in source_world['world']['road']['lanes']}
    for ai,a in enumerate(result['actors']):
        for pi,p in enumerate(a['trajectory']):
            prefix=f'/actors/{ai}/trajectory/{pi}'
            f=frames.get(p.get('frame_id'))
            if f is None or f['time_s']!=p.get('time_s',p.get('timestamp_s')): raise ValueError('preview frame/time mismatch')
            if p.get('lane_id') is not None and p['lane_id'] not in lanes: raise ValueError('preview invalid lane')
            if any(prefix+'/'+key in submitted for key in ('road_id','ordinal_from_right','lane_id','detail')):
                if prefix+'/detail' not in submitted or not isinstance(p.get('detail'),str) or not re.search(
                    r'(?<![A-Za-z0-9_-])'+re.escape(p['frame_id'])+r'(?![A-Za-z0-9_-])',p['detail']):
                    raise ValueError('preview matching frame detail required')
            old=base['actors'][ai]['trajectory'][pi]
            if p.get('lane_id')!=old.get('lane_id'):
                if a['actor_id']=='ego' and f['time_s']==0:
                    if p.get('position_ego_m',{}).get('x') not in (None,0) or p.get('position_ego_m',{}).get('y') not in (None,0):
                        raise ValueError('ego frozen origin conflict')
                else: p.get('position_ego_m',{}).pop('y',None)
                p['detail']+=' [Program: lane corrected; old lateral estimate withdrawn except defined ego t=0 origin. Speed prose remains historical.]'
    facts=response['relations']
    times=[f['time_s'] for f in packet['review_frames']]; frame_ids=list(frames)
    if facts['sample_times_s']!=times or facts['time_range_s']!=[times[0],times[-1]] or facts['evidence_frame_ids']!=frame_ids:
        raise ValueError('preview evidence window mismatch')
    if not all(isinstance(facts[k],str) and facts[k].strip() for k in ('geometry_reason','event_reason')): raise ValueError('preview reasons required')
    ids={a['actor_id'] for a in result['actors']}; seen=set()
    from _lib.behavior.preview_contract import actor_evidence_requirements, PreviewEvidenceError
    required_evidence=actor_evidence_requirements(result['actors'], packet['review_frames'])
    for index, rel in enumerate(facts['actors']):
        aid=rel['actor_id']
        if aid not in ids or aid in seen:
            raise contract_error(packet, 'preview actor relation identity invalid',
                rule_id='preview.actor_identity', field_path=f'/relations/actors/{index}/actor_id',
                expected=sorted(ids-seen), actual=aid, actor_id=aid)
        seen.add(aid)
        a=next(a for a in result['actors'] if a['actor_id']==aid)
        evidence_ids=rel['evidence_frame_ids']
        expected_ids=required_evidence[aid]
        if evidence_ids!=expected_ids or not expected_ids or not isinstance(rel['reason'],str) or not rel['reason'].strip():
            if 'required_actor_evidence' in packet:
                raise PreviewEvidenceError(aid, f'/relations/actors/{index}/evidence_frame_ids', expected_ids, evidence_ids)
            raise ValueError('preview actor evidence missing')
        if set(rel.get('context_frame_ids', []))-set(frame_ids):
            raise PreviewEvidenceError(aid, f'/relations/actors/{index}/context_frame_ids', frame_ids, rel['context_frame_ids'])
        if rel['lane_motion']=='keeping' and any(p.get('lane_id')!=rel['lane_id'] for p in a['trajectory']):
            raise ValueError('preview relation and lane patch disagree')
    if seen!=ids:
        raise contract_error(packet, 'preview actor omitted', rule_id='preview.actor_coverage',
            field_path='/relations/actors', expected=sorted(ids), actual=sorted(seen))
    for aid,lid in packet.get('confirmed_lane_assignments',{}).items():
        actor=next((a for a in result['actors'] if a['actor_id']==aid),None)
        if actor is None or any(p.get('lane_id')!=lid for p in actor['trajectory']):
            raise contract_error(packet,
                'preview conflicts with user-confirmed lane: '+aid,
                legacy_code='preview_contract_blocked', code='preview_fact_conflict',
                rule_id='preview.confirmed_lane_conflict', actor_id=aid,
                field_path='/confirmed_lane_assignments/'+aid, expected=lid,
                actual=[p.get('lane_id') for p in actor['trajectory']] if actor else None,
                retry_kind='fact_conflict',
                required_change='Preserve the explicit user-confirmed lane or report an evidence conflict; unchanged retries cannot override it.')
    for key,known in (('lane_order_right_to_left',lanes),('lead_order_near_to_far',ids-{'ego'})):
        values=facts[key]
        if not isinstance(values,list) or len(values)!=len(set(values)) or set(values)-known: raise ValueError('preview order invalid')
    lead_id=facts.get('ego_lane_lead_actor_id')
    # New packets require an independently declared same-lane lead. Frozen
    # legacy packets do not contain this field and retain their old contract.
    if 'ego_lane_lead_actor_id' in facts:
        relations={item['actor_id']:item for item in facts['actors']}
        if lead_id is not None and (lead_id not in ids-{'ego'} or
                relations[lead_id]['relative_position']!='ahead' or
                relations[lead_id]['lane_id'] is None or
                relations[lead_id]['lane_id']!=relations['ego']['lane_id']):
            raise contract_error(packet,
                'ego-lane lead and lane assignments are inconsistent',
                legacy_code='preview_evidence_insufficient', code='preview_evidence_insufficient',
                rule_id='preview.lead_consistency', field_path='/relations/ego_lane_lead_actor_id',
                expected={'relative_position':'ahead','lane_id':relations['ego']['lane_id']},
                actual=relations.get(lead_id), actor_id=lead_id, retry_kind='new_evidence',
                required_change='Recheck the ego travel corridor and direct lead against near-field lane boundaries.')
    outcome=review_outcome(response, result['actors'])
    limitations={l['limitation_id']:l for l in base['semantic_limitations']}; nonblocking=[]
    for item in facts['non_blocking_limitations']:
        expected={'limitation_id','kind','reason'} | ({'affected_actor_ids','affected_event_ids'}
                 if item.get('kind')=='execution_scope' else set())
        if set(item)!=expected or item['limitation_id'] not in limitations or item['limitation_id'] in nonblocking:
            raise ValueError('preview limitation reference invalid')
        limitation = limitations[item['limitation_id']]
        from _lib.behavior.compile import preview_scale_limitation
        if limitation.get('execution_effect')=='blocks_execution' and not (
                item['kind']=='absolute_scale' and preview_scale_limitation(limitation)):
            raise contract_error(packet, 'cannot relabel blocking limitation: '+item['limitation_id']+'; impact_domains='+str(limitation.get('impact_domains', [])),
                legacy_code='preview_contract_blocked', code='preview_fact_conflict',
                rule_id='preview.blocking_limitation', field_path='/relations/non_blocking_limitations',
                expected='Preserve blocking identity, path and event limitations', actual=item,
                retry_kind='fact_conflict', required_change='Correct the upstream evidence/classification or the bound candidate; unchanged visual retries cannot resolve this prerequisite.')
        if item['kind']=='execution_scope':
            from _lib.world.readiness import ego_carriageway_execution_scope
            scope=ego_carriageway_execution_scope(source_world, result)
            actor_ids=set(item['affected_actor_ids']); actors={a['actor_id']:a for a in result['actors']}
            relations={r['actor_id']:r for r in facts['actors']}
            forbidden_domains={'actor_identity','event_type','event_order'}
            if (not scope or item['affected_event_ids'] or
                    not actor_ids <= set(scope['excluded_actor_ids']) or
                    any(actors[aid].get('simulation_role')!='background' or
                        relations[aid]['direction']!='opposite' for aid in actor_ids) or
                    forbidden_domains.intersection(limitation.get('impact_domains', []))):
                raise contract_error(packet, 'limitation is not outside the reviewed execution scope',
                    legacy_code='preview_contract_blocked', code='preview_fact_conflict',
                    rule_id='preview.execution_scope_conflict', field_path='/relations/non_blocking_limitations',
                    expected={'excluded_actor_ids':scope.get('excluded_actor_ids',[]) if scope else [], 'affected_event_ids':[]},
                    actual=item, retry_kind='fact_conflict', required_change='Correct the upstream evidence/classification or the bound candidate; unchanged visual retries cannot resolve this prerequisite.')
        nonblocking.append(item['limitation_id'])
    from _lib.behavior.compile import observation_scope_exclusions
    excluded={item['limitation_id'] for item in observation_scope_exclusions(source_world,result)}
    missing=set(limitations)-set(nonblocking)-excluded
    if missing:
        raise contract_error(packet,
            'preview omitted limitations: '+', '.join(sorted(missing)),
            legacy_code='preview_contract_blocked', code='preview_evidence_insufficient',
            rule_id='preview.limitation_coverage', field_path='/relations/non_blocking_limitations',
            expected=sorted(set(limitations)-excluded), actual=sorted(nonblocking), retry_kind='content_repair',
            required_change='Account for every limitation or return inconclusive; omission cannot authorize execution.')
    if facts['event_state']=='none' and result['events']:
        raise contract_error(packet, 'preview relation contradicts existing events',
            legacy_code='preview_contract_blocked', code='preview_fact_conflict',
            rule_id='preview.event_conflict', field_path='/relations/event_state',
            expected='present', actual=facts['event_state'], retry_kind='fact_conflict',
            required_change='Correct the upstream evidence/classification or the bound candidate; unchanged visual retries cannot resolve this prerequisite.')
    for rel in facts['actors']:
        if rel['lane_id'] is not None and rel['lane_id'] not in lanes:
            raise ValueError('preview relation references unknown lane')
        if (rel['actor_id']=='ego' and (rel['relative_position']!='ego' or rel['relative_motion']!='ego') or
                rel['actor_id']!='ego' and ('ego' in (rel['relative_position'],rel['relative_motion']))):
            raise ValueError('preview ego relation identity mismatch')
    facts=copy.deepcopy(facts); facts['non_blocking_limitation_ids']=nonblocking
    world=copy.deepcopy(source_world); r=world['world']['road']['roads']
    if facts['shape_type']=='straight':
        if len(r)!=1:
            raise contract_error(packet, 'preview cannot replace road topology',
                legacy_code='preview_contract_blocked', code='unsupported_scene_type',
                rule_id='preview.road_topology', owner='world', field_path='/world/road/roads',
                expected=1, actual=len(r), retry_kind='unsupported_capability',
                required_change='Correct the upstream evidence/classification or the bound candidate; unchanged visual retries cannot resolve this prerequisite.')
        geometry=r[0].setdefault('road_geometry',{})
        if geometry.get('shape_type') not in (None,'straight'):
            raise contract_error(packet, 'preview cannot overwrite known curvature',
                legacy_code='preview_contract_blocked', code='preview_fact_conflict',
                rule_id='preview.geometry_conflict', owner='world', field_path='/world/road/roads/0/road_geometry/shape_type',
                expected=geometry.get('shape_type'), actual=facts['shape_type'], retry_kind='fact_conflict',
                required_change='Correct the upstream evidence/classification or the bound candidate; unchanged visual retries cannot resolve this prerequisite.')
        geometry.update(shape_type='straight',observed_time_range_s=facts['time_range_s'])
        world['evidence_epoch']+=1
        world.setdefault('field_evidence',{})['world.road.roads[0].road_geometry.shape_type']=[{
            'status':'observed','frame_id':f['frame_id'],'time_s':f['time_s'],'reason':facts['geometry_reason']} for f in packet['review_frames']]
    result['world_evidence_epoch']=world['evidence_epoch']
    result=derive_initial_states(result,sync_actor_ids=ids)
    from _lib.world.gaps import enabled
    validate_world(world, observation_contract=enabled(case)); validate_behavior(result,world['evidence_epoch'],execution=False)
    certificate={'status':'accepted','outcome':outcome,'relations':facts,'observation_sha256':preview_digest(preview_observations(result['actors'])),
        'world_sha256':preview_digest(world),'response_sha256':preview_digest(response),
        'input_hashes':packet['input_hashes'],'review_frames':packet['review_frames']}
    if legacy_normalizations: certificate['legacy_normalizations']=legacy_normalizations
    return world,result,certificate
