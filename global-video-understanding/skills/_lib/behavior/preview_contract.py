"""One contract for preview_relations.v1, including frozen legacy responses."""
import copy
from jsonschema import validate

SCOPE = 'preview_relations.v1'
MAX_REPLACEMENTS = 24
OUTCOMES = ('confirmed', 'corrected', 'inconclusive')
def enumeration(*values):
    return {'enum': list(values)}


TEXT = {'type': 'string', 'minLength': 1}
STRINGS = {'type': 'array', 'uniqueItems': True, 'items': TEXT}
LIMITATION_PROPERTIES = {
    'limitation_id': TEXT, 'reason': TEXT,
    'kind': enumeration('absolute_scale','acceleration_class','appearance','window_extent','execution_scope'),
    'affected_actor_ids': STRINGS, 'affected_event_ids': STRINGS,
}
LIMITATION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['limitation_id','kind','reason'], 'properties': LIMITATION_PROPERTIES,
    'allOf': [{'if': {'properties': {'kind': {'const': 'execution_scope'}}},
               'then': {'required': ['affected_actor_ids','affected_event_ids'],
                        'properties': {'affected_actor_ids': {'minItems': 1},
                                       'affected_event_ids': {'maxItems': 0}}}}],
}
ACTOR_PROPERTIES = {
    'actor_id': TEXT, 'lane_id': {'type': ['string', 'null']},
    'relative_position': enumeration('ego', 'ahead', 'behind', 'unknown'),
    'relative_motion': enumeration('ego', 'receding', 'approaching', 'steady', 'unknown'),
    'direction': enumeration('same_direction', 'opposite', 'unknown'),
    'lane_motion': enumeration('keeping', 'changing', 'unknown'),
    'evidence_frame_ids': STRINGS, 'reason': TEXT,
}
RELATION_PROPERTIES = {
    'shape_type': enumeration('straight', 'curved', 'unknown'),
    'event_state': enumeration('none', 'present', 'unknown'),
    'motion_conflicts': enumeration('none', 'present', 'unknown'),
    'time_range_s': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'number'}},
    'sample_times_s': {'type': 'array', 'minItems': 1, 'items': {'type': 'number'}},
    'evidence_frame_ids': STRINGS, 'geometry_reason': TEXT, 'event_reason': TEXT,
    'lane_order_right_to_left': STRINGS, 'lead_order_near_to_far': STRINGS,
    'ego_lane_lead_actor_id': {'type': ['string', 'null']},
    'non_blocking_limitations': {'type': 'array', 'items': LIMITATION_SCHEMA},
    'actors': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                       'required': list(ACTOR_PROPERTIES), 'properties': ACTOR_PROPERTIES}},
}
RELATIONS_SCHEMA = {'type': 'object', 'additionalProperties': False,
                    'required': list(RELATION_PROPERTIES), 'properties': RELATION_PROPERTIES}
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['base_sha256', 'outcome', 'replacements', 'relations'],
    'properties': {
        'base_sha256': {'type': 'string'},
        'outcome': {'enum': list(OUTCOMES)},
        'relations': RELATIONS_SCHEMA,
        'replacements': {'type': 'array', 'maxItems': MAX_REPLACEMENTS, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['path', 'value'],
            'properties': {'path': {'type': 'string'}, 'value': {}}}},
    },
    'allOf': [
        {'if': {'properties': {'outcome': {'const': 'confirmed'}}},
         'then': {'properties': {'replacements': {'maxItems': 0}}}},
        {'if': {'properties': {'outcome': {'const': 'corrected'}}},
         'then': {'properties': {'replacements': {'minItems': 1}}}},
    ],
}


class PreviewStopped(ValueError):
    def __init__(self, code, message, *, rule_id=None, required_change=None, **details):
        super().__init__(message)
        self.code = code
        self.diagnostics = {'code': code, 'stage': 'preview_review',
                            'rule_id': rule_id or code, 'message': message,
                            'required_change': required_change or 'Correct the candidate against the frozen evidence.'}
        self.diagnostics.update(owner='behavior', actor_id=None, field_path=None,
                                expected=None, actual=None, retry_kind='new_evidence')
        self.diagnostics.update(details)


class PreviewEvidenceError(ValueError):
    code = 'schema_validation_failed'

    def __init__(self, actor_id, path, expected, actual):
        self.actor_id = actor_id
        self.rule_id = 'preview.actor_evidence'
        super().__init__(f'preview actor evidence missing or inconsistent: {actor_id} {path}: expected {expected!r}; actual {actual!r}')
        self.diagnostics = dict(code=self.code, rule_id=self.rule_id, owner='behavior',
                                actor_id=actor_id, field_path=path, expected=expected,
                                actual=actual, retry_kind='content_repair', message=str(self))


def contract_error(packet, message, *, rule_id, field_path, expected, actual,
                   code='schema_validation_failed', retry_kind='content_repair',
                   owner='behavior', actor_id=None, legacy_code=None, required_change=None):
    """New request diagnostics must not reinterpret a frozen legacy protocol."""
    if 'required_actor_evidence' not in packet:
        return (PreviewStopped(legacy_code, message, required_change=required_change)
                if legacy_code else ValueError(message))
    details = dict(code=code, rule_id=rule_id, owner=owner, actor_id=actor_id,
                   field_path=field_path, expected=expected, actual=actual,
                   retry_kind=retry_kind, message=message)
    if legacy_code:
        return PreviewStopped(code, message, required_change=required_change, **{
            k: v for k, v in details.items() if k not in {'code', 'message'}})
    error = ValueError(message)
    error.code = code
    error.diagnostics = details
    return error


def actor_evidence_requirements(actors, frames):
    ids = [f['frame_id'] for f in frames]
    return {a['actor_id']: (ids if a.get('simulation_role') == 'participant' else
            [fid for fid in ids if any(p.get('frame_id') == fid for p in a['trajectory'])]) for a in actors}


def response_schema():
    """The optional context field is available only in newly generated requests."""
    schema = copy.deepcopy(SCHEMA)
    schema['properties']['relations']['properties']['actors']['items']['properties']['context_frame_ids'] = copy.deepcopy(STRINGS)
    return schema


def output_rule():
    return (f'Return exactly {", ".join(SCHEMA["required"])}. '
            f'outcome is {"|".join(OUTCOMES)}. confirmed requires zero replacements; '
            f'corrected requires 1..{MAX_REPLACEMENTS} effective replacements; '
            f'inconclusive allows 0..{MAX_REPLACEMENTS} proposed replacements, which are not committed. '
            'Unknown/conflicting core relations require inconclusive. Absolute-scale uncertainty may remain. '
            'execution_scope requires nonempty affected_actor_ids and empty affected_event_ids; '
            'these actors must be evidenced, physically separated background actors excluded from execution. '
            'Behavior limitations with scope_binding.kind=physically_separated_opposing_observations '
            'are validated deterministically and must not be repeated here with invented Actor IDs. '
            'Only unique assigned lane_id/detail leaves; no same-value or whole-trajectory replacements.')


def _scope_fields_required(schema):
    try:
        limitation = schema['properties']['relations']['properties']['non_blocking_limitations']['items']
        required = limitation['allOf'][0]['then']['required']
        return {'affected_actor_ids','affected_event_ids'} <= set(required)
    except (KeyError, IndexError, TypeError):
        return False


def normalize_legacy_response(response, packet):
    """Bind old execution_scope claims to the conservative excluded Actor set.

    Old frozen requests lacked dependency fields. The only lossless derivation is
    the complete set of background Actors whose reviewed relation is opposite
    and whose trajectory has no ego-road lane assignment. Anything less remains
    invalid instead of parsing IDs from prose.
    """
    result = __import__('copy').deepcopy(response)
    repairs = []
    if _scope_fields_required(packet.get('response_schema', {})):
        return result, repairs
    relations = {item.get('actor_id'): item for item in result.get('relations', {}).get('actors', [])}
    excluded = sorted(actor['actor_id'] for actor in packet.get('actors', [])
                      if actor.get('simulation_role') == 'background'
                      and actor.get('actor_id') in relations
                      and relations[actor['actor_id']].get('direction') == 'opposite'
                      and all(point.get('lane_id') is None for point in actor.get('trajectory', [])))
    for item in result.get('relations', {}).get('non_blocking_limitations', []):
        if item.get('kind') != 'execution_scope':
            continue
        if not excluded or packet.get('events'):
            continue
        item['affected_actor_ids'] = excluded
        item['affected_event_ids'] = []
        repairs.append({'limitation_id': item.get('limitation_id'),
                        'derived_affected_actor_ids': excluded,
                        'reason': 'Frozen request predates dependency fields; bound to every reviewed opposite background Actor with no ego-road lane assignment.'})
    return result, repairs


def validate_envelope(response, packet):
    # Frozen old requests did not ask for outcome. Do not edit them or infer a
    # successful review here: inference occurs only after all semantic checks.
    normalized, repairs = normalize_legacy_response(response, packet)
    if isinstance(normalized, dict) and 'outcome' not in normalized and not packet.get('response_schema'):
        validate({**normalized, 'outcome': 'inconclusive'}, SCHEMA)
    elif 'required_actor_evidence' in packet:
        validate(normalized, response_schema())
    else:
        validate(normalized, SCHEMA)
    return normalized, repairs


def review_outcome(response, actors):
    facts = response['relations']
    participant_ids = {a['actor_id'] for a in actors if a.get('simulation_role') == 'participant'}
    uncertain = (facts['shape_type'] == 'unknown' or facts['event_state'] == 'unknown'
                 or facts['motion_conflicts'] != 'none')
    uncertain |= any(r['actor_id'] in participant_ids and
                     (r['lane_id'] is None or any(r[k] == 'unknown' for k in
                      ('relative_position', 'relative_motion', 'direction', 'lane_motion')))
                     for r in facts['actors'])
    outcome = response.get('outcome')
    if outcome in ('confirmed', 'corrected') and uncertain:
        raise PreviewStopped('preview_evidence_insufficient', 'preview cannot confirm unknown/conflicting core relations')
    if outcome == 'inconclusive' or uncertain:
        raise PreviewStopped('preview_evidence_insufficient',
                             'Preview review is inconclusive; observations remain unchanged.',
                             required_change='Resolve the reported core relations using evidence before retrying.')
    return outcome or ('corrected' if response['replacements'] else 'confirmed')
