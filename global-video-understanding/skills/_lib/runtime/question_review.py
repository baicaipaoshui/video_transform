"""Scoped World-owner review of an existing question in the active fact version."""
from __future__ import annotations

import copy
import math
import re
import uuid
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError, world_conflict_value
from _lib.paths import SCENARIO_ROOT
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.runtime import fact_versions
from _lib.world import admission, point_review
from _lib.world.actor_facts import (actor_lane_path, lane_id_for_path,
                                    question_contracts, resolved_lane_comparisons)

ROOT = Path('diagnostics/question_review')
REVISION = 'world-question-review.v1'
OBSERVATION_REVISION = 'world-source-observation-review.v1'
_FRAME_ID = re.compile(r'frame_\d{4,}(?:/\d{4,})*')


def _cited_frame_ids(description):
    if not isinstance(description, str):
        return set()
    return {('frame_' + part if index else part)
            for match in _FRAME_ID.finditer(description)
            for index, part in enumerate(match.group().split('/'))}


def _fail(code, message):
    error = ContractError(f'{code}: {message}', rule_id=code)
    error.code = code
    raise error


def _question(world, issue_id):
    matches = [q for q in world.get('questions_to_verify', []) if q.get('question_id') == issue_id]
    if len(matches) != 1:
        _fail('binding_missing', 'Question ID is absent or repeated in the active World')
    return matches[0]


def _frames(case, ids):
    from _lib.behavior.compile import evidence_frames
    index = {f['frame_id']: f for f in evidence_frames(case)}
    original_case = (SCENARIO_ROOT/'video_extractor/cases'/Path(case).name).resolve()
    rows = []
    for frame_id in sorted(ids):
        frame = index.get(frame_id)
        if frame is None:
            _fail('evidence_insufficient', 'Original source frame is unavailable: ' + frame_id)
        candidates = [frame, *frame.get('variants', [])]
        source = None
        for row in candidates:
            for key in ('source_path', 'path'):
                if not row.get(key):
                    continue
                original = Path(row[key]).resolve()
                if original.is_relative_to(Path(case).resolve()) and original.is_file():
                    source = original
                elif original.is_relative_to(original_case) and original.is_file():
                    copied = Path(case).resolve()/original.relative_to(original_case)
                    if copied.is_file() and sha256(copied) == sha256(original):
                        source = copied
                if source is not None:
                    break
            if source is not None:
                break
        if source is None:
            _fail('evidence_insufficient', 'Original source frame is outside the Case or missing')
        rows.append(dict(frame_id=frame_id, time_s=frame['time_s'],
                         path=str(source), sha256=sha256(source)))
    return rows


def prepare_unregistered(case, source_path=None):
    """Locate unindexed Behavior observations without assigning a fact identity.

    A source pointer identifies the exact limitation, while frame IDs only
    identify pictures for the World reviewer. Neither is an observation ID.
    """
    case = Path(case).resolve()
    source_path = Path(source_path or case/'state/behavior.candidate.json').resolve()
    if (not source_path.is_relative_to(case) or not source_path.is_file()
            or source_path.name != 'behavior.candidate.json'):
        _fail('binding_missing', 'Unregistered review needs the Case Behavior candidate')
    behavior = read_json(source_path)
    limitations = behavior.get('semantic_limitations', [])
    if not isinstance(limitations, list):
        _fail('binding_missing', 'Behavior limitations are not an array')
    from _lib.behavior.compile import observation_scope_exclusions
    view = fact_versions.current_facts(case)
    excluded = {row['limitation_id'] for row in observation_scope_exclusions(view.world, behavior)}
    from _lib.behavior.compile import evidence_frames
    timeline = sorted(evidence_frames(case), key=lambda row: row['time_s'])
    ordered = [row['frame_id'] for row in timeline]
    requests = []
    for index, item in enumerate(limitations):
        if not isinstance(item, dict) or 'actor_identity' not in item.get('impact_domains', []):
            continue
        # A structured opposing-scope claim belongs to its existing Behavior
        # scope validator. An invalid claim stays blocked there; a valid one
        # is reused without asking World to look at the same barrier again.
        if (item.get('census_issue_ids') or item.get('actor_ids') or item.get('actor_id')
                or item.get('scope_binding') or item.get('limitation_id') in excluded):
            continue
        description = item.get('description')
        ids = sorted(_cited_frame_ids(description))
        if not ids:
            continue  # No locatable original frame; retain the original blocker.
        if any(frame_id not in ordered for frame_id in ids):
            _fail('evidence_insufficient', 'Cited observation frame is not in frozen evidence')
        first = min(ordered.index(frame_id) for frame_id in ids)
        last = max(ordered.index(frame_id) for frame_id in ids)
        context = [frame_id for frame_id in ordered[max(0, first-1):last+1]
                   if frame_id not in ids]
        requests.append(prepare_source_observation(
            case, source_path=source_path,
            source_pointer=f'/semantic_limitations/{index}',
            source_sha256=sha256(source_path), frame_ids=ids,
            context_frame_ids=context))
    return requests


def prepare_source_observation(case, *, source_path, source_pointer, source_sha256, frame_ids,
                               context_frame_ids=()):
    """Freeze a World visual question from a real source, with no fabricated ID."""
    case = Path(case).resolve()
    source = Path(source_path).resolve()
    allowed = {case/'state/behavior.candidate.json', case/'final/analysis.json'}
    if (not source.is_relative_to(case) or not source.is_file()
            or source not in allowed
            or sha256(source) != source_sha256
            or not re.fullmatch(r'/(?:semantic_limitations|limitations)/(0|[1-9]\d*)', source_pointer)):
        _fail('binding_missing', 'Observation source path, hash or pointer is invalid')
    index = int(source_pointer.rsplit('/', 1)[1])
    collection = source_pointer.split('/')[1]
    if ((source.name == 'behavior.candidate.json') != (collection == 'semantic_limitations')):
        _fail('binding_missing', 'Observation pointer does not match the source contract')
    limitations = read_json(source).get(collection, [])
    if not isinstance(limitations, list) or index >= len(limitations):
        _fail('binding_missing', 'Observation source pointer does not exist')
    item = limitations[index]
    if not isinstance(item, dict) or 'actor_identity' not in item.get('impact_domains', []):
        _fail('model_out_of_scope', 'Source does not identify an unresolved object observation')
    if item.get('census_issue_ids') or item.get('question_id'):
        _fail('model_out_of_scope', 'Registered observation must use its existing review owner')
    if collection == 'limitations':
        behavior_path = case/'state/behavior.candidate.json'
        if not behavior_path.is_file() or sum(
                original == item for original in read_json(behavior_path).get('semantic_limitations', [])) != 1:
            _fail('binding_missing', 'Analysis limitation lacks one exact Behavior source')
    description = item.get('description')
    text_ids = _cited_frame_ids(description)
    if (not isinstance(frame_ids, (list, tuple)) or not frame_ids
            or len(frame_ids) != len(set(frame_ids)) or set(frame_ids) != text_ids):
        _fail('evidence_insufficient', 'All and only source-cited frame IDs must be supplied')
    if (not isinstance(context_frame_ids, (list, tuple))
            or len(context_frame_ids) != len(set(context_frame_ids))
            or set(context_frame_ids) & set(frame_ids)
            or len(context_frame_ids) + len(frame_ids) > 12):
        _fail('evidence_insufficient', 'Observation review context must fit twelve distinct real frames')
    frames = _frames(case, set(frame_ids) | set(context_frame_ids))
    view = fact_versions.current_facts(case)
    originals = [case/'state/behavior_input.request.json', case/'state/actor_registry.json',
                 case/'state/global_frames.json', case/'manifest.json']
    if collection == 'limitations':
        originals.append(case/'state/behavior.candidate.json')
    if any(not path.is_file() for path in originals):
        _fail('binding_missing', 'Observation review lacks its original Case inputs')
    body = dict(revision=OBSERVATION_REVISION, case_id=case.name,
        source_path=str(source), source_pointer=source_pointer, source_sha256=source_sha256,
        source_value=copy.deepcopy(item), source_value_sha256=digest(item),
        source_frames=frames, observation_frame_ids=sorted(frame_ids),
        context_frame_ids=sorted(context_frame_ids), parent_version=view.version_id,
        parent_manifest_sha256=sha256(view.directory/'manifest.json'),
        world_path=str(view.world_path), world_sha256=sha256(view.world_path),
        input_hashes={str(path): sha256(path) for path in originals})
    packet = dict(body, request_sha256=digest(body))
    path = case/ROOT/f'observation-{packet["request_sha256"][:24]}.request.json'
    if path.is_file() and read_json(path) != packet:
        _fail('fact_conflict', 'Source observation request changed')
    if not path.is_file():
        atomic_json(path, packet)
    return path


def verify_source_observation(case, request_path):
    case = Path(case).resolve()
    path = Path(request_path).resolve()
    if (not path.is_relative_to((case/ROOT).resolve())
            or not path.name.startswith('observation-') or not path.name.endswith('.request.json')):
        _fail('model_out_of_scope', 'Source observation request is outside this Case')
    request = read_json(path)
    body = dict(request); signature = body.pop('request_sha256', None)
    if request.get('revision') != OBSERVATION_REVISION or digest(body) != signature or request['case_id'] != case.name:
        _fail('fact_conflict', 'Source observation request binding changed')
    source = Path(request['source_path']).resolve()
    if (not source.is_relative_to(case) or not source.is_file()
            or sha256(source) != request['source_sha256']):
        _fail('stale_version', 'Source observation changed')
    pointer = request['source_pointer']
    if not isinstance(pointer, str) or not re.fullmatch(
            r'/(?:semantic_limitations|limitations)/(0|[1-9]\d*)', pointer):
        _fail('binding_missing', 'Observation source pointer changed')
    if (source, pointer.split('/')[1]) not in {
            (case/'state/behavior.candidate.json', 'semantic_limitations'),
            (case/'final/analysis.json', 'limitations')}:
        _fail('binding_missing', 'Observation source contract changed')
    rows = read_json(source).get(pointer.split('/')[1], [])
    index = int(pointer.rsplit('/', 1)[1])
    if not isinstance(rows, list) or index >= len(rows):
        _fail('binding_missing', 'Observation source pointer changed')
    item = rows[index]
    if item != request['source_value'] or digest(item) != request['source_value_sha256']:
        _fail('stale_version', 'Source observation pointer changed')
    if pointer.split('/')[1] == 'limitations':
        behavior_path = case/'state/behavior.candidate.json'
        if (request['input_hashes'].get(str(behavior_path)) != sha256(behavior_path)
                or sum(original == item for original in
                       read_json(behavior_path).get('semantic_limitations', [])) != 1):
            _fail('binding_missing', 'Analysis limitation lost its Behavior source')
    ids = _cited_frame_ids(item.get('description'))
    context = request.get('context_frame_ids')
    if (not ids or ids != set(request.get('observation_frame_ids', []))
            or not isinstance(context, list) or len(context) != len(set(context))
            or ids & set(context) or len(ids) + len(context) > 12
            or ids | set(context) != {row.get('frame_id') for row in request['source_frames']}
            or _frames(case, ids | set(context)) != request['source_frames']):
        _fail('binding_missing', 'Observation original-frame binding changed')
    view = fact_versions.current_facts(case)
    if (view.version_id != request['parent_version']
            or sha256(view.directory/'manifest.json') != request['parent_manifest_sha256']
            or Path(request['world_path']).resolve() != view.world_path.resolve()
            or sha256(view.world_path) != request['world_sha256']):
        _fail('stale_version', 'World fact version changed')
    for name, expected in request['input_hashes'].items():
        if not Path(name).is_file() or sha256(name) != expected:
            _fail('stale_version', 'Observation review input changed')
    for frame in request['source_frames']:
        image = Path(frame['path']).resolve()
        if not image.is_relative_to(case) or not image.is_file() or sha256(image) != frame['sha256']:
            _fail('stale_version', 'Observation original frame changed')
    return request


def source_observation_prompt(request_path, candidate_path):
    """Use the existing World owner for visual facts only."""
    return (
        f'你是本 Case 的 World 定向视觉裁决者。只读取冻结请求 {request_path} '
        '及其中哈希绑定的 World、Behavior 来源和原帧。不要创建 observation_id、'
        'question_id、Actor、轨迹或运动事实。逐帧判断是否为真实车辆、是否与已有车辆同一物体、'
        '对象是否独立，以及它与 Ego 核心路径的物理关系；遮挡和远小目标须保留不确定。'
        '动态交互与运动证据只说明是否需要 Behavior 复核，不替 Behavior 下结论。'
        f'输出 JSON 到 {candidate_path}，字段为 request_sha256、vehicle_status '
        '(vehicle|not_vehicle|uncertain)、identity_status '
        '(existing|distinct|uncertain|not_applicable)、existing_actor_id (或 null)、'
        'ego_path_relation (separated|potential_interaction|uncertain)、'
        'motion_evidence (sufficient|insufficient|uncertain)、'
        'interaction_evidence (sufficient|insufficient|uncertain)、'
        'physical_boundary_id (或 null)、time_range_s、evidence；'
        'evidence 对每张 source_frames 原帧给出 frame_id、time_s、status=observed、reason。'
        '证据不足时明确 uncertain，不推断未观察到的轨迹。')


def commit_source_observation(case, request_path, candidate_path):
    """Accept a source-bound World answer as a supplemental receipt only."""
    case = Path(case).resolve()
    request = verify_source_observation(case, request_path)
    candidate_path = Path(candidate_path).resolve()
    if not candidate_path.is_relative_to((case/ROOT).resolve()):
        _fail('model_out_of_scope', 'World observation answer is outside this Case')
    answer = read_json(candidate_path)
    fields = {'request_sha256', 'vehicle_status', 'identity_status', 'existing_actor_id',
              'ego_path_relation', 'motion_evidence', 'interaction_evidence',
              'physical_boundary_id', 'time_range_s', 'evidence'}
    if (not isinstance(answer, dict) or set(answer) != fields
            or answer['request_sha256'] != request['request_sha256']
            or answer['vehicle_status'] not in {'vehicle', 'not_vehicle', 'uncertain'}
            or answer['identity_status'] not in {'existing', 'distinct', 'uncertain', 'not_applicable'}
            or answer['ego_path_relation'] not in {'separated', 'potential_interaction', 'uncertain'}
            or answer['motion_evidence'] not in {'sufficient', 'insufficient', 'uncertain'}
            or answer['interaction_evidence'] not in {'sufficient', 'insufficient', 'uncertain'}):
        _fail('model_out_of_scope', 'World observation answer is malformed or unbound')
    world = read_json(request['world_path'])
    actor_ids = {row['actor_id'] for row in world.get('actor_hypotheses', [])}
    if ((answer['identity_status'] == 'existing') != (answer['existing_actor_id'] in actor_ids)
            or answer['identity_status'] != 'existing' and answer['existing_actor_id'] is not None
            or answer['vehicle_status'] == 'not_vehicle' and answer['identity_status'] != 'not_applicable'
            or answer['vehicle_status'] != 'vehicle' and answer['identity_status'] in {'existing', 'distinct'}):
        _fail('binding_missing', 'Object identity contradicts the active World registry')
    source = {row['frame_id']: row for row in request['source_frames']}
    evidence = answer['evidence']
    if (not isinstance(evidence, list) or len(evidence) != len(source)
            or {row.get('frame_id') for row in evidence if isinstance(row, dict)} != set(source)
            or any(not isinstance(row, dict) or set(row) != {'frame_id', 'time_s', 'status', 'reason'}
                   or row['status'] != 'observed' or row['time_s'] != source[row['frame_id']]['time_s']
                   or not isinstance(row['reason'], str) or not row['reason'].strip()
                   for row in evidence)):
        _fail('evidence_insufficient', 'World answer must cite every frozen original frame')
    window = answer['time_range_s']
    if (not isinstance(window, list) or len(window) != 2
            or any(type(t) not in (int, float) or not math.isfinite(t) for t in window)
            or window[0] > window[1]
            or window[0] < min(row['time_s'] for row in source.values())
            or window[1] > max(row['time_s'] for row in source.values())):
        _fail('evidence_insufficient', 'World answer has no bounded source-frame window')
    disposition = 'needs_semantic_review'
    if answer['vehicle_status'] == 'not_vehicle':
        disposition = 'not_vehicle'
    elif answer['vehicle_status'] == 'vehicle' and answer['ego_path_relation'] == 'separated':
        boundary = answer['physical_boundary_id']
        from _lib.world.readiness import ego_carriageway_execution_scope
        if (boundary != 'world.road.roads[0].road_geometry.opposing_separation'
                or not (ego_carriageway_execution_scope(world, read_json(request['source_path'])) or {})
                    .get('opposing_physically_separated')):
            _fail('binding_missing', 'Separated path lacks the existing World/Behavior isolation proof')
        behavior = read_json(request['source_path'])
        ego_events = [row.get('time_range_s') for row in behavior.get('events', [])
                      if 'ego' in row.get('involved_actor_ids', [])]
        if (any(not isinstance(times, list) or len(times) != 2
                       or window[0] > times[0] or window[1] < times[1]
                       for times in ego_events)):
            _fail('evidence_insufficient', 'Path separation lacks Ego event or interaction coverage')
        disposition = 'physically_separated'
    result_path = Path(request_path).with_name(Path(request_path).name.replace('.request.json', '.result.json'))
    result = dict(revision=OBSERVATION_REVISION, request_sha256=request['request_sha256'],
        candidate_path=str(candidate_path), candidate_sha256=sha256(candidate_path),
        parent_version=request['parent_version'], disposition=disposition,
        status='reviewed' if disposition != 'needs_semantic_review' else 'needs_review')
    if result_path.is_file() and read_json(result_path) != result:
        _fail('fact_conflict', 'World observation result changed')
    if not result_path.is_file():
        atomic_json(result_path, result)
    return result


def run_source_observation(runner, case, request_path):
    """Dispatch via the existing World closure task owner when authorized."""
    case = Path(case).resolve()
    verify_source_observation(case, request_path)
    request_path = Path(request_path).resolve()
    candidate_path = request_path.with_name(
        request_path.name.replace('.request.json', '.candidate.json'))
    result_path = request_path.with_name(
        request_path.name.replace('.request.json', '.result.json'))
    if result_path.is_file():
        return commit_source_observation(case, request_path, candidate_path)
    def adopt(_session=None):
        return commit_source_observation(case, request_path, candidate_path)
    from _lib.runtime.world_adjudication import ACTIVE as CENSUS_ACTIVE
    if (case/CENSUS_ACTIVE).is_file():
        _fail('model_out_of_scope', 'Finish active Census continuation before observation review')
    previous = getattr(runner, '_reconciliation_point_task', None)
    runner._reconciliation_point_task = True
    try:
        runner.model_stage(case, 'world_closure', 'global-video-understanding',
                           source_observation_prompt(request_path, candidate_path),
                           adopt, adopt if candidate_path.is_file() else None,
                           candidate_path=candidate_path)
    finally:
        if previous is None:
            del runner._reconciliation_point_task
        else:
            runner._reconciliation_point_task = previous
    if not result_path.is_file():
        _fail('evidence_insufficient', 'World observation task has no accepted answer')
    return commit_source_observation(case, request_path, candidate_path)


def apply_source_observation_reviews(case, behavior):
    """Remove only a World-refuted, unbound object claim in a private copy."""
    case = Path(case).resolve()
    source_path = case/'state/behavior.candidate.json'
    if not source_path.is_file() or not isinstance(behavior, dict):
        return behavior, []
    rows = behavior.get('semantic_limitations')
    if not isinstance(rows, list):
        return behavior, []
    accepted = {}
    for result_path in sorted((case/ROOT).glob('observation-*.result.json')):
        request_path = result_path.with_name(result_path.name.replace('.result.json', '.request.json'))
        request = read_json(request_path)
        if request.get('source_path') != str(source_path) or request.get('source_sha256') != sha256(source_path):
            continue
        result = commit_source_observation(case, request_path,
            result_path.with_name(result_path.name.replace('.result.json', '.candidate.json')))
        if result['status'] != 'reviewed' or result['disposition'] not in {
                'not_vehicle', 'physically_separated'}:
            continue
        index = int(request['source_pointer'].rsplit('/', 1)[1])
        if (index >= len(rows) or rows[index] != request['source_value']
                or any(rows[index].get(key) for key in ('actor_ids', 'actor_id',
                    'affected_actor_ids', 'affected_event_ids', 'census_issue_ids',
                    'world_paths', 'related_world_paths', 'scope_binding'))
                or index in accepted):
            _fail('fact_conflict', 'World source review cannot remove a bound or changed limitation')
        accepted[index] = dict(request_sha256=request['request_sha256'],
            result_sha256=sha256(result_path), disposition=result['disposition'])
    if not accepted:
        return behavior, []
    projected = copy.deepcopy(behavior)
    projected['semantic_limitations'] = [row for index, row in enumerate(rows)
                                          if index not in accepted]
    audit = dict(rule_id='behavior.world_source_observation.v1',
        action='remove_world_refuted_unbound_observation_from_execution_copy',
        before_sha256=digest(behavior), after_sha256=digest(projected),
        decisions=[dict(source_pointer=f'/semantic_limitations/{index}', **accepted[index])
                   for index in sorted(accepted)])
    return projected, [audit]


def prepare(case, issue_id, *, review_id=None, extra_frame_ids=(), scope_event_id=None):
    """Freeze a read-only review against exact World bytes and original frames."""
    case = Path(case).resolve()
    view = fact_versions.current_facts(case)
    item = _question(view.world, issue_id)
    if item.get('resolution') not in ('unresolved', 'resolved') or item.get('owner') == 'behavior':
        _fail('model_out_of_scope', 'Only a World-owned question may be reviewed')
    point = point_review.inspected_points(view.world)[0].get(issue_id)
    ids = {e['frame_id'] for e in item.get('evidence', []) if e.get('frame_id')}
    ids.update(extra_frame_ids)
    if point:
        ids.add(point['frame_id'])
    if not ids:
        _fail('evidence_insufficient', 'Review needs at least one original source frame')
    frames = _frames(case, ids)
    if point is not None and next(row['time_s'] for row in frames
                                  if row['frame_id'] == point['frame_id']) != point['time_s']:
        _fail('binding_missing', 'Target frame time differs from the original source frame')
    review_id = review_id or uuid.uuid4().hex
    if not isinstance(review_id, str) or not review_id or not review_id.replace('-', '').isalnum():
        _fail('binding_missing', 'Review ID must be a simple stable identifier')
    originals = [case/'state/world_context.json', case/'state/actor_registry.json',
                 case/'state/reconciliation.json', case/'state/global_frames.json']
    originals += [path for path in (case/'case.json', case/'manifest.json',
                  case/'state/execution_policy.json', case/'state/probe.json',
                  case/'state/binding.json') if path.is_file()]
    originals += sorted((case/'batches').glob('batch_*.json'))
    scope_context = None
    if scope_event_id is not None:
        if item.get('impact_domains') != ['actor_lane'] or len(item.get('actor_ids', [])) != 1:
            _fail('model_out_of_scope', 'Core path review needs one World-owned Actor lane question')
        behavior_path = case/'state/behavior.candidate.json'
        if not behavior_path.is_file():
            _fail('binding_missing', 'Core path review needs the existing Behavior candidate')
        behavior = read_json(behavior_path)
        events = [row for row in behavior.get('events', [])
                  if row.get('event_id') == scope_event_id]
        if len(events) != 1 or 'ego' not in events[0].get('involved_actor_ids', []):
            _fail('binding_missing', 'Core path review needs one existing Ego event')
        scope_context = dict(actor_id=item['actor_ids'][0], event=events[0],
                             ego=next((row for row in behavior.get('actors', [])
                                       if row.get('actor_id') == 'ego'), None),
                             primitives=[row for row in behavior.get('executor_primitives', [])
                                         if row.get('event_id') == scope_event_id],
                             behavior_path=str(behavior_path),
                             behavior_sha256=sha256(behavior_path))
        if scope_context['ego'] is None:
            _fail('binding_missing', 'Core path review lacks the original Ego track')
        originals.append(behavior_path)
    census = fact_versions.census_path(case)
    if census.is_file():
        originals.append(census)
    body = dict(revision=REVISION, review_id=review_id, case_id=case.name,
        issue_id=issue_id, parent_version=view.version_id,
        parent_manifest_sha256=sha256(view.directory/'manifest.json'),
        world_path=str(view.world_path), world_sha256=sha256(view.world_path),
        question=copy.deepcopy(item), issue_sha256=digest(item),
        input_hashes={str(path): sha256(path) for path in originals},
        source_frames=frames, target_frame=point,
        permitted_actor_ids=sorted({'ego', *(a['actor_id'] for a in view.world['actor_hypotheses'])}))
    if scope_context is not None:
        body['scope_context'] = scope_context
        body['scope_instruction'] = (
            'Review the original frames and source World/Batch/Behavior. Keep the '
            'World question outcome unresolved and the exact Actor lane unknown. '
            'Add scope_relation with actor_id, event_id, road_id, boundary_id, '
            'occupancy_side, occupancy_description, occupancy_frame_ids, occlusion, '
            'time_range_s, evidence_frame_ids, interaction_reason, and outcome '
            '(path_separated, potential_interaction, or evidence_insufficient). '
            'Cite every requested frame in decision.evidence with its time and visual basis. '
            'Choose path_separated only when positive visual occupancy and road-boundary '
            'evidence supports separation throughout the Ego event window; later '
            'nonappearance alone does not prove it. If occlusion permits interaction '
            'or coverage is insufficient, return the corresponding nonpositive outcome.')
    request = dict(body, request_sha256=digest(body))
    path = case/ROOT/(review_id+'.request.json')
    if path.is_file() and read_json(path) != request:
        _fail('fact_conflict', 'Review ID already binds a different request')
    if not path.is_file():
        atomic_json(path, request)
    return path


def _verified_request(case, request_path):
    case = Path(case).resolve()
    path = Path(request_path).resolve()
    if not path.is_relative_to((case/ROOT).resolve()) or not path.name.endswith('.request.json'):
        _fail('model_out_of_scope', 'Review request is outside this Case')
    request = read_json(path)
    body = dict(request); signature = body.pop('request_sha256', None)
    if request.get('revision') != REVISION or signature != digest(body):
        _fail('fact_conflict', 'Review request hash changed')
    if request['case_id'] != case.name:
        _fail('model_out_of_scope', 'Review request belongs to another Case')
    world_path = Path(request['world_path']).resolve()
    if (not world_path.is_relative_to((case/fact_versions.ROOT).resolve())
            or not world_path.is_file() or sha256(world_path) != request['world_sha256']
            or digest(request['question']) != request['issue_sha256']):
        _fail('stale_version', 'Frozen World question or fact file changed')
    for name, expected in request['input_hashes'].items():
        if not Path(name).is_file() or sha256(name) != expected:
            _fail('stale_version', 'Review input changed: ' + name)
    for row in request['source_frames']:
        source = Path(row['path']).resolve()
        if (not source.is_relative_to(case) or not source.is_file()
                or sha256(source) != row['sha256']):
            _fail('stale_version', 'Original review frame changed')
    context = request.get('scope_context')
    if context is not None:
        source = Path(context['behavior_path']).resolve()
        if (not source.is_relative_to(case) or not source.is_file()
                or sha256(source) != context['behavior_sha256']):
            _fail('stale_version', 'Core path Behavior candidate changed')
        behavior = read_json(source)
        if (context['event'] not in behavior.get('events', [])
                or context['ego'] not in behavior.get('actors', [])
                or context['primitives'] != [row for row in behavior.get('executor_primitives', [])
                                             if row.get('event_id') == context['event']['event_id']]):
            _fail('stale_version', 'Core path event or Ego track changed')
    return request


def _decision(request, decision):
    if not isinstance(decision, dict):
        _fail('model_out_of_scope', 'Review decision must be an object')
    body = dict(decision); signature = body.pop('decision_sha256', None)
    if signature != digest(body) or body.get('request_sha256') != request['request_sha256']:
        _fail('fact_conflict', 'Decision hash or request binding changed')
    if body.get('issue_id') != request['issue_id'] or body.get('outcome') not in {
            'binding', 'resolved', 'unresolved', 'evidence_insufficient'}:
        _fail('model_out_of_scope', 'Decision addresses another question or outcome')
    if not isinstance(body.get('review_reason'), str) or not body['review_reason'].strip():
        _fail('evidence_insufficient', 'World reviewer must record the evidence basis')
    if request.get('scope_context') is not None:
        _scope_relation(request, body)
    elif 'scope_relation' in body:
        _fail('model_out_of_scope', 'Core path relation needs a frozen scope request')
    return body, signature


def _scope_relation(request, decision):
    """Check a World visual verdict and its bindings, without inferring the verdict."""
    relation = decision.get('scope_relation')
    context = request['scope_context']
    required = {'actor_id', 'event_id', 'outcome', 'road_id', 'boundary_id',
                'occupancy_side', 'occupancy_description', 'occupancy_frame_ids',
                'occlusion', 'time_range_s', 'evidence_frame_ids', 'interaction_reason'}
    if (decision['outcome'] != 'unresolved' or not isinstance(relation, dict)
            or set(relation) != required or relation['actor_id'] != context['actor_id']
            or relation['event_id'] != context['event']['event_id']
            or relation['outcome'] not in {'path_separated', 'potential_interaction',
                                          'evidence_insufficient'}
            or relation['occupancy_side'] not in {'left', 'right', 'straddling', 'unknown'}
            or relation['occlusion'] not in {'clear', 'potential', 'unknown'}
            or any(not isinstance(relation[k], str) or not relation[k].strip()
                   for k in ('road_id', 'boundary_id', 'occupancy_description',
                             'interaction_reason'))):
        _fail('model_out_of_scope', 'World core path answer is malformed or changes the lane finding')
    source = {row['frame_id']: row for row in request['source_frames']}
    ids = relation['evidence_frame_ids']
    occupied = relation['occupancy_frame_ids']
    evidence = decision.get('evidence')
    if (not isinstance(ids, list) or len(ids) != len(source) or set(ids) != set(source)
            or not isinstance(occupied, list) or not occupied or len(occupied) != len(set(occupied))
            or not set(occupied) <= set(ids) or not isinstance(evidence, list)
            or {row.get('frame_id') for row in evidence if isinstance(row, dict)} != set(ids)
            or len(evidence) != len(ids)
            or any(not isinstance(row, dict) or row.get('status') != 'observed'
                   or row.get('frame_id') not in source
                   or row.get('time_s') != source[row['frame_id']]['time_s']
                   or not isinstance(row.get('reason'), str) or not row['reason'].strip()
                   for row in evidence)):
        _fail('evidence_insufficient', 'Core path answer lacks complete original-frame references')
    window = relation['time_range_s']
    event_window = context['event'].get('time_range_s')
    if (not isinstance(window, list) or len(window) != 2
            or any(type(t) not in (int, float) or not math.isfinite(t) for t in window)
            or window[0] > window[1] or window[0] < min(row['time_s'] for row in source.values())
            or window[1] > max(row['time_s'] for row in source.values())
            or not isinstance(event_window, list) or len(event_window) != 2):
        _fail('evidence_insufficient', 'Core path answer has no bounded observed time window')
    if relation['outcome'] != 'path_separated':
        return relation
    if (window[0] > event_window[0] or window[1] < event_window[1]
            or relation['occlusion'] != 'clear'
            or relation['occupancy_side'] not in {'left', 'right'}
            or not any(source[fid]['time_s'] <= event_window[0] for fid in ids)
            or not any(source[fid]['time_s'] >= event_window[1] for fid in ids)):
        _fail('evidence_insufficient', 'Positive core path answer lacks event-window or occlusion coverage')
    world = read_json(request['world_path'])
    actor = next((row for row in world['actor_hypotheses']
                  if row['actor_id'] == context['actor_id']), None)
    if actor is None or actor.get('hypothesized_lane') is not None:
        _fail('fact_conflict', 'Relative scope review cannot replace a known Actor lane')
    road = world['world']['road']
    lanes = {row['lane_id']: row for row in road.get('lanes', [])}
    primitive = [row for row in context['primitives'] if row.get('type') == 'lane_change'
                 and row.get('actor_id') == 'ego']
    if len(primitive) != 1 or primitive[0].get('time_range_s') != event_window:
        _fail('binding_missing', 'World core path answer needs the existing Ego lane-change primitive')
    start = lanes.get(primitive[0].get('from_lane_id'))
    end = lanes.get(primitive[0].get('lane_id'))
    if (not start or not end or start.get('road_id') != end.get('road_id')
            or relation['road_id'] != start['road_id']
            or start.get('travel_direction') != 'same_as_ego'
            or end.get('travel_direction') != 'same_as_ego'):
        _fail('fact_conflict', 'Core path relation disagrees with the accepted road and Ego event')
    side = relation['occupancy_side']
    boundary = start.get(side+'_boundary')
    if (relation['boundary_id'] != boundary
            or not any(mark.get('marking_id') == boundary and mark.get('road_id') == start['road_id']
                       for mark in road.get('lane_markings', []))
            or (end['ordinal_from_right'] - start['ordinal_from_right'])
               * (1 if side == 'right' else -1) <= 0):
        _fail('fact_conflict', 'World boundary or Ego motion conflicts with the visual verdict')
    candidate = read_json(context['behavior_path'])
    subject = next((row for row in candidate.get('actors', [])
                    if row.get('actor_id') == context['actor_id']), None)
    if subject is None or not set(occupied) <= {
            row.get('frame_id') for row in subject.get('trajectory', [])}:
        _fail('evidence_insufficient', 'Positive occupancy lacks a visible Actor source frame')
    return relation


def _revised_world(world, request, decision):
    item = _question(world, request['issue_id'])
    if digest(item) != request['issue_sha256']:
        _fail('stale_version', 'Question changed since review preparation')
    if decision['outcome'] == 'evidence_insufficient':
        return None
    if item['resolution'] == 'resolved' and decision['outcome'] != 'unresolved':
        _fail('model_out_of_scope', 'A closed question can only be returned to unresolved')
    actors = decision.get('actor_ids', item.get('actor_ids', []))
    paths = decision.get('world_paths', item.get('world_paths', []))
    if (not isinstance(actors, list) or len(actors) != len(set(actors))
            or not set(actors) <= set(request['permitted_actor_ids'])
            or not isinstance(paths, list) or len(paths) != len(set(paths))):
        _fail('binding_missing', 'Revised Actor or fact-path binding is invalid')
    domains = item.get('impact_domains', [])
    if domains == ['actor_lane'] and len(actors) > 1:
        _fail('binding_missing', 'Atomic lane review cannot bind multiple Actors')
    for path in decision.get('world_paths', []):
        try:
            world_conflict_value(world, path)
        except (ContractError, KeyError, IndexError, TypeError, ValueError):
            _fail('binding_missing', 'Fact path is not an existing World value')
        candidates = actors or request['permitted_actor_ids']
        if domains == ['actor_lane'] and not any(
                actor_lane_path(world, actor_id, path) for actor_id in candidates):
            _fail('binding_missing', 'Lane fact path does not identify the reviewed Actor')
    source = {r['frame_id']: r for r in request['source_frames']}
    evidence = decision.get('evidence', [])
    if not isinstance(evidence, list):
        _fail('evidence_insufficient', 'Review evidence must be an array')
    for row in evidence:
        if (not isinstance(row, dict) or row.get('status') != 'observed'
                or row.get('frame_id') not in source
                or row.get('time_s') != source[row['frame_id']]['time_s']
                or not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            _fail('evidence_insufficient', 'Review evidence must cite frozen original frames')
    revised = copy.deepcopy(world)
    target = _question(revised, request['issue_id'])
    if set(target.get('actor_ids', [])) != set(actors):
        target['actor_ids'] = actors
    if set(target.get('world_paths', [])) != set(paths):
        target['world_paths'] = paths
    for row in evidence:
        if row not in target['evidence']:
            target['evidence'].append(copy.deepcopy(row))
    frame_id = decision.get('target_frame_id')
    if frame_id is not None:
        if domains != ['actor_lane'] or frame_id not in source:
            _fail('binding_missing', 'Lane target must be one frozen original frame')
        if any(request['issue_id'] in interval.get('question_ids', []) and
               interval.get('end_s', 0) > interval.get('start_s', 0)
               for interval in world.get('critical_intervals', [])):
            _fail('unsupported_temporal_scope', 'An interval claim cannot be reduced to one frame')
        if not any(e['frame_id'] == frame_id for e in evidence):
            _fail('evidence_insufficient', 'Target revision needs a new target-frame observation')
        point_review.store(revised, target, source[frame_id]['time_s'],
                           [dict(frame_id=frame_id, time_s=source[frame_id]['time_s'])])
    elif request['target_frame'] is not None:
        frame_id = request['target_frame']['frame_id']
    if decision['outcome'] == 'resolved':
        if not actors or not paths or (domains == ['actor_lane'] and frame_id is None):
            _fail('binding_missing', 'Resolution needs complete Actor, fact and time bindings')
        if domains == ['actor_lane'] and any(not actor_lane_path(world, actors[0], path) for path in paths):
            _fail('binding_missing', 'Lane fact path does not identify the reviewed Actor')
        if len(domains) != 1:
            _fail('evidence_insufficient', 'Composite question needs separate fact-level review')
        if domains == ['actor_lane']:
            if not any(e['frame_id'] == frame_id for e in evidence):
                _fail('evidence_insufficient', 'Resolved lane needs a new target-frame observation')
            lanes = {lane_id_for_path(world, path) for path in paths}
            if len(lanes) != 1 or None in lanes or decision.get('lane_id') not in lanes:
                _fail('fact_conflict', 'Reviewed lane differs from current structured World fact')
        facts = decision.get('fact_values')
        if not isinstance(facts, dict) or set(facts) != set(paths):
            _fail('binding_missing', 'Resolution must name every exact target fact')
        if any(world_conflict_value(world, path) != facts[path] for path in paths):
            _fail('fact_conflict', 'Resolution differs from the active World fact values')
        if not evidence:
            _fail('evidence_insufficient', 'Resolution needs fresh observed frame evidence')
        target['resolution'] = 'resolved'
    elif decision['outcome'] == 'unresolved':
        target['resolution'] = 'unresolved'
    if revised != world and not evidence:
        _fail('evidence_insufficient', 'A changed binding or status needs a new original-frame observation')
    contract = next(row for row in question_contracts(revised, list(source.values()))
                    if row['collection'] == 'questions_to_verify' and row['issue_id'] == request['issue_id'])
    if target['resolution'] == 'resolved' and set(contract['diagnostics']) & {
            'binding_missing', 'unsupported_temporal_scope', 'evidence_insufficient',
            'fact_conflict', 'undetermined'}:
        _fail('evidence_insufficient', 'Resolved question does not satisfy its current fact contract')
    if decision['outcome'] == 'resolved':
        for comparison in resolved_lane_comparisons(revised, list(source.values())):
            if request['issue_id'] in comparison['issue_ids'] and comparison['verdict'] != 'consistent':
                _fail(comparison['verdict'], 'Resolved lane conclusion disagrees or cannot be compared')
    admission.validate(revised, case=None)
    return revised


def _published(case, request_sha256):
    """Find verified published attempts, including a crash before pointer switch."""
    root = Path(case)/fact_versions.ROOT
    for directory in sorted(root.glob('v*')):
        manifest = directory/'manifest.json'
        if not manifest.is_file():
            continue
        view = fact_versions._read(case, dict(version_id=directory.name,
                                             manifest_sha256=sha256(manifest)))
        for row in view.revisions:
            if (row.get('kind') == REVISION and row.get('version_id') == view.version_id
                    and row.get('request_sha256') == request_sha256):
                yield view, row


def _mark_revalidation_pending(case, parent_version, version_id):
    path = Path(case)/'state/progress.json'
    if not path.is_file():
        return
    progress = read_json(path)
    diagnostics = progress.setdefault('diagnostics', {})
    diagnostics['fact_revision'] = dict(status='committed', version_id=version_id,
                                        parent_version_id=parent_version)
    diagnostics['behavior_revalidation'] = dict(status='pending', validation_version_id=version_id)
    diagnostics['target_delivery'] = dict(status='pending', validation_version_id=version_id)
    if progress.get('phase') == 'done':
        progress.update(phase='failed', status='needs_review',
            error=dict(code='fact_revision_requires_revalidation',
                       message='Effective facts changed; Behavior and Target need revalidation',
                       resume_from='aggregating'))
    atomic_json(path, progress)


def commit(case, request_path, decision_path, *, fault_at=None):
    """Publish only a proved World change; retry every publication boundary."""
    case = Path(case).resolve()
    request = _verified_request(case, request_path)
    decision_path = Path(decision_path).resolve()
    if not decision_path.is_relative_to((case/ROOT).resolve()):
        _fail('model_out_of_scope', 'Review decision is outside this Case')
    decision, decision_sha256 = _decision(request, read_json(decision_path))
    request_name = Path(request_path).name
    result_path = case/ROOT/(request_name.removesuffix('.request.json')+'.result.json')
    with fact_versions._lock(case):
        if _verified_request(case, request_path) != request:
            _fail('stale_version', 'Review inputs changed before publication')
        if _decision(request, read_json(decision_path)) != (decision, decision_sha256):
            _fail('fact_conflict', 'Review decision changed before publication')
        active = fact_versions.current_facts(case)
        if result_path.is_file():
            result = read_json(result_path)
            if (result.get('request_sha256') != request['request_sha256'] or
                    result.get('decision_sha256') != decision_sha256 or
                    result.get('decision_file_sha256') != sha256(decision_path)):
                _fail('fact_conflict', 'Same review request has a different decision')
            if result['status'] == 'committed':
                matches = list(_published(case, request['request_sha256']))
                if (len(matches) != 1 or matches[0][0].version_id != result['effective_version']
                        or matches[0][1]['decision_sha256'] != decision_sha256):
                    _fail('fact_conflict', 'Saved review result does not match a verified version')
                if active.version_id != result['effective_version']:
                    _fail('stale_version', 'Review result is no longer the active fact version')
            elif (active.version_id != request['parent_version'] or
                  (result['status'] == 'scope_reviewed' and
                   sha256(active.directory/'manifest.json') != request['parent_manifest_sha256'])):
                _fail('stale_version', 'Unchanged review belongs to an older fact version')
            return result
        if request.get('scope_context') is not None:
            if (active.version_id != request['parent_version'] or
                    sha256(active.directory/'manifest.json') != request['parent_manifest_sha256']):
                _fail('stale_version', 'Active fact version differs from review parent')
            # This World-owner answer is supplemental: it does not fill the
            # unknown lane or publish a new fact version.
            result = dict(revision=REVISION, review_id=request['review_id'],
                request_sha256=request['request_sha256'], decision_sha256=decision_sha256,
                decision_path=str(decision_path), decision_file_sha256=sha256(decision_path),
                parent_version=active.version_id, effective_version=active.version_id,
                status='scope_reviewed', input_hashes=request['input_hashes'])
            atomic_json(result_path, result)
            return result
        previous = list(_published(case, request['request_sha256']))
        if previous:
            if (len(previous) != 1 or previous[0][1]['decision_sha256'] != decision_sha256
                    or previous[0][1]['decision_file_sha256'] != sha256(decision_path)
                    or previous[0][1]['parent_version'] != request['parent_version']
                    or previous[0][1]['before_world_sha256'] != request['world_sha256']):
                _fail('fact_conflict', 'Published review has a different decision')
            view, _ = previous[0]
            if active.version_id not in {request['parent_version'], view.version_id}:
                _fail('stale_version', 'Published review is no longer the active fact version')
            if active.version_id == request['parent_version']:
                atomic_json(case/fact_versions.POINTER, dict(revision=fact_versions.REVISION,
                    version_id=view.version_id, manifest_sha256=sha256(view.directory/'manifest.json')))
            _mark_revalidation_pending(case, request['parent_version'], view.version_id)
            result = dict(revision=REVISION, review_id=request['review_id'],
                request_sha256=request['request_sha256'], decision_sha256=decision_sha256,
                decision_path=str(decision_path), decision_file_sha256=sha256(decision_path),
                parent_version=request['parent_version'], effective_version=view.version_id,
                status='committed', input_hashes=request['input_hashes'])
            atomic_json(result_path, result)
            return result
        if active.version_id != request['parent_version'] or \
                sha256(active.directory/'manifest.json') != request['parent_manifest_sha256']:
            _fail('stale_version', 'Active fact version differs from review parent')
        revised = _revised_world(active.world, request, decision)
        if revised is None or revised == active.world:
            result = dict(revision=REVISION, review_id=request['review_id'],
                request_sha256=request['request_sha256'], decision_sha256=decision_sha256,
                decision_path=str(decision_path), decision_file_sha256=sha256(decision_path),
                parent_version=active.version_id, effective_version=active.version_id,
                status='needs_review', diagnosis='evidence_insufficient',
                input_hashes=request['input_hashes'])
            atomic_json(result_path, result)
            return result
        number = int(active.version_id[1:].split('-', 1)[0]) + 1
        version_id = f'v{number:06d}-{uuid.uuid4().hex[:12]}'
        history = [*active.revisions, dict(kind=REVISION,
            version_id=version_id,
            request_sha256=request['request_sha256'], decision_sha256=decision_sha256,
            parent_version=active.version_id, issue_id=request['issue_id'],
            before_world_sha256=sha256(active.world_path),
            after_world_digest=digest(revised), source_frames=request['source_frames'],
            outcome=decision['outcome'], decision_path=str(decision_path),
            decision_file_sha256=sha256(decision_path), decision=decision)]
        files = {name: (active.directory/name).read_bytes() for name in fact_versions.FILES}
        files['world_context.json'] = revised
        files['revisions.json'] = history
        if fault_at == 'before_publish':
            raise fact_versions.FactVersionError('Injected interruption before question publish')
        manifest_sha256 = fact_versions._publish(case, version_id, files)
        if fault_at == 'before_pointer':
            raise fact_versions.FactVersionError('Injected interruption before question pointer')
        atomic_json(case/fact_versions.POINTER, dict(revision=fact_versions.REVISION,
            version_id=version_id, manifest_sha256=manifest_sha256))
        if fault_at == 'after_pointer':
            raise fact_versions.FactVersionError('Injected interruption after question pointer')
        _mark_revalidation_pending(case, active.version_id, version_id)
        result = dict(revision=REVISION, review_id=request['review_id'],
            request_sha256=request['request_sha256'], decision_sha256=decision_sha256,
            decision_path=str(decision_path), decision_file_sha256=sha256(decision_path),
            parent_version=active.version_id, effective_version=version_id,
            status='committed', input_hashes=request['input_hashes'])
        atomic_json(result_path, result)
        return result


def bound_scope_relation(case, actor_id, event_id, behavior):
    """Replay one World-owner relation against the current frozen sources."""
    case = Path(case).resolve()
    active = fact_versions.current_facts(case)
    matches = []
    for result_path in sorted((case/ROOT).glob('*.result.json')):
        result = read_json(result_path)
        if result.get('status') != 'scope_reviewed':
            continue
        request_path = result_path.with_name(result_path.name.replace('.result.json', '.request.json'))
        request = _verified_request(case, request_path)
        context = request.get('scope_context')
        if not context or (context['actor_id'], context['event']['event_id']) != (actor_id, event_id):
            continue
        decision_path = Path(result.get('decision_path', '')).resolve()
        if (not decision_path.is_relative_to((case/ROOT).resolve())
                or not decision_path.is_file()
                or sha256(decision_path) != result.get('decision_file_sha256')
                or result.get('revision') != REVISION
                or result.get('review_id') != request['review_id']
                or result.get('request_sha256') != request['request_sha256']
                or result.get('input_hashes') != request['input_hashes']
                or result.get('parent_version') != active.version_id
                or result.get('effective_version') != active.version_id
                or request['parent_manifest_sha256'] != sha256(active.directory/'manifest.json')
                or request['world_sha256'] != sha256(active.world_path)
                or context['event'] not in behavior.get('events', [])):
            _fail('stale_version', 'Core path World review changed or belongs to older facts')
        decision = read_json(decision_path)
        body, signature = _decision(request, decision)
        if result.get('decision_sha256') != signature:
            _fail('fact_conflict', 'Core path World answer changed')
        matches.append(dict(revision=REVISION, request_sha256=request['request_sha256'],
                            decision_sha256=signature, result_sha256=sha256(result_path),
                            scope_relation=body['scope_relation'],
                            source_frames=request['source_frames']))
    if len(matches) > 1:
        _fail('fact_conflict', 'Multiple World answers claim the same core path relation')
    return matches[0] if matches else None


def project_archived(case, archive, *, expected_version=None):
    """Compare redundant archived lane answers before any derived revalidation."""
    case = Path(case).resolve()
    archive = Path(archive).resolve()
    from _lib.behavior import input_binding
    if not archive.is_relative_to(case) or not input_binding.archive_matches(case, archive):
        _fail('model_out_of_scope', 'Archived Behavior is not bound to this Case')
    view = fact_versions.current_facts(case, expected_version)
    source = read_json(archive)
    derived = copy.deepcopy(source)
    challenges = source.get('challenge_resolutions', [])
    if not isinstance(challenges, list):
        _fail('model_out_of_scope', 'Archived challenge answers are malformed')
    resolved_ids = {q['question_id'] for q in view.world.get('questions_to_verify', [])
                    if q.get('resolution') == 'resolved'}
    if any(isinstance(row, dict) and row.get('challenge_id') in resolved_ids
           for row in challenges):
        _fail('evidence_insufficient', 'Archived challenge answer cannot be compared to a World lane fact')
    closures = derived.get('lane_closures', [])
    if not isinstance(closures, list) or any(not isinstance(row, dict) for row in closures):
        _fail('model_out_of_scope', 'Archived lane answers are malformed')
    comparisons = []
    for row in list(closures):
        if row.get('collection') != 'questions_to_verify':
            continue
        question = _question(view.world, row.get('issue_id'))
        if question.get('resolution') != 'resolved':
            continue
        if (question.get('impact_domains') != ['actor_lane'] or
                question.get('actor_ids') != [row.get('actor_id')]):
            _fail('fact_conflict', 'Archived answer differs from resolved question binding')
        point = point_review.points(view.world).get(row['issue_id'])
        lanes = {lane_id_for_path(view.world, path) for path in question.get('world_paths', [])}
        actor = next((a for a in source.get('actors', [])
                      if a.get('actor_id') == row['actor_id']), None)
        samples = [p for p in actor.get('trajectory', []) if p.get('frame_id') == point['frame_id']] \
            if actor and point else []
        if len(lanes) != 1 or None in lanes or len(samples) != 1:
            _fail('evidence_insufficient', 'Cannot compare archived lane to a unique World fact')
        lane_id = next(iter(lanes))
        if row.get('outcome') != 'confirmed' or point['frame_id'] not in row.get('evidence_frame_ids', []):
            _fail('evidence_insufficient', 'Archived lane answer has no comparable target-frame conclusion')
        if samples[0].get('lane_id') != lane_id:
            _fail('fact_conflict', 'Archived lane answer contradicts resolved World fact')
        comparisons.append(dict(issue_id=row['issue_id'], actor_id=row['actor_id'],
            target_frame=point, world_paths=question['world_paths'], lane_id=lane_id))
        closures.remove(row)
    if not comparisons:
        return source, None
    if closures:
        derived['lane_closures'] = closures
    else:
        derived.pop('lane_closures', None)
    candidate_path = case/ROOT/('archive.'+digest(derived)+'.candidate.json')
    if candidate_path.is_file() and read_json(candidate_path) != derived:
        _fail('fact_conflict', 'Derived archived candidate changed')
    if not candidate_path.is_file():
        atomic_json(candidate_path, derived)
    body = dict(revision=REVISION, source_path=str(archive), source_sha256=sha256(archive),
        fact_version_id=view.version_id, fact_manifest_sha256=sha256(view.directory/'manifest.json'),
        behavior_request_sha256=(input_binding.verify_origin(case) or {}).get('request_sha256'),
        derived_path=str(candidate_path), derived_sha256=sha256(candidate_path),
        derived_digest=digest(derived), comparisons=comparisons)
    receipt = dict(body, audit_sha256=digest(body))
    path = case/ROOT/('archive.'+receipt['audit_sha256']+'.json')
    if path.is_file() and read_json(path) != receipt:
        _fail('fact_conflict', 'Archive projection audit changed')
    if not path.is_file():
        atomic_json(path, receipt)
    return derived, receipt
