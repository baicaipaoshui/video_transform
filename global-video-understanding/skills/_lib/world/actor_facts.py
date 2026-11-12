"""Opt-in Actor question and lane evidence rules; never infer facts from prose."""
import copy
import re
from pathlib import Path

from _lib.runtime.case_core import read_json

REVISION = 'actor-facts.v1'
STRICT_REVISION = 'actor-facts.v2'
POLICY_KEY = 'actor_fact_review'
EGO_LANE_OBSERVATIONS = 'ego-lane-observations.v1'
EGO_LANE_OBSERVATIONS_KEY = 'ego_lane_observations'
_INDEXED_LANE = re.compile(r'^world\.actor_hypotheses\[(0|[1-9][0-9]*)\]\.hypothesized_lane$')


def ego_lane_observations_enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return (path.is_file()
            and read_json(path).get(EGO_LANE_OBSERVATIONS_KEY) == EGO_LANE_OBSERVATIONS)


def is_ego_lane_observation(row):
    return (isinstance(row, dict) and row.get('actor_id') == 'ego'
            and ('lane_id' in row or 'ordinal_from_right' in row))


def ego_lane_observation_rule():
    """Extend existing evidence rows with an optional, explicit Ego candidate."""
    return {
        'if': {
            'required': ['actor_id'],
            'properties': {'actor_id': {'const': 'ego'}},
            'anyOf': [{'required': ['lane_id']}, {'required': ['ordinal_from_right']}],
        },
        'then': {
            'required': ['status', 'frame_id', 'time_s', 'reason'],
            'properties': {
                'status': {'enum': ['observed', 'unknown']},
                'frame_id': {'type': 'string', 'minLength': 1},
                'time_s': {'type': 'number', 'minimum': 0},
                'reason': {'type': 'string', 'pattern': r'\S'},
                'lane_id': {'type': ['string', 'null'], 'pattern': r'\S'},
                'ordinal_from_right': {'type': ['integer', 'null'], 'minimum': 0},
            },
            'if': {'properties': {'status': {'const': 'observed'}}},
            'then': {'anyOf': [
                {'required': ['lane_id'], 'properties': {'lane_id': {'type': 'string'}}},
                {'required': ['ordinal_from_right'],
                 'properties': {'ordinal_from_right': {'type': 'integer'}}},
            ]},
            'else': {'properties': {'lane_id': {'type': 'null'},
                                    'ordinal_from_right': {'type': 'null'}}},
        },
    }


def extend_ego_lane_observation_schema(schema, *, world=False):
    rule = ego_lane_observation_rule()
    schema['$defs']['evidence']['allOf'].append(rule)
    if world:
        fields = schema['properties']['field_evidence']
        rows = fields['additionalProperties']
        # Preserve this observation across layout compilation without making
        # it evidence for an initial lane, road property, or another Actor.
        fields.setdefault('properties', {})[EGO_LANE_OBSERVATIONS_KEY] = {
            **rows, 'items': {'allOf': [rows['items'], rule['if']]},
        }
        fields['additionalProperties'] = {
            **rows, 'items': {'allOf': [rows['items'], {'not': rule['if']}]},
        }


def validate_ego_lane_observations(document, request, frames, *, world=False):
    """Validate the frozen evidence fragment before admitting candidate rows."""
    if EGO_LANE_OBSERVATIONS_KEY not in request:
        return
    from jsonschema import Draft202012Validator, ValidationError
    from _lib.contracts.v3 import ContractError

    rules = request.get('response_schema', {}).get('$defs', {}).get('evidence', {}).get('allOf', [])
    rule = ego_lane_observation_rule()
    if request[EGO_LANE_OBSERVATIONS_KEY] != EGO_LANE_OBSERVATIONS or rule not in rules:
        raise ContractError('Frozen Ego lane observation contract is missing or changed',
                            rule_id='evidence.ego_lane_observation')
    if not isinstance(document, dict):
        return  # The existing document validator owns malformed envelopes.
    if world:
        fields = document.get('field_evidence', {})
        if not isinstance(fields, dict):
            return
        groups = ((f'/field_evidence/{path.replace("~", "~0").replace("/", "~1")}', rows)
                  for path, rows in fields.items())
    else:
        observations = document.get('observations', [])
        if not isinstance(observations, list):
            return
        groups = ((f'/observations/{index}/evidence', item.get('evidence', []))
                  for index, item in enumerate(observations) if isinstance(item, dict))
    times = {frame['frame_id']: frame['time_s'] for frame in frames}
    validator = Draft202012Validator(rule)
    reserved_path = f'/field_evidence/{EGO_LANE_OBSERVATIONS_KEY}'

    def invalid(message, path):
        failure = ContractError('Invalid Ego lane observation: ' + message,
                                rule_id='evidence.ego_lane_observation', actor_id='ego')
        failure.diagnostics = dict(path=path, rule_id=failure.rule_id)
        return failure

    for path, rows in groups:
        reserved = world and path == reserved_path
        if reserved and (not isinstance(rows, list) or not rows):
            raise invalid('Reserved World evidence must be a nonempty array', path)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            typed = is_ego_lane_observation(row)
            if world and typed != reserved:
                raise invalid('World Ego lane candidates belong only in the reserved evidence array',
                              f'{path}/{index}')
            if not typed:
                continue
            try:
                validator.validate(row)
                if row['frame_id'] not in times or times[row['frame_id']] != row['time_s']:
                    raise ValidationError('Frame and time must match the frozen image')
                ordinal = row.get('ordinal_from_right')
                if ordinal is not None and type(ordinal) is not int:
                    raise ValidationError('Lane ordinal must be an integer')
            except ValidationError as error:
                raise invalid(error.message, f'{path}/{index}') from error


def normalize_question_paths(world):
    """Canonicalize only independently bound, identical Actor lane references.

    The index locates a declared Actor; it never establishes which Actor a
    question concerns. The separate actor_ids binding must agree, and the
    canonical reader must return the same existing fact.
    """
    from _lib.contracts.v3 import ContractError, world_conflict_value

    actors = world.get('actor_hypotheses') if isinstance(world, dict) else None
    questions = world.get('questions_to_verify') if isinstance(world, dict) else None
    if not isinstance(actors, list) or not isinstance(questions, list):
        return world, []
    ids = [actor.get('actor_id') for actor in actors if isinstance(actor, dict)]
    if (len(ids) != len(actors) or any(not isinstance(actor_id, str) or not actor_id for actor_id in ids)
            or len(ids) != len(set(ids))):
        return world, []
    changes = []
    for question_index, question in enumerate(questions):
        if not isinstance(question, dict) or question.get('impact_domains') != ['actor_lane']:
            continue
        paths = question.get('world_paths')
        if not isinstance(paths, list) or not any(isinstance(path, str) and _INDEXED_LANE.fullmatch(path)
                                                   for path in paths):
            continue
        bound = question.get('actor_ids')
        if not isinstance(bound, list) or len(bound) != 1 or bound[0] not in ids:
            return world, []
        actor_id = bound[0]
        canonical = f'world.actors.{actor_id}.hypothesized_lane'
        try:
            fact = world_conflict_value(world, canonical)
        except (ContractError, KeyError, IndexError, TypeError, ValueError):
            return world, []
        for path_index, path in enumerate(paths):
            match = _INDEXED_LANE.fullmatch(path) if isinstance(path, str) else None
            if match is None:
                if not actor_lane_path(world, actor_id, path):
                    return world, []
                continue
            index = int(match[1])
            if index >= len(actors) or actors[index]['actor_id'] != actor_id or (
                    'hypothesized_lane' not in actors[index]
                    or actors[index]['hypothesized_lane'] != fact):
                return world, []
            changes.append((question_index, path_index, path, canonical, index, actor_id))
    if not changes:
        return world, []
    result = copy.deepcopy(world)
    repairs = []
    for question_index, path_index, before, after, index, actor_id in changes:
        result['questions_to_verify'][question_index]['world_paths'][path_index] = after
        repairs.append(dict(path=f'/questions_to_verify/{question_index}/world_paths/{path_index}',
                            before=before, after=after, actor_id=actor_id, actor_index=index,
                            reason='unique_question_actor_matches_indexed_actor_and_existing_lane_fact'))
    return result, repairs


def enabled(case):
    path = Path(case)/'state/execution_policy.json' if case is not None else None
    return bool(path and path.is_file() and read_json(path).get(POLICY_KEY) in (REVISION, STRICT_REVISION))


def strict_enabled(case):
    path = Path(case)/'state/execution_policy.json' if case is not None else None
    return bool(path and path.is_file() and read_json(path).get(POLICY_KEY) == STRICT_REVISION)


def frames_for(case):
    probe = Path(case)/'state/probe.json'
    if probe.is_file():
        frames = read_json(probe).get('decoded_frames')
        if frames:
            return frames
    path = Path(case)/'state/global_frames.json'
    return read_json(path).get('frames', []) if path.is_file() else []


def actor_lane_path(world, actor_id, path):
    """A lane target must read an existing fact about the named Actor."""
    from _lib.contracts.v3 import ContractError, world_conflict_value

    if path not in {
            f'world.actors.{actor_id}.hypothesized_lane',
            f'world.actor_hypotheses.{actor_id}.hypothesized_lane',
            f'world.road.{actor_id}_initial_lane',
            f'world.metadata.lane_layout_review.observation.{actor_id}_initial.ordinal_from_right',
    }:
        return False
    try:
        world_conflict_value(world, path)
    except (ContractError, KeyError, IndexError, TypeError, ValueError):
        return False
    return True


def lane_id_for_path(world, path):
    """Resolve a lane fact only when the active road gives one identity."""
    from _lib.contracts.v3 import world_conflict_value

    value = world_conflict_value(world, path)
    lanes = world['world']['road'].get('lanes', [])
    if isinstance(value, str):
        identity = _lane_identity(world, value)
        return identity[1] if identity else None
    if type(value) is int and path.endswith('.ordinal_from_right'):
        from _lib.contracts.v3 import ContractError
        from _lib.contracts.lane_locations import lane_locations
        try:
            locations = lane_locations(world['world']['road'])
        except (ContractError, KeyError, TypeError, ValueError):
            return None
        matches = [lane['lane_id'] for lane in lanes
                   if locations.get(lane['lane_id'], (None, None))[1] == value
                   and lane.get('travel_direction', 'same_as_ego') == 'same_as_ego']
        if len(matches) == 1:
            return matches[0]
    return None


def question_contracts(world, frames=None):
    """Read-only issue bindings; keep incomplete historical issues visible.

    A point is a target instant, while interval assignments only schedule
    observations. Neither prose nor a sampled interval supplies a fact target.
    """
    from .point_review import inspected_points

    point_bindings, invalid_points = inspected_points(world)
    frame_ids = None if frames is None else {f['frame_id'] for f in frames}
    actors = {'ego', *(a['actor_id'] for a in world.get('actor_hypotheses', []))}
    records = []
    for collection, key in (('questions_to_verify', 'question_id'), ('ambiguities', 'ambiguity_id')):
        for item in world.get(collection, []):
            issue_id = item.get(key)
            domains = item.get('impact_domains', [])
            ids = item.get('actor_ids', [])
            paths = item.get('world_paths', [])
            point = point_bindings.get(issue_id) if collection == 'questions_to_verify' else None
            evidence = item.get('evidence', [])
            diagnostics = []
            if domains == ['actor_lane'] and (len(ids) != 1 or ids[0] not in actors or not paths
                    or any(not actor_lane_path(world, ids[0], path) for path in paths)):
                diagnostics.append('binding_missing')
            if issue_id in invalid_points:
                diagnostics.append('binding_missing')
            lane_id = None
            if (domains == ['actor_lane'] and len(ids) == 1 and ids[0] in actors and paths
                    and all(actor_lane_path(world, ids[0], path) for path in paths)):
                lanes = {lane_id_for_path(world, path) for path in paths}
                if len(lanes) == 1 and None not in lanes:
                    lane_id = next(iter(lanes))
                elif item.get('resolution') == 'resolved':
                    diagnostics.append('undetermined')
            if domains == ['actor_lane']:
                interval_target = any(
                    issue_id in interval.get('question_ids' if key == 'question_id' else 'ambiguity_ids', [])
                    and interval.get('end_s', 0) > interval.get('start_s', 0)
                    for interval in world.get('critical_intervals', []))
                if interval_target:
                    diagnostics.append('unsupported_temporal_scope')
                elif point is None:
                    diagnostics.append('binding_missing')
            if (not any(e.get('status') == 'observed' and e.get('frame_id')
                        and str(e.get('reason', '')).strip() for e in evidence)
                    or (domains == ['actor_lane'] and item.get('resolution') == 'resolved'
                        and point is not None and not any(
                            e.get('status') == 'observed' and e.get('frame_id') == point['frame_id']
                            and e.get('time_s') == point['time_s']
                            and str(e.get('reason', '')).strip() for e in evidence))
                    or (point is not None and frame_ids is not None and point['frame_id'] not in frame_ids)):
                diagnostics.append('evidence_insufficient')
            records.append(dict(collection=collection, issue_id=issue_id,
                actor_ids=list(ids), world_paths=list(paths), target_frame=point,
                lane_id=lane_id, resolution=item.get('resolution'),
                diagnostics=list(dict.fromkeys(diagnostics))))
    return records


def question_issues(world, frames=None, *, strict=False):
    """Validate identity scope, active bindings and resolved evidence, not visual truth."""
    found = []
    actors = {'ego', *(a['actor_id'] for a in world['actor_hypotheses'])}
    frame_ids = None if frames is None else {f['frame_id'] for f in frames}
    for collection in ('questions_to_verify', 'ambiguities'):
        for index, item in enumerate(world.get(collection, [])):
            path = f'/{collection}/{index}'
            def add(rule, field, message):
                found.append(dict(rule_id=rule, kind='fact_conflict',
                                  path=path + field, message=message))
            ids = item.get('actor_ids', [])
            if (not isinstance(ids, list) or any(not isinstance(a, str) for a in ids)
                    or len(ids) != len(set(ids)) or set(ids) - actors):
                add('world.actor_binding', '/actor_ids', 'Actor question must bind unique existing actor_ids')
                continue
            if strict and collection == 'questions_to_verify' and item.get('impact_domains') == ['actor_lane']:
                contract = next(record for record in question_contracts(world, frames)
                                if record['collection'] == collection and record['issue_id'] == item['question_id'])
                for diagnosis in contract['diagnostics']:
                    if diagnosis in ('binding_missing', 'unsupported_temporal_scope', 'undetermined') or (
                            item.get('resolution') == 'resolved' and diagnosis == 'evidence_insufficient'):
                        found.append(dict(rule_id='world.question_contract', kind='fact_conflict',
                                          path=path, message=f'{diagnosis}: actor_lane needs one known Actor, '
                                          'an explicit fact path and a single target frame'))
            if 'actor_identity' not in item.get('impact_domains', []):
                continue
            if set(item['impact_domains']) != {'actor_identity'}:
                add('world.identity_question_scope', '/impact_domains',
                    'Ask Actor identity separately from lane, appearance, actor count and motion')
            if item.get('subject_field') not in (None, 'actor_identity'):
                add('world.identity_question_scope', '/subject_field',
                    'An identity issue cannot declare appearance or motion as its subject')
            binding = item.get('identity_binding') or {}
            bound_ids = binding.get('actor_ids', [])
            resolved = item.get('resolution') == 'resolved'
            # Accepted identity proof retires the unknown binding. A retained
            # binding must still agree with the question and its evidence.
            if (ids or bound_ids) and (not resolved or 'identity_binding' in item):
                if not ids or not bound_ids or set(ids) != set(bound_ids):
                    add('world.identity_binding', '/identity_binding',
                        'Known-Actor identity questions require matching actor_ids and identity_binding.actor_ids')
            evidence_ids = {e.get('frame_id') for e in item.get('evidence', [])
                            if e.get('frame_id') and str(e.get('reason', '')).strip()}
            required = set(binding.get('evidence_frame_ids', []))
            if not evidence_ids or (bound_ids and not required) or not required <= evidence_ids:
                add('world.identity_evidence', '/evidence',
                    'Identity questions need reasoned frame evidence for every identity_binding frame')
            if resolved and not any(e.get('status') == 'observed' and e.get('frame_id')
                                    and str(e.get('reason', '')).strip() for e in item.get('evidence', [])):
                add('world.identity_evidence', '/evidence',
                    'Resolved identity questions need observed frame evidence with a reason')
            if frame_ids is not None and (required | evidence_ids) - frame_ids:
                add('world.identity_evidence', '/evidence', 'Identity evidence references an unavailable source frame')
    if strict:
        for comparison in resolved_lane_comparisons(world, frames):
            if comparison['verdict'] != 'consistent':
                found.append(dict(rule_id='world.lane_conclusion.' + comparison['verdict'],
                    kind='fact_conflict', path='/questions_to_verify',
                    message=(comparison['verdict'] + ': independent formal lane conclusions ' +
                             '/'.join(comparison['issue_ids']) + ' require review')))
    return found


def resolved_lane_issues(world, frames=None, behavior=None, parameters=None):
    """Current delivery gate for formally closed lane facts; legacy reads stay frozen."""
    issues = [row for row in question_contracts(world, frames)
            if row['collection'] == 'questions_to_verify' and row['resolution'] == 'resolved'
            and any(q.get('question_id') == row['issue_id']
                    and q.get('impact_domains') == ['actor_lane']
                    for q in world.get('questions_to_verify', []))
            and set(row['diagnostics']) & {
                'binding_missing', 'unsupported_temporal_scope',
                'evidence_insufficient', 'fact_conflict', 'undetermined'}]
    if behavior is None:
        return issues
    from _lib.world.readiness import execution_dependencies
    retained = []
    for row in issues:
        issue = dict(question_id=row['issue_id'], actor_ids=row['actor_ids'],
                     world_paths=row['world_paths'], impact_domains=['actor_lane'])
        dependency = execution_dependencies(world, behavior, parameters,
            dict(questions_to_verify=[issue], ambiguities=[]))['decisions'][0]['decision']
        if dependency != 'excluded':
            retained.append(dict(row, dependency=dependency))
    return retained


def _lane_identity(world, lane_id):
    """Use the current road membership, or a uniquely mapped retired lane ID."""
    from _lib.contracts.v3 import ContractError
    from _lib.contracts.lane_locations import lane_locations

    try:
        locations = lane_locations(world['world']['road'])
    except (ContractError, KeyError, TypeError, ValueError):
        return None
    if lane_id not in locations:
        mapping = (world['world'].get('metadata', {}).get('lane_layout_review', {})
                   .get('adjudication', {}).get('lane_id_map', {}))
        if not isinstance(mapping, dict) or not isinstance(lane_id, str):
            return None
        mapped = mapping.get(lane_id)
        if (not isinstance(mapped, str) or mapped not in locations
                or list(mapping.values()).count(mapped) != 1):
            return None
        lane_id = mapped
    road_id, _ = locations[lane_id]
    return road_id, lane_id


def resolved_lane_comparisons(world, frames=None):
    """Compare independent formal answers only at the same Actor and target instant."""
    contracts = {row['issue_id']: row for row in question_contracts(world, frames)
                 if row['collection'] == 'questions_to_verify'}
    questions = [q for q in world.get('questions_to_verify', [])
                 if q.get('impact_domains') == ['actor_lane'] and q.get('resolution') == 'resolved']
    result = []
    for index, left in enumerate(questions):
        a = contracts[left['question_id']]
        for right in questions[index + 1:]:
            b = contracts[right['question_id']]
            if (len(a['actor_ids']) != 1 or a['actor_ids'] != b['actor_ids']
                    or a['target_frame'] is None or a['target_frame'] != b['target_frame']):
                continue
            blocked = {'binding_missing', 'unsupported_temporal_scope',
                       'evidence_insufficient', 'fact_conflict', 'undetermined'}
            identities = [_lane_identity(world, row['lane_id']) for row in (a, b)]
            if (blocked & set(a['diagnostics']) or blocked & set(b['diagnostics'])
                    or None in identities):
                verdict = 'undetermined'
            else:
                verdict = 'consistent' if identities[0] == identities[1] else 'fact_conflict'
            result.append(dict(issue_ids=[a['issue_id'], b['issue_id']],
                actor_id=a['actor_ids'][0], target_frame=a['target_frame'],
                world_paths=sorted(set(a['world_paths'] + b['world_paths'])),
                lane_identities=identities, verdict=verdict))
    return result


def execution_lane_comparison_issues(world, behavior, frames=None, parameters=None):
    """Gate only comparisons that the existing execution dependency rules require."""
    from _lib.world.readiness import execution_dependencies

    issues = []
    for comparison in resolved_lane_comparisons(world, frames):
        if comparison['verdict'] == 'consistent':
            continue
        issue = dict(question_id=comparison['issue_ids'][0], actor_ids=[comparison['actor_id']],
                     world_paths=comparison['world_paths'], impact_domains=['actor_lane'])
        dependency = execution_dependencies(world, behavior, parameters,
            dict(questions_to_verify=[issue], ambiguities=[]))['decisions'][0]['decision']
        if dependency != 'excluded':
            issues.append(dict(**comparison, dependency=dependency))
    return issues


def confirmation_covers(world, item, observations, frames=()):
    """One explicit complete answer may supersede local partial observations.

    Only atomic Actor identity/lane questions qualify. The answer must cover
    all bound evidence and every requested window; a partial is never promoted.
    Contradictory patches remain the caller's independent blocking condition.
    """
    if item.get('impact_domains') not in (['actor_identity'], ['actor_lane']):
        return False
    key = 'question_id' if 'question_id' in item else 'ambiguity_id'
    windows = [w for w in world.get('critical_intervals', [])
               if item[key] in w.get('question_ids' if key == 'question_id' else 'ambiguity_ids', [])]
    if not windows:
        return False
    times = {f['frame_id']: f['time_s'] for f in frames}
    required = set((item.get('identity_binding') or {}).get('evidence_frame_ids', []))
    required.update(e['frame_id'] for e in item.get('evidence', []) if e.get('frame_id'))
    required.update(e['frame_id'] for o in observations if o.get('answer_resolution') == 'partial'
                    for e in o.get('evidence', [o]) if e.get('frame_id'))
    if not required:
        return False
    for answer in observations:
        if answer.get('answer_resolution') != 'confirmed' or answer.get('status') != 'observed':
            continue
        evidence = [e for e in answer.get('evidence', [answer]) if e.get('status') == 'observed']
        ids = {e.get('frame_id') for e in evidence}
        observed_times = [times.get(e.get('frame_id'), e.get('time_s')) for e in evidence]
        observed_times = [t for t in observed_times if type(t) in (int, float)]
        if not required <= ids or not observed_times:
            continue
        end_of_frames = max(times.values()) if times else None
        if all(min(observed_times) <= w['start_s'] and max(observed_times) >=
               (min(w['end_s'], end_of_frames) if end_of_frames is not None else w['end_s'])
               for w in windows):
            return True
    return False


def point_confirmation_covers(contract, item, observations, frames=()):
    """Require an observed target-frame witness and all original cited frames."""
    point = contract['target_frame']
    if point is None or contract.get('lane_id') is None or \
            'binding_missing' in contract['diagnostics'] or \
            'unsupported_temporal_scope' in contract['diagnostics']:
        return False
    available = {frame['frame_id'] for frame in frames}
    required = {point['frame_id']}
    required.update(e['frame_id'] for e in item.get('evidence', []) if e.get('frame_id'))
    if available and not required <= available:
        return False
    return any(
        answer.get('status') == 'observed' and required <= {
            e.get('frame_id') for e in answer.get('evidence', [])
            if e.get('status') == 'observed' and str(e.get('reason', '')).strip()}
        for answer in observations if answer.get('answer_resolution') in ('confirmed', 'needs_world_patch'))
