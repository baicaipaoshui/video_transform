"""Compile a small, blind visual answer into the existing World contract.

The v1 functions remain for frozen two-task Cases. New Cases use single_world:
one World answer, compiled before the existing Batch/reconciliation boundary.
No public World schema is changed.
"""
from __future__ import annotations

import copy
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from _lib.contracts.v3 import CONTRACT, ContractError
from _lib.behavior.compile import preview_digest
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.paths import SKILLS

REVISION = 'visual-answers.v1'


def enabled(case):
    from .single_world import enabled as single_enabled
    return legacy_topology_enabled(case) or single_enabled(case)


def legacy_topology_enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('semantic_compilation') == REVISION


def fail(rule, message):
    error = ContractError(message, rule_id='semantic.' + rule)
    paths = {'boundary_count': '/boundaries_right_to_left',
             'direction_order': '/lanes_right_to_left', 'direction_conflict': '/road_context/one_way',
             'window_evidence': '/road_context/frame_ids', 'roadside': '/roadside',
             'roadside_unknown': '/roadside', 'ego_ordinal': '/ego_initial/ordinal_from_right',
             'ego_evidence': '/ego_initial/frame_ids', 'road_conflict': '/world/road',
             'scene_conflict': '/world/scene_type', 'evidence_conflict': '/field_evidence'}
    error.diagnostics = dict(rule_id=error.rule_id, path=paths.get(rule, '/'))
    if rule == 'input_binding':
        error.code = 'source_changed'
    raise error


def response_schema():
    """Private question form. Existing World remains the only delivery schema."""
    text = {'type': 'string', 'minLength': 1}
    refs = {'type': 'array', 'minItems': 2, 'uniqueItems': True, 'items': text}
    def evidence(properties):
        return {'type': 'object', 'additionalProperties': False,
                'required': [*properties, 'reason', 'frame_ids'],
                'properties': {**properties, 'reason': text, 'frame_ids': refs}}
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['request_sha256', 'outcome', 'reason'],
        'properties': {
            'request_sha256': text,
            'outcome': {'enum': ['complete_corridor', 'other_scene', 'insufficient_evidence']},
            'reason': text,
            'road_context': evidence({
                'shape': {'const': 'straight'},
                'physically_separated': {'const': False},
                'complete_cross_section': {'const': True},
                'stable_over_window': {'const': True},
                'no_junction_entry': {'const': True},
                'one_way': {'type': 'boolean'}}),
            'lanes_right_to_left': {'type': 'array', 'minItems': 1, 'maxItems': 16,
                'items': evidence({'direction': {'enum': ['forward', 'backward']}})},
            'boundaries_right_to_left': {'type': 'array', 'minItems': 2, 'maxItems': 17,
                'items': evidence({'type': {'enum': ['dashed_white', 'solid_white',
                    'dashed_yellow', 'solid_yellow', 'double_yellow', 'curb', 'unmarked_edge']}})},
            'roadside': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                'items': evidence({'side': {'enum': ['right', 'left']},
                    'type': {'enum': ['continuous_paved_shoulder', 'driveway_apron',
                                     'curb_only', 'unknown']}})},
            'ego_initial': {'anyOf': [{'type': 'null'}, evidence({
                'ordinal_from_right': {'type': 'integer', 'minimum': 0}})]},
        },
        'allOf': [{'if': {'properties': {'outcome': {'const': 'complete_corridor'}}},
                   'then': {'required': ['lanes_right_to_left', 'boundaries_right_to_left',
                                        'roadside', 'ego_initial', 'road_context']}}],
    }


def packet(case):
    """Freeze full-resolution frame bytes before any task is submitted."""
    case = Path(case).resolve()
    target = case/'state/topology_answers.request.json'
    if target.exists():
        result = read_json(target)
        validate_packet(case, result)
        return result
    records = read_json(case/'state/global_frames.json')['frames']
    from _lib.contracts.fidelity import frame_index
    frame_index(records)
    frames = []
    for row in records:
        path = Path(row.get('source_path') or row['path']).resolve(strict=True)
        if not path.is_relative_to(case):
            fail('frame_path', 'Topology evidence must belong to this Case')
        frames.append(dict(frame_id=row['frame_id'], time_s=row['time_s'],
                           path=str(path), sha256=sha256(path)))
    result = dict(revision=REVISION, case_id=case.name,
        input_hashes={name: sha256(case/name) for name in
                      ('case.json', 'state/global_frames.json')},
        frames=frames, response_schema=response_schema(), world_parts_schema=world_response_schema(),
        output_path=str(case/'state/topology_answers.candidate.json'),
        access_enforcement='prompt_only')
    result['request_sha256'] = preview_digest(result)
    atomic_json(target, result)
    return result


def validate_packet(case, request):
    body = dict(request); signature = body.pop('request_sha256', None)
    if (request.get('revision') != REVISION or request.get('case_id') != Path(case).name
            or preview_digest(body) != signature):
        fail('input_binding', 'Topology request binding changed')
    expected = {'case.json', 'state/global_frames.json'}
    if set(request.get('input_hashes', {})) != expected:
        fail('input_binding', 'Topology request source set changed')
    for name, digest in request['input_hashes'].items():
        if sha256(Path(case)/name) != digest:
            fail('input_binding', 'Topology source changed: ' + name)
    for row in request['frames']:
        path = Path(row['path']).resolve(strict=True)
        if not path.is_relative_to(Path(case).resolve()) or sha256(path) != row['sha256']:
            fail('input_binding', 'Topology image bytes changed: ' + row['frame_id'])


def compile_road(answer, request):
    try:
        Draft202012Validator(request['response_schema']).validate(answer)
    except ValidationError as cause:
        error = ContractError(cause.message, rule_id='semantic.schema.' + str(cause.validator))
        error.diagnostics = dict(rule_id=error.rule_id,
            path='/'+'/'.join(str(p).replace('~','~0').replace('/','~1') for p in cause.absolute_path))
        raise error from cause
    if answer['request_sha256'] != request['request_sha256']:
        fail('input_binding', 'Answer belongs to another frozen topology request')
    if answer['outcome'] != 'complete_corridor':
        if set(answer) - {'request_sha256', 'outcome', 'reason'}:
            fail('outcome', 'Non-complete answers must not declare a compiled topology')
        return None
    frames = {row['frame_id']: row for row in request['frames']}
    def evidence(row):
        if any(fid not in frames for fid in row['frame_ids']):
            fail('evidence', 'Unknown topology evidence frame')
        if len({frames[fid]['time_s'] for fid in row['frame_ids']}) < 2:
            fail('evidence', 'Topology evidence needs two distinct source times')
        return [dict(status='observed', frame_id=fid, time_s=frames[fid]['time_s'],
                     reason=row['reason']) for fid in row['frame_ids']]
    rows = answer['lanes_right_to_left']; borders = answer['boundaries_right_to_left']
    if len(borders) != len(rows) + 1:
        fail('boundary_count', 'N lanes require N+1 explicitly observed boundaries')
    directions = [r['direction'] for r in rows]
    forward = directions.count('forward'); backward = directions.count('backward')
    if not forward or directions != ['forward'] * forward + ['backward'] * backward:
        fail('direction_order', 'This compiler supports a contiguous ego carriageway then opposing lanes')
    context = answer['road_context']
    context_evidence = evidence(context)
    endpoints = {min(frames, key=lambda f: frames[f]['time_s']),
                 max(frames, key=lambda f: frames[f]['time_s'])}
    if not endpoints.issubset(context['frame_ids']):
        fail('window_evidence', 'Stable corridor evidence must include both real source endpoints')
    if context['one_way'] != (backward == 0):
        fail('direction_conflict', 'Explicit one-way observation contradicts the lane directions')
    if {r['side'] for r in answer['roadside']} != {'left', 'right'}:
        fail('roadside', 'Each road side must be described exactly once')
    ids = [f'lane_{i+1:03d}' for i in range(len(rows))]
    fields = {}; all_refs = set()
    lanes = []; anchors = []; markings = []; areas = []
    for i, row in enumerate(rows):
        fields[f'world.road.lanes[{i}]'] = evidence(row)
        all_refs.update(row['frame_ids'])
        lanes.append(dict(lane_id=ids[i], road_id='road_001', ordinal_from_right=i,
                          travel_direction=row['direction'], lane_type='driving'))
        anchors.append(dict(lane_id=ids[i], ordinal_from_right=i,
                            evidence_frame_ids=row['frame_ids']))
    for i, row in enumerate(borders):
        refs = evidence(row)
        # A visible curb/unpainted edge is boundary evidence, not a painted
        # marking. Its original answer stays in the hash-bound snapshot.
        if row['type'] in {'curb', 'unmarked_edge'}:
            if i not in (0, len(rows)):
                fail('boundary_count', 'Interior curb/unmarked edge is not a supported driving-lane separator')
            continue
        fields[f'world.road.lane_markings[{len(markings)}]'] = refs
        item = dict(marking_id=f'marking_{len(markings)+1:03d}', type=row['type'],
                    road_id='road_001', evidence_frame_ids=row['frame_ids'], location=row['reason'])
        if 0 < i < len(rows): item['between'] = ids[i-1:i+1]
        else: item.update(lane_id=ids[0] if i == 0 else ids[-1], side='right' if i == 0 else 'left')
        markings.append(item)
    for row in answer['roadside']:
        refs = evidence(row)
        if row['type'] == 'unknown':
            fail('roadside_unknown', 'Use insufficient_evidence when roadside classification is unresolved')
        if row['type'] in {'continuous_paved_shoulder', 'driveway_apron'}:
            i = len(areas)
            areas.append(dict(area_id=f'area_{i+1:03d}', road_id='road_001', side=row['side'],
                area_type={'continuous_paved_shoulder': 'paved_shoulder',
                           'driveway_apron': 'driveway_apron'}[row['type']],
                description=row['reason'], evidence_frame_ids=row['frame_ids']))
            fields[f'world.road.roadside_areas[{i}]'] = refs
    ordered_refs = sorted(all_refs, key=lambda fid: frames[fid]['time_s'])
    summary = [dict(status='observed', frame_id=fid, time_s=frames[fid]['time_s'],
                    reason=answer['reason']) for fid in ordered_refs]
    geometry = dict(shape_type='straight', opposing_separation=dict(
        physically_separated=False, side='left'))
    road = dict(ego_direction_lanes=forward, opposite_direction_lanes=backward,
        one_way=not backward, roads=[dict(road_id='road_001', lane_ids=ids,
            heading_rad=0.0, road_geometry=geometry,
            lane_knowledge=dict(kind='exact', count=len(rows), anchors=anchors,
                                evidence_frame_ids=ordered_refs))],
        lanes=lanes, lane_markings=markings, roadside_areas=areas)
    for name in ('roads', 'lanes', 'ego_direction_lanes', 'opposite_direction_lanes', 'one_way'):
        fields['world.road.' + name] = copy.deepcopy(summary)
    fields['world.scene_type'] = copy.deepcopy(context_evidence)
    fields['world.road.roads[0].road_geometry.shape_type'] = copy.deepcopy(context_evidence)
    fields['world.road.roads[0].road_geometry.opposing_separation'] = copy.deepcopy(context_evidence)
    fields['world.road.one_way'] = copy.deepcopy(context_evidence)
    fields['world.road.lane_markings'] = [item for row in borders for item in evidence(row)]
    fields['world.road.roadside_areas'] = [item for row in answer['roadside'] for item in evidence(row)]
    ego = copy.deepcopy(answer['ego_initial'])
    if ego is not None:
        evidence(ego)
        if ego['ordinal_from_right'] >= forward:
            fail('ego_ordinal', 'Ego ordinal must refer to an observed forward lane')
        first = min(request['frames'], key=lambda f: f['time_s'])['frame_id']
        if first not in ego['frame_ids']:
            fail('ego_evidence', 'Initial ego location must include the first source frame')
        ego['lane_id'] = ids[ego['ordinal_from_right']]
    return dict(road=road, field_evidence=fields, ego_initial_hint=ego)


def load_compiled(case):
    case = Path(case)
    if not legacy_topology_enabled(case): return None
    receipt = case/'state/topology_answers.json'
    if not receipt.is_file(): return None
    request = read_json(case/'state/topology_answers.request.json')
    validate_packet(case, request)
    saved = read_json(receipt)
    if (saved.get('revision') != REVISION or
            saved.get('request_sha256') != request['request_sha256']):
        fail('input_binding', 'Accepted topology receipt belongs to another request')
    source = Path(saved['candidate_snapshot']).resolve()
    if (not source.is_relative_to((case/'diagnostics/candidate_snapshots').resolve())
            or sha256(source) != saved['candidate_sha256']):
        fail('input_binding', 'Accepted topology candidate changed')
    result = compile_road(read_json(source), request)
    if preview_digest(result) != saved['compiled_sha256']:
        fail('input_binding', 'Accepted topology compiler output changed')
    return result


def world_response_schema():
    schema = read_json(SKILLS/'schemas/world_context.schema.json')
    schema['required'] = [k for k in schema['required'] if k not in {'contract_version', 'evidence_epoch'}]
    for key in ('contract_version', 'evidence_epoch'):
        schema['properties'].pop(key)
    scene = schema['properties']['world']
    scene['required'] = ['environment']
    scene['properties'].pop('road')
    scene['properties']['scene_type'] = {'const': 'highway'}
    # Other observed scene fields remain valid; road itself is compiler-owned.
    scene['not'] = {'required': ['road']}
    scene['properties']['junctions'] = {'type': 'array', 'maxItems': 0}
    schema['properties']['field_evidence']['propertyNames'] = {
        'not': {'anyOf': [{'const': 'world.scene_type'},
                          {'pattern': r'^world\.road(?:\.|\[|$)'}]}}
    return schema


def materialize_world(case, raw, *, initial=False):
    from . import single_world
    if single_world.enabled(case):
        if initial or (isinstance(raw, dict) and
                       ('request_sha256' in raw or 'road_observation' in raw)):
            return single_world.materialize(case, raw)
        # A reconciled checkpoint/field-repair baseline is already a full World.
        return copy.deepcopy(raw)
    compiled = load_compiled(case)
    if compiled is None: return copy.deepcopy(raw)
    value = copy.deepcopy(raw)
    if not isinstance(value, dict) or not isinstance(value.get('world'), dict):
        fail('parts_shape', 'World parts must contain a world object')
    scene = value.setdefault('world', {})
    if 'road' in scene and scene['road'] != compiled['road']:
        fail('road_conflict', 'World parts contradict the compiled road; report evidence for World adjudication')
    if scene.get('scene_type', 'highway') != 'highway' or scene.get('junctions'):
        fail('scene_conflict', 'World parts conflict with the straight-corridor answer')
    for key, expected in (('contract_version', CONTRACT), ('evidence_epoch', 1)):
        if key in value and value[key] != expected:
            fail('metadata', 'World parts conflict with compiler-owned ' + key)
        value[key] = expected
    scene['scene_type'] = 'highway'; scene['road'] = copy.deepcopy(compiled['road'])
    evidence = value.setdefault('field_evidence', {})
    if not isinstance(evidence, dict):
        fail('parts_shape', 'World field_evidence must be an object')
    for path, items in compiled['field_evidence'].items():
        # Explicit alternative evidence remains available to the World owner;
        # never replace an existing declaration under a compiler-owned path.
        if path in evidence and evidence[path] != items:
            fail('evidence_conflict', 'World parts must omit compiler-owned evidence: ' + path)
        evidence[path] = copy.deepcopy(items)
    return value
