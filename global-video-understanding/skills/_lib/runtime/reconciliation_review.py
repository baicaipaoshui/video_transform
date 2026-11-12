"""Source-bound World review of active Reconciliation point disputes.

The Actor timeline remains the only Batch text inventory.  This module only
freezes a bounded World question and checks its answer before fact publication.
"""
from __future__ import annotations

import copy
from pathlib import Path

from _lib.behavior.input_binding import evidence_timeline
from _lib.contracts.conversion_policy import digest
from _lib.contracts.reconciliation_alignment import inspect
from _lib.contracts.v3 import ContractError
from _lib.runtime import fact_versions, question_review
from _lib.runtime.case_core import atomic_json, read_json, sha256

ROOT = Path('diagnostics/question_review')
REVISION = 'reconciliation-world-point-review.v1'
ROAD_REVISION = 'reconciliation-world-point-review.v2'


def _stop(code, message):
    error = ContractError(f'{code}: {message}', rule_id=code)
    error.code = code
    raise error


def _lane_geometry(world):
    observation = world.get('world', {}).get('metadata', {}).get(
        'lane_layout_review', {}).get('observation', {})
    boundaries = observation.get('boundaries_right_to_left', [])
    anchor_rows = [row for road in world.get('world', {}).get('road', {}).get('roads', [])
                   for row in road.get('lane_knowledge', {}).get('anchors', [])]
    anchors = {row.get('ordinal_from_right'): row.get('lane_id') for row in anchor_rows}
    if (not boundaries or len(anchor_rows) != len(anchors)
            or len(boundaries) != len(anchors) + 1
            or set(anchors) != set(range(len(anchors)))):
        _stop('evidence_insufficient', 'Effective World has no complete physical lane anchors')
    return boundaries, anchors


def _source_file(case, source_id):
    stem = source_id.split('#/', 1)[0]
    if stem == 'world:original' and source_id.startswith('world:original#/field_evidence/'):
        return _world_source(case)
    if not stem or '/' in stem or '\\' in stem:
        _stop('binding_missing', 'Batch source ID is not local to this Case')
    return case/'batches'/f'{stem}.json'


def _world_source(case):
    from _lib.contracts.core_lane_continuity import _source_world_path
    return _source_world_path(case)


def prepare(case):
    """Turn active, locatable World lane disputes into one immutable task packet."""
    case = Path(case).resolve()
    view = fact_versions.current_facts(case)
    timeline = evidence_timeline(case)
    report = inspect(case, timeline=timeline)
    if not report['blocking']:
        return None
    blocking = [report['rows'][index] for index in report['blocking']]
    rows = [row for row in blocking if row['field'] in {'actor_lane', 'source_text'}
            and row.get('actor_id') and row.get('frame_id')]
    road_rows = [] if rows else [row for row in blocking if row['field'] == 'road_source_text'
                                 and row.get('frame_id') and row.get('values', {}).get('road_id')]
    if not rows and not road_rows:
        _stop('evidence_insufficient', 'Alignment has no locatable World-owned Actor/frame lane dispute')
    boundaries, anchors = _lane_geometry(view.world) if rows else ([], {})
    grouped = {}
    for row in rows:
        grouped.setdefault((row['actor_id'], row['frame_id']), []).append(row)
    road_grouped = {}
    for row in road_rows:
        source = row['sources'][0]
        key = (row['values']['road_id'], row['values']['road_fact'], source['question_id'])
        road_grouped.setdefault(key, []).append(row)
    from _lib.behavior.compile import evidence_frames
    admissible_frames = {row['frame_id'] for row in evidence_frames(case, include_census=False)}
    if any(frame_id not in admissible_frames for _, frame_id in grouped) or any(
            row['frame_id'] not in admissible_frames for row in road_rows):
        _stop('evidence_insufficient',
              'A disputed frame lacks the frozen global or Batch frame binding needed for fact publication')
    frame_ids = {frame_id for _, frame_id in grouped} | {row['frame_id'] for row in road_rows}
    ego_review = any(row.get('values', {}).get('ego_lane_observation') for row in rows)
    if ego_review and len(frame_ids) > 12:
        _stop('evidence_insufficient', 'Ego lane point review exceeds twelve original frames')
    frames = {row['frame_id']: row for row in question_review._frames(case, frame_ids)}
    original_path = _world_source(case)
    if not original_path.is_file():
        _stop('evidence_insufficient', 'Original World description is unavailable')
    original = read_json(original_path)
    by_actor = {row.get('actor_id'): row for row in view.world.get('actor_hypotheses', [])}
    original_actors = {row.get('actor_id'): row for row in original.get('actor_hypotheses', [])}
    groups = []
    sources = {str(original_path): sha256(original_path),
               str(view.world_path): sha256(view.world_path),
               str(view.registry_path): sha256(view.registry_path),
               str(case/'state/global_frames.json'): sha256(case/'state/global_frames.json'),
               str(case/'state/execution_policy.json'): sha256(case/'state/execution_policy.json'),
               str(case/'manifest.json'): sha256(case/'manifest.json')}
    for (actor_id, frame_id), conflicts in sorted(grouped.items()):
        actor = by_actor.get(actor_id)
        if actor_id != 'ego' and actor is None:
            _stop('binding_missing', 'Disputed Actor is absent from effective World')
        key = f'point:{actor_id}:{frame_id}:actor_lane'
        if key in view.claims:
            _stop('fact_conflict', 'An accepted point already exists; new evidence is required before another review')
        same_frame_lane = None
        if actor_id == 'ego':
            initial = view.world['world']['metadata']['lane_layout_review']['observation'].get('ego_initial', {})
            if frame_id in initial.get('evidence_frame_ids', []):
                same_frame_lane = anchors.get(initial.get('ordinal_from_right'))
        elif actor.get('first_seen_frame_id') == frame_id:
            same_frame_lane = actor.get('hypothesized_lane')
        indexed = next((group for group in timeline['groups']
                        if group['actor_id'] == actor_id and group['frame_id'] == frame_id), None)
        text_sources = copy.deepcopy(indexed['sources']) if indexed else []
        ego_sources = {source['source_id']: source for conflict in conflicts
                       if conflict.get('values', {}).get('ego_lane_observation')
                       for source in conflict['sources']}
        ids = {'world:original', *(row['source_id'] for row in text_sources)}
        for conflict in conflicts:
            for source in conflict['sources']:
                if source.get('source_id'):
                    ids.add(source['source_id'])
        for source_id in ids - {'world:original'}:
            path = _source_file(case, source_id)
            expected_hash = (ego_sources[source_id]['source_sha256'] if source_id in ego_sources
                             else timeline['batch_sha256'].get(str(path)))
            if not path.is_file() or expected_hash != sha256(path):
                _stop('source_changed', 'Cited source changed before World review')
            sources[str(path)] = sha256(path)
        raw = dict(actor_id=actor_id, frame_id=frame_id,
                   frame=frames[frame_id], current_lane_id=same_frame_lane,
                   original_actor=copy.deepcopy(original_actors.get(actor_id)),
                   effective_actor=copy.deepcopy(actor),
                   original_layout=copy.deepcopy(original.get('world', {}).get('metadata', {}).get(
                       'lane_layout_review', {}).get('observation', {})),
                   effective_layout=copy.deepcopy(view.world['world']['metadata'][
                       'lane_layout_review']['observation']),
                   original_field_evidence=copy.deepcopy(original.get('field_evidence', {}).get(
                       f'world.actors.{actor_id}.hypothesized_lane', [])),
                   effective_field_evidence=copy.deepcopy(view.world.get('field_evidence', {}).get(
                       f'world.actors.{actor_id}.hypothesized_lane', [])),
                   batch_sources=text_sources, source_ids=sorted(ids),
                   conflict_rows=copy.deepcopy(conflicts))
        if ego_sources:
            raw['ego_lane_observations'] = list(ego_sources.values())
        raw['dispute_id'] = digest(dict(actor_id=actor_id, frame_id=frame_id,
                                      field='actor_lane', sources=raw['source_ids']))[:24]
        groups.append(raw)
    road_groups = []
    questions = {row.get('question_id'): row for row in view.world.get('questions_to_verify', [])}
    layout = view.world.get('world', {}).get('metadata', {}).get('lane_layout_review', {}).get('observation', {})
    for (road_id, field, question_id), conflicts in sorted(road_grouped.items()):
        question = questions.get(question_id)
        if not question:
            _stop('binding_missing', 'Road source question is absent from effective World')
        text_sources = []
        seen = set()
        for conflict in conflicts:
            source = conflict['sources'][0]
            pair = (source['source_id'], conflict['frame_id'])
            if pair in seen:
                continue
            seen.add(pair)
            path = _source_file(case, source['source_id'])
            if not path.is_file() or timeline['batch_sha256'].get(str(path)) != sha256(path):
                _stop('source_changed', 'Cited road Batch source changed before World review')
            sources[str(path)] = sha256(path)
            text_sources.append(dict(source_id=source['source_id'], frame_id=conflict['frame_id'],
                                     time_s=source['time_s'], text=source['text'],
                                     batch_sha256=source['batch_sha256']))
        text_sources.sort(key=lambda row: (row['frame_id'], row['source_id']))
        road = next(row for row in view.world['world']['road']['roads'] if row['road_id'] == road_id)
        evidence = (layout.get('right_roadside', {}) if field == 'shoulder_presence'
                    else layout.get('same_direction_count', {}))
        if field == 'road_boundary':
            evidence = layout.get('right_outer_boundary', {})
        evidence_ids = set(evidence.get('evidence_frame_ids', []))
        if field == 'lane_count_boundary':
            evidence_ids &= set(layout.get('right_outer_boundary', {}).get('evidence_frame_ids', []))
        field_evidence = copy.deepcopy(view.world.get('field_evidence', {}).get(
            'world.road.roadside_areas' if field == 'shoulder_presence'
            else 'world.road.ego_direction_lanes', []))
        raw = dict(road_id=road_id, road_fact=field, question_id=question_id,
                   question=copy.deepcopy(question), world_paths=copy.deepcopy(question.get('world_paths', [])),
                   effective_road=copy.deepcopy(road), effective_layout=copy.deepcopy(layout),
                   effective_field_evidence=field_evidence,
                   layout_evidence_frame_ids=sorted(evidence_ids),
                   sources=text_sources, source_ids=sorted({row['source_id'] for row in text_sources}),
                   frame_ids=sorted({row['frame_id'] for row in text_sources}))
        raw['dispute_id'] = digest(dict(road_id=road_id, road_fact=field,
                                      question_id=question_id, sources=text_sources))[:24]
        road_groups.append(raw)
    if road_groups:
        evidence_frame_ids = {frame_id for group in road_groups
                              for frame_id in group['layout_evidence_frame_ids']
                              if frame_id in admissible_frames}
        frames.update({row['frame_id']: row for row in question_review._frames(
            case, evidence_frame_ids - set(frames))})
    body = dict(revision=ROAD_REVISION if road_groups else REVISION,
                case_id=case.name, parent_version=view.version_id,
                parent_manifest_sha256=sha256(view.directory/'manifest.json'),
                alignment_sha256=digest(report), timeline_sha256=digest(timeline),
                source_hashes=sources, source_frames=list(frames.values()),
                boundaries_right_to_left=boundaries,
                lane_by_ordinal={str(key): value for key, value in anchors.items()},
                groups=groups, deferred_blocking=[row for row in blocking if row not in rows and row not in road_rows])
    if road_groups:
        body['road_groups'] = road_groups
    if ego_review:
        body['ego_lane_observation_review'] = True
    request = dict(body, request_sha256=digest(body))
    path = case/ROOT/f'alignment-{request["request_sha256"][:24]}.request.json'
    if ego_review and any(other != path and read_json(other).get('ego_lane_observation_review')
                          for other in path.parent.glob('alignment-*.request.json')):
        _stop('evidence_insufficient', 'Ego lane observations already have a frozen point review')
    if path.is_file() and read_json(path) != request:
        _stop('fact_conflict', 'Frozen World point request changed')
    if not path.is_file():
        atomic_json(path, request)
    return path


def _verified(case, request_path):
    case = Path(case).resolve()
    path = Path(request_path).resolve()
    if not path.is_relative_to((case/ROOT).resolve()):
        _stop('model_out_of_scope', 'World point request belongs to another Case')
    request = read_json(path)
    body = dict(request); signature = body.pop('request_sha256', None)
    if request.get('revision') not in {REVISION, ROAD_REVISION} or digest(body) != signature or request['case_id'] != case.name:
        _stop('fact_conflict', 'World point request binding changed')
    for name, expected in request['source_hashes'].items():
        source = Path(name).resolve()
        if not source.is_relative_to(case) or not source.is_file() or sha256(source) != expected:
            _stop('source_changed', 'World point source changed: ' + name)
    for frame in request['source_frames']:
        source = Path(frame['path']).resolve()
        if not source.is_relative_to(case) or not source.is_file() or sha256(source) != frame['sha256']:
            _stop('source_changed', 'World point original frame changed')
    active = fact_versions.current_facts(case)
    if (active.version_id != request['parent_version'] or
            sha256(active.directory/'manifest.json') != request['parent_manifest_sha256']):
        _stop('stale_version', 'World point request targets an old fact version')
    timeline = evidence_timeline(case)
    result_path = path.with_name(path.name.replace('.request.json', '.result.json'))
    published = result_path.is_file() and request.get('revision') == ROAD_REVISION
    if digest(timeline) != request['timeline_sha256'] or (
            digest(inspect(case, timeline=timeline)) != request['alignment_sha256'] and not published):
        _stop('source_changed', 'Reconciliation evidence changed since World review')
    return request


def prompt(request_path, candidate_path):
    """Use the current World owner with an exact, source-bound point task."""
    request = read_json(request_path)
    if request.get('revision') == ROAD_REVISION:
        return (f'你是本 Case 的 World 定向道路事实裁决者。只读取冻结请求 {request_path}。'
                '每个 road_groups 的 dispute_id 回答一次，不把道路事实绑定到 Actor，也不重新执行完整 World Closure。'
                '比较有效 World 的车道数量、右边界或路肩是否存在与每条 Batch 原文；逐条保留 source_id、frame_id。'
                '区分 explained、partially_explained、contradiction、evidence_insufficient。视频时间窗内 Ego 驾驶及必要交互是目标；不得要求证明视频外出口或道路延伸。单帧遮挡不能证明路肩存在或推翻已有布局；'
                '解释遮挡须确认同一道路段且指出有效布局的真实可见证据帧。仅判断路肩存在性，不细分铺装。'
                '输出 JSON 到 ' + str(candidate_path) + '，字段为 '
                '{"request_sha256":"...","decisions":[],"road_decisions":['
                '{"dispute_id":"...","outcome":"explained|partially_explained|contradiction|evidence_insufficient",'
                '"road_id":"...","road_fact":"...","same_segment":true,'
                '"sources":[{"source_id":"...","frame_id":"...",'
                '"assessment":"covered|visibility_limited|contradictory|unresolved",'
                '"evidence_frame_ids":["..."],"reason":"..."}],"reason":"..."}]}。'
                'explained 必须逐条覆盖或说明可见度受限，并以已接纳布局可见帧支撑；冲突和证据不足保留未决。'
                '混合来源用 partially_explained；同一来源 assessment=partial，另加 components 数组。'
                '每项为 {text, fact, assessment, evidence_frame_ids, reason}；text 按顺序拼接必须精确等于完整原文。'
                'fact 只能为 current_layout、visible_sign、marking_interpretation、future_topology、roadside_structure；'
                'assessment 只能为 covered 或 unresolved，必须同时保留两种分量。已确认分量必须引用该来源原帧；'
                'current_layout 另须引用已接纳布局帧。标志可见不证明分叉，标记含义不明或视频外道路未见应为 unresolved。'
                'partial 不覆盖整条来源，不修改 World 或宣称没有分叉；能否继续仅由原执行依赖门禁决定。')
    ego_instruction = ('对 ego_lane_observations，检查原帧相机所在道路走廊及实际可见边界。'
                       '候选值和原文只作线索，不能以其他车辆相对相机的横向位移代替空间证据。'
                       if request.get('ego_lane_observation_review') else '')
    return (f'你是本 Case 的 World 定向事实裁决者。只读取冻结请求 {request_path}，'
            '逐组查看其中原始 World、有效 World、Batch 原文、物理边界和 source_frames 指向的原帧。'
            '只裁决同 Actor、同帧的 actor_lane 基础事实；不要推断变道事件或跨帧轨迹。'
            + ego_instruction +
            '每个 dispute_id 恰好回答一次：confirmed（与该帧当前结构化值一致）、'
            'revised（原帧足以支持另一物理车道）、evidence_insufficient。'
            '证据不足不得猜测；说明必须引用可见边界及已列 source_ids。'
            '确认或改判时填写 actor_id、frame_id、lane_id、ordinal_from_right、'
            'right_boundary_id、left_boundary_id、reviewed_source_ids（完整列出该组 source_ids）、'
            'evidence_frame_ids（必须包含该组原帧）、reason。证据不足仅填写 dispute_id、'
            'outcome、reason。只输出 JSON 到 ' + str(candidate_path) + '，格式为 '
            '{"request_sha256":"...","decisions":[{...}]}。请求哈希见请求文件。')


def _commit_road(case, request_path, candidate_path, request):
    """Accept explanations of existing road facts, never publish a new road fact."""
    candidate = read_json(candidate_path)
    if (not isinstance(candidate, dict) or set(candidate) != {'request_sha256', 'decisions', 'road_decisions'}
            or candidate['request_sha256'] != request['request_sha256'] or candidate['decisions'] != []):
        _stop('fact_conflict', 'Road answer is not bound to its road-only request')
    expected = {group['dispute_id']: group for group in request['road_groups']}
    decisions = candidate['road_decisions']
    if (not isinstance(decisions, list) or len(decisions) != len(expected)
            or any(not isinstance(row, dict) for row in decisions)
            or {row.get('dispute_id') for row in decisions} != set(expected)):
        _stop('binding_missing', 'Road answer must address every grouped dispute once')
    packet_frames = {row['frame_id'] for row in request['source_frames']}
    for decision in decisions:
        group = expected[decision['dispute_id']]
        if (set(decision) != {'dispute_id', 'outcome', 'road_id', 'road_fact', 'same_segment',
                             'sources', 'reason'} or decision['road_id'] != group['road_id']
                or decision['road_fact'] != group['road_fact']
                or decision['outcome'] not in {'explained', 'partially_explained', 'contradiction', 'evidence_insufficient'}
                or not isinstance(decision['reason'], str) or not decision['reason'].strip()
                or not isinstance(decision['same_segment'], bool)):
            _stop('binding_missing', 'Road decision lacks its exact road/fact binding')
        source_rows = decision['sources']
        expected_pairs = {(row['source_id'], row['frame_id']) for row in group['sources']}
        if (not isinstance(source_rows, list) or len(source_rows) != len(expected_pairs)
                or any(not isinstance(row, dict) for row in source_rows)
                or {(row.get('source_id'), row.get('frame_id')) for row in source_rows} != expected_pairs):
            _stop('binding_missing', 'Road answer must preserve every source/frame pair')
        evidence_ids = set(group['layout_evidence_frame_ids']) & packet_frames
        layout = group['effective_layout']
        road = group['effective_road']
        if (decision['outcome'] == 'evidence_insufficient'
                and group['road_fact'] == 'shoulder_presence'
                and decision['same_segment'] and not evidence_ids):
            _stop('evidence_insufficient', 'Roadside dependency handoff needs a located road segment')
        if decision['outcome'] in {'explained', 'partially_explained'}:
            if not decision['same_segment'] or not evidence_ids:
                _stop('evidence_insufficient', 'Road explanation lacks same-segment layout evidence')
            if group['road_fact'] == 'lane_count_boundary':
                count = layout.get('same_direction_count', {})
                if (count.get('kind') != 'exact' or count.get('count') != len(road.get('lane_ids', []))
                        or layout.get('right_outer_boundary', {}).get('confirmed') is not True):
                    _stop('fact_conflict', 'Accepted lane count is not an exact layout fact')
            elif group['road_fact'] == 'road_boundary':
                if layout.get('right_outer_boundary', {}).get('confirmed') is not True:
                    _stop('evidence_insufficient', 'Road boundary is not confirmed in effective layout')
            elif group['road_fact'] == 'shoulder_presence':
                areas = group['effective_layout'].get('right_roadside', {})
                active = fact_versions.current_facts(case).world
                shoulder = [row for row in active.get('world', {}).get('road', {}).get('roadside_areas', [])
                            if row.get('road_id') == group['road_id'] and row.get('side') == 'right'
                            and 'shoulder' in str(row.get('type', ''))]
                if not shoulder or 'shoulder' not in str(areas.get('type', '')):
                    _stop('evidence_insufficient', 'Effective World does not establish shoulder presence')
        original_text = {(row['source_id'], row['frame_id']): row['text'] for row in group['sources']}
        for source in source_rows:
            partial = source.get('assessment') == 'partial'
            keys = {'source_id', 'frame_id', 'assessment', 'evidence_frame_ids', 'reason'}
            if partial:
                keys.add('components')
            if (set(source) != keys
                    or source['assessment'] not in {'covered', 'visibility_limited', 'contradictory', 'unresolved', 'partial'}
                    or not isinstance(source['reason'], str) or not source['reason'].strip()
                    or not isinstance(source['evidence_frame_ids'], list)
                    or len(source['evidence_frame_ids']) != len(set(source['evidence_frame_ids']))
                    or not set(source['evidence_frame_ids']) <= packet_frames):
                _stop('binding_missing', 'Road source assessment has invalid original-frame evidence')
            if partial:
                parts = source['components']
                if (decision['outcome'] != 'partially_explained' or not isinstance(parts, list) or not parts
                        or any(not isinstance(part, dict) or set(part) != {
                            'text', 'fact', 'assessment', 'evidence_frame_ids', 'reason'} for part in parts)
                        or any(not isinstance(part['text'], str) or not part['text'].strip() for part in parts)
                        or ''.join(part['text'] for part in parts) != original_text[(source['source_id'], source['frame_id'])]
                        or {part['assessment'] for part in parts} != {'covered', 'unresolved'}):
                    _stop('binding_missing', 'Partial source must retain an exact complete partition of the original text')
                for part in parts:
                    refs = part['evidence_frame_ids']
                    if (part['fact'] not in {'current_layout', 'visible_sign', 'marking_interpretation',
                                            'future_topology', 'roadside_structure'}
                            or not isinstance(part['reason'], str) or not part['reason'].strip()
                            or not isinstance(refs, list) or len(refs) != len(set(refs))
                            or not set(refs) <= set(source['evidence_frame_ids'])
                            or source['frame_id'] not in refs):
                        _stop('binding_missing', 'Partial component needs its original source frame and fact kind')
                    if part['assessment'] == 'covered':
                        if part['fact'] == 'future_topology':
                            _stop('fact_conflict', 'Road explanation cannot confirm unseen future topology')
                        if part['fact'] == 'current_layout' and not set(refs) & evidence_ids:
                            _stop('evidence_insufficient', 'Confirmed layout component lacks accepted layout evidence')
                continue
            if source['assessment'] in {'covered', 'visibility_limited'}:
                if decision['outcome'] not in {'explained', 'partially_explained'}:
                    _stop('fact_conflict', 'Non-explained road outcome cannot clear a source')
                if not set(source['evidence_frame_ids']) & evidence_ids:
                    _stop('evidence_insufficient', 'Road source lacks accepted layout evidence')
                if source['assessment'] == 'covered' and source['frame_id'] not in source['evidence_frame_ids']:
                    _stop('evidence_insufficient', 'Directly covered source needs its own visible frame')
            elif decision['outcome'] == 'explained' or (
                    decision['outcome'] == 'partially_explained' and source['assessment'] != 'unresolved'):
                _stop('fact_conflict', 'Contrary or unresolved source cannot be explained')
            if (source['assessment'] in {'contradictory', 'unresolved'}
                    and source['frame_id'] not in source['evidence_frame_ids']):
                _stop('evidence_insufficient', 'Unresolved or contrary source needs its original frame')
        if decision['outcome'] == 'partially_explained' and not (
                any(row['assessment'] in {'partial', 'unresolved'} for row in source_rows)
                and any(row['assessment'] in {'partial', 'covered', 'visibility_limited'} for row in source_rows)):
            _stop('binding_missing', 'Partial outcome needs both confirmed and unresolved source content')
    result_path = Path(request_path).with_name(
        Path(request_path).name.replace('.request.json', '.result.json'))
    if result_path.is_file():
        result = read_json(result_path)
        if (result.get('request_sha256') != request['request_sha256']
                or result.get('candidate_sha256') != sha256(candidate_path)
                or result.get('effective_version') != request['parent_version']
                or result.get('road_decisions') != decisions
                or result.get('status') != ('committed' if all(
                    row['outcome'] == 'explained' for row in decisions) else 'needs_review')):
            _stop('fact_conflict', 'Published road review has different source or answer')
        if 'alignment_sha256' in result:
            return result
    result = dict(revision=ROAD_REVISION, request_sha256=request['request_sha256'],
                  candidate_sha256=sha256(candidate_path),
                  status='committed' if all(row['outcome'] == 'explained' for row in decisions) else 'needs_review',
                  parent_version=request['parent_version'], effective_version=request['parent_version'],
                  road_decisions=decisions)
    if not result_path.is_file():
        atomic_json(result_path, result)
    after = inspect(case)
    targeted = {(source['source_id'], source['frame_id'])
                for group in request['road_groups'] for source in group['sources']}
    result['remaining_targeted'] = [after['rows'][index] for index in after['blocking']
                                    if any((source.get('source_id'), after['rows'][index]['frame_id']) in targeted
                                           for source in after['rows'][index]['sources'])]
    result['remaining_blocking'] = len(after['blocking'])
    result['alignment_sha256'] = digest(after)
    result['deferred_dependency'] = (result['status'] == 'needs_review' and
                                     not after['blocking'] and
                                     all(row['outcome'] in {'explained', 'partially_explained', 'evidence_insufficient'}
                                         for row in decisions))
    atomic_json(result_path, result)
    return result


def commit(case, request_path, candidate_path):
    """Accept one complete World answer or stop without a partial fact update."""
    case = Path(case).resolve()
    request_path = Path(request_path).resolve()
    candidate_path = Path(candidate_path).resolve()
    if not candidate_path.is_relative_to((case/ROOT).resolve()) or not candidate_path.is_file():
        _stop('missing_output', 'World point candidate is absent or outside the Case')
    try:
        request = _verified(case, request_path)
    except ContractError as error:
        if getattr(error, 'rule_id', None) != 'stale_version':
            raise
        # The pointer may have switched just before the result receipt was
        # written. Recover only the exact request/candidate pair in that version.
        request = read_json(request_path)
        body = dict(request); signature = body.pop('request_sha256', None)
        active = fact_versions.current_facts(case)
        binding = dict(request_sha256=signature, candidate_sha256=sha256(candidate_path))
        markers = [row for row in active.revisions
                   if row.get('kind') == 'reconciliation-lane-points.v1'
                   and row.get('version_id') == active.version_id
                   and row.get('parent_version') == request.get('parent_version')
                   and row.get('review_binding') == binding]
        if (request.get('revision') != REVISION or digest(body) != signature
                or len(markers) != 1):
            raise error
        after = inspect(case)
        targeted = {(group['actor_id'], group['frame_id']) for group in request['groups']}
        result = dict(revision=REVISION, request_sha256=signature,
                      candidate_sha256=binding['candidate_sha256'], status='committed',
                      parent_version=request['parent_version'],
                      effective_version=active.version_id,
                      remaining_targeted=[after['rows'][index] for index in after['blocking']
                                          if (after['rows'][index].get('actor_id'),
                                              after['rows'][index].get('frame_id')) in targeted],
                      remaining_blocking=len(after['blocking']), alignment_sha256=digest(after))
        result_path = Path(request_path).with_name(
            Path(request_path).name.replace('.request.json', '.result.json'))
        if result_path.is_file() and read_json(result_path) != result:
            _stop('fact_conflict', 'Published review receipt differs from active version')
        if not result_path.is_file():
            atomic_json(result_path, result)
        return result
    if request.get('revision') == ROAD_REVISION:
        return _commit_road(case, request_path, candidate_path, request)
    candidate = read_json(candidate_path)
    if not isinstance(candidate, dict) or set(candidate) != {'request_sha256', 'decisions'} or \
            candidate['request_sha256'] != request['request_sha256']:
        _stop('fact_conflict', 'World point answer is not bound to its request')
    expected = {group['dispute_id']: group for group in request['groups']}
    decisions = candidate['decisions']
    if (not isinstance(decisions, list) or len(decisions) != len(expected) or
            any(not isinstance(row, dict) for row in decisions) or
            {row.get('dispute_id') for row in decisions} != set(expected)):
        _stop('binding_missing', 'World point answer must address every dispute once')
    observed_fields = {'dispute_id', 'outcome', 'actor_id', 'frame_id', 'lane_id',
                       'ordinal_from_right', 'right_boundary_id', 'left_boundary_id',
                       'reviewed_source_ids', 'evidence_frame_ids', 'reason'}
    for row in decisions:
        expected_fields = ({'dispute_id', 'outcome', 'reason'}
                           if row.get('outcome') == 'evidence_insufficient'
                           else observed_fields)
        if (set(row) != expected_fields or
                not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            _stop('binding_missing', 'World point answer has invalid or missing evidence fields')
    result_path = Path(request_path).with_name(
        Path(request_path).name.replace('.request.json', '.result.json'))
    if result_path.is_file():
        result = read_json(result_path)
        if result.get('effective_version') != fact_versions.current_facts(case).version_id:
            _stop('stale_version', 'World point result is no longer the active fact version')
        if result.get('candidate_sha256') != sha256(candidate_path):
            _stop('fact_conflict', 'A reviewed request has a different answer')
        return result
    if any(row.get('outcome') == 'evidence_insufficient' for row in decisions):
        if any(row.get('outcome') not in {'confirmed', 'revised', 'evidence_insufficient'}
               for row in decisions):
            _stop('binding_missing', 'World point answer has an unknown outcome')
        result = dict(revision=REVISION, request_sha256=request['request_sha256'],
                      candidate_sha256=sha256(candidate_path), status='needs_review',
                      parent_version=request['parent_version'],
                      effective_version=request['parent_version'],
                      reasons=[row.get('reason') for row in decisions
                               if row.get('outcome') == 'evidence_insufficient'])
        atomic_json(result_path, result)
        return result
    rows = []
    active = fact_versions.current_facts(case, request['parent_version'])
    anchors = {int(key): value for key, value in request['lane_by_ordinal'].items()}
    ordinal_by_lane = {lane_id: ordinal for ordinal, lane_id in anchors.items()}
    lane_map = active.world.get('world', {}).get('metadata', {}).get(
        'lane_layout_review', {}).get('adjudication', {}).get('lane_id_map', {})
    boundaries = request['boundaries_right_to_left']
    for decision in decisions:
        group = expected[decision['dispute_id']]
        if (decision.get('outcome') not in {'confirmed', 'revised'}
                or decision.get('actor_id') != group['actor_id']
                or decision.get('frame_id') != group['frame_id']
                or decision.get('reviewed_source_ids') != group['source_ids']
                or decision.get('evidence_frame_ids') != [group['frame']['frame_id']]
                or not isinstance(decision.get('reason'), str) or not decision['reason'].strip()):
            _stop('binding_missing', 'World point decision lacks exact Actor/frame/source evidence')
        ordinal = decision.get('ordinal_from_right')
        if (type(ordinal) is not int or anchors.get(ordinal) != decision.get('lane_id')
                or ordinal < 0 or ordinal + 1 >= len(boundaries)
                or (decision.get('right_boundary_id'), decision.get('left_boundary_id')) !=
                    (boundaries[ordinal], boundaries[ordinal + 1])):
            _stop('fact_conflict', 'World point lane does not match effective physical boundaries')
        current = group['current_lane_id']
        if (decision['outcome'] == 'confirmed' and decision['lane_id'] != current or
                decision['outcome'] == 'revised' and current == decision['lane_id']):
            _stop('fact_conflict', 'World point outcome disagrees with same-frame effective fact')
        if any(claim.get('field') == 'actor_lane'
               and claim.get('actor_id') == group['actor_id']
               and claim.get('frame_id') == group['frame_id']
               and ordinal_by_lane.get(lane_map.get(claim.get('value'),
                                                    claim.get('value'))) != ordinal
               for claim in active.claims.values()):
            _stop('fact_conflict',
                  'An accepted same-frame claim conflicts; World claim revision is required')
        ego_sources = group.get('ego_lane_observations', [])
        cited = (ego_sources[0]['source_id'] if ego_sources else
                 next((source_id for source_id in group['source_ids'] if '#/' in source_id), None))
        source = _source_file(case, cited) if cited else _world_source(case)
        if cited and cited.startswith('world:original#/') and source.is_relative_to(case/fact_versions.ROOT):
            # The point publisher accepts immutable World snapshots under diagnostics.
            from _lib.runtime.case_core import atomic_bytes
            snapshot = case/ROOT/sha256(source)/'world.json'
            if not snapshot.is_file():
                atomic_bytes(snapshot, source.read_bytes())
            if sha256(snapshot) != sha256(source):
                _stop('source_changed', 'Ego lane observation World snapshot changed')
            source = snapshot
        source_path = str(source.relative_to(case))
        rows.append(dict(actor_id=group['actor_id'], frame_id=group['frame_id'],
                         lane_id=decision['lane_id'], ordinal_from_right=ordinal,
                         right_boundary_id=boundaries[ordinal],
                         left_boundary_id=boundaries[ordinal + 1],
                         source_path=source_path, source_sha256=sha256(source),
                         reason=decision['reason'],
                         review_source_ids=group['source_ids']))
    binding = dict(request_sha256=request['request_sha256'],
                   candidate_sha256=sha256(candidate_path))
    view = fact_versions.commit_reconciliation_lane_points(
        case, request['parent_version'], rows, review_binding=binding)
    after = inspect(case)
    still = [after['rows'][index] for index in after['blocking']
             if (after['rows'][index].get('actor_id'), after['rows'][index].get('frame_id'))
             in {(group['actor_id'], group['frame_id']) for group in request['groups']}]
    result = dict(revision=REVISION, request_sha256=request['request_sha256'],
                  candidate_sha256=binding['candidate_sha256'], status='committed',
                  parent_version=request['parent_version'], effective_version=view.version_id,
                  remaining_targeted=still, remaining_blocking=len(after['blocking']),
                  alignment_sha256=digest(after))
    atomic_json(result_path, result)
    return result


def run(runner, case):
    """Use the existing World closure task budget once before Behavior."""
    from _lib.contracts.reconciliation_alignment import enabled
    if not enabled(case):
        return None
    case = Path(case).resolve()
    active_version = fact_versions.current_facts(case).version_id
    if not inspect(case)['blocking']:
        return None
    for previous_path in sorted((case/ROOT).glob('alignment-*.result.json')):
        previous = read_json(previous_path)
        if (previous.get('effective_version') == active_version
                and (previous.get('status') == 'needs_review' and not previous.get('deferred_dependency')
                     or previous.get('remaining_targeted'))):
            _stop('evidence_insufficient',
                  'Previous World point review did not close all disputes; new evidence is required')
    request_path = prepare(case)
    if request_path is None:
        return None
    candidate_path = request_path.with_name(
        request_path.name.replace('.request.json', '.candidate.json'))
    result_path = request_path.with_name(
        request_path.name.replace('.request.json', '.result.json'))
    if result_path.is_file():
        result = (commit(case, request_path, candidate_path)
                  if read_json(request_path).get('revision') == ROAD_REVISION
                  else read_json(result_path))
        if result.get('effective_version') != fact_versions.current_facts(case).version_id:
            _stop('stale_version', 'World point result is no longer the active fact version')
    else:
        def adopt(_session=None):
            return commit(case, request_path, candidate_path)
        from _lib.runtime.world_adjudication import ACTIVE as CENSUS_ACTIVE
        if (case/CENSUS_ACTIVE).is_file():
            _stop('evidence_insufficient', 'Finish the active Census World continuation before point review')
        previous_task = getattr(runner, '_reconciliation_point_task', None)
        runner._reconciliation_point_task = True
        try:
            runner.model_stage(case, 'world_closure', 'global-video-understanding',
                               prompt(request_path, candidate_path), adopt,
                               adopt if candidate_path.is_file() else None,
                               candidate_path=candidate_path)
        finally:
            if previous_task is None:
                del runner._reconciliation_point_task
            else:
                runner._reconciliation_point_task = previous_task
        result = read_json(result_path)
    if (result.get('status') == 'needs_review' and result.get('deferred_dependency')
            and not inspect(case)['blocking']):
        return result
    if result.get('status') != 'committed' or result.get('remaining_targeted'):
        _stop('evidence_insufficient', 'World point review has not closed its assigned facts')
    after = inspect(case)
    if after['blocking'] and request_path.is_file() and read_json(request_path).get('revision') == REVISION:
        remaining = [after['rows'][index] for index in after['blocking']]
        if all(row.get('field') == 'road_source_text' for row in remaining):
            return run(runner, case)
    if after['blocking']:
        _stop('evidence_insufficient', 'Reconciliation still has execution-relevant disputed facts')
    return result
