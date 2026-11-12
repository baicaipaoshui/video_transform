"""Bounded lane-layout adjudication inside the existing World closure stage."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from _lib.runtime.case_core import read_json, sha256

from _lib.contracts.v3 import ContractError
from .single_world import layout_review, layout_schema, layout_differences, validate_layout

ISSUE = 'world.layout.review_required '
REVISION = 'lane-layout-closure.v1'
LEGACY_DECISION_REVISION = 'lane-layout-decision.v2'
DECISION_REVISION = 'lane-layout-decision.v3'
EVIDENCE_DECISION_REVISION = 'lane-layout-decision.v4'
DERIVED_DECISION_REVISION = 'lane-layout-decision.v5'
PHYSICAL_DECISION_REVISION = 'lane-layout-decision.v6'
CORRECTION_DECISION_REVISION = 'lane-layout-decision.v7'
SOURCE_CONSISTENCY_REVISION = 'correlated-layout-sources.v1'
PHYSICAL_DECISION_REVISIONS = frozenset({PHYSICAL_DECISION_REVISION, CORRECTION_DECISION_REVISION})
DERIVED_DECISION_REVISIONS = frozenset({DERIVED_DECISION_REVISION, *PHYSICAL_DECISION_REVISIONS})
DECISION_REVISIONS = frozenset({LEGACY_DECISION_REVISION, DECISION_REVISION,
                                EVIDENCE_DECISION_REVISION, *DERIVED_DECISION_REVISIONS})
ROADSIDE_DECISION_REVISIONS = frozenset({DECISION_REVISION, EVIDENCE_DECISION_REVISION,
                                         *DERIVED_DECISION_REVISIONS})
CANONICAL_LANE_FIELDS = ('lane_id', 'road_id', 'lane_type', 'travel_direction',
                         'ordinal_from_right', 'right_boundary', 'left_boundary')
BOUNDARY_ALIASES = ('right_boundary_id', 'left_boundary_id')
BOUNDARY_REGISTRY_RULE = 'world.layout.boundary_registry'
REPAIRABLE_RULES = frozenset({
    'world.layout.batch_disagreement',
    'world.layout.correlated_source_disagreement',
    'world.layout.claim_adjudication',
    'world.layout.claim_dispute',
    'world.layout.patch_shape',
    BOUNDARY_REGISTRY_RULE,
    'world.layout.decision_shape',
    'world.layout.boundary_reference',
    'world.layout.corridor_reference',
    'world.layout.lane_mapping',
})


def repairable_outcome(outcome):
    """Recognize bounded layout corrections, including the pre-rule-id legacy stop."""
    if not isinstance(outcome, dict) or outcome.get('code') != 'world_patch_contract_blocked':
        return False
    if outcome.get('rule_id') in REPAIRABLE_RULES:
        return True
    # Before boundary_registry had its own stable rule, this exact validator
    # failure was persisted as the generic world.layout.patch terminal stop.
    return (outcome.get('rule_id') in {None, 'world.layout.patch'}
            and 'world.layout.source_boundary_chain' in str(
                outcome.get('message') or outcome.get('reason') or ''))


def applicable(issues):
    return bool(issues) and all(str(item).startswith(ISSUE) for item in issues)


def layout_fact_path(path):
    if path in {'world.road.ego_direction_lanes', 'world.road.roads[0].lane_ids',
                'world.road.roads[0].lane_knowledge'}:
        return True
    if path.startswith('world.road.roads[0].lane_knowledge.'):
        return True
    match = re.fullmatch(r'world\.road\.(lanes|lane_markings|roadside_areas)(?:\[\d+\])?(?:\.(\w+))?', path)
    return bool(match and (match[2] is None or match[2] in {
        'lane_id', 'road_id', 'lane_ids', 'lane_type', 'travel_direction', 'ordinal_from_right',
        'left_boundary', 'right_boundary', 'type', 'marking_type', 'between', 'side', 'area_type'}))


def handles_conflicts(result, batches):
    unresolved = set(result['unresolved_conflict_ids'])
    conflicts = [c for b in batches for c in b['semantic_conflicts'] if c['conflict_id'] in unresolved]
    return len(conflicts) == len(unresolved) and all(
        layout_fact_path(c['world_path']) and set(c['impact_domains']) <= {'road_topology', 'lane_count', 'actor_lane'}
        for c in conflicts)


def paths(world):
    review = layout_review(world)
    road = world['world']['road']
    if not review or len(road.get('roads', [])) != 1 or world['world'].get('junctions'):
        raise ContractError('Lane layout closure requires one road without junctions',
                            rule_id='world.layout.scope')
    result = ['/world/road/roads/0/lane_ids', '/world/road/roads/0/lane_knowledge',
              '/world/road/lanes', '/world/road/lane_markings', '/world/road/roadside_areas',
              '/world/road/ego_direction_lanes', '/world/metadata/lane_layout_review/observation']
    result += [f'/actor_hypotheses/{i}/hypothesized_lane'
               for i, actor in enumerate(world['actor_hypotheses'])
               if actor.get('actor_type') in {'vehicle', 'car', 'suv', 'van'} or actor['actor_id'] == 'ego']
    # Only bound road questions can be closed, not appearance or dynamic facts.
    for i, question in enumerate(world['questions_to_verify']):
        if (question['question_id'] == review['question_id'] or
                (question.get('world_paths') and all(layout_fact_path(p) for p in question['world_paths'])
                 and set(question['impact_domains']) <= {'lane_count', 'road_topology', 'actor_lane'})):
            result += [f'/questions_to_verify/{i}/resolution', f'/questions_to_verify/{i}/evidence']
    facts = [p for p in result if p.startswith(('/world/', '/actor_hypotheses/'))]
    result += ['/field_evidence/' + dotted(p).replace('~', '~0').replace('/', '~1') for p in facts]
    return sorted(result)


def dotted(pointer):
    parts = pointer.strip('/').split('/')
    value = parts[0]
    for part in parts[1:]:
        value += '[' + part + ']' if part.isdecimal() else '.' + part
    return value


def configure(payload, world, schema):
    review = layout_review(world)
    payload['lane_layout_contract'] = dict(revision=REVISION, question_id=review['question_id'],
        ego_direction_lane_fields=list(CANONICAL_LANE_FIELDS))
    payload['output_contract']['lane_id_map'] = 'Object mapping every old lane ID to its same physical lane after correction; identity map when unchanged.'
    payload['resolution_values'] = ['resolved']
    payload['focused_context']['scope_rule'] = (
        'Independently examine the original evidence frames before comparing the two observations. '
        'World owns the decision; Batch is a challenge, not ground truth. Determine only the visible '
        'ego-direction cross-section and initial actor corridors. Correct count, full boundary chain, '
        'lane definitions, markings, right roadside and all affected lane references together. '
        'Do not infer opposing lane count from an unknown opposite boundary. Preserve Actor identities, '
        'known metrics, geometry, environment, all questions and unrelated facts. '
        'Use lane_id_map to preserve each old physical lane and its properties across renumbering. '
        'Submit layout_observation at the assigned metadata path with local physical boundary identifiers; '
        'these identifiers must be the boundary IDs used in lane left/right_boundary. '
        'Every changed fact needs corresponding observed field_evidence at the assigned exact fact path. '
        'Resolve the review question only after its full cross-section and initial ego are evidenced. '
        'If no unique evidence-backed layout can be established, use needs_evidence; never invent a lane. '
        'A minimum TOTAL lane count caused by unknown opposing lanes does not forbid exact SAME-direction count. '
        'For every confirmed ego-direction driving lane use lane_type=driving, '
        'travel_direction=forward|same_as_ego, ordinal_from_right and the canonical '
        'right_boundary/left_boundary keys; never use right_boundary_id/left_boundary_id. '
        'If the chosen count or initial ego ordinal rejects an exact independent Batch observation, '
        're-examine that disputed corridor on the original frames instead of repeating the initial World premise.')
    road_schema = schema['properties']['world']['properties']['road']['properties']
    lane_schema = copy.deepcopy(road_schema['lanes'])
    lane_schema['description'] = ('The confirmed ego-direction lanes must each provide ' +
        ', '.join(CANONICAL_LANE_FIELDS) + '. Do not use boundary_id aliases. '
        'Existing opposing or non-driving lanes retain their original known facts.')
    lane_schema['items']['properties'].update({
        'lane_type': {'type': 'string'}, 'travel_direction': {'type': 'string'},
        'ordinal_from_right': {'type': 'integer', 'minimum': 0},
        'right_boundary': {'type': 'string', 'minLength': 1},
        'left_boundary': {'type': 'string', 'minLength': 1},
    })
    lane_schema['items']['not'] = {'anyOf': [{'required': [key]} for key in BOUNDARY_ALIASES]}
    payload['value_schemas'] = {
        '/world/metadata/lane_layout_review/observation': layout_schema(),
        '/world/road/lanes': lane_schema,
        **{'/world/road/' + k: road_schema[k] for k in ('lane_markings', 'roadside_areas', 'ego_direction_lanes')},
        **{'/world/road/roads/0/' + k: road_schema['roads']['items']['properties'][k]
           for k in ('lane_ids', 'lane_knowledge')},
    }


def decision_schema(revision=DECISION_REVISION, *, roadside_intervals=True):
    """One evidence-backed physical cross-section, without derived World fields."""
    refs = {'type':'array', 'minItems':1, 'uniqueItems':True,
            'items':{'type':'string', 'minLength':1}}
    witness = {'reason':{'type':'string', 'minLength':1}, 'evidence_frame_ids':refs}
    boundary = {'type':'object', 'additionalProperties':False,
        'required':['boundary_id','kind','reason','evidence_frame_ids'],
        'properties':{'boundary_id':{'type':'string','minLength':1},
            'kind':{'enum':['lane_marking','curb','road_edge']},
            'marking_type':{'type':'string','minLength':1}, **witness}}
    if revision not in DERIVED_DECISION_REVISIONS:
        boundary['required'].append('marking_type')
    corridor = {'type':'object','additionalProperties':False,
        'required':['ordinal_from_right','reason','evidence_frame_ids'],
        'properties':{'ordinal_from_right':{'type':'integer','minimum':0}, **witness}}
    roadside = {'type':'object','additionalProperties':False,
        'required':['type','reason','evidence_frame_ids'],
        'properties':{'type':{'enum':(['curbside_parking','shoulder','sidewalk',
                                      'driveway_apron','curb_only','unknown']
                                     if revision in DERIVED_DECISION_REVISIONS else
                                     ['curbside_parking','shoulder','driveway_apron',
                                      'curb_only','unknown'])}, **witness}}
    if revision in ROADSIDE_DECISION_REVISIONS:
        roadside['required'].extend(('outer_boundary', 'surface'))
        roadside['properties']['outer_boundary'] = {'oneOf':[boundary, {'type':'null'}]}
        roadside['properties']['surface'] = {'enum':['paved','unpaved','unknown']}
    elif revision != LEGACY_DECISION_REVISION:
        raise ValueError('Unsupported lane layout decision revision: ' + str(revision))
    result = {'type':'object','additionalProperties':False,
        'required':['boundaries_right_to_left','ego_initial','right_roadside','old_lane_corridors',
                    'actor_corridors'],
        'properties':{
            'boundaries_right_to_left':{'type':'array','minItems':2,'maxItems':17,'items':boundary},
            'ego_initial':corridor,
            'right_roadside':roadside,
            'old_lane_corridors':{'type':'object','additionalProperties':corridor},
            'actor_corridors':{'type':'object','additionalProperties':corridor},
        }}
    if revision in {EVIDENCE_DECISION_REVISION, *DERIVED_DECISION_REVISIONS}:
        assessment = {'type':'object','additionalProperties':False,
            'required':['verdict','reason','evidence_frame_ids'],
            'properties':{'verdict':{'enum':['supported','refuted','not_asserted']},
                **witness,
                'disputed_interval':{'type':'object','additionalProperties':False,
                    'required':['right_boundary_id','left_boundary_id','observed_role'],
                    'properties':{
                        'right_boundary_id':{'type':'string','minLength':1},
                        'left_boundary_id':{'type':'string','minLength':1},
                        'observed_role':{'enum':['driving','non_driving']}}}}}
        result['required'].append('driving_corridors')
        result['properties']['driving_corridors'] = {'type':'array','minItems':1,'maxItems':16,
            'items':{'type':'object','additionalProperties':False,
                'required':['right_boundary_id','left_boundary_id','reason','evidence_frame_ids'],
                'properties':{'right_boundary_id':{'type':'string','minLength':1},
                    'left_boundary_id':{'type':'string','minLength':1},**witness}}}
        if revision == EVIDENCE_DECISION_REVISION:
            result['required'].append('claim_assessments')
            result['properties']['claim_assessments'] = {'type':'array','minItems':1,
                'items':{'type':'object','additionalProperties':False,
                    'required':['claim_id','count','ego_initial'],
                    'properties':{'claim_id':{'type':'string','minLength':1},
                        'count':assessment,'ego_initial':assessment}}}
        else:
            result['required'].append('claim_disputes')
            result['properties']['claim_disputes'] = {'type':'array','maxItems':32,
                'items':{'type':'object','additionalProperties':False,
                    'required':['claim_id','field','reason','evidence_frame_ids'],
                    'properties':{'claim_id':{'type':'string','minLength':1},
                        'field':{'enum':['count','ego_initial']},**witness,
                        'disputed_interval':assessment['properties']['disputed_interval']}}}
    if revision in DERIVED_DECISION_REVISIONS and roadside_intervals:
        # Newly frozen v5 requests inventory every physical band. Historical
        # v5 requests retain their embedded scalar schema and compile below.
        result['required'].remove('right_roadside')
        del result['properties']['right_roadside']
        band = {'type':'object','additionalProperties':False,
            'required':['inner_boundary_id','outer_boundary','type','surface',
                        'reason','evidence_frame_ids'],
            'properties':{
                'inner_boundary_id':{'type':'string','minLength':1},
                'outer_boundary':boundary,
                'type':{'enum':['paved_strip','shoulder','sidewalk','curbside_parking']},
                'surface':{'enum':['paved','unpaved','unknown']}, **witness}}
        result['required'].append('roadside_intervals')
        result['properties']['roadside_intervals'] = {'type':'array','maxItems':16,'items':band}
    if revision in PHYSICAL_DECISION_REVISIONS:
        result['properties']['claim_disputes']['items']['properties']['field'] = {'const':'count'}
        corridor['required'].remove('ordinal_from_right')
        del corridor['properties']['ordinal_from_right']
        for side in ('right', 'left'):
            key = side + '_boundary_id'
            corridor['required'].append(key)
            corridor['properties'][key] = {'type':'string', 'minLength':1}
    if revision == CORRECTION_DECISION_REVISION:
        interval = result['properties']['claim_disputes']['items']['properties']['disputed_interval']
        interval['properties']['absent_boundary_ids'] = {
            'type':'array', 'minItems':1, 'uniqueItems':True,
            'items':{'type':'string', 'minLength':1}}
    return result


def _neutral_prior_structure(context):
    """Retain identifiers needed for remapping without replaying old visual claims."""
    current = context.pop('current_values', {})
    lanes = current.get('/world/road/lanes', [])
    markings = current.get('/world/road/lane_markings', [])
    areas = current.get('/world/road/roadside_areas', [])
    lane_ids = current.get('/world/road/roads/0/lane_ids', [])
    keep = lambda row, fields: {key:copy.deepcopy(row[key]) for key in fields if key in row}
    identities = context.get('actor_identity_lock', [])
    actor_refs = []
    for path, lane_id in current.items():
        match = re.fullmatch(r'/actor_hypotheses/(\d+)/hypothesized_lane', path)
        if not match or lane_id is None:
            continue
        index = int(match.group(1))
        if index < len(identities) and identities[index].get('actor_id'):
            actor_refs.append({'actor_id':identities[index]['actor_id'], 'lane_id':lane_id})
    context['prior_world_structure'] = {
        'purpose': ('Identifier continuity only. These labels and relationships are not visual '
                    'evidence and may contain the layout error being adjudicated.'),
        'lane_ids': copy.deepcopy(lane_ids) if isinstance(lane_ids,list) else [],
        'lanes': [keep(row, ('lane_id','road_id','right_boundary','left_boundary'))
                  for row in lanes if isinstance(row,dict)],
        'boundary_ids': [row['marking_id'] for row in markings
                         if isinstance(row,dict) and isinstance(row.get('marking_id'),str)],
        'roadside_areas': [keep(row, ('area_id','road_id','side','boundary_left','boundary_right'))
                           for row in areas if isinstance(row,dict)],
        'actor_lane_refs': actor_refs,
    }


def _layout_claims(context, world):
    """Freeze the competing facts without either source's persuasive prose."""
    review = layout_review(world) or {}
    claims = []
    def add(claim_id, source, observation):
        if not isinstance(observation, dict):
            return
        count = observation.get('same_direction_count') or {}
        ego = observation.get('ego_initial') or {}
        if count.get('kind') not in {'exact','minimum','unknown'}:
            return
        witnesses = sorted({fid for row in observation.values() if isinstance(row,dict)
                            for fid in row.get('evidence_frame_ids', []) if isinstance(fid,str)})
        claims.append(dict(claim_id=claim_id, source=source,
            same_direction_count={'kind':count['kind'], 'count':count.get('count')},
            ego_initial_ordinal_from_right=ego.get('ordinal_from_right'),
            right_roadside_type=(observation.get('right_roadside') or {}).get('type','unknown'),
            boundaries_right_to_left=copy.deepcopy(observation.get('boundaries_right_to_left', [])),
            evidence_frame_ids=witnesses))
    add('world', 'world', review.get('observation'))
    for batch in context.get('relevant_batch_evidence', []):
        for index, item in enumerate(batch.get('observations', [])):
            if item.get('question_id') == review.get('question_id'):
                add(f"{batch['batch_id']}:{index}", 'batch', item.get('layout_observation'))
    return claims


def configure_decision(payload, revision=DECISION_REVISION):
    payload['lane_layout_contract']['revision'] = revision
    if revision == CORRECTION_DECISION_REVISION:
        payload['lane_layout_contract']['source_consistency_revision'] = SOURCE_CONSISTENCY_REVISION
    payload['allowed_paths'] = []
    payload['value_schemas'] = {'decision':decision_schema(revision)}
    payload['output_contract'] = {
        'contract_version':revision,
        'request_sha256':'copy exact request hash',
        'base_sha256':'copy exact base hash',
        'status':'decision|needs_evidence|blocked_by_patch_contract',
        'decision':'One layout decision matching value_schemas.decision when status=decision; '
                   'no World operations or derived road fields',
    }
    if revision in ROADSIDE_DECISION_REVISIONS:
        _neutral_prior_structure(payload['focused_context'])
    if revision in {EVIDENCE_DECISION_REVISION, *DERIVED_DECISION_REVISIONS}:
        context = payload['focused_context']
        world = read_json(payload['base_path'])
        context['layout_claims'] = _layout_claims(context, world)
        context['relevant_batch_evidence'] = []
        context['verification_items'] = [
            {'question_id':row['question_id']}
            for row in context.get('verification_items', []) if row.get('question_id')]
        context['scope_rule'] = (
            'First independently inventory physical boundaries and the corridor between each adjacent '
            'pair from original frames. Then compare every normalized layout_claim equally. '
            'Neither World nor Batch is presumed correct. For each claim submit count and initial-ego '
            'assessments. A refuted count needs two direct frames and the specific adjacent boundary '
            'interval: observed_role=driving when the claim omitted a lane, or non_driving when it '
            'counted a roadside band as a lane. A refuted initial-ego ordinal needs the true first '
            'frame. If an asserted claim cannot be supported or refuted from supplied frames, return '
            'needs_evidence; do not submit a decision. Old World IDs are for continuity only. '
            'The boundary array contains N+1 physical boundaries of N driving corridors. '
            'Give the ego corridor ordinal, a one-to-one old lane ID to corridor ordinal mapping, '
            'and actor corridor changes only when directly evidenced. Python derives the repeated '
            'World layout fields. Empty occupancy alone is not evidence that a corridor is roadside. '
            'A continuous shoulder or parking band has a separate outer edge and cannot overlap a '
            'driving lane. Preserve known metrics and identities; if the complete map is not '
            'established, return needs_evidence.')
        if revision in DERIVED_DECISION_REVISIONS:
            context['scope_rule'] = (
                'First independently inventory original-frame physical boundaries and every '
                'driving corridor. Then compare the normalized World and Batch claims without '
                'preferring either source. Do not output supported/refuted labels: Python derives '
                'numeric claim verdicts. A minimum count is a lower bound, so a larger chosen '
                'count does not dispute that numeric claim; correct missing physical geometry '
                'through the chosen boundary chain. Submit claim_disputes only for exact count '
                'mismatches, chosen counts below a minimum, or known initial-ego ordinal mismatches. '
                'Every count dispute needs two direct frames and an adjacent physical interval, '
                'driving when the claim omitted a lane or non_driving when it counted a roadside '
                'band. Ego disputes need the real first frame. For curb and road_edge boundaries '
                'kind alone specifies the type; omit marking_type. Only lane_marking needs a '
                'visual marking_type. Old World IDs are for continuity, not visual evidence. '
                'Empty occupancy alone does not make a corridor roadside. A continuous right '
                'shoulder, sidewalk, or parking band must have a separate outer edge and cannot overlap '
                'a driving lane. Preserve known metrics and identities; if key physical facts '
                'cannot be decided from supplied frames, return needs_evidence.')
    else:
        payload['focused_context']['scope_rule'] = (
        'Adjudicate the ego-direction driving cross-section once from original frames. '
        'An independent Batch exact same-direction count and non-null initial ego ordinal are '
        'acceptance constraints for a decision response: preserve them, or return needs_evidence '
        'when the original frames do not support them; never emit a conflicting decision. '
        'Prior World structure supplies identifiers for remapping only and is not visual evidence. '
        'The boundary array contains N+1 physical boundaries of N driving corridors; '
        'the area outside the rightmost driving boundary is right_roadside. '
        'Label each boundary as lane_marking, curb, or road_edge and provide its actual '
        'marking_type, reason, and direct frame IDs. Give the ego corridor ordinal, '
        'a one-to-one old lane ID to corridor ordinal mapping, and actor corridor changes '
        'only when directly evidenced. Python derives all World lane arrays, counts, '
        'boundary registration, references, and exact-path evidence. Preserve known '
        'lane metrics and identities. If the complete map or any boundary cannot be '
        'established, return needs_evidence with zero operations; do not guess. An empty paved '
        'corridor remains a driving lane when adjacent physical boundaries establish lane width; '
        'vehicle occupancy is not a lane-existence criterion, and a faded dashed or reflector '
        'line between two lane-width corridors is an interior separator, not the road edge.')
    if revision in ROADSIDE_DECISION_REVISIONS:
        payload['focused_context']['scope_rule'] += (
            ' For a continuous right shoulder or curbside parking band, the first driving '
            'boundary is its INNER edge, not the curb beyond it. Provide the separate '
            'outer_boundary with direct frame evidence and state whether its surface is '
            'paved, unpaved or unknown; use null outer_boundary for other roadside types. '
            'Reuse existing boundary IDs for the same physical line. Resolve an existing '
            'continuous right roadside area against this decision; if the band, its outer '
            'edge, or its separation from the driving lane cannot be established, stop '
            'with needs_evidence rather than assigning the same pavement twice.')
        if revision in DERIVED_DECISION_REVISIONS:
            payload['focused_context']['scope_rule'] += (
                ' For new requests, roadside_intervals inventories EVERY continuous physical '
                'band from the rightmost driving edge outward, including a paved strip followed '
                'by a sidewalk. Each inner_boundary_id must equal the preceding outer edge; '
                'provide a separately evidenced outer_boundary for each band. An empty array '
                'means no continuous band. Do not collapse bands because they share an execution '
                'type. Old area IDs are continuity hints, not visual evidence. If an old band is '
                'missing from the visual chain and cannot be refuted, return needs_evidence.')


    if revision in PHYSICAL_DECISION_REVISIONS:
        scope = payload['focused_context']['scope_rule']
        scope = scope.replace('or known initial-ego ordinal mismatches.', 'only; initial-ego discrepancies are compiled by Python.')
        scope = scope.replace('Ego disputes need the real first frame.', 'The ego boundary pair needs the real first frame.')
        payload['focused_context']['scope_rule'] = scope + (
            ' For ego_initial, old_lane_corridors and actor_corridors submit only '
            'right_boundary_id/left_boundary_id of adjacent driving boundaries with evidence; '
            'never output ordinal_from_right. Submit only count disputes; Python derives initial-ego '
            'disputes from the evidenced boundary pair, zero-based positions and all IDs. '
            'Use needs_evidence if travel direction, boundary order or physical membership is uncertain.')
    if revision == CORRECTION_DECISION_REVISION:
        payload['focused_context']['scope_rule'] += (
            ' If World and an independent Batch agree on the initial ego corridor, a different '
            'corridor requires a separate evidence resolution; return needs_evidence here rather '
            'than replacing both matching observations with one contradictory decision. '
            ' A lower count may also refute nonexistent internal dividers: submit a driving '
            'disputed_interval bounded by two surviving old boundaries, absent_boundary_ids '
            'listing every old internal divider between them, and at least two frames showing '
            'both surviving edges and the uninterrupted corridor. Only those contiguous old '
            'lanes may map to one corridor; conflicting known metrics remain blocked. '
            'Every ego/actor/old-lane relation and its two boundaries need a common frame; '
            'ego needs the first frame. Never add unseen frame references to pass validation.')

def compile_corridor_positions(request, decision):
    """Compile v6 boundary pairs; frozen ordinal contracts remain unchanged."""
    if request['lane_layout_contract']['revision'] not in PHYSICAL_DECISION_REVISIONS:
        return decision
    result = copy.deepcopy(decision)
    ids = [row['boundary_id'] for row in result['boundaries_right_to_left']]
    if len(set(ids)) != len(ids):
        layout_error('world.layout.boundary_reference', '/decision/boundaries_right_to_left',
                     'Physical boundary IDs must be unique', actual=ids)
    pairs = {pair:index for index,pair in enumerate(zip(ids, ids[1:]))}
    rows = [('/decision/ego_initial', result['ego_initial'])]
    for name in ('old_lane_corridors', 'actor_corridors'):
        rows.extend(('/decision/' + name + '/' + key.replace('~','~0').replace('/','~1'), row)
                    for key,row in result[name].items())
    for path,row in rows:
        pair = (row['right_boundary_id'], row['left_boundary_id'])
        if pair not in pairs:
            layout_error('world.layout.corridor_reference', path,
                         'Actor/lane corridor must reference an adjacent right-to-left driving boundary pair',
                         actual=list(pair), expected=[list(pair) for pair in pairs])
        row['ordinal_from_right'] = pairs[pair]
    # Initial-ego numeric discrepancies are derived from the physical pair,
    # so the model never needs to compute a zero-based claim verdict.
    ego = result['ego_initial']
    existing = {(row['claim_id'], row['field']) for row in result['claim_disputes']}
    for claim in request['focused_context'].get('layout_claims', []):
        old = claim['ego_initial_ordinal_from_right']
        if old is not None and old != ego['ordinal_from_right'] and (claim['claim_id'], 'ego_initial') not in existing:
            result['claim_disputes'].append(dict(claim_id=claim['claim_id'], field='ego_initial',
                reason='Compiled initial boundary pair differs from the frozen numeric claim: ' + ego['reason'],
                evidence_frame_ids=copy.deepcopy(ego['evidence_frame_ids'])))
    return result


def layout_error(rule_id, path, message, **details):
    error = ContractError(message, rule_id=rule_id)
    error.path = path
    error.diagnostics = dict(rule_id=rule_id, path=path, stage='world_closure', **details)
    raise error


def validator_version():
    from _lib.world.field_repair import validator_version as world_validator_version
    from _lib.behavior.compile import preview_digest
    return preview_digest({'world':world_validator_version(), 'consumers':[
        sha256(Path(__file__)), sha256(Path(__file__).with_name('closure.py')),
        sha256(Path(__file__).with_name('layout_correction.py')),
        sha256(Path(__file__).parents[1]/'stages/reconciliation.py'),
        sha256(Path(__file__).parents[1]/'contracts/frame_evidence.py'),
        sha256(Path(__file__).parents[1]/'runtime/case_core.py')]})


def decision_lane_map(request, original, candidate):
    mapping = compile_corridor_positions(request, candidate['decision'])['old_lane_corridors']
    return {old:f"lane_{row['ordinal_from_right']+1:03}" for old,row in mapping.items()}


def _validate_claim_assessments(request, decision, frame_ids, first_frame):
    """Require explicit, frame-bound treatment of every frozen competing claim."""
    def reject(message):
        raise ContractError(message, rule_id='world.layout.claim_adjudication')
    claims = request['focused_context'].get('layout_claims', [])
    assessments = decision['claim_assessments']
    by_id = {row['claim_id']:row for row in assessments}
    if (not claims or len(by_id) != len(assessments)
            or set(by_id) != {row['claim_id'] for row in claims}):
        reject('Every frozen layout claim needs exactly one assessment')
    chosen_boundaries = [row['boundary_id'] for row in decision['boundaries_right_to_left']]
    chosen_count = len(chosen_boundaries)-1
    chosen_ego = decision['ego_initial']['ordinal_from_right']
    for claim in claims:
        assessment = by_id[claim['claim_id']]
        source_count = claim['same_direction_count']
        kind, value = source_count['kind'], source_count['count']
        count_verdict = ('not_asserted' if kind == 'unknown' or value is None else
                         'supported' if (chosen_count == value if kind == 'exact'
                                         else chosen_count >= value) else 'refuted')
        source_ego = claim['ego_initial_ordinal_from_right']
        ego_verdict = ('not_asserted' if source_ego is None else
                       'supported' if chosen_ego == source_ego else 'refuted')
        for name, expected in (('count',count_verdict),('ego_initial',ego_verdict)):
            row = assessment[name]
            if row['verdict'] != expected:
                reject(f"{claim['claim_id']} {name} verdict disagrees with the submitted cross-section")
            witnesses = set(row['evidence_frame_ids'])
            if not witnesses <= frame_ids:
                reject(f"{claim['claim_id']} {name} cites a foreign frame")
            if name == 'count' and expected != 'not_asserted' and len(witnesses) < 2:
                reject(f"{claim['claim_id']} count needs two distinct source frames")
            if name == 'ego_initial' and expected != 'not_asserted' and first_frame not in witnesses:
                reject(f"{claim['claim_id']} initial ego needs the real first frame")
            interval = row.get('disputed_interval')
            if name != 'count' or expected != 'refuted':
                if interval is not None:
                    reject(f"{claim['claim_id']} {name} must not invent a disputed interval")
                continue
            if interval is None:
                reject(f"{claim['claim_id']} rejected count needs a physical disputed interval")
            if chosen_count > value:
                chain, role = chosen_boundaries, 'driving'
            else:
                chain, role = claim['boundaries_right_to_left'], 'non_driving'
            pair = (interval['right_boundary_id'], interval['left_boundary_id'])
            if (interval['observed_role'] != role
                    or pair not in list(zip(chain,chain[1:]))):
                reject(f"{claim['claim_id']} disputed interval does not explain the count difference")
            if role == 'driving':
                right = next(row for row in decision['boundaries_right_to_left']
                             if row['boundary_id'] == pair[0])
                left = next(row for row in decision['boundaries_right_to_left']
                            if row['boundary_id'] == pair[1])
                if not witnesses & set(right['evidence_frame_ids']) & set(left['evidence_frame_ids']):
                    reject(f"{claim['claim_id']} disputed driving interval lacks a shared frame")


def derived_claim_assessments(request, decision):
    """Numeric verdicts are program facts derived from the chosen cross-section."""
    decision = compile_corridor_positions(request, decision)
    count = len(decision['boundaries_right_to_left'])-1
    ego = decision['ego_initial']['ordinal_from_right']
    result = []
    for claim in request['focused_context'].get('layout_claims', []):
        original = claim['same_direction_count']
        kind, value = original['kind'], original['count']
        count_verdict = ('not_asserted' if kind == 'unknown' or value is None else
                         'supported' if (count == value if kind == 'exact' else count >= value)
                         else 'refuted')
        old_ego = claim['ego_initial_ordinal_from_right']
        ego_verdict = ('not_asserted' if old_ego is None else
                       'supported' if old_ego == ego else 'refuted')
        result.append({'claim_id':claim['claim_id'],
                       'count':count_verdict,'ego_initial':ego_verdict})
    return result


def _validate_claim_disputes(request, decision, frame_ids, first_frame):
    def reject(message):
        layout_error('world.layout.claim_dispute', path, message)
    path = '/decision/claim_disputes'
    claims = {row['claim_id']:row for row in request['focused_context'].get('layout_claims', [])}
    verdicts = derived_claim_assessments(request,decision)
    required = {(row['claim_id'],field) for row in verdicts
                for field in ('count','ego_initial') if row[field] == 'refuted'}
    disputes = decision['claim_disputes']
    given = {(row['claim_id'],row['field']) for row in disputes}
    if len(given) != len(disputes) or given != required:
        reject('Submit one direct-evidence dispute for each numeric claim mismatch, and no others')
    chosen = [row['boundary_id'] for row in decision['boundaries_right_to_left']]
    count = len(chosen)-1
    for index, dispute in enumerate(disputes):
        path = '/decision/claim_disputes/' + str(index)
        claim = claims[dispute['claim_id']]
        witnesses = set(dispute['evidence_frame_ids'])
        if not witnesses <= frame_ids:
            reject('Claim dispute cites a foreign frame: '+dispute['claim_id'])
        interval = dispute.get('disputed_interval')
        if dispute['field'] == 'ego_initial':
            if first_frame not in witnesses or interval is not None:
                reject('Initial ego dispute needs the real first frame and no count interval')
            continue
        if len(witnesses) < 2 or interval is None:
            reject('Count dispute needs two source frames and a physical interval')
        value = claim['same_direction_count']['count']
        if interval.get('absent_boundary_ids'):
            from .layout_correction import absent_dividers
            absent_dividers(request, decision, claim, dispute)
            continue
        chain, role = ((chosen,'driving') if count > value else
                       (claim['boundaries_right_to_left'],'non_driving'))
        pair = (interval['right_boundary_id'],interval['left_boundary_id'])
        if (interval['observed_role'] != role
                or pair not in list(zip(chain,chain[1:]))):
            layout_error('world.layout.claim_dispute', path,
                'Claim dispute interval does not explain the numeric difference',
                claim_id=claim['claim_id'], original_count=value, chosen_count=count,
                actual=interval, expected_role=role, expected_adjacent_pairs=list(zip(chain,chain[1:])))
        if role == 'driving':
            right = next(row for row in decision['boundaries_right_to_left']
                         if row['boundary_id'] == pair[0])
            left = next(row for row in decision['boundaries_right_to_left']
                        if row['boundary_id'] == pair[1])
            if not witnesses & set(right['evidence_frame_ids']) & set(left['evidence_frame_ids']):
                reject('Disputed driving interval lacks a shared source frame')


def physical_decision_issues(request, decision, original):
    """Collect independent cross-field failures without modifying visual facts."""
    from .layout_correction import lane_sources
    issues = []
    # A physical re-inventory may correct either source, but agreement between
    # the original World and an independent Batch is not disposable merely by
    # presenting a different corridor pair.  Stop for source adjudication.
    claims = request['focused_context'].get('layout_claims', [])
    world_ego = {row['ego_initial_ordinal_from_right'] for row in claims
                 if row.get('source') == 'world' and row.get('ego_initial_ordinal_from_right') is not None}
    batch_ego = {row['ego_initial_ordinal_from_right'] for row in claims
                 if row.get('source') == 'batch' and row.get('ego_initial_ordinal_from_right') is not None}
    if (request['lane_layout_contract'].get('source_consistency_revision') == SOURCE_CONSISTENCY_REVISION
            and len(world_ego) == len(batch_ego) == 1 and world_ego == batch_ego):
        agreed = next(iter(world_ego))
        if decision['ego_initial']['ordinal_from_right'] != agreed:
            issues.append(dict(rule_id='world.layout.correlated_source_disagreement',
                path='/decision/ego_initial',
                message='Initial ego corridor conflicts with agreeing World and independent Batch observations',
                source_ordinal=agreed,
                decision_ordinal=decision['ego_initial']['ordinal_from_right'],
                claim_ids=[row['claim_id'] for row in claims if row.get('ego_initial_ordinal_from_right') == agreed]))
    bounds = {row['boundary_id']:row for row in decision['boundaries_right_to_left']}
    frames = request['focused_context']['evidence_frames']
    first = min(frames, key=lambda row:row['time_s'])['frame_id']
    rows = [('/decision/ego_initial', decision['ego_initial'])]
    for name in ('old_lane_corridors', 'actor_corridors'):
        rows.extend(('/decision/'+name+'/'+key, row) for key,row in decision[name].items())
    for path,row in rows:
        shared = (set(row['evidence_frame_ids']) & set(bounds[row['right_boundary_id']]['evidence_frame_ids'])
                  & set(bounds[row['left_boundary_id']]['evidence_frame_ids']))
        if not shared or (path == '/decision/ego_initial' and first not in shared):
            issues.append(dict(rule_id='world.layout.corridor_reference', path=path,
                message='Physical corridor needs a common frame for its relation and both boundaries; ego needs the first frame',
                relation_frames=row['evidence_frame_ids'],
                right_boundary_frames=bounds[row['right_boundary_id']]['evidence_frame_ids'],
                left_boundary_frames=bounds[row['left_boundary_id']]['evidence_frame_ids']))
    for check, path in (
            (lambda: _validate_claim_disputes(request, decision, {r['frame_id'] for r in frames}, first),
             '/decision/claim_disputes'),
            (lambda: lane_sources(request, decision, original), '/decision/old_lane_corridors')):
        try:
            check()
        except ContractError as error:
            detail = dict(getattr(error, 'diagnostics', {}) or {})
            detail.update(rule_id=error.rule_id, path=getattr(error, 'path', path), message=str(error))
            issues.append(detail)
    return issues


def materialize_decision(request, candidate):
    """Compile one frozen visual decision into the existing World shape."""
    from .closure import ClosureStopped, request_binding
    from .single_world import validate_layout
    def reject(message, *, rule_id='world.layout.decision_shape'):
        raise ContractError(message, rule_id=rule_id)
    if request.get('request_sha256') != request_binding(request):
        raise ClosureStopped('source_changed','Lane layout request binding changed')
    revision = request['lane_layout_contract'].get('revision')
    if revision not in DECISION_REVISIONS:
        reject('Unsupported lane layout decision revision')
    def marking_type(row):
        if revision in DERIVED_DECISION_REVISIONS:
            if row['kind'] in {'curb','road_edge'}:
                return row['kind']
            value = row.get('marking_type')
            if not value or value in {'none','curb','road_edge'}:
                reject('Visible lane marking needs its actual painted marking_type')
            return value
        return row['marking_type']
    if not isinstance(candidate, dict) or candidate.get('contract_version') != revision:
        reject('Lane layout candidate must use the frozen decision revision')
    if (candidate.get('request_sha256') != request['request_sha256']
            or candidate.get('base_sha256') != request['base_sha256']):
        raise ClosureStopped('source_changed','Lane layout candidate input binding changed')
    base = request['base_path']
    if sha256(base) != request['base_sha256'] or any(
            not Path(path).is_file() or sha256(path) != digest
            for path,digest in request['input_hashes'].items()):
        raise ClosureStopped('source_changed','Lane layout frozen input changed')
    if len(json.dumps(candidate,ensure_ascii=False,allow_nan=False).encode()) > 16*1024:
        reject('Lane layout decision exceeds 16 KiB')
    status = candidate.get('status')
    if status in {'needs_evidence','blocked_by_patch_contract'}:
        if (set(candidate) - {'contract_version','request_sha256','base_sha256','status',
                               'blocking_issues','reason','required_paths','operations'}
                or candidate.get('operations',[]) != []
                or not isinstance(candidate.get('blocking_issues'),list)
                or not candidate['blocking_issues']
                or any(not isinstance(item,str) or item not in request['issues']
                       for item in candidate['blocking_issues'])
                or not isinstance(candidate.get('reason'),str) or not candidate['reason'].strip()):
            reject('Lane layout stop envelope is invalid')
        required_paths = candidate.get('required_paths',[])
        if (not isinstance(required_paths,list) or
                any(not isinstance(path,str) or not path.startswith('/') for path in required_paths) or
                (status == 'blocked_by_patch_contract' and not required_paths)):
            reject('Blocked layout decision must identify required paths')
        raise ClosureStopped('world_evidence_insufficient' if status == 'needs_evidence'
                             else 'world_patch_contract_blocked', candidate['reason'],candidate)
    if status != 'decision' or set(candidate) != {'contract_version','request_sha256',
                                                   'base_sha256','status','decision'}:
        reject('Lane layout decision envelope is incomplete or contains World patch operations')
    decision = candidate['decision']
    # Persisted requests keep their frozen decision enum, even when a later
    # code revision adds a new roadside type for newly created Cases.
    frozen_schema = request.get('value_schemas', {}).get('decision', decision_schema(revision))
    errors = list(Draft202012Validator(frozen_schema).iter_errors(decision))
    if errors:
        error = errors[0]
        path = '/decision' + ''.join('/' + str(part).replace('~','~0').replace('/','~1') for part in error.absolute_path)
        layout_error('world.layout.decision_shape', path, 'Invalid layout decision: ' + error.message)
    decision = compile_corridor_positions(request, decision)
    multi_roadside = revision in DERIVED_DECISION_REVISIONS and (
        'roadside_intervals' in frozen_schema.get('properties', {}))
    boundaries = decision['boundaries_right_to_left']
    ids = [row['boundary_id'] for row in boundaries]
    if len(set(ids)) != len(ids): reject('Layout boundary IDs must be unique')
    count = len(boundaries)-1
    if decision['ego_initial']['ordinal_from_right'] >= count:
        reject('Initial ego corridor is outside the confirmed driving cross-section')
    frames = request['focused_context']['evidence_frames']
    if not frames:
        raise ClosureStopped('world_evidence_insufficient','No source frames were assigned to lane layout closure')
    frame_ids = {row['frame_id'] for row in frames}
    first_frame = min(frames,key=lambda row:row['time_s'])['frame_id']
    def witness(row, name):
        if not set(row['evidence_frame_ids']) <= frame_ids:
            reject('Foreign evidence frame in '+name)
        return [dict(status='observed',frame_id=fid,reason=row['reason'])
                for fid in row['evidence_frame_ids']]
    all_frames = sorted({fid for row in boundaries for fid in row['evidence_frame_ids']})
    if len(all_frames) < 2 or len(boundaries[0]['evidence_frame_ids']) < 2:
        reject('Count and right outer boundary require two direct frame witnesses')
    if first_frame not in decision['ego_initial']['evidence_frame_ids']:
        reject('Initial ego corridor requires the real first frame')
    for row in boundaries: witness(row,'boundary')
    if revision in PHYSICAL_DECISION_REVISIONS:
        issues = physical_decision_issues(request, decision, read_json(base))
        if issues:
            first = issues[0]
            layout_error(first['rule_id'], first['path'], first['message'], issues=issues)
    if revision in {EVIDENCE_DECISION_REVISION, *DERIVED_DECISION_REVISIONS}:
        corridors = decision['driving_corridors']
        if len(corridors) != count:
            reject('Every chosen driving corridor needs one direct visual assessment')
        for index, row in enumerate(corridors):
            pair = (row['right_boundary_id'],row['left_boundary_id'])
            if pair != (ids[index],ids[index+1]):
                reject('Driving corridor assessments must follow the physical boundary chain')
            if not (set(row['evidence_frame_ids']) <= frame_ids and
                    set(row['evidence_frame_ids']) &
                    set(boundaries[index]['evidence_frame_ids']) &
                    set(boundaries[index+1]['evidence_frame_ids'])):
                reject('Driving corridor needs a frame showing both physical edges')
        if revision == EVIDENCE_DECISION_REVISION:
            _validate_claim_assessments(request, decision, frame_ids, first_frame)
        elif revision not in PHYSICAL_DECISION_REVISIONS:
            _validate_claim_disputes(request, decision, frame_ids, first_frame)
    lane_frame_ids = [sorted(set(boundaries[index]['evidence_frame_ids']) &
                             set(boundaries[index+1]['evidence_frame_ids']))
                      for index in range(count)]
    if any(not ids for ids in lane_frame_ids):
        reject('Each driving corridor needs a frame showing both adjacent boundaries')
    witness(decision['ego_initial'],'ego_initial')
    roadside = None if multi_roadside else decision['right_roadside']
    outer = (roadside.get('outer_boundary')
             if roadside is not None and revision in ROADSIDE_DECISION_REVISIONS else None)
    intervals = decision['roadside_intervals'] if multi_roadside else []
    if multi_roadside:
        expected_inner = ids[0]
        seen_edges = set(ids)
        for index, band in enumerate(intervals):
            edge = band['outer_boundary']
            witness(band, f'roadside_intervals[{index}]')
            witness(edge, f'roadside_intervals[{index}].outer_boundary')
            if band['inner_boundary_id'] != expected_inner:
                reject('Right roadside intervals must form one connected boundary chain',
                       rule_id='world.layout.roadside_geometry')
            if (len(edge['evidence_frame_ids']) < 2 or edge['boundary_id'] in seen_edges
                    or edge['kind'] not in {'curb','road_edge'}
                    or marking_type(edge) != edge['kind']):
                reject('Right roadside outer boundary must be a distinct evidenced curb or road edge',
                       rule_id='world.layout.roadside_geometry')
            if not (set(band['evidence_frame_ids']) &
                    set(edge['evidence_frame_ids'])):
                reject('Roadside interval needs a frame showing its outer boundary',
                       rule_id='world.layout.roadside_geometry')
            if band['surface'] != 'paved':
                raise ClosureStopped('world_evidence_insufficient',
                    'Right roadside surface is not confirmed paved; cannot compile a paved roadside band')
            seen_edges.add(edge['boundary_id'])
            expected_inner = edge['boundary_id']
    else:
        witness(roadside,'right_roadside')
    if revision in ROADSIDE_DECISION_REVISIONS and not multi_roadside:
        continuous = roadside['type'] in {'shoulder','curbside_parking'} or (
            revision in DERIVED_DECISION_REVISIONS and roadside['type'] == 'sidewalk')
        if continuous != (outer is not None):
            reject('Continuous right roadside requires a separate outer boundary; other types require null',
                   rule_id='world.layout.decision_shape')
        if continuous:
            witness(outer,'right_roadside.outer_boundary')
            if (len(outer['evidence_frame_ids']) < 2 or outer['boundary_id'] in ids
                    or outer['kind'] not in {'curb','road_edge'}
                    or marking_type(outer) != outer['kind']):
                reject('Right roadside outer boundary must be a distinct evidenced curb or road edge',
                       rule_id='world.layout.roadside_geometry')
            if roadside['surface'] != 'paved':
                raise ClosureStopped('world_evidence_insufficient',
                    'Right roadside surface is not confirmed paved; cannot compile a paved roadside band')
    for row in decision['old_lane_corridors'].values(): witness(row,'old_lane_corridor')
    for row in decision['actor_corridors'].values(): witness(row,'actor_corridor')
    original = read_json(base)
    road = original['world']['road']
    if len(road.get('roads',[])) != 1 or original['world'].get('junctions'):
        raise ClosureStopped('world_evidence_insufficient','Layout decision supports one road without junctions')
    old_lanes = {lane['lane_id']:lane for lane in road['lanes']}
    mapping = decision['old_lane_corridors']
    ordinals = [row['ordinal_from_right'] for row in mapping.values()]
    if set(mapping) != set(old_lanes):
        layout_error('world.layout.lane_mapping', '/decision/old_lane_corridors',
                     'Every old physical lane requires a corridor mapping',
                     actual=sorted(mapping), expected=sorted(old_lanes))
    if len(set(ordinals)) != len(ordinals) and revision != CORRECTION_DECISION_REVISION:
        layout_error('world.layout.lane_mapping', '/decision/old_lane_corridors',
                     'Every old physical lane requires a distinct evidenced corridor mapping', actual=ordinals)
    for lane_id,row in mapping.items():
        if row['ordinal_from_right'] >= count:
            layout_error('world.layout.corridor_reference',
                         '/decision/old_lane_corridors/' + lane_id + '/ordinal_from_right',
                         'Lane corridor ordinal exceeds the frozen driving cross-section',
                         actual=row['ordinal_from_right'], expected={'minimum':0, 'maximum':count-1})
    if any(lane.get('travel_direction') not in (None,'unknown','forward','same_as_ego')
           or lane.get('lane_type') not in (None,'unknown','driving')
           or lane.get('road_id') not in (None,road['roads'][0]['road_id'])
           for lane in old_lanes.values()):
        raise ClosureStopped('world_evidence_insufficient',
            'Known opposing, non-driving, or other-road lanes need a separate adjudication')
    old_order = road['roads'][0]['lane_ids']
    try:
        known_positions = [mapping[lane_id]['ordinal_from_right'] for lane_id in old_order]
    except KeyError:
        reject('Road lane_ids reference an unmapped old physical lane')
    if known_positions != sorted(known_positions):
        raise ClosureStopped('world_evidence_insufficient',
            'Decision reverses the known physical order of old lanes')
    from .layout_correction import lane_sources, removed_boundaries
    reverse = (lane_sources(request, decision, original) if revision == CORRECTION_DECISION_REVISION else
               {row['ordinal_from_right']:old_lanes[old] for old,row in mapping.items()})
    removed = removed_boundaries(request, decision)
    revised = copy.deepcopy(original)
    new_road = revised['world']['road']
    road_id = road['roads'][0]['road_id']
    new_lanes = []
    for index in range(count):
        old = copy.deepcopy(reverse.get(index,{}))
        old.update(lane_id=f'lane_{index+1:03}',road_id=road_id,lane_type='driving',
                   travel_direction=old.get('travel_direction') if old.get('travel_direction')
                   in {'forward','same_as_ego'} else 'same_as_ego',
                   ordinal_from_right=index,right_boundary=ids[index],left_boundary=ids[index+1])
        old.pop('right_boundary_id',None); old.pop('left_boundary_id',None)
        new_lanes.append(old)
    new_road['lanes'] = new_lanes
    new_road['roads'][0]['lane_ids'] = [lane['lane_id'] for lane in new_lanes]
    old_knowledge = new_road['roads'][0].get('lane_knowledge') or {}
    old_knowledge.update(kind='minimum' if old_knowledge.get('kind') == 'minimum' else 'exact',
        count=count,evidence_frame_ids=all_frames,
        anchors=[dict(lane_id=lane['lane_id'],ordinal_from_right=index,
                      evidence_frame_ids=lane_frame_ids[index]) for index,lane in enumerate(new_lanes)])
    new_road['roads'][0]['lane_knowledge'] = old_knowledge
    new_road['ego_direction_lanes'] = count
    if 'lane_count_ego_direction' in new_road:
        new_road['lane_count_ego_direction'] = count
    lane_map = decision_lane_map(request,original,candidate)
    old_markings = {row['marking_id']:row for row in road.get('lane_markings',[])
                    if row.get('marking_id')}
    if len(old_markings) != len(road.get('lane_markings',[])):
        raise ClosureStopped('world_evidence_insufficient','Anonymous or duplicate old boundary cannot be remapped')
    markings = []
    for index,row in enumerate(boundaries):
        value = marking_type(row)
        if ((row['kind'] == 'curb' and value != 'curb')
                or (row['kind'] == 'road_edge' and value != 'road_edge')
                or (row['kind'] == 'lane_marking' and value in {'curb','road_edge'})):
            reject('Boundary kind and marking_type disagree: '+row['boundary_id'])
        old = copy.deepcopy(old_markings.pop(row['boundary_id'],{}))
        if (revision not in ROADSIDE_DECISION_REVISIONS and
                old.get('marking_type',old.get('type')) not in (None,'unknown',value)):
            raise ClosureStopped('world_evidence_insufficient',
                'Boundary type conflicts with known World fact: '+row['boundary_id'])
        old.update(marking_id=row['boundary_id'],marking_type=value,road_id=road_id)
        old.pop('type',None)
        for key in ('between','lane_id','side','location'): old.pop(key,None)
        if index == 0: old.update(side='right',lane_id=new_lanes[0]['lane_id'])
        elif index == count: old.update(side='left',lane_id=new_lanes[-1]['lane_id'])
        else: old['between'] = [new_lanes[index-1]['lane_id'],new_lanes[index]['lane_id']]
        markings.append(old)
    for old in old_markings.values():
        if old['marking_id'] in removed:
            continue
        item = copy.deepcopy(old)
        if item.get('lane_id') in lane_map: item['lane_id'] = lane_map[item['lane_id']]
        if item.get('between'): item['between'] = [lane_map.get(lid,lid) for lid in item['between']]
        markings.append(item)
    outer_rows = ([band['outer_boundary'] for band in intervals] if multi_roadside else
                  [outer] if revision in ROADSIDE_DECISION_REVISIONS and outer is not None else [])
    outer_marking_indexes = []
    for outer_row in outer_rows:
        outer_id = outer_row['boundary_id']
        old_index = next((i for i, row in enumerate(markings)
                          if row.get('marking_id') == outer_id), None)
        old_outer = copy.deepcopy(markings[old_index]) if old_index is not None else {}
        if old_index is not None and not multi_roadside:
            markings.pop(old_index)
        old_outer.update(marking_id=outer_id, marking_type=marking_type(outer_row),
                         road_id=road_id, side='right')
        for key in ('type','between','lane_id','location'): old_outer.pop(key,None)
        if old_index is not None and multi_roadside:
            markings[old_index] = old_outer
            outer_marking_indexes.append(old_index)
        else:
            outer_marking_indexes.append(len(markings))
            markings.append(old_outer)
    new_road['lane_markings'] = markings
    roadside_area_index = None
    roadside_area_indexes = []
    if multi_roadside:
        from _lib.contracts.parking import roadside_execution_type
        areas = new_road.get('roadside_areas', [])
        # The admitted road order already identifies its rightmost old lane.
        # Optional derived lane fields and execution-type aliases do not own
        # the identity of a physical roadside interval.
        old_right_edge = old_lanes[old_order[0]].get('right_boundary') if old_order else None
        connected_old = set()
        edge = old_right_edge
        while edge:
            matches = [(i, area) for i, area in enumerate(areas)
                       if i not in connected_old and area.get('side') == 'right'
                       and area.get('road_id') in (None, road_id)
                       and area.get('boundary_left') == edge and area.get('boundary_right')]
            if len(matches) > 1:
                raise ClosureStopped('world_evidence_insufficient',
                    'Existing right roadside boundary chain branches ambiguously')
            if not matches:
                break
            index, old_area = matches[0]
            connected_old.add(index)
            edge = old_area['boundary_right']
        reviewed_pairs = {(band['inner_boundary_id'], band['outer_boundary']['boundary_id'])
                          for band in intervals}
        # Reconcile old right-side bands with the same shoulder recognition the
        # compiled-layout invariant check uses: a legacy 'shoulder'/'*_shoulder'
        # type label must count as an existing band even when its outer edge was
        # never bound (boundary_right missing) and its vocabulary predates the
        # paved_shoulder execution type. Otherwise the decision band compiles to
        # a duplicate area beside the old one and the invariant check fails.
        def _old_shoulder_area(area):
            if roadside_execution_type(area) == 'paved_shoulder':
                return True
            observed = area.get('area_type') if area.get('area_type') is not None else area.get('type')
            return isinstance(observed, str) and 'shoulder' in observed.split('_')
        required_old = connected_old | {i for i, area in enumerate(areas)
            if area.get('side') == 'right' and area.get('road_id') in (None, road_id)
            and ((area.get('boundary_left'), area.get('boundary_right')) in reviewed_pairs
                 or _old_shoulder_area(area))}
        used_old = set()
        existing_ids = {row.get('area_id') for row in areas}
        for band in intervals:
            pair = (band['inner_boundary_id'], band['outer_boundary']['boundary_id'])
            exact = [i for i in required_old - used_old
                     if (areas[i].get('boundary_left'), areas[i].get('boundary_right')) == pair]
            touching = [i for i in required_old - used_old
                        if areas[i].get('boundary_left') in pair or
                        areas[i].get('boundary_right') in pair]
            unbound = [i for i in required_old - used_old
                       if not areas[i].get('boundary_left') or not areas[i].get('boundary_right')]
            if (len(exact) > 1 or (not exact and len(touching) > 1)
                    or (not exact and not touching and len(unbound) > 1)):
                raise ClosureStopped('world_evidence_insufficient',
                    'Right roadside interval matches multiple old physical bands')
            old_index = (exact or touching or unbound or [None])[0]
            if old_index is not None:
                used_old.add(old_index)
                area = copy.deepcopy(areas[old_index])
                if (area.get('boundary_left'), area.get('boundary_right')) != pair:
                    area.pop('width_m', None)
            else:
                number = 1
                while f'area_{number:03}' in existing_ids: number += 1
                area = {'area_id':f'area_{number:03}'}
                existing_ids.add(area['area_id'])
            area.update(road_id=road_id, side='right', boundary_left=pair[0],
                boundary_right=pair[1],
                type={'paved_strip':'paved_strip','shoulder':'continuous_paved_shoulder',
                      'sidewalk':'sidewalk','curbside_parking':'curbside_parking'}[band['type']],
                reason=band['reason'],
                evidence_frame_ids=sorted(set(band['evidence_frame_ids']) |
                                          set(band['outer_boundary']['evidence_frame_ids'])))
            area.pop('area_type', None)
            if old_index is None:
                roadside_area_indexes.append(len(areas))
                areas.append(area)
            else:
                roadside_area_indexes.append(old_index)
                areas[old_index] = area
        if required_old - used_old:
            raise ClosureStopped('world_evidence_insufficient',
                'Decision omits an existing right roadside band; recheck every physical interval')
        new_road['roadside_areas'] = areas
    elif revision in ROADSIDE_DECISION_REVISIONS:
        from _lib.contracts.parking import roadside_execution_type
        areas = new_road.get('roadside_areas', [])
        continuous_areas = [(index,area) for index,area in enumerate(areas)
            if area.get('side') == 'right' and area.get('road_id') in (None,road_id)
            and roadside_execution_type(area) == 'paved_shoulder']
        if len(continuous_areas) > 1:
            raise ClosureStopped('world_evidence_insufficient',
                'Multiple existing right roadside bands cannot be uniquely reconciled')
        if outer is None and continuous_areas:
            raise ClosureStopped('world_evidence_insufficient',
                'Decision omits an existing continuous right roadside band; recheck its two boundaries')
        if outer is not None:
            if continuous_areas:
                roadside_area_index, old_area = continuous_areas[0]
                area = copy.deepcopy(old_area)
            else:
                existing = {row.get('area_id') for row in areas}
                number = 1
                while f'area_{number:03}' in existing: number += 1
                area = {'area_id':f'area_{number:03}'}
                roadside_area_index = len(areas)
            old_pair = (area.get('boundary_left'), area.get('boundary_right'))
            new_pair = (ids[0],outer['boundary_id'])
            if old_pair != new_pair:
                # A width is reusable only when both evidenced edges describe
                # the same physical band; missing old edges prove no such match.
                area.pop('width_m',None)
            area.update(road_id=road_id, side='right', boundary_left=ids[0],
                        boundary_right=outer['boundary_id'],
                        type=('curbside_parking' if roadside['type'] == 'curbside_parking'
                              else 'sidewalk' if roadside['type'] == 'sidewalk'
                              else 'continuous_paved_shoulder'),
                        reason=roadside['reason'],
                        evidence_frame_ids=sorted(set(roadside['evidence_frame_ids']) |
                                                  set(outer['evidence_frame_ids'])))
            area.pop('area_type',None)
            if roadside_area_index == len(areas): areas.append(area)
            else: areas[roadside_area_index] = area
            new_road['roadside_areas'] = areas
    for actor in revised['actor_hypotheses']:
        old_lane = actor.get('hypothesized_lane')
        if old_lane in lane_map:
            actor['hypothesized_lane'] = lane_map[old_lane]
    actors = {row['actor_id']:row for row in revised['actor_hypotheses']}
    for actor_id,row in decision['actor_corridors'].items():
        if actor_id not in actors or row['ordinal_from_right'] >= count:
            reject('Actor corridor must name an existing Actor and confirmed driving corridor')
        actors[actor_id]['hypothesized_lane'] = new_lanes[row['ordinal_from_right']]['lane_id']
    if 'ego' in actors:
        actors['ego']['hypothesized_lane'] = new_lanes[decision['ego_initial']['ordinal_from_right']]['lane_id']
    ego = decision['ego_initial']
    if multi_roadside:
        first_band = intervals[0] if intervals else None
        roadside_observation = dict(
            type=({'paved_strip':'unknown','shoulder':'shoulder','sidewalk':'sidewalk',
                   'curbside_parking':'curbside_parking'}[first_band['type']]
                  if first_band else 'unknown'),
            reason=first_band['reason'] if first_band else 'No continuous roadside band was established',
            evidence_frame_ids=first_band['evidence_frame_ids'] if first_band else all_frames)
    else:
        roadside_observation = ({key:value for key,value in roadside.items()
                                 if key not in {'outer_boundary','surface'}}
                                if revision in ROADSIDE_DECISION_REVISIONS else roadside)
    observation = dict(
        same_direction_count=dict(kind='exact',count=count,
            reason='Confirmed driving corridors between consecutive physical boundaries',
            evidence_frame_ids=all_frames),
        right_outer_boundary=dict(confirmed=True,reason=boundaries[0]['reason'],
            evidence_frame_ids=boundaries[0]['evidence_frame_ids']),
        boundaries_right_to_left=ids,
        ego_initial=dict(ordinal_from_right=ego['ordinal_from_right'],
            right_boundary_id=ids[ego['ordinal_from_right']],
            left_boundary_id=ids[ego['ordinal_from_right']+1],
            reason=ego['reason'],evidence_frame_ids=ego['evidence_frame_ids']),
        right_roadside=copy.deepcopy(roadside_observation))
    if multi_roadside:
        observation['right_roadside_intervals'] = [dict(
            inner_boundary_id=band['inner_boundary_id'],
            outer_boundary_id=band['outer_boundary']['boundary_id'],
            type=band['type'], reason=band['reason'],
            evidence_frame_ids=band['evidence_frame_ids']) for band in intervals]
    validate_layout(observation,frames)
    review = revised['world']['metadata']['lane_layout_review']
    review['observation'] = observation
    evidence = revised['field_evidence']
    if removed:
        # Array indexes changed. Rebuild canonical evidence below and retain only
        # matching noncanonical properties, never evidence for retired indexes.
        roots = ('world.road.lanes[', 'world.road.lane_markings[',
                 'world.road.roads[0].lane_ids[', 'world.road.roads[0].lane_knowledge.anchors[')
        saved = copy.deepcopy(evidence)
        for key in list(evidence):
            if key.startswith(roots):
                del evidence[key]
        def carry_evidence(old_prefix, new_prefix):
            for path, rows in saved.items():
                if path == old_prefix or path.startswith((old_prefix+'.', old_prefix+'[')):
                    target = new_prefix + path[len(old_prefix):]
                    evidence.setdefault(target, []).extend(copy.deepcopy(rows))
        for index,lane in enumerate(new_lanes):
            for old_index,old_lane in enumerate(road['lanes']):
                if lane_map[old_lane['lane_id']] != lane['lane_id']:
                    continue
                for key,value in old_lane.items():
                    if key not in CANONICAL_LANE_FIELDS and lane.get(key) == value:
                        carry_evidence(f'world.road.lanes[{old_index}].{key}',
                                       f'world.road.lanes[{index}].{key}')
        for index,marking in enumerate(markings):
            for old_index,old_marking in enumerate(road.get('lane_markings', [])):
                if old_marking['marking_id'] != marking['marking_id']:
                    continue
                for key,value in old_marking.items():
                    if key not in {'marking_id','type','marking_type','road_id','lane_id','between','side','location'} and marking.get(key) == value:
                        carry_evidence(f'world.road.lane_markings[{old_index}].{key}',
                                       f'world.road.lane_markings[{index}].{key}')
    def set_evidence(path, rows): evidence[path] = rows
    chain_evidence = [item for row in boundaries for item in witness(row,'boundary')]
    for path in ('world.road.lanes','world.road.lane_markings',
                 'world.road.roads[0].lane_ids','world.road.roads[0].lane_knowledge',
                 'world.road.ego_direction_lanes','world.metadata.lane_layout_review.observation'):
        set_evidence(path,copy.deepcopy(chain_evidence))
    if multi_roadside:
        all_roadside_evidence = []
        for band_index, (band, area_index, marking_index) in enumerate(zip(
                intervals, roadside_area_indexes, outer_marking_indexes)):
            edge = band['outer_boundary']
            roadside_evidence = (witness(band,f'roadside_intervals[{band_index}]') +
                witness(edge,f'roadside_intervals[{band_index}].outer_boundary'))
            all_roadside_evidence.extend(roadside_evidence)
            for name in ('area_id','road_id','side','boundary_left','boundary_right','type',
                         'reason','evidence_frame_ids'):
                set_evidence(f'world.road.roadside_areas[{area_index}].{name}',
                             copy.deepcopy(roadside_evidence))
            width_path = f'world.road.roadside_areas[{area_index}].width_m'
            if 'width_m' not in new_road['roadside_areas'][area_index]:
                evidence.pop(width_path,None)
            for name in ('marking_id','marking_type','road_id','side'):
                set_evidence(f'world.road.lane_markings[{marking_index}].{name}',
                             witness(edge,f'roadside_intervals[{band_index}].outer_boundary'))
        if all_roadside_evidence:
            set_evidence('world.road.roadside_areas', all_roadside_evidence)
    elif revision in ROADSIDE_DECISION_REVISIONS and outer is not None:
        roadside_evidence = witness(roadside,'right_roadside') + witness(outer,'right_roadside.outer_boundary')
        set_evidence('world.road.roadside_areas',copy.deepcopy(roadside_evidence))
        for name in ('area_id','road_id','side','boundary_left','boundary_right','type',
                     'reason','evidence_frame_ids'):
            set_evidence(f'world.road.roadside_areas[{roadside_area_index}].{name}',
                         copy.deepcopy(roadside_evidence))
        width_path = f'world.road.roadside_areas[{roadside_area_index}].width_m'
        if 'width_m' not in new_road['roadside_areas'][roadside_area_index]:
            evidence.pop(width_path,None)
        for name in ('marking_id','marking_type','road_id','side'):
            set_evidence(f'world.road.lane_markings[{outer_marking_indexes[0]}].{name}',
                         witness(outer,'right_roadside.outer_boundary'))
    if 'lane_count_ego_direction' in new_road:
        set_evidence('world.road.lane_count_ego_direction',copy.deepcopy(chain_evidence))
    for index,lane in enumerate(new_lanes):
        local = [item for row in (boundaries[index],boundaries[index+1])
                 for item in witness(row,'boundary') if item['frame_id'] in lane_frame_ids[index]]
        if index in reverse:
            old_id = next(old_id for old_id,row in mapping.items()
                          if row['ordinal_from_right'] == index)
            local += witness(mapping[old_id],'old_lane_corridor')
        for name in CANONICAL_LANE_FIELDS:
            set_evidence(f'world.road.lanes[{index}].{name}',copy.deepcopy(local))
        set_evidence(f'world.road.roads[0].lane_ids[{index}]',copy.deepcopy(local))
        set_evidence(f'world.road.roads[0].lane_knowledge.anchors[{index}]',copy.deepcopy(local))
    for index,row in enumerate(boundaries):
        local = witness(row,'boundary')
        for name in ('marking_id','marking_type','road_id'):
            set_evidence(f'world.road.lane_markings[{index}].{name}',copy.deepcopy(local))
        for name in ('side','lane_id','between'):
            if name in markings[index]:
                set_evidence(f'world.road.lane_markings[{index}].{name}',copy.deepcopy(local))
    for name,row in (('same_direction_count',observation['same_direction_count']),
                     ('right_outer_boundary',observation['right_outer_boundary']),
                     ('ego_initial',ego),('right_roadside',roadside_observation)):
        set_evidence('world.metadata.lane_layout_review.observation.'+name,witness(row,name))
    for actor_id,row in decision['actor_corridors'].items():
        index = next(i for i,actor in enumerate(revised['actor_hypotheses'])
                     if actor['actor_id'] == actor_id)
        set_evidence(f'actor_hypotheses[{index}].hypothesized_lane',witness(row,'actor_corridor'))
    for index,actor in enumerate(revised['actor_hypotheses']):
        path = f'actor_hypotheses[{index}].hypothesized_lane'
        if actor['actor_id'] == 'ego':
            set_evidence(path,witness(ego,'ego_initial'))
        elif actor['actor_id'] not in decision['actor_corridors']:
            old_lane = original['actor_hypotheses'][index].get('hypothesized_lane')
            if old_lane in mapping and actor.get('hypothesized_lane') != old_lane:
                set_evidence(path,witness(mapping[old_lane],'old_lane_corridor'))
    question_id = request['lane_layout_contract']['question_id']
    for question in revised['questions_to_verify']:
        if question['question_id'] == question_id:
            question['resolution'] = 'resolved'
            question['evidence'] = question.get('evidence',[]) + copy.deepcopy(chain_evidence)
    revised['evidence_epoch'] = request['expected_epoch']
    issues = layout_differences(revised,observation)
    if revision in ROADSIDE_DECISION_REVISIONS:
        from .single_world import layout_roadside_differences
        issues.extend(layout_roadside_differences(revised, observation))
    if issues:
        # Both representations were built above from the same validated
        # decision. Disagreement here is a compiler invariant failure, not a
        # request for the model to change its visual evidence.
        error = ClosureStopped('internal_pipeline_error',
            'Compiled layout conflicts with its validated decision: '+', '.join(issues),
            dict(rule_id='world.layout.compilation_invariant', retry_kind='local',
                 issues=sorted(set(issues)), request_sha256=request['request_sha256'],
                 base_sha256=request['base_sha256']))
        error.rule_id = 'world.layout.compilation_invariant'
        raise error
    disagreements = (batch_disagreements(request,observation)
                     if revision not in {EVIDENCE_DECISION_REVISION, *DERIVED_DECISION_REVISIONS}
                     else [])
    if disagreements:
        raise ContractError('Layout decision conflicts with independent Batch: '
                            + ', '.join(disagreements), rule_id='world.layout.batch_disagreement')
    return revised


def batch_disagreements(request, observation):
    """Compare only independently evidenced core layout facts, not roadside use."""
    result = []
    question_id = request['lane_layout_contract']['question_id']
    for batch in request['focused_context']['relevant_batch_evidence']:
        for item in batch.get('observations', []):
            if item.get('question_id') != question_id or 'layout_observation' not in item:
                continue
            independent = item['layout_observation']
            batch_count = independent['same_direction_count']
            chosen_count = observation['same_direction_count']
            if batch_count['kind'] == chosen_count['kind'] == 'exact' and batch_count['count'] != chosen_count['count']:
                result.append(f"{batch['batch_id']}:same_direction_count:{batch_count['count']}!={chosen_count['count']}")
            batch_ego = independent['ego_initial']['ordinal_from_right']
            chosen_ego = observation['ego_initial']['ordinal_from_right']
            if batch_ego is not None and chosen_ego is not None and batch_ego != chosen_ego:
                result.append(f"{batch['batch_id']}:ego_initial:{batch_ego}!={chosen_ego}")
    return sorted(set(result))


def validate(request, original, revised, candidate):
    """A coherent structural unit, not a numeric count patch or fact rewrite."""
    def reject(message, *, rule_id='world.layout.patch'):
        raise ContractError(message, rule_id=rule_id)
    from _lib.contracts.fidelity import validator
    errors = list(validator('world_context.schema.json').iter_errors(revised))
    if errors:
        reject('Layout patch has an invalid World value: ' + errors[0].message)
    review = layout_review(original)
    if (request['lane_layout_contract'].get('revision') != REVISION or not review
            or request['lane_layout_contract'].get('question_id') != review['question_id']
            or request['allowed_paths'] != paths(original)):
        reject('Layout closure contract/scope binding is invalid')
    mapping = candidate.get('lane_id_map')
    old_road, new_road = original['world']['road'], revised['world']['road']
    old_lanes = {item['lane_id']: item for item in old_road['lanes']}
    new_lanes = {item['lane_id']: item for item in new_road['lanes']}
    if (not isinstance(mapping, dict) or set(mapping) != set(old_lanes)
            or any(not isinstance(v, str) or v not in new_lanes for v in mapping.values())
            or len(set(mapping.values())) != len(mapping)):
        reject('Layout closure requires a one-to-one map retaining every old physical lane')
    # Changing ordinal/ID or referenced boundaries is the point of this repair.
    # Existing known lane semantics and metrics are not disposable collateral.
    mutable = {'lane_id', 'ordinal_from_right', 'left_boundary', 'right_boundary', 'evidence_frame_ids'}
    for identifier, before in old_lanes.items():
        after = new_lanes[mapping[identifier]]
        for key, value in before.items():
            if key not in mutable and value not in (None, 'unknown') and after.get(key) != value:
                reject('Layout closure changed known lane property: ' + identifier + '.' + key)
        if before.get('travel_direction') == 'backward' and after != before:
            reject('Layout closure cannot revise opposing lanes')
    ordered_before = old_road['roads'][0]['lane_ids']
    ordered_after = new_road['roads'][0]['lane_ids']
    try:
        positions = [ordered_after.index(mapping[lid]) for lid in ordered_before]
    except (KeyError, ValueError):
        reject('Layout lane map is not represented by the road lane_ids')
    if positions != sorted(positions): reject('Layout closure reverses existing physical lane order')
    # No Actor is moved just because lane numbering changed. A different initial
    # physical corridor requires its own patch and frame evidence below.
    for old, new in zip(original['actor_hypotheses'], revised['actor_hypotheses']):
        lane = old.get('hypothesized_lane')
        expected = mapping.get(lane, lane)
        if new.get('hypothesized_lane') != expected:
            path = f"/actor_hypotheses/{original['actor_hypotheses'].index(old)}/hypothesized_lane"
            if not any(op['path'] == path for op in candidate['operations']):
                reject('Layout closure left an Actor reference unmapped')
    for collection, key, mutable_fields in (
            ('lane_markings', 'marking_id', {'type', 'marking_type', 'lane_ids', 'between', 'lane_id', 'side', 'location', 'evidence_frame_ids'}),
            ('roadside_areas', 'area_id', {'area_type', 'type', 'evidence_frame_ids'})):
        if any(key not in item for item in old_road.get(collection, [])):
            reject('Existing anonymous geometry cannot be uniquely patched: ' + collection)
        before = {item[key]: item for item in old_road.get(collection, [])}
        after = {item[key]: item for item in new_road.get(collection, [])}
        for identifier, old in before.items():
            if identifier not in after: reject('Layout closure deleted existing ' + collection)
            for field, value in old.items():
                if field not in mutable_fields and value not in (None, 'unknown') and after[identifier].get(field) != value:
                    reject('Layout closure changed known ' + collection + '.' + field)
    frames = request['focused_context']['evidence_frames']
    observation = layout_review(revised)['observation']
    validate_layout(observation, frames)
    for lane_id in ordered_after:
        lane = new_lanes[lane_id]
        aliases = [key for key in BOUNDARY_ALIASES if key in lane]
        if aliases:
            reject('Layout lane uses noncanonical boundary keys: ' + lane_id + '.' + ','.join(aliases),
                   rule_id='world.layout.patch_shape')
        if lane.get('travel_direction') == 'backward' or lane.get('lane_type') not in (None, 'driving'):
            continue
        missing = [key for key in CANONICAL_LANE_FIELDS if lane.get(key) is None]
        if missing:
            reject('Layout driving lane is missing canonical fields: ' + lane_id + '.' + ','.join(missing),
                   rule_id='world.layout.patch_shape')
        if lane['travel_direction'] not in {'forward', 'same_as_ego'}:
            reject('Layout driving lane has an invalid ego-direction: ' + lane_id,
                   rule_id='world.layout.patch_shape')
    count = observation['same_direction_count']['count']
    bounds = observation['boundaries_right_to_left']
    forward = [new_lanes[lid] for lid in ordered_after
               if new_lanes[lid].get('travel_direction') in {'forward', 'same_as_ego'}
               and new_lanes[lid].get('lane_type') == 'driving']
    markings = {item.get('marking_id') for item in new_road.get('lane_markings', [])}
    missing_boundaries = sorted(set(bounds) - markings)
    if missing_boundaries:
        reject('Layout boundary registry is missing marking_id entries for: '
               + ', '.join(missing_boundaries)
               + '. Register every physical boundary, including curbs and road edges, in lane_markings.',
               rule_id=BOUNDARY_REGISTRY_RULE)
    issues = layout_differences(revised, observation)
    if issues:
        rule_id = ('world.layout.patch_shape'
                   if set(issues) == {'world.layout.source_boundary_chain'}
                   else 'world.layout.patch')
        reject('Layout closure is incomplete or inconsistent: ' + ', '.join(issues),
               rule_id=rule_id)
    if len(forward) != count: reject('Layout closure lane definitions disagree with count')
    for ordinal, lane in enumerate(forward):
        if (lane.get('right_boundary') != bounds[ordinal] or lane.get('left_boundary') != bounds[ordinal+1]
                or bounds[ordinal] not in markings or bounds[ordinal+1] not in markings):
            reject('Layout closure must provide N+1 actual boundary definitions and lane references',
                   rule_id='world.layout.patch_shape')
    question_id = request['lane_layout_contract']['question_id']
    if not any(q['question_id'] == question_id and q['resolution'] == 'resolved' for q in revised['questions_to_verify']):
        reject('Layout review question remains unresolved')
    allowed_frames = {f['frame_id'] for f in frames}
    for op in candidate['operations']:
        if not set(op['evidence_frame_ids']) <= allowed_frames:
            reject('Layout patch cites frames outside its focused review packet')
        if op['path'].startswith(('/world/', '/actor_hypotheses/')):
            rows = revised['field_evidence'].get(dotted(op['path']), [])
            witnessed = {row.get('frame_id') for row in rows if row.get('status') == 'observed'}
            if not set(op['evidence_frame_ids']) <= witnessed:
                reject('Changed layout fact lacks exact-path observed evidence: ' + op['path'])
    for index, question in enumerate(revised['questions_to_verify']):
        if question['resolution'] != original['questions_to_verify'][index]['resolution']:
            if question['resolution'] != 'resolved' or not question.get('evidence'):
                reject('Layout closure cannot mark road questions non_core or remove their evidence')
    disagreements = batch_disagreements(request, observation)
    if disagreements:
        raise ContractError('World layout still conflicts with independent Batch: ' + ', '.join(disagreements)
            + '. Re-examine the disputed right-side corridor on the supplied original frames; '
              'submit an evidence-backed correction or needs_evidence.',
            rule_id='world.layout.batch_disagreement')
    return mapping


def remap_registry(registry, mapping, revised):
    result = copy.deepcopy(registry)
    actors = {a['actor_id']: a for a in revised['actor_hypotheses']}
    for actor in result:
        if actor.get('hypothesized_lane') is not None:
            actor['hypothesized_lane'] = mapping.get(actor['hypothesized_lane'], actor['hypothesized_lane'])
        if actor['actor_id'] in actors and 'hypothesized_lane' in actors[actor['actor_id']]:
            actor['hypothesized_lane'] = actors[actor['actor_id']]['hypothesized_lane']
    return result
