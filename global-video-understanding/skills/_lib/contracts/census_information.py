"""Versioned information updates; immutable registration is not a model echo.

Expansion supplies existing registration fields and shared observation references
before the ordinary identity checks. Corrections are applied only after those
checks succeed, with their previous values and witnesses retained in the proof.
"""
import copy

from _lib.contracts.actors import vehicle_class_hint
from _lib.contracts.v3 import ContractError

REVISION = 'census-information.v1'
FIELDS = ('identity_description', 'color', 'vehicle_class')


def fail(message):
    raise ContractError(message, rule_id='identity.census.information')


def extend_schema(schema):
    """Existing binding/comparison descriptors are inherited, not re-generated."""
    vehicle = schema['properties']['vehicles']['items']
    for field in FIELDS:
        vehicle['required'].remove(field)
    string = dict(type='string', minLength=1)
    correction = dict(type='object', additionalProperties=False,
        required=['field', 'previous_value', 'value', 'reason', 'evidence_frame_ids'],
        properties=dict(field=dict(enum=list(FIELDS)), previous_value={}, value={}, reason=string,
            evidence_frame_ids=dict(type='array', minItems=2, uniqueItems=True, items=string)))
    vehicle['properties']['corrections'] = dict(type='array', maxItems=3, items=correction)
    vehicle['required'].append('corrections')
    refutation = schema['properties']['bindings']['items']['properties']['candidate_refutations']['items']
    refutation['required'].remove('subject_sightings')
    refutation['properties']['subject_sighting_frame_ids'] = dict(
        type='array', minItems=1, maxItems=12, uniqueItems=True, items=string)
    refutation['oneOf'] = [dict(required=['subject_sightings'],
                               **{'not': dict(required=['subject_sighting_frame_ids'])}),
                          dict(required=['subject_sighting_frame_ids'],
                               **{'not': dict(required=['subject_sightings'])})]


def expand(response, registry):
    """Pure projection; never rewrite a raw candidate or infer a distinction."""
    result = copy.deepcopy(response)
    old = {a['actor_id']: a for a in registry}
    groups = {g['vehicle_key'] for g in response['identity_groups']}
    splits = {v['split_from_actor_id'] for v in response['vehicles']} - {None}
    vehicles = {v['key']: v for v in result['vehicles']}
    for vehicle in result['vehicles']:
        aid = vehicle['actor_id']
        explicit = aid is None or vehicle['key'] in groups or aid in splits
        if explicit:
            if any(field not in vehicle for field in FIELDS) or vehicle['corrections']:
                fail('New/split/merged identities require complete descriptors and their dedicated identity proof')
        else:
            if aid not in old:
                fail('Information update references an unregistered identity')
            if any(field in vehicle for field in FIELDS):
                fail('Existing identity descriptors are inherited; use sightings or explicit corrections')
            actor = old[aid]
            vehicle.update(identity_description=actor['identity_description'],
                color=copy.deepcopy(actor.get('color', 'unknown')),
                vehicle_class=vehicle_class_hint(actor) or 'unknown')
        if vehicle.get('role') == 'comparison' and vehicle['corrections']:
            fail('Comparison witnesses cannot revise registration information')
    for binding in result['bindings']:
        for refutation in binding['candidate_refutations']:
            ids = refutation.pop('subject_sighting_frame_ids', None)
            if ids is None:
                continue
            vehicle = vehicles.get(binding['vehicle_key'])
            if binding['outcome'] not in {'existing', 'new'} or vehicle is None:
                fail('Shared subject sightings require a positive identity binding')
            sightings = {s['frame_id']: s for s in vehicle['sightings']}
            if any(fid not in sightings for fid in ids):
                fail('Shared subject sighting reference does not exist')
            refutation['subject_sightings'] = [copy.deepcopy(sightings[fid]) for fid in ids]
    return result


def apply_updates(packet, response, original_registry, result):
    """After full identity admission, apply only explicitly witnessed corrections.

    This is evidence-contract validation, not independent machine verification of
    image content. The visual worker must justify corrections from supplied frames.
    Road, lane, actor ID, first-seen and identity relationships are never patched here.
    """
    old = {a['actor_id']: a for a in original_registry}
    current = {a['actor_id']: a for a in result['registry']}
    records = []
    for vehicle in response['vehicles']:
        if vehicle.get('role') == 'comparison':
            continue
        aid = result['vehicle_ids'][vehicle['key']]
        actor = current[aid]
        registration = old.get(aid)
        updates = []
        seen = set()
        sights = {s['frame_id']: s for s in vehicle['sightings']}
        for correction in vehicle['corrections']:
            field = correction['field']
            if field in seen or not registration:
                fail('Correction must reference one unique field on an existing registration')
            seen.add(field)
            previous = (vehicle_class_hint(registration) or 'unknown') if field == 'vehicle_class' else registration.get(field, 'unknown')
            if correction['previous_value'] != previous or correction['value'] == previous:
                fail('Correction must bind the current value and propose an actual change')
            value = correction['value']
            if (not isinstance(value, str) or not value.strip()
                    or field in {'color', 'vehicle_class'} and value == 'unknown'):
                fail('Correction requires an explicit supported descriptor, not loss of a known fact')
            evidence = correction['evidence_frame_ids']
            first = registration['first_seen_frame_id']
            if (first not in evidence or any(fid not in sights for fid in evidence)
                    or not any(sights[fid]['time_s'] > sights[first]['time_s'] for fid in evidence)):
                fail('Correction requires the original identity anchor and a later reviewed sighting')
            # Proof must cover the whole supplied identity interval, not merely
            # two matching-looking stills selected for a changed appearance.
            start, end = min(s['time_s'] for s in sights.values()), max(s['time_s'] for s in sights.values())
            if not {f['frame_id'] for f in packet['frames'] if start <= f['time_s'] <= end} <= set(sights):
                fail('Correction omits supplied identity transition evidence')
            revised = (dict(status='observed', value=value, frame_id=first,
                            time_s=sights[first]['time_s'], reason=correction['reason'])
                       if field == 'vehicle_class' else value)
            actor[field] = copy.deepcopy(revised)
            for world_actor in result['world']['actor_hypotheses']:
                if world_actor['actor_id'] == aid:
                    world_actor[field] = copy.deepcopy(revised)
            updates.append(copy.deepcopy(correction))
        records.append(dict(actor_id=aid, registration=copy.deepcopy(registration),
            evidence_frame_ids=list(sights), corrections=updates))
    result['information_updates'] = dict(revision=REVISION, actors=records)
