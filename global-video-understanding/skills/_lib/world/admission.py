"""World admission without changing the public World contract.

The revision is frozen only for new Cases. Unknown observations remain unknown;
only explicitly redundant metadata is repaired by Python.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from _lib.contracts.v3 import ContractError, validate_world_facts
from _lib.paths import SKILLS
from _lib.runtime.case_core import read_json

REVISION = 'world-admission.v1'


def enabled(case):
    path = Path(case) / 'state/execution_policy.json'
    return path.is_file() and read_json(path).get('world_admission_revision') == REVISION


def duration_for(case):
    """All Case boundaries consume the same frozen media duration."""
    path = Path(case) / 'state/probe.json'
    if not path.is_file():
        return None  # Legacy unit callers without a probe retain their contract.
    value = read_json(path)['probe']['duration_s']
    import math
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ContractError('Invalid frozen source duration', rule_id='world.duration')
    return value


def pointer(parts):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def schema():
    return read_json(SKILLS / 'schemas/world_context.schema.json')


class AdmissionError(ContractError):
    code = 'schema_validation_failed'

    def __init__(self, issues):
        super().__init__('World admission: ' + '; '.join(i['message'] for i in issues))
        self.diagnostics = {'issues': issues}


def normalize(data, *, case=None, normalize_identity=True, normalize_actor_paths=False):
    """Never coerce physical road roles or fill observation/evidence defaults."""
    from _lib.contracts.world_metadata import normalize as adapt
    from _lib.world.topology import normalize_memberships
    value, repairs = adapt(data, fill_missing=True)
    if isinstance(value, dict):
        repairs.extend(normalize_memberships(value.get('world')))
        if normalize_actor_paths:
            from .actor_facts import normalize_question_paths
            value, path_repairs = normalize_question_paths(value)
            repairs.extend(path_repairs)
        from _lib.contracts.vehicle_census import temporal_admission
        if normalize_identity and case is not None and temporal_admission(case):
            actors = value.get('actor_hypotheses')
            for index, actor in enumerate(actors if isinstance(actors, list) else []):
                if not isinstance(actor, dict) or not isinstance(actor.get('identity_description'), str):
                    continue
                first = actor.get('first_seen_frame_id')
                original = actor['identity_description']
                # The immutable descriptor identifies the first sighting. Later
                # frame associations remain reviewable evidence, not identity text.
                parts = re.split(r'[；;。]', original)
                retained = []
                for part in parts:
                    refs = re.findall(r'frame_\d+', part)
                    if any(fid != first for fid in refs):
                        break
                    if part.strip():
                        retained.append(part.strip())
                if len(retained) == len([p for p in parts if p.strip()]):
                    continue
                if retained and (first in retained[0] or '首帧' in retained[0]):
                    anchor = '；'.join(retained)
                else:
                    from _lib.contracts.actors import vehicle_class_hint
                    color = actor.get('color')
                    if isinstance(color, dict):
                        color = color.get('value')
                    anchor = ' '.join(str(item) for item in
                        (color, vehicle_class_hint(actor), first) if item not in (None, '', 'unknown'))
                    if not anchor:
                        anchor = str(first or actor.get('actor_type') or 'unknown')
                actor['identity_description'] = anchor
                repairs.append(dict(path=f'/actor_hypotheses/{index}/identity_description',
                    before=original, after=anchor, reason='defer_unreviewed_temporal_identity',
                    validator_revision='vehicle-census-admission.v3'))
        # lane_knowledge is a closed structural claim. Some World responses also
        # put its prose explanation in an unsupported `reason` member. Discard
        # only that duplicate prose when the same claim has bound field evidence;
        # retain the immutable raw candidate and this adapter receipt for audit.
        road = value.get('world', {}).get('road', {}) if isinstance(value.get('world'), dict) else {}
        roads = road.get('roads', []) if isinstance(road, dict) else []
        evidence = value.get('field_evidence', {})
        for index, item in enumerate(roads if isinstance(roads, list) else []):
            knowledge = item.get('lane_knowledge') if isinstance(item, dict) else None
            key = f'world.road.roads[{index}].lane_knowledge'
            bound = evidence.get(key) if isinstance(evidence, dict) else None
            if (not isinstance(knowledge, dict) or 'reason' not in knowledge
                    or not isinstance(knowledge['reason'], str) or not knowledge['reason'].strip()
                    or not isinstance(bound, list) or not bound
                    or not all(name in knowledge for name in ('kind', 'count', 'anchors', 'evidence_frame_ids'))):
                continue
            reason = knowledge.pop('reason')
            repairs.append({'path': f'/world/road/roads/{index}/lane_knowledge/reason',
                            'before': reason, 'after': None,
                            'reason': 'unsupported_duplicate_lane_knowledge_prose'})
    return value, repairs


def schema_issues(data):
    from _lib.contracts.fidelity import validator
    errors = [(error, []) for error in validator('world_context.schema.json').iter_errors(data)]
    issues = []
    for error, prefix in errors:
        path = prefix + list(error.absolute_path)
        missing = ([key for key in error.validator_value if key not in error.instance]
                   if error.validator == 'required' and isinstance(error.instance, dict) else [None])
        for key in missing:
            parts = path + ([key] if key is not None else [])
            issues.append({'rule_id': 'schema.' + str(error.validator),
                           'kind': 'representation_error', 'path': pointer(parts),
                           'schema_path': pointer(error.absolute_schema_path),
                           'message': f'{pointer(parts) or "/"}: ' +
                           (f'missing required field {key}' if key is not None else error.message)})
    # Paths can contain both numeric indices and property names: sort encoded strings.
    unique = {(i['path'], i['rule_id'], i['message']): i for i in issues}
    return [unique[key] for key in sorted(unique)]


def _topology_issues(data, *, check_numbering=True):
    from _lib.world.topology import ObservationTopologyError, validate_observed_topology
    from _lib.world.readiness import check_lane_numbering
    try:
        if check_numbering:
            check_lane_numbering(data['world']['road'])
        _, warnings = validate_observed_topology(data['world'])
    except (ObservationTopologyError, ContractError) as error:
        return [{'rule_id':getattr(error, 'rule', None) or error.rule_id,'kind':getattr(error, 'kind', 'fact_conflict'),
                 'path':getattr(error,'path','/world/road'),
                 'related_paths':getattr(error,'related_paths',[]),'message':str(error)}]
    return warnings


def issues(data, duration=None, *, observation=True, case=None):
    found = schema_issues(data)
    if found:
        # Metadata errors do not prevent checking the independently well-shaped
        # road declarations. Never call topology when its own prerequisites fail.
        if isinstance(data,dict) and isinstance(data.get('world'),dict) and not any(
                not i['path'] or i['path']=='/world' or i['path'].startswith('/world/') for i in found):
            found.extend(_topology_issues(data))
        return found
    # Some legacy schema objects leave identity property types unconstrained.
    # Check those declared values before the older set-based validators run.
    for collection, key in (('actor_hypotheses','actor_id'), ('questions_to_verify','question_id'),
                            ('ambiguities','ambiguity_id')):
        seen = set()
        for index, item in enumerate(data.get(collection, [])):
            value = item.get(key)
            path = f'/{collection}/{index}/{key}'
            if not isinstance(value, str) or not value:
                found.append({'rule_id':'schema.type', 'kind':'representation_error',
                              'path':path, 'message':'Declared identity must be a nonempty string'})
            elif value in seen:
                found.append({'rule_id':'world.identity', 'kind':'fact_conflict',
                              'path':path, 'message':'Duplicate declared identity: ' + value})
            else:
                seen.add(value)
    if found:
        return found
    from _lib.world.readiness import check_world_declarations, world_consistency_issues
    # Only contract exceptions are content failures. Programming errors propagate.
    for check in (lambda: validate_world_facts(data, duration, observation_contract=observation),
                  lambda: check_world_declarations(data, declarations_only=True)):
        try:
            check()
        except ContractError as error:
            return [{'rule_id': error.rule_id or 'world.contract', 'kind': 'fact_conflict',
                     'path': getattr(error, 'path', ''), 'message': str(error)}]
    from . import actor_facts
    if actor_facts.enabled(case):
        found.extend(actor_facts.question_issues(data, actor_facts.frames_for(case),
                                                strict=actor_facts.strict_enabled(case)))
    found.extend(_topology_issues(data, check_numbering=False))
    found.extend({'rule_id': 'world.evidence_binding', 'kind': 'degraded_warning',
                  'path': '', 'message': message} for message in world_consistency_issues(data))
    return found


def blocking_issues(found):
    """Shared by candidate validation and every formal World admission."""
    return [i for i in found if i.get('kind') in (
        'schema_error', 'representation_error', 'fact_conflict')]


def validate(data, duration=None, *, observation=True, case=None):
    found = issues(data, duration, observation=observation, case=case)
    blocking = blocking_issues(found)
    warnings = [i for i in found if i not in blocking]
    if blocking:
        raise AdmissionError(blocking)
    return warnings


def required_outline():
    """Derive required keys from the actual schema, not another hand-written contract."""
    document = schema()
    rows = {}

    def visit(node, path, seen=()):
        ref = node.get('$ref')
        if ref and ref not in seen and ref.startswith('#/'):
            target = document
            for part in ref[2:].split('/'):
                target = target[part]
            visit(target, path, (*seen, ref))
        if node.get('required'):
            rows[path or '/'] = node['required']
        for key, child in node.get('properties', {}).items():
            visit(child, path + '/' + key, seen)
        if isinstance(node.get('items'), dict):
            visit(node['items'], path + '/*', seen)
    visit(document, '')
    return json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
