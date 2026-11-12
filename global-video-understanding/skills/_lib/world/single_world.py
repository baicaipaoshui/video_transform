"""Single World response -> existing World, without inferring missing facts.

Compact road answers cover evidenced complete corridors only. Partial roads and
other scenes use the public World shape in the very same response/session.
"""
from __future__ import annotations

import copy
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from _lib.behavior.compile import preview_digest
from _lib.contracts.v3 import CONTRACT, ContractError
from _lib.contracts.frame_evidence import validate_frame_evidence
from _lib.runtime.case_core import atomic_json, read_json, sha256
from _lib.paths import SKILLS

REVISION = 'visual-answers.v2'
REQUEST = 'state/world_observation.request.json'
LAYOUT_REVISION = 'lane-layout-review.v1'


def layout_schema():
    """Small shared visual answer; no global lane IDs or execution values."""
    refs = {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 1}}
    def fact(properties):
        return dict(type='object', additionalProperties=False,
                    required=list(properties) + ['reason', 'evidence_frame_ids'],
                    properties={**properties, 'reason': {'type': 'string', 'minLength': 1},
                                'evidence_frame_ids': refs})
    return dict(type='object', additionalProperties=False,
        required=['same_direction_count', 'right_outer_boundary', 'boundaries_right_to_left',
                  'ego_initial', 'right_roadside'], properties={
        'same_direction_count': fact({'kind': {'enum': ['exact', 'minimum', 'unknown']},
                                     'count': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 16}}),
        'right_outer_boundary': fact({'confirmed': {'type': ['boolean', 'null']}}),
        'boundaries_right_to_left': {'type': 'array', 'uniqueItems': True, 'maxItems': 17,
                                   'items': {'type': 'string', 'minLength': 1}},
        'ego_initial': fact({'ordinal_from_right': {'type': ['integer', 'null'], 'minimum': 0},
                             'right_boundary_id': {'type': ['string', 'null']},
                             'left_boundary_id': {'type': ['string', 'null']}}),
        'right_roadside': fact({'type': {'enum': ['curbside_parking', 'shoulder',
                                                'sidewalk', 'driveway_apron', 'curb_only', 'unknown']}}),
        'right_roadside_intervals': {'type':'array','maxItems':16,'items':{
            'type':'object','additionalProperties':False,
            'required':['inner_boundary_id','outer_boundary_id','type','reason','evidence_frame_ids'],
            'properties':{
                'inner_boundary_id':{'type':'string','minLength':1},
                'outer_boundary_id':{'type':'string','minLength':1},
                'type':{'enum':['paved_strip','shoulder','sidewalk','curbside_parking']},
                'reason':{'type':'string','minLength':1},
                'evidence_frame_ids':refs}}}})


def validate_layout(value, frames=None):
    from _lib.contracts.v3 import ContractError
    errors = sorted(Draft202012Validator(layout_schema()).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        raise ContractError('Invalid layout observation: ' + errors[0].message,
                            rule_id='world.layout.response')
    count = value['same_direction_count']
    if (count['kind'] == 'unknown') != (count['count'] is None):
        raise ContractError('Layout count kind/value disagree', rule_id='world.layout.count')
    if frames is not None:
        validate_frame_evidence(value, frames, stage='layout_review')
        first = min(frames, key=lambda f: f['time_s'])['frame_id']
        if (value['ego_initial']['ordinal_from_right'] is not None
                and first not in value['ego_initial']['evidence_frame_ids']):
            raise ContractError('Initial ego layout needs the supplied first frame', rule_id='world.layout.ego_frame')
    for name in ('same_direction_count', 'right_outer_boundary', 'ego_initial', 'right_roadside'):
        row = value[name]
        known = (row.get('kind') != 'unknown' if name == 'same_direction_count' else
                 row.get('confirmed') is True if name == 'right_outer_boundary' else
                 row.get('ordinal_from_right') is not None if name == 'ego_initial' else
                 row.get('type') != 'unknown')
        if known and not row['evidence_frame_ids']:
            raise ContractError('Known layout fact needs evidence: ' + name, rule_id='world.layout.evidence')
        if name in {'same_direction_count', 'right_outer_boundary'} and known and len(row['evidence_frame_ids']) < 2:
            raise ContractError('Layout boundaries/count need two frame witnesses', rule_id='world.layout.evidence')
    return value


def layout_problems(value):
    """Declaration checks only; never infer geometry from prose or pixels."""
    count = value['same_direction_count']; ego = value['ego_initial']
    boundaries = value['boundaries_right_to_left']; problems = []
    if count['kind'] != 'exact': problems.append('world.layout.count_unconfirmed')
    if value['right_outer_boundary']['confirmed'] is not True:
        problems.append('world.layout.right_boundary_unconfirmed')
    if ego['ordinal_from_right'] is None:
        problems.append('world.layout.ego_unconfirmed')
    elif value['right_outer_boundary']['confirmed'] is not True:
        problems.append('world.layout.ordinal_without_boundary')
    if count['kind'] == 'exact':
        if len(boundaries) != count['count'] + 1:
            problems.append('world.layout.boundary_chain')
        ordinal = ego['ordinal_from_right']
        if ordinal is not None and (ordinal >= count['count'] or ordinal + 1 >= len(boundaries)
                or ego['right_boundary_id'] != boundaries[ordinal]
                or ego['left_boundary_id'] != boundaries[ordinal + 1]):
            problems.append('world.layout.ego_boundary_chain')
    return problems


def layout_review(world):
    result = world.get('world', {}).get('metadata', {}).get('lane_layout_review')
    return result if isinstance(result, dict) and result.get('revision') == LAYOUT_REVISION else None


def layout_differences(world, observation):
    """Identify review dependencies, not an automatic visual fact replacement."""
    validate_layout(observation)
    differences = layout_problems(observation)
    review = layout_review(world)
    prior = review.get('observation') if review else None
    if prior is None:
        differences.append('world.layout.initial_observation_missing')
    else:
        differences.extend(layout_problems(prior))
        for name, fields in (('same_direction_count', ('kind', 'count')),
                             ('ego_initial', ('ordinal_from_right',)),
                             ('right_outer_boundary', ('confirmed',))):
            if any(prior[name][key] != observation[name][key] for key in fields):
                differences.append('world.layout.' + name + '_changed')
        if (observation['right_roadside']['type'] != 'unknown'
                and prior['right_roadside']['type'] != observation['right_roadside']['type']):
            differences.append('world.layout.roadside_changed')
    road = world['world']['road']; arms = road.get('roads', [])
    if len(arms) != 1:
        differences.append('world.layout.unsupported_road_scope')
    else:
        knowledge = arms[0].get('lane_knowledge') or {}
        lanes = {lane['lane_id']: lane for lane in road.get('lanes', [])}
        forward = [lid for lid in arms[0]['lane_ids'] if lanes[lid].get('travel_direction', 'forward') in {'forward', 'same_as_ego'}
                   and lanes[lid].get('lane_type', 'driving') == 'driving']
        # Total road knowledge may remain minimum because opposing lanes are
        # unknown. That does not invalidate a complete ego-direction chain.
        if (len(forward) != observation['same_direction_count']['count']
                or road.get('ego_direction_lanes', len(forward)) != len(forward)
                or (knowledge.get('kind') == 'exact' and knowledge.get('count') != len(arms[0]['lane_ids']))):
            differences.append('world.layout.source_count')
        if any(lanes[lid].get('ordinal_from_right', i) != i for i, lid in enumerate(forward)):
            differences.append('world.layout.source_order')
        # Independent local labels need not match each other. The initial
        # World's chain, however, must bind to its actual lane definitions.
        if prior is not None and not layout_problems(prior):
            boundaries = prior['boundaries_right_to_left']
            markings = {row.get('marking_id') for row in road.get('lane_markings', [])}
            if (len(forward) + 1 != len(boundaries) or any(
                    lanes[lid].get('right_boundary') != boundaries[i]
                    or lanes[lid].get('left_boundary') != boundaries[i+1]
                    or boundaries[i] not in markings or boundaries[i+1] not in markings
                    for i,lid in enumerate(forward))):
                differences.append('world.layout.source_boundary_chain')
        anchors = {a['lane_id']: a['ordinal_from_right'] for a in knowledge.get('anchors', [])}
        for actor in world.get('actor_hypotheses', []):
            if actor['actor_id'] == 'ego' and actor.get('hypothesized_lane') is not None:
                ordinal = anchors.get(actor['hypothesized_lane'])
                if ordinal is None and actor['hypothesized_lane'] in forward:
                    ordinal = forward.index(actor['hypothesized_lane'])
                if ordinal != observation['ego_initial']['ordinal_from_right']:
                    differences.append('world.layout.source_ego')
        if observation['right_roadside']['type'] == 'sidewalk':
            from _lib.contracts.parking import sidewalk_area_type
            if not any(area.get('road_id') == arms[0]['road_id'] and area.get('side') == 'right'
                       and sidewalk_area_type(area) == 'sidewalk'
                       for area in road.get('roadside_areas', [])):
                differences.append('world.layout.source_sidewalk')
        if observation['right_roadside']['type'] == 'curbside_parking':
            from _lib.contracts.parking import parking_area_type
            if not any(area.get('road_id') == arms[0]['road_id'] and area.get('side') == 'right'
                       and parking_area_type(area) == 'curbside_parking'
                       for area in road.get('roadside_areas', [])):
                differences.append('world.layout.source_parking')
    return sorted(set(differences))


def layout_roadside_differences(world, observation):
    """Compare target-relevant roadside presence and side at the driving edge.

    Only the new decision contract calls this: historical layout reviews did not
    carry an evidenced outer roadside boundary and retain their frozen rules.
    """
    from _lib.contracts.parking import roadside_execution_type
    road = world['world']['road']
    roads = road.get('roads', [])
    if len(roads) != 1:
        return ['world.layout.roadside_scope']
    road_id = roads[0]['road_id']
    lanes = {lane['lane_id']: lane for lane in road.get('lanes', [])}
    driving = [lanes[lid] for lid in roads[0]['lane_ids']
               if lanes[lid].get('travel_direction', 'forward') in {'forward', 'same_as_ego'}
               and lanes[lid].get('lane_type', 'driving') == 'driving']
    if not driving or not driving[0].get('right_boundary'):
        return ['world.layout.roadside_boundary_chain']
    driving_edges = {lane.get(edge) for lane in road.get('lanes', [])
                     for edge in ('left_boundary', 'right_boundary')}
    areas = [area for area in road.get('roadside_areas', [])
             if area.get('road_id') in (None, road_id)]

    def observed_shoulder(area):
        observed = area.get('area_type') if area.get('area_type') is not None else area.get('type')
        return (roadside_execution_type(area) == 'paved_shoulder'
                or isinstance(observed, str) and 'shoulder' in observed.split('_'))

    # Each source uses its own local boundary IDs. Check physical adjacency
    # within World, without requiring Batch labels to occur in World markings.
    edge = driving[0]['right_boundary']
    connected = []
    seen = {edge}
    while True:
        matches = [area for area in areas if area.get('side') == 'right'
                   and area.get('boundary_left') == edge]
        if not matches:
            break
        if len(matches) != 1:
            return ['world.layout.roadside_boundary_chain']
        area = matches[0]
        outer = area.get('boundary_right')
        if not outer or outer in seen or outer in driving_edges:
            return ['world.layout.roadside_boundary_chain']
        connected.append(area)
        edge = outer
        seen.add(edge)
    stray = any(observed_shoulder(area) and area not in connected for area in areas)
    if stray:
        return ['world.layout.source_shoulder']
    if 'right_roadside_intervals' in observation:
        expected = observation['right_roadside_intervals']
        bounds = observation['boundaries_right_to_left']
        current = bounds[0] if bounds else None
        seen = set(bounds)
        for interval in expected:
            outer = interval['outer_boundary_id']
            if (interval['inner_boundary_id'] != current or not outer
                    or outer in seen):
                return ['world.layout.roadside_boundary_chain']
            current = outer
            seen.add(outer)
        expected_shoulder = bool(expected)
    else:
        expected_shoulder = observation['right_roadside']['type'] in {
            'shoulder', 'curbside_parking', 'sidewalk'}
    actual_shoulder = any(observed_shoulder(area) for area in connected)
    if expected_shoulder and not actual_shoulder:
        return ['world.layout.source_shoulder']
    if not expected_shoulder and (actual_shoulder or connected):
        return ['world.layout.roadside_unreconciled']
    if expected_shoulder and any(not observed_shoulder(area) for area in connected):
        return ['world.layout.roadside_unreconciled']
    return []


def _attach_layout(value, raw, request):
    if request.get('layout_review_revision') != LAYOUT_REVISION:
        return
    scene = value['world']; roads = scene.get('road', {}).get('roads', [])
    if scene.get('scene_type') != 'highway' or len(roads) != 1 or scene.get('junctions'):
        return
    observation = raw.get('layout_observation')
    # Older frozen compact requests retain their original contract. New v3
    # roadside-geometry requests must receive the same independent review as
    # full World: the compact compiler does not bind a shoulder to two edges.
    if ('road_observation' in raw and observation is None
            and request.get('roadside_geometry_revision') is None):
        return
    if observation is not None:
        validate_layout(observation, request['frames'])
    metadata = scene.setdefault('metadata', {})
    if 'lane_layout_review' in metadata:
        fail('layout_metadata', 'Layout review metadata is compiler-owned')
    frames = sorted(request['frames'], key=lambda f: f['time_s'])
    witnesses = [frames[0]] if len(frames) == 1 else [frames[0], frames[-1]]
    index = len(value['questions_to_verify'])
    value['questions_to_verify'].append(dict(
        question='Independently verify the visible ego-direction lane boundaries, count, '
                 'and initial ego corridor. Separately record right roadside observations; '
                 'unknown ancillary use alone does not negate a confirmed driving-lane chain.',
        impact_domains=['lane_count', 'actor_lane', 'road_topology'], resolution='unresolved',
        actor_ids=['ego'], world_paths=['world.road.roads[0].lane_ids',
                                     'world.road.roads[0].lane_knowledge', 'world.road.lanes'],
        evidence=[dict(status='observed', frame_id=f['frame_id'], time_s=f['time_s'],
                       reason='Source frame assigned to independent layout verification; no count assumed.')
                  for f in witnesses]))
    _questions(value)
    qid = value['questions_to_verify'][index]['question_id']
    metadata['lane_layout_review'] = dict(revision=LAYOUT_REVISION, question_id=qid,
        observation=copy.deepcopy(observation), source_request_sha256=request['request_sha256'])
    if observation is not None:
        for name in ('same_direction_count', 'right_outer_boundary', 'ego_initial', 'right_roadside'):
            row = observation[name]
            known = (row.get('kind') != 'unknown' if name == 'same_direction_count' else
                     row.get('confirmed') is True if name == 'right_outer_boundary' else
                     row.get('ordinal_from_right') is not None if name == 'ego_initial' else
                     row.get('type') != 'unknown')
            value['field_evidence']['world.metadata.lane_layout_review.observation.' + name] = [
                dict(status='observed', frame_id=fid, reason=row['reason'])
                for fid in row['evidence_frame_ids'] if known] or [dict(status='unknown', reason=row['reason'])]


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('semantic_compilation') == REVISION


def fail(rule, message, path='/'):
    error = ContractError(message, rule_id='semantic.' + rule)
    error.code = 'source_changed' if rule == 'input_binding' else 'world_patch_contract_blocked'
    error.diagnostics = dict(rule_id=error.rule_id, path=path, stage='world',
                            automatic_retry=False)
    raise error


def _window_schema():
    interval = read_json(SKILLS/'schemas/world_context.schema.json')['properties']['critical_intervals']['items']
    return dict(type='object', additionalProperties=False, required=['start_s', 'end_s'],
                description='end >= start; equal times request exactly one bound evidence image for a static question. Temporal/identity questions need a positive interval.',
                properties={key: interval['properties'][key] for key in ('start_s', 'end_s')})


def response_schema(*, layout_review=False, actor_fact_review=False, ego_lane_observations=False):
    """Derive the task format from the public schema, never duplicate its enums."""
    from .semantic import response_schema as road_schema
    schema = read_json(SKILLS/'schemas/world_context.schema.json')
    # The model may propose at most four focus windows. Compilation can add
    # exact windows for separately stated questions; the admitted World keeps
    # all of them and Batch planning bounds the number of model sessions.
    schema['properties']['critical_intervals']['maxItems'] = 4
    schema['required'] = [k for k in schema['required']
                          if k not in {'contract_version', 'evidence_epoch'}] + ['request_sha256']
    schema['properties']['request_sha256'] = {'type': 'string', 'minLength': 1}
    schema['properties']['evidence_epoch'] = {'const': 1}
    road = road_schema()
    road['required'] += road.pop('allOf')[0]['then']['required']
    road['required'] = [k for k in road['required'] if k not in {'request_sha256', 'outcome'}]
    for key in ('request_sha256', 'outcome'):
        road['properties'].pop(key)
    schema['properties']['road_observation'] = road
    scene = schema['properties']['world']
    scene['required'].remove('road')
    schema['allOf'] = [{
        'if': {'required': ['road_observation']},
        'then': {'properties': {'world': {
            'not': {'required': ['road']},
            'properties': {'scene_type': {'const': 'highway'},
                           'junctions': {'type': 'array', 'maxItems': 0}}}}},
        'else': {'properties': {'world': {'required': ['road']},
                               'actor_hypotheses': {'items': {
                                   'not': {'required': ['hypothesized_ordinal_from_right']}}}}},
    }]
    question = schema['$defs']['question']
    question['required'].remove('question_id')
    question['properties']['verification_window'] = _window_schema()
    actor = schema['properties']['actor_hypotheses']['items']
    actor['properties']['hypothesized_ordinal_from_right'] = {
        'type': ['integer', 'null'], 'minimum': 0}
    if layout_review:
        schema['properties']['layout_observation'] = layout_schema()
    if actor_fact_review:
        lane_path = (r'^world\.(?:actors\.[^.\[\]]+\.hypothesized_lane|'
                     r'actor_hypotheses\.[^.\[\]]+\.hypothesized_lane|'
                     r'actor_hypotheses\[(?:0|[1-9][0-9]*)\]\.hypothesized_lane|'
                     r'road\.[^.\[\]]+_initial_lane|'
                     r'metadata\.lane_layout_review\.observation\.'
                     r'[^.\[\]]+_initial\.ordinal_from_right)$')
        lane_rule = {
            'if': {'required': ['impact_domains'], 'properties': {
                'impact_domains': {'contains': {'const': 'actor_lane'}}}},
            'then': {'required': ['actor_ids', 'world_paths'], 'properties': {
                'impact_domains': {'maxItems': 1},
                'actor_ids': {'minItems': 1, 'maxItems': 1},
                'world_paths': {'minItems': 1, 'items': {'type': 'string', 'pattern': lane_path}}}}}
        identity_rule = {
            'if': {'required': ['impact_domains'], 'properties': {
                'impact_domains': {'contains': {'const': 'actor_identity'}}}},
            'then': {'properties': {'impact_domains': {'maxItems': 1}}, 'allOf': [{
                'if': {'required': ['actor_ids'], 'properties': {
                    'actor_ids': {'minItems': 1}, 'resolution': {'not': {'const': 'resolved'}}}},
                'then': {'required': ['identity_binding']}}]}}
        question.setdefault('allOf', []).append(copy.deepcopy(identity_rule))
        question['allOf'].append(lane_rule)
        schema['properties']['ambiguities']['items'].setdefault('allOf', []).append(identity_rule)
    if ego_lane_observations:
        from .actor_facts import extend_ego_lane_observation_schema
        extend_ego_lane_observation_schema(schema, world=True)
    return schema


def packet(case, *, layout_review=True):
    """Called by the lifecycle owner; read-only validators never create packets."""
    case = Path(case).resolve()
    target = case/REQUEST
    if target.is_file():
        request = read_json(target)
        validate_packet(case, request)
        return request
    if not enabled(case):
        fail('input_binding', 'Single World task requires the frozen v2 policy')
    from _lib.contracts.fidelity import frame_index
    rows = read_json(case/'state/global_frames.json')['frames']
    frame_index(rows)
    frames = []
    source_hashes = {}
    for row in rows:
        # The Agent must read the packed JPEG persisted for this request
        # (materialize_pack); source_path is provenance only. Full-resolution
        # decodes routinely exceed the model-side image viewer size cap.
        path = Path(row['path']).resolve(strict=True)
        if not path.is_relative_to(case):
            fail('input_binding', 'World frame must belong to this Case')
        frames.append(dict(frame_id=row['frame_id'], time_s=row['time_s'],
                           path=str(path), sha256=sha256(path)))
        if row.get('source_path'):
            source = Path(row['source_path']).resolve(strict=True)
            if not source.is_relative_to(case):
                fail('input_binding', 'World source frame must belong to this Case')
            source_hashes[str(source.relative_to(case))] = sha256(source)
    from .actor_facts import (enabled as actor_fact_enabled, ego_lane_observations_enabled,
                              EGO_LANE_OBSERVATIONS, EGO_LANE_OBSERVATIONS_KEY)
    ego_lane_observations = ego_lane_observations_enabled(case)
    request = dict(revision=REVISION, case_id=case.name,
        input_hashes={name: sha256(case/name) for name in
                      ('case.json', 'state/global_frames.json', 'state/execution_policy.json')},
        frames=frames, response_schema=response_schema(layout_review=layout_review,
            actor_fact_review=actor_fact_enabled(case), ego_lane_observations=ego_lane_observations),
        source_hashes=source_hashes,
        output_path=str(case/'state/world_context.candidate.json'), access_enforcement='prompt_only')
    if ego_lane_observations:
        request[EGO_LANE_OBSERVATIONS_KEY] = EGO_LANE_OBSERVATIONS
    if layout_review:
        request['layout_review_revision'] = LAYOUT_REVISION
        from .lane_layout import ROADSIDE_DECISION_REVISIONS
        policy = read_json(case/'state/execution_policy.json')
        if policy.get('lane_layout_closure') in ROADSIDE_DECISION_REVISIONS:
            request['roadside_geometry_revision'] = policy['lane_layout_closure']
    request['request_sha256'] = preview_digest(request)
    atomic_json(target, request)
    return request


def validate_packet(case, request):
    case = Path(case).resolve()
    body = dict(request)
    signature = body.pop('request_sha256', None)
    if (not enabled(case) or request.get('revision') != REVISION
            or request.get('case_id') != case.name or preview_digest(body) != signature):
        fail('input_binding', 'World observation request binding changed')
    sources = {'case.json', 'state/global_frames.json', 'state/execution_policy.json'}
    if set(request.get('input_hashes', {})) != sources:
        fail('input_binding', 'World observation source set changed')
    for name, digest in request['input_hashes'].items():
        if not (case/name).is_file() or sha256(case/name) != digest:
            fail('input_binding', 'World observation source changed: ' + name)
    from .actor_facts import (ego_lane_observations_enabled, EGO_LANE_OBSERVATIONS,
                              EGO_LANE_OBSERVATIONS_KEY)
    expected = EGO_LANE_OBSERVATIONS if ego_lane_observations_enabled(case) else None
    if request.get(EGO_LANE_OBSERVATIONS_KEY) != expected:
        fail('input_binding', 'World Ego lane observation contract differs from frozen policy')
    # Older frozen packets bound originals directly in frames. New packets
    # bind packed pixels there and retain original provenance independently.
    if 'source_hashes' in request:
        originals = {str(Path(row['source_path']).resolve().relative_to(case))
                     for row in read_json(case/'state/global_frames.json')['frames']
                     if row.get('source_path')}
        if set(request['source_hashes']) != originals:
            fail('input_binding', 'World original image source set changed')
        for name, digest in request['source_hashes'].items():
            path = (case/name).resolve()
            if not path.is_relative_to(case) or not path.is_file() or sha256(path) != digest:
                fail('input_binding', 'World original image bytes changed: ' + name)
    for row in request['frames']:
        path = Path(row['path']).resolve()
        if not path.is_relative_to(case) or not path.is_file() or sha256(path) != row['sha256']:
            fail('input_binding', 'World image bytes changed: ' + row['frame_id'])


def validate_inputs(case):
    """Verify the original observation at reuse/downstream boundaries; no writes.

    Never create a request to retroactively bind an existing World to new bytes.
    Cases without this frozen mode retain their legacy contract.
    """
    from _lib.runtime.identity_revision import validate_lineage
    if validate_lineage(case):
        return
    if not enabled(case):
        return
    path = Path(case)/REQUEST
    if not path.is_file():
        fail('input_binding', 'Frozen World observation request is missing')
    validate_packet(case, read_json(path))


def _questions(value, frames=None):
    questions = value.get('questions_to_verify', [])
    if not isinstance(questions, list) or any(not isinstance(q, dict) for q in questions):
        return  # The unchanged admission gate reports structural errors.
    used = {q['question_id'] for q in questions if isinstance(q.get('question_id'), str)}
    intervals = value.get('critical_intervals')
    # Do not accidentally bind an existing dangling reference to an unnamed
    # question merely because its auto-generated number happens to match.
    for row in intervals if isinstance(intervals, list) else []:
        refs = row.get('question_ids', []) if isinstance(row, dict) else []
        if isinstance(refs, list):
            used.update(ref for ref in refs if isinstance(ref, str))
    ambiguities = value.get('ambiguities')
    for row in ambiguities if isinstance(ambiguities, list) else []:
        refs = row.get('related_question_ids', []) if isinstance(row, dict) else []
        if isinstance(refs, list):
            used.update(ref for ref in refs if isinstance(ref, str))
    for index, question in enumerate(questions):
        if 'question_id' not in question:
            number = 1
            while f'question_{number:03d}' in used:
                number += 1
            question['question_id'] = f'question_{number:03d}'
            used.add(question['question_id'])
        if 'verification_window' not in question:
            continue
        window = question.pop('verification_window')
        if (not Draft202012Validator(_window_schema()).is_valid(window)
                or window['end_s'] < window['start_s'] or not isinstance(intervals, list)):
            fail('question_window', 'A verification window needs valid start/end and critical_intervals',
                 f'/questions_to_verify/{index}/verification_window')
        if window['end_s'] == window['start_s']:
            from .point_review import store
            store(value, question, window['start_s'], frames or [])
            continue
        # Group identical windows without widening, dropping or inventing scope.
        matched = next((row for row in intervals if isinstance(row, dict)
                        and all(row.get(k) == window[k] for k in ('start_s', 'end_s'))), None)
        if matched is None:
            intervals.append(dict(window, question_ids=[question['question_id']]))
        else:
            refs = matched.setdefault('question_ids', [])
            if not isinstance(refs, list):
                fail('question_window', 'critical_intervals.question_ids must be an array')
            if question['question_id'] not in refs:
                refs.append(question['question_id'])


def _frame_times(value, frames):
    # Check explicit values first: null/wrong time must never be overwritten.
    validate_frame_evidence(value, frames, stage='world')
    times = {row['frame_id']: row['time_s'] for row in frames}

    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            if 'frame_id' in node and 'time_s' not in node:
                node['time_s'] = times[node['frame_id']]
            for child in node.values():
                walk(child)
    walk(value)


def _full_world_actor_ordinals(value):
    """Remove only task-local Actor ordinals proved by the submitted World.

    ``hypothesized_ordinal_from_right`` is not part of the public Actor shape.
    The private single-World response accepts it for compact road observations,
    where the compiler owns the lane mapping.  Models can nevertheless emit the
    optional key in the Full World branch.  Treat null/null as a representation
    no-op and accept a numeric duplicate only when the Full World itself proves
    the exact lane/ordinal mapping.  Never derive a lane from an ordinal here.
    """
    actors = value.get('actor_hypotheses')
    if not isinstance(actors, list):
        return
    if not any(isinstance(actor, dict) and 'hypothesized_ordinal_from_right' in actor for actor in actors):
        return
    road = value.get('world', {}).get('road')
    from _lib.contracts.lane_locations import lane_locations
    locations = lane_locations(road) if isinstance(road, dict) else {}

    def known_ordinals(lane_id):
        ordinal = locations.get(lane_id, (None, None))[1]
        return [] if ordinal is None else [ordinal]

    for index, actor in enumerate(actors):
        if not isinstance(actor, dict) or 'hypothesized_ordinal_from_right' not in actor:
            continue
        ordinal = actor['hypothesized_ordinal_from_right']
        lane_id = actor.get('hypothesized_lane')
        path = f'/actor_hypotheses/{index}/hypothesized_ordinal_from_right'
        if ordinal is None and lane_id is None:
            actor.pop('hypothesized_ordinal_from_right')
            continue
        if type(ordinal) is not int or not isinstance(lane_id, str):
            fail('actor_ordinal',
                 'Full World Actor ordinal may only duplicate a known lane reference', path)
        mappings = known_ordinals(lane_id)
        if not mappings:
            fail('actor_ordinal',
                 'Full World does not prove the Actor lane ordinal; keep only the lane reference', path)
        if any(item != ordinal for item in mappings):
            fail('actor_lane_conflict',
                 'Full World Actor lane reference and ordinal disagree', path)
        actor.pop('hypothesized_ordinal_from_right')


def compile_world(raw, request):
    """Pure compiler. Semantic/content failures stop, programming errors propagate."""
    from .semantic import compile_road, response_schema as road_schema
    if not isinstance(raw, dict) or raw.get('request_sha256') != request['request_sha256']:
        fail('input_binding', 'World candidate must echo its frozen request_sha256', '/request_sha256')
    if 'layout_observation' in raw and request.get('layout_review_revision') != LAYOUT_REVISION:
        fail('layout_contract', 'Frozen legacy World request does not permit layout_observation')
    from .point_review import KEY
    if isinstance(raw.get('world'), dict) and KEY in (raw['world'].get('metadata') or {}):
        fail('point_metadata', 'Single-frame review metadata is compiler-owned')
    from .compilation_repair import validate_initial
    validate_initial(raw, request)
    from .actor_facts import validate_ego_lane_observations
    validate_ego_lane_observations(raw, request, request['frames'], world=True)
    from .lane_numbering import normalize_lane_ids
    raw, _ = normalize_lane_ids(raw, request)
    value = copy.deepcopy(raw)
    value.pop('request_sha256')
    value.pop('layout_observation', None)
    for name, expected in (('contract_version', CONTRACT), ('evidence_epoch', 1)):
        if name in value and (value[name] != expected or
                             (name == 'evidence_epoch' and type(value[name]) is not int)):
            fail('metadata', 'Initial World conflicts with ' + name, '/' + name)
        value[name] = expected
    scene = value.get('world')
    if not isinstance(scene, dict):
        fail('world_shape', 'World candidate needs a world object', '/world')
    if 'road_observation' in value:
        if 'road' in scene or scene.get('scene_type') != 'highway' or scene.get('junctions'):
            fail('road_conflict', 'Choose one complete road representation in this World response', '/world')
        observation = value.pop('road_observation')
        if not isinstance(observation, dict) or {'request_sha256', 'outcome'} & observation.keys():
            fail('road_shape', 'road_observation contains only visual road answers', '/road_observation')
        road_request = {**request, 'response_schema': road_schema()}
        try:
            compiled = compile_road(dict(observation, request_sha256=request['request_sha256'],
                                         outcome='complete_corridor'), road_request)
        except ContractError as error:
            details = getattr(error, 'diagnostics', {})
            details['path'] = '/road_observation' + details.get('path', '/')
            error.diagnostics = details
            raise
        scene['road'] = compiled['road']
        fields = value.get('field_evidence')
        if not isinstance(fields, dict):
            fail('evidence_shape', 'field_evidence must be an object', '/field_evidence')
        for path, items in compiled['field_evidence'].items():
            if path in fields and fields[path] != items:
                fail('evidence_conflict', 'Conflicting road evidence: ' + path, '/field_evidence')
            fields[path] = items
        actors = value.get('actor_hypotheses')
        if isinstance(actors, list):
            for index, actor in enumerate(actors):
                if not isinstance(actor, dict) or 'hypothesized_ordinal_from_right' not in actor:
                    continue
                ordinal = actor.pop('hypothesized_ordinal_from_right')
                if ordinal is None:
                    if actor.get('hypothesized_lane') is not None:
                        fail('actor_lane_conflict', 'Unknown ordinal conflicts with a known lane')
                    actor.setdefault('hypothesized_lane', None)
                    continue
                if type(ordinal) is not int or not 0 <= ordinal < len(compiled['road']['lanes']):
                    fail('actor_ordinal', 'Actor ordinal is outside the observed road',
                         f'/actor_hypotheses/{index}/hypothesized_ordinal_from_right')
                lane = compiled['road']['roads'][0]['lane_ids'][ordinal]
                if 'hypothesized_lane' in actor and actor['hypothesized_lane'] != lane:
                    fail('actor_lane_conflict', 'Explicit lane and ordinal disagree (including null)')
                actor['hypothesized_lane'] = lane
        # The ego hint only concerns the real first frame; never apply it later.
        first = min(request['frames'], key=lambda row: row['time_s'])['frame_id']
        hint = compiled['ego_initial_hint']
        for actor in actors if isinstance(actors, list) else []:
            if (isinstance(actor, dict) and actor.get('actor_id') == 'ego'
                    and actor.get('first_seen_frame_id') == first and hint is not None):
                if 'hypothesized_lane' in actor and actor['hypothesized_lane'] != hint['lane_id']:
                    fail('ego_lane_conflict', 'Ego initial hint and Actor observation disagree')
                actor['hypothesized_lane'] = hint['lane_id']
    else:
        _full_world_actor_ordinals(value)
    _questions(value, request['frames'])
    _attach_layout(value, raw, request)
    _frame_times(value, request['frames'])
    return value


def materialize(case, raw):
    request = read_json(Path(case)/REQUEST)
    try:
        validate_packet(case, request)
        return compile_world(raw, request)
    except (ContractError, ValidationError) as error:
        if getattr(error, 'code', None) not in {'source_changed', 'world_compilation_repairable'}:
            error.code = 'world_patch_contract_blocked'
        details = getattr(error, 'diagnostics', {})
        details.update(stage='world', candidate_sha256=preview_digest(raw),
                       input_sha256=request['request_sha256'], automatic_retry=False,
                       bounded_repair_available=getattr(error, 'code', None) == 'world_compilation_repairable',
                       recovery='Retain the candidate; fix the specific contract/evidence issue. No whole-World retry.')
        error.diagnostics = details
        raise
