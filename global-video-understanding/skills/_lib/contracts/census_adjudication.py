"""Private, image-bound census decisions within the existing World closure task.

No task submission, ordinary identity-lock bypass, motion metrics or prose-based
background filtering lives here. Raw Batch observations remain immutable.
"""
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from _lib.contracts.census_evidence import OBLIGATION_REVISION
from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json, raw_read_json, sha256, validate_registry

REVISION = 'world-census-adjudication.v1'
FOCUSED_REVISION = 'world-census-adjudication.v2'
MERGE_REVISION = 'world-census-adjudication.v3'
SCOPE_REVISION = 'world-census-adjudication.v4'
INFORMATION_REVISION = 'world-census-adjudication.v5'
RELEVANCE_REVISION = 'world-census-adjudication.v6'
INFORMATION_REVISIONS = {INFORMATION_REVISION, RELEVANCE_REVISION}
SCOPE_REVISIONS = {SCOPE_REVISION, *INFORMATION_REVISIONS}
MERGE_REVISIONS = {MERGE_REVISION, *SCOPE_REVISIONS}
REVISIONS = {REVISION, FOCUSED_REVISION, *MERGE_REVISIONS}
FOCUSED_REVISIONS = {FOCUSED_REVISION, *MERGE_REVISIONS}
ACCEPTED = 'state/census_adjudication.accepted.json'
LANE_POSITION_KEY = 'census_lane_position_evidence'
LANE_POSITION_REVISION = 'census-lane-position-evidence.v1'


def fail(message):
    raise ContractError(message, rule_id='identity.census.adjudication')


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('world_census_adjudication') in REVISIONS


def records(case):
    result = []
    for path in sorted((Path(case)/'batches').glob('batch_*.json')):
        for frame in read_json(path).get('vehicle_census', []):
            for row in frame['vehicles']:
                if row['binding'] == 'unresolved':
                    result.append(dict(row, issue_id=f"{path.stem}:{frame['frame_id']}:{row['object_id']}",
                        batch_id=path.stem, frame_id=frame['frame_id'], time_s=frame['time_s']))
    return result


def response_schema(revision=REVISION, *, layout=None, lane_position_evidence=None):
    def obj(properties):
        return dict(type='object', additionalProperties=False,
                    required=list(properties), properties=properties)
    string = dict(type='string', minLength=1)
    optional = dict(type=['string', 'null'], minLength=1)
    ids = dict(type='array', uniqueItems=True, minItems=1, items=string)
    sighting = obj(dict(frame_id=string, time_s=dict(type='number', minimum=0),
        features=string, lane_id=optional,
        distinct_from_actor_ids=dict(type='array', uniqueItems=True, items=string)))
    if lane_position_evidence == LANE_POSITION_REVISION:
        sighting['properties']['lane_position_evidence'] = obj(dict(
            right_boundary_id=string, left_boundary_id=string))
    vehicle = obj(dict(key=string, actor_id=optional, split_from_actor_id=optional,
        identity_description=string, color=dict(anyOf=[string, dict(type='object')]), vehicle_class=string,
        sightings=dict(type='array', minItems=1, maxItems=12, items=sighting)))
    separation = obj(dict(direction=dict(const='opposite_to_ego'),
        side=dict(enum=['left', 'right']), separator=dict(enum=['barrier', 'median']),
        no_interaction=dict(const=True), reason=string, evidence_frame_ids=ids))
    binding = obj(dict(issue_id=string,
        outcome=dict(enum=['existing', 'new', 'excluded_opposing', 'unresolved']),
        vehicle_key=optional, reason=string, evidence_frame_ids=ids,
        separation=dict(anyOf=[dict(type='null'), separation])))
    if revision in FOCUSED_REVISIONS:
        vehicle['properties']['role'] = dict(enum=['binding', 'comparison'])
        vehicle['required'].append('role')
        binding['properties']['candidate_refutations'] = dict(type='array', maxItems=64,
            items=obj(dict(actor_id=string, witness_vehicle_key=string, reason=string,
                subject_sightings=dict(type='array', minItems=1, maxItems=12, items=sighting))))
        binding['required'].append('candidate_refutations')
    schema = obj(dict(revision=dict(const=revision), request_sha256=string,
        layout=dict(type=['object', 'null']),
        bindings=dict(type='array', minItems=1, maxItems=256, items=binding),
        vehicles=dict(type='array', maxItems=64, items=vehicle)))
    if revision in SCOPE_REVISIONS:
        from _lib.contracts.census_scope import schema as scope_schema
        binding['properties']['outcome']['enum'].append('observation_only')
        binding['properties']['scope_claim'] = dict(anyOf=[dict(type='null'), scope_schema()])
        binding['required'].append('scope_claim')
    if revision in MERGE_REVISIONS:
        schema['properties']['identity_groups'] = dict(type='array', maxItems=32, items=obj(dict(
            actor_ids=dict(type='array', minItems=2, maxItems=64, uniqueItems=True, items=string),
            vehicle_key=string, reason=string,
            anchor_matches=dict(type='array', minItems=2, maxItems=64, items=obj(dict(
                actor_id=string, frame_id=string, time_s=dict(type='number', minimum=0), features=string))))))
        schema['required'].append('identity_groups')
        if layout is None:
            schema['properties']['layout'] = dict(type='null')
        elif 'decision' in layout.get('value_schemas', {}):
            decision = copy.deepcopy(layout['value_schemas']['decision'])
            allowed = [f['frame_id'] for f in layout['focused_context']['evidence_frames']]
            def bind(node):
                if isinstance(node, dict):
                    fields = node.get('properties', {})
                    if 'evidence_frame_ids' in fields:
                        fields['evidence_frame_ids']['items'] = dict(type='string', enum=allowed)
                    for value in node.values(): bind(value)
                elif isinstance(node, list):
                    for value in node: bind(value)
            bind(decision)
            normal = obj(dict(contract_version=dict(const=layout['lane_layout_contract']['revision']),
                request_sha256=dict(const=layout['request_sha256']), base_sha256=dict(const=layout['base_sha256']),
                status=dict(const='decision'), decision=decision))
            # The existing inner compiler owns its evidence-insufficient envelope.
            stop = dict(type='object', required=['status'], properties=dict(
                status=dict(enum=['needs_evidence', 'blocked_by_patch_contract'])))
            schema['properties']['layout'] = dict(anyOf=[normal, stop])
    if revision in INFORMATION_REVISIONS:
        from _lib.contracts.census_information import extend_schema
        extend_schema(schema)
    if revision == RELEVANCE_REVISION:
        from _lib.contracts.census_relevance import extend_schema
        extend_schema(schema)
    return schema


def evidence_scopes(packet):
    return dict(layout=[f['frame_id'] for f in (packet.get('layout_request') or {}).get(
        'focused_context', {}).get('evidence_frames', [])], census=[f['frame_id'] for f in packet['frames']])


def validate_response(packet, response):
    errors = list(Draft202012Validator(packet['response_schema']).iter_errors(response))
    if not errors:
        return
    issues = []
    def leaves(error):
        if error.context:
            for child in error.context: yield from leaves(child)
        else: yield error
    for error in errors:
        for leaf in leaves(error):
            if len(issues) >= 64: break
            issues.append(dict(field_path='/'+'/'.join(str(v).replace('~','~0').replace('/','~1')
                for v in leaf.absolute_path), message=leaf.message,
                allowed_values=leaf.validator_value if leaf.validator == 'enum' else None))
    error = ContractError('Invalid combined census response', rule_id='identity.census.response_shape')
    error.diagnostics = dict(rule_id=error.rule_id, validation_issues=issues, retry_kind='content_repair')
    raise error


def verify_packet(packet):
    body = dict(packet); signature = body.pop('request_sha256', None)
    if packet.get('revision') not in REVISIONS or digest(body) != signature:
        fail('Census adjudication request binding changed')
    for path, expected in packet['input_hashes'].items():
        if not Path(path).is_file() or sha256(path) != expected:
            fail('Census adjudication input changed: ' + path)
    case = Path(packet['case_dir'])
    from _lib.contracts import census_history as history
    prior = history.parent(packet)
    lane_position = packet.get(LANE_POSITION_KEY)
    policy_path = (case/'state/execution_policy.json').resolve()
    policy = read_json(policy_path) if policy_path.is_file() else {}
    if (lane_position != policy.get(LANE_POSITION_KEY)
            or prior and lane_position != prior['request'].get(LANE_POSITION_KEY)
            or lane_position is not None and (lane_position != LANE_POSITION_REVISION
                or packet['revision'] != RELEVANCE_REVISION
                or str(policy_path) not in packet['input_hashes'])):
        fail('Census lane position evidence differs from the frozen policy or parent')
    if packet.get('relevance_adoption'):
        from _lib.runtime.census_relevance_adoption import verify
        verify(packet)
    elif packet.get('information_adoption'):
        from _lib.runtime.census_information_adoption import verify
        verify(packet)
    elif (packet['revision'] in INFORMATION_REVISIONS
            and read_json(case/'state/execution_policy.json').get('world_census_adjudication') != packet['revision']
            and not (prior and prior['request']['revision'] == packet['revision'])):
        fail('Information updates require frozen v5 policy or a verified adoption lineage')
    reopen = packet.get('scope_revocations', [])
    if packet.get('scope_recovery'):
        from _lib.runtime.census_scope_recovery import verify_trigger
        verify_trigger(packet, prior)
    expected_records = history.remaining_records(case, prior, reopen=reopen)
    if packet.get('scope_recovery'):
        from _lib.runtime.census_scope_recovery import selected_records
        expected_records = selected_records(expected_records, packet['scope_recovery'])
    if packet['records'] != expected_records:
        fail('Census request does not match its immutable Batch records')
    if prior:
        history.validate_scope(packet, prior)
    if packet['mode'] not in {'layout_and_census', 'census_after_layout'}:
        fail('Unknown census adjudication mode')
    if packet.get('layout_request') is not None and read_json(packet['layout_path']) != packet['layout_request']:
        fail('Nested layout request changed')
    if packet['revision'] in FOCUSED_REVISIONS:
        from _lib.contracts import census_evidence
        from _lib.contracts.census_evidence import build, EvidencePlanError
        from _lib.world.actor_facts import frames_for
        inherited = dict(confirmed_sightings=packet['confirmed_sightings']) if prior else {}
        options = dict(scope_coverage=packet['revision'] in SCOPE_REVISIONS and packet.get('scope_coverage_enabled', True),
                       source_world=raw_read_json(packet['world_path']), **inherited)
        from _lib.runtime.census_scope_recovery import alternative_actors
        options['alternative_actor_ids'] = alternative_actors(case, packet.get('scope_recovery'))
        planner = packet.get('evidence_plan', {}).get('revision', census_evidence.REVISION)
        policy = read_json(case/'state/execution_policy.json')
        frozen = policy.get(census_evidence.POLICY_KEY, census_evidence.REVISION)
        if prior:
            frozen = prior['request'].get('evidence_plan', {}).get('revision', frozen)
        if packet.get('evidence_plan_upgrade') is not None:
            preparation = Path(packet.get('evidence_preparation_path', ''))
            if (planner not in census_evidence.upgrades(frozen)
                    or packet['evidence_plan_upgrade'] != dict(from_revision=frozen,
                        reason='pre_submission_evidence_budget')
                    or prior or packet.get('scope_recovery') or packet.get('recovery_lineage')
                    or preparation != case/'diagnostics/world_census_adjudication/initial/before/evidence_preparation.json'
                    or not preparation.is_file()
                    or packet['input_hashes'].get(str(preparation)) != sha256(preparation)
                    or not census_evidence.unsubmitted(read_json(preparation))):
                fail('Census evidence upgrade lacks an unsubmitted preparation binding')
            predecessors = (frozen, *census_evidence.upgrades(frozen))
            for previous in predecessors[:predecessors.index(planner)]:
                try:
                    build(case, raw_read_json(packet['registry_path']), packet['records'], packet['layout_request'],
                          revision=previous, **options)
                except EvidencePlanError as error:
                    if (error.details['reason'] != 'evidence_budget_exceeded'
                            or error.details.get('required_count', 0) <= census_evidence.MAX_FRAMES):
                        raise
                else:
                    fail('Census evidence upgrade requires an original frame-budget overflow')
        elif planner != frozen:
            fail('Census evidence revision differs from the frozen policy')
        expected = build(case, raw_read_json(packet['registry_path']), packet['records'], packet['layout_request'],
                         revision=planner, **options)
        if (packet.get('evidence_plan') != expected
                or [f['frame_id'] for f in packet['frames']] != expected['selected_frame_ids']
                or packet['response_schema'] != response_schema(packet['revision'], layout=packet['layout_request'],
                                                                 lane_position_evidence=lane_position)):
            fail('Census evidence obligations or selected frames changed')
        if packet['revision'] in MERGE_REVISIONS and packet.get('evidence_scopes') != evidence_scopes(packet):
            fail('Combined evidence scopes changed')
        if packet['revision'] in SCOPE_REVISIONS:
            from _lib.contracts.census_scope import plan
            expected_scope = plan(case, packet['frames'])
            if type(packet.get('scope_coverage_enabled')) is not bool:
                fail('Observation scope coverage capability missing')
            if not packet['scope_coverage_enabled']:
                expected_scope['eligible'] = False
            if packet.get('scope_plan') != expected_scope:
                fail('Observation scope coverage plan changed')
        originals = {f['frame_id']:f for f in frames_for(case)}
        for frame in packet['frames']:
            source = originals[frame['frame_id']]
            if (frame['time_s'] != source['time_s']
                    or frame['source_path'] != str(Path(source.get('source_path') or source['path']).resolve())
                    or packet['input_hashes'].get(frame['path']) != frame['sha256']
                    or frame['source_path'] not in packet['input_hashes']):
                fail('Census selected frame lost source/time/hash binding')
    return prior


def validate_refutations(packet, row, decision, descriptors, observations, old, frames, lanes):
    """A rejected candidate and an alternative positive link are separate proofs."""
    obligation = next(o for o in packet['evidence_plan']['obligations'] if o['issue_id'] == row['issue_id'])
    refs = {r['actor_id']: r for r in decision['candidate_refutations']}
    if len(refs) != len(decision['candidate_refutations']) or set(refs)-set(obligation['comparison_actor_ids']):
        fail('Candidate refutations must name unique in-scope identities')
    kind, key = decision['outcome'], decision['vehicle_key']
    target = descriptors.get(key)
    if kind in {'existing', 'new'} and target is None:
        fail('Missing reviewed binding vehicle')
    if kind == 'excluded_opposing' and refs:
        fail('Opposing exclusion cannot carry identity refutations')

    def between(first, last, actor_id=None):
        lo, hi = sorted((frames[first]['time_s'], frames[last]['time_s']))
        selected = packet['evidence_plan']['selected_frame_ids']
        if packet['evidence_plan']['revision'] == OBLIGATION_REVISION and actor_id is not None:
            # Whole-case scope frames are inspection context, not an identity
            # sighting obligation for every vehicle in this issue.
            selected = obligation['links'].get(actor_id)
            if selected is None:
                fail('Refutation lacks a frozen vehicle identity link')
        return {fid for fid in selected if lo <= frames[fid]['time_s'] <= hi}

    for aid, ref in refs.items():
        witness_key = ref['witness_vehicle_key']
        witness = descriptors.get(witness_key)
        if witness is None or witness['actor_id'] != aid or witness['split_from_actor_id'] is not None:
            fail('Refutation requires a separately anchored existing vehicle')
        if target and aid in {target['actor_id'], target['split_from_actor_id']}:
            fail('Binding cannot refute its own identity')
        subject = {s['frame_id']: s for s in ref['subject_sightings']}
        if (len(subject) != len(ref['subject_sightings']) or row['frame_id'] not in subject
                or set(subject)-set(frames)):
            fail('Refutation must track the source object without invented frames')
        for fid, sight in subject.items():
            if (sight['time_s'] != frames[fid]['time_s']
                    or sight['lane_id'] is not None and sight['lane_id'] not in lanes
                    or not set(sight['distinct_from_actor_ids']) <= set(old)-{'ego'}):
                fail('Refutation subject frame/time/lane invalid')
            if target and observations[key].get(fid) != sight:
                fail('Refutation and positive binding describe different subject sightings')
        common = set(subject) & set(observations[witness_key])
        distinct = [fid for fid in common if aid in subject[fid]['distinct_from_actor_ids']]
        anchor = old[aid]['first_seen_frame_id']
        subject_actor = target['actor_id'] if kind == 'existing' else None
        if anchor not in observations[witness_key] or not any(
                between(row['frame_id'], fid, subject_actor) <= set(subject)
                and between(anchor, fid, aid) <= set(observations[witness_key]) for fid in distinct):
            fail('Refutation needs source tracking and co-visible anchored distinct vehicles')
    if kind in {'existing', 'new'}:
        aid, split = target['actor_id'], target['split_from_actor_id']
        if aid is not None:
            required = obligation['links'].get(aid)
            if required is None or not set(required) <= set(observations[key]):
                fail('Positive identity link omits frozen proof obligations')
        required_refs = set(row['candidate_actor_ids']) - {aid, split}
        if kind == 'new' and split is None:
            required_refs.update(obligation['comparison_actor_ids'])
        if not required_refs <= set(refs):
            fail('Alternative identity needs candidate refutations and duplicate checks')


def project_group_memberships(packet, response, registry):
    """Keep co-visible group members pending without certifying one-car identity."""
    if (packet['revision'] != RELEVANCE_REVISION
            or packet['evidence_plan']['revision'] != OBLIGATION_REVISION):
        return response, []
    actors = {a['actor_id']: a for a in registry}
    rows = {r['issue_id']: r for r in packet['records']}
    relevance = {r['issue_id']: r['assessment'] for r in response['relevance']}
    projected = copy.deepcopy(response)
    changes = []
    for vehicle in response['vehicles']:
        aid, key = vehicle['actor_id'], vehicle['key']
        actor = actors.get(aid)
        if (not actor or actor['actor_type'] != 'vehicle_group'
                or vehicle['role'] != 'binding' or vehicle['split_from_actor_id'] is not None
                or vehicle['corrections'] or len(vehicle['sightings']) != 1):
            continue
        anchor = actor['first_seen_frame_id']
        if vehicle['sightings'][0]['frame_id'] != anchor:
            continue
        bindings = [b for b in response['bindings'] if b['vehicle_key'] == key]
        slots = {(rows[b['issue_id']]['batch_id'], rows[b['issue_id']]['frame_id'])
                 for b in bindings if b['issue_id'] in rows}
        if (not bindings or len(bindings) < 2 or len(slots) == len(bindings)
                or any(g['vehicle_key'] == key or aid in g['actor_ids']
                       for g in response['identity_groups'])
                or any(ref['witness_vehicle_key'] == key for b in response['bindings']
                       for ref in b['candidate_refutations'])
                or any(b['issue_id'] not in rows or b['outcome'] != 'existing'
                       or rows[b['issue_id']]['frame_id'] != anchor
                       or rows[b['issue_id']]['candidate_actor_ids'] != [aid]
                       or b['evidence_frame_ids'] != [anchor]
                       or b['separation'] is not None or b['candidate_refutations']
                       or b['scope_claim'] is not None
                       or relevance.get(b['issue_id']) != 'background_candidate'
                       for b in bindings)):
            continue
        for binding in projected['bindings']:
            if binding['vehicle_key'] == key:
                binding.update(outcome='unresolved', vehicle_key=None)
        projected['vehicles'] = [v for v in projected['vehicles'] if v['key'] != key]
        changes.append(dict(actor_id=aid, vehicle_key=key, source_frame_id=anchor,
            issue_ids=sorted(b['issue_id'] for b in bindings),
            handling='group_membership_pending_individual_identity'))
    return (projected, changes) if changes else (response, [])


def verify_group_projection(saved):
    """A certificate's retained raw answer must reproduce its checking view."""
    raw = saved.get('raw_response')
    if raw is None:
        if 'response_projection' in saved:
            fail('Census group projection lost its original answer')
        return
    projected, changes = project_group_memberships(saved['request'], raw,
        raw_read_json(saved['request']['registry_path']))
    if not changes or projected != saved['response'] or changes != saved.get('response_projection'):
        fail('Census group projection differs from its original answer')


def identity_transition_frames(packet, frames, first, end):
    selected = {fid for fid, frame in frames.items()
                if frames[first]['time_s'] <= frame['time_s'] <= frames[end]['time_s']}
    if packet.get('evidence_plan', {}).get('revision') == OBLIGATION_REVISION:
        # Full-case scope inspection is not a sighting requirement for this Actor.
        selected = {fid for fid in selected if set(packet['evidence_plan']
            ['frame_reasons'][fid]) != {'full_case_scope_context'}}
    return selected


def comparison_anchor_lane_matches(packet, actor, sight):
    known = actor.get('hypothesized_lane')
    if known is None or sight['lane_id'] == known:
        return True
    # A lane established by the simultaneous layout decision can advance the
    # Registry without rewriting a comparison witness's original unknown lane.
    return (packet['mode'] == 'layout_and_census' and sight['lane_id'] is None
        and next(a for a in packet['actors'] if a['actor_id'] == actor['actor_id'])
            .get('hypothesized_lane') is None)


def _validate_lane_positions(descriptors, observations, old, frames, lanes, prior, aliases):
    """Require an observed corridor at the first known point and at changes."""
    from _lib.contracts.census_history import aliases as historical_aliases, rounds

    def corridor(lane_id):
        lane = lanes[lane_id]
        right, left = lane.get('right_boundary'), lane.get('left_boundary')
        if lane.get('road_id') and isinstance(right, str) and isinstance(left, str) and right and left and right != left:
            return lane['road_id'], right, left
        return (lane_id,)

    history = {}
    inherited_ids = historical_aliases(prior)
    for item in rounds(prior):
        response = item['response']
        if item['request']['mode'] == 'layout_and_census':
            lane_map = item['result']['world']['world']['metadata'][
                'lane_layout_review']['adjudication']['lane_id_map']
            response = normalize_confirmed_layout_sightings(
                response, raw_read_json(item['request']['registry_path']), lane_map)
        for vehicle in response['vehicles']:
            if vehicle.get('role') == 'comparison':
                continue
            actor_id = item['result']['vehicle_ids'][vehicle['key']]
            actor_id = inherited_ids.get(actor_id, actor_id)
            actor_id = aliases.get(actor_id, actor_id)
            for sight in vehicle['sightings']:
                if sight['lane_id'] in lanes:
                    history.setdefault(actor_id, {})[sight['frame_id']] = (sight['time_s'], sight['lane_id'])

    for key, vehicle in descriptors.items():
        if vehicle.get('role') == 'comparison':
            continue
        actor_id = vehicle.get('actor_id')
        actor = old.get(actor_id, {})
        anchors = dict(history.get(actor_id, {}))
        first = actor.get('first_seen_frame_id')
        if first in frames and actor.get('hypothesized_lane') in lanes:
            anchors[first] = (frames[first]['time_s'], actor['hypothesized_lane'])
        sights = observations[key]
        times = {fid: time for fid, (time, _) in anchors.items()}
        times.update((fid, sight['time_s']) for fid, sight in sights.items())
        previous = None
        for fid in sorted(times, key=lambda fid: (times[fid], fid)):
            if fid in anchors:
                previous = corridor(anchors[fid][1])
            sight = sights.get(fid)
            if sight is None or sight['lane_id'] is None:
                continue
            current = corridor(sight['lane_id'])
            proof = sight.get('lane_position_evidence')
            if proof is None:
                if current != previous:
                    fail(f'Census lane position evidence is required for {key} at {fid}')
            elif len(current) != 3 or (proof['right_boundary_id'], proof['left_boundary_id']) != current[1:]:
                fail(f'Census lane position evidence contradicts the physical corridor for {key} at {fid}')
            previous = current


def compile_census(packet, response, world, registry):
    """Compile evidence-only changes after the separate layout compiler passes."""
    prior = verify_packet(packet)
    errors = list(Draft202012Validator(packet['response_schema']).iter_errors(response))
    if errors:
        fail('Invalid census response: ' + errors[0].message)
    if response['request_sha256'] != packet['request_sha256']:
        fail('Census candidate belongs to another request')
    if len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode()) > 48*1024:
        fail('Combined adjudication response exceeds 48 KiB')
    response, _ = project_group_memberships(packet, response, registry)
    relevance = None
    if packet['revision'] == RELEVANCE_REVISION:
        from _lib.contracts.census_relevance import compile_scope
        relevance = compile_scope(packet, response)
    original_registry = registry
    if packet['revision'] in INFORMATION_REVISIONS:
        from _lib.contracts.census_information import expand
        response = expand(response, registry)
    aliases = {}; original_world=world
    if packet['revision'] in MERGE_REVISIONS:
        from _lib.contracts.census_identity import canonicalize
        packet, response, world, registry, aliases = canonicalize(packet, response, world, registry)
    frames = {f['frame_id']: f for f in packet['frames']}
    supplied = {r['issue_id']: r for r in packet['records']}
    decisions = {r['issue_id']: r for r in response['bindings']}
    if len(decisions) != len(response['bindings']) or set(decisions) != set(supplied):
        fail('Census must adjudicate every supplied record exactly once')
    if packet.get('scope_revocations') and (response['vehicles'] or response.get('identity_groups')
            or any(r['outcome'] != 'unresolved' for r in response['bindings'])):
        fail('Deterministic scope revocation cannot modify identities or reauthorize scope')
    old = {a['actor_id']: a for a in registry}
    if len(old) != len(registry):
        fail('Duplicate baseline Actor')
    descriptors = {v['key']: v for v in response['vehicles']}
    if len(descriptors) != len(response['vehicles']):
        fail('Duplicate reviewed vehicle key')
    used = {r['vehicle_key'] for r in response['bindings'] if r['outcome'] in {'existing', 'new'}}
    bound_keys = set(used)
    focused = packet['revision'] in FOCUSED_REVISIONS
    comparison_keys = {v['key'] for v in descriptors.values() if v.get('role') == 'comparison'}
    if focused:
        witnesses = {r['witness_vehicle_key'] for b in response['bindings'] for r in b['candidate_refutations']}
        if comparison_keys & bound_keys or comparison_keys - witnesses:
            fail('Comparison vehicles must only support explicit refutations')
        used.update(witnesses)
    split_parents={v['split_from_actor_id'] for v in descriptors.values() if v['split_from_actor_id']}
    retained_keys = {k for k,v in descriptors.items() if v['actor_id'] in split_parents}
    used.update(retained_keys)
    if focused and set(descriptors)-comparison_keys != bound_keys | retained_keys:
        fail('Binding vehicles must bind records or retain a split parent')
    if used != set(descriptors):
        fail('Reviewed vehicles must bind exactly the assigned records')
    by_actor, observations = {}, {}
    lanes = {l['lane_id']: l for l in world['world']['road']['lanes']}
    for key, vehicle in descriptors.items():
        aid, split = vehicle['actor_id'], vehicle['split_from_actor_id']
        if key in comparison_keys and (aid is None or split is not None):
            fail('Comparison witness must be an existing identity')
        if aid is not None and (aid not in old or aid == 'ego' or aid in by_actor or split is not None):
            fail('Existing vehicle identity missing, duplicated or mixed with split origin')
        if split is not None and (aid is not None or split not in old or split == 'ego'):
            fail('Split origin requires one existing non-Ego Actor')
        if aid is not None:
            by_actor[aid] = key
        sightings = {}
        for sight in vehicle['sightings']:
            fid = sight['frame_id']
            if (fid not in frames or fid in sightings or sight['time_s'] != frames[fid]['time_s']
                    or sight['lane_id'] is not None and sight['lane_id'] not in lanes
                    or not set(sight['distinct_from_actor_ids']) <= set(old)-{'ego', aid}):
                fail('Reviewed sighting frame/time/lane/distinction invalid')
            sightings[fid] = sight
        observations[key] = sightings
    if packet.get(LANE_POSITION_KEY) == LANE_POSITION_REVISION:
        _validate_lane_positions(descriptors, observations, old, frames, lanes, prior, aliases)
    split_ids = {v['split_from_actor_id'] for v in descriptors.values() if v['split_from_actor_id']}
    for aid in split_ids:
        if aid not in by_actor:
            fail('Split must review both retained and new vehicle')
        children = [k for k, v in descriptors.items() if v['split_from_actor_id'] == aid]
        if len(children) != 1:
            fail('One original Actor can split into exactly two vehicles')
        first, second = observations[by_actor[aid]], observations[children[0]]
        if len(first) < 2 or len(second) < 2 or not set(first)&set(second):
            fail('Split needs two sightings per vehicle and co-visibility')
        if old[aid]['first_seen_frame_id'] not in first:
            fail('Retained split identity must own its original first sighting')
    # Distinct reviewed objects cannot also claim an already-bound object in
    # the same Batch frame. Require explicit distinction for unrelated IDs.
    confirmed = packet['confirmed_sightings']
    distinction_issues = []
    for index, vehicle in enumerate(response['vehicles']):
        key = vehicle['key']
        own = {vehicle['actor_id'], vehicle['split_from_actor_id']} - {None}
        for sight_index, sight in enumerate(vehicle['sightings']):
            fid = sight['frame_id']
            required = set(confirmed.get(fid, [])) - own
            missing = sorted(required - set(sight['distinct_from_actor_ids']))
            if missing:
                distinction_issues.append(dict(
                    field_path=f'/vehicles/{index}/sightings/{sight_index}/distinct_from_actor_ids',
                    actor_id=vehicle['actor_id'], vehicle_key=key, frame_id=fid,
                    time_s=sight['time_s'], missing_actor_ids=missing,
                    message='Review each missing co-visible identity against the supplied evidence. '
                        'Do not fill distinctions without evidence; keep matching refutation '
                        'subject_sightings consistent with this observation.'))
    deferred_distinctions = []
    if distinction_issues and packet['revision'] == RELEVANCE_REVISION:
        from _lib.contracts.census_relevance import obligations
        deferred_distinctions = obligations(packet, distinction_issues)
    elif distinction_issues:
        first = distinction_issues[0]
        error = ContractError('Review must distinguish co-visible registered vehicles: '
            + f"{first['vehicle_key']} at {first['frame_id']} missing "
            + ', '.join(first['missing_actor_ids']), rule_id='identity.census.adjudication')
        error.diagnostics = dict(rule_id=error.rule_id, retry_kind='content_repair',
                                 validation_issues=distinction_issues)
        raise error
    resolved, excluded, claims, observation_only = [], [], {}, []
    for iid, decision in decisions.items():
        row = supplied[iid]; evidence = set(decision['evidence_frame_ids'])
        if not evidence <= set(frames) or row['frame_id'] not in evidence:
            fail('Census decision omits its source frame or invents evidence')
        kind, key = decision['outcome'], decision['vehicle_key']
        if packet['revision'] in SCOPE_REVISIONS and kind != 'observation_only' and decision['scope_claim'] is not None:
            fail('Identity/opposing/unresolved decision cannot carry a scope claim')
        if focused:
            validate_refutations(packet, row, decision, descriptors, observations, old, frames, lanes)
        if kind in {'existing', 'new'}:
            if decision['separation'] is not None or key not in descriptors:
                fail('Identity binding cannot also exclude an object')
            vehicle = descriptors[key]
            slot=(row['batch_id'],row['frame_id'],key)
            if slot in claims and claims[slot]!=row['object_id']:
                fail('Distinct co-visible census objects cannot share a reviewed identity')
            claims[slot]=row['object_id']
            if any(r['batch_id']==row['batch_id'] and r['frame_id']==row['frame_id']
                   and r['object_id']!=row['object_id'] and r['actor_id']==vehicle['actor_id']
                   for r in packet['confirmed_objects']):
                fail('Census binding merges with a distinct already-bound object in the same frame')
            if (kind == 'existing') != (vehicle['actor_id'] is not None):
                fail('Census outcome and reviewed vehicle disagree')
            if not focused and row['candidate_actor_ids'] and not ({vehicle['actor_id'], vehicle['split_from_actor_id']}
                                                   & set(row['candidate_actor_ids'])):
                fail('Census binding changes its candidate identity scope')
            if vehicle['split_from_actor_id'] and vehicle['split_from_actor_id'] not in row['candidate_actor_ids']:
                fail('Split must be bound to an assigned original identity')
            if not evidence <= set(observations[key]):
                fail('Identity decision lacks per-frame vehicle sightings')
            resolved.append(iid)
        elif kind == 'observation_only':
            from _lib.contracts.census_scope import validate_claim
            observation_only.append(validate_claim(packet, row, decision, world))
        elif kind == 'excluded_opposing':
            separation = decision['separation']
            if (key is not None or row['candidate_actor_ids'] or separation is None
                    or world['world']['road'].get('one_way') is True
                    or not evidence <= set(separation['evidence_frame_ids'])
                    or not set(separation['evidence_frame_ids']) <= set(frames)
                    or not {min(frames, key=lambda f:frames[f]['time_s']),
                            max(frames, key=lambda f:frames[f]['time_s'])} <= set(separation['evidence_frame_ids'])):
                fail('Opposing exclusion needs unbound object, separated direction and full-window evidence')
            for road in world['world']['road'].get('roads', []):
                known=(road.get('road_geometry') or {}).get('opposing_separation') or {}
                if (known.get('physically_separated') is False or
                        known.get('side') in {'left','right'} and known['side']!=separation['side']):
                    fail('Census exclusion contradicts known road separation')
            excluded.append(iid)
        elif key is not None or decision['separation'] is not None:
            fail('Unresolved census cannot assert identity or exclusion')
    result_world, result_registry = copy.deepcopy(world), copy.deepcopy(registry)
    updates, replacements, key_ids = {}, {}, {}
    occupied = set(old)|set(aliases)|set(packet.get('inherited_actor_aliases', {}))|{a['actor_id'] for a in world['actor_hypotheses']}
    for key, vehicle in descriptors.items():
        aid = vehicle['actor_id']
        sights = observations[key]
        first = min(sights, key=lambda f:frames[f]['time_s'])
        if key in comparison_keys:
            # A witness is checked, but never advances first_seen or edits Registry.
            actor = old[aid]
            from _lib.contracts.actors import vehicle_class_hint
            if (actor['first_seen_frame_id'] not in sights
                    or vehicle['identity_description'] != actor['identity_description']
                    or vehicle['color'] != actor.get('color', 'unknown')
                    or vehicle['vehicle_class'] != (vehicle_class_hint(actor) or 'unknown')
                    or not comparison_anchor_lane_matches(packet, actor,
                        sights[actor['first_seen_frame_id']])):
                fail('Comparison witness changed its frozen identity anchor')
            continue
        if aid is None:
            if len(sights) < 2:
                fail('New identity requires multiple reviewed sightings')
            index = 1
            while f'actor_{index:03d}' in occupied:
                index += 1
            aid = f'actor_{index:03d}'; occupied.add(aid)
            actor = dict(actor_id=aid, actor_type='vehicle')
        else:
            actor = copy.deepcopy(old[aid])
            old_first = actor['first_seen_frame_id']
            if old_first not in sights:
                fail('Existing identity review must include its frozen first sighting')
            if old_first != first and aid not in split_ids:
                end = max(sights, key=lambda f:frames[f]['time_s'])
                transition = identity_transition_frames(packet, frames, first, end)
                if (len(sights) < 3 or frames[first]['time_s'] >= frames[old_first]['time_s']
                        or frames[end]['time_s'] <= frames[old_first]['time_s'] or not transition <= set(sights)):
                    fail('Earlier identity needs original/later sightings and all supplied transition frames')
            # An explicit same-frame match to the frozen first sighting does
            # not claim a cross-frame identity extension. Its single source
            # frame is enough; later sightings still need the usual chain.
            same_anchor_only = (len(sights) == 1 and first == old_first)
            if len(sights) < 2 and not same_anchor_only:
                fail('Existing cross-frame identity requires multiple sightings')
            changed = [k for k in ('identity_description', 'color')
                       if vehicle[k] != actor.get(k, 'unknown')]
            if aid not in split_ids and changed:
                error = ContractError('Same-vehicle link cannot change existing identity/appearance: '
                    + aid + ' (' + ', '.join(changed) + ')', rule_id='identity.census.adjudication')
                index = next(i for i, item in enumerate(response['vehicles']) if item['key'] == key)
                error.diagnostics = dict(rule_id=error.rule_id, retry_kind='content_repair',
                    actor_id=aid, validation_issues=[dict(field_path=f'/vehicles/{index}/{field}',
                        message='Preserve the frozen Registry field; add new observations to sightings.',
                        expected_value=actor.get(field, 'unknown'), actual_value=vehicle[field]) for field in changed])
                raise error
            from _lib.contracts.actors import vehicle_class_hint
            if aid not in split_ids and vehicle['vehicle_class'] != (vehicle_class_hint(actor) or 'unknown'):
                fail('Same-vehicle link cannot change existing vehicle class')
            known_lane = actor.get('hypothesized_lane')
            if known_lane is not None and sights[old_first]['lane_id'] != known_lane:
                fail('Identity review cannot change a known initial lane')
        if vehicle['actor_id'] is None or aid in split_ids:
            actor.update(identity_description=vehicle['identity_description'], color=vehicle['color'],
                vehicle_class=(dict(status='unknown', value=None, reason='Not distinguishable')
                    if vehicle['vehicle_class']=='unknown' else dict(status='observed', value=vehicle['vehicle_class'],
                        frame_id=first, time_s=frames[first]['time_s'], reason=sights[first]['features'])))
        actor['first_seen_frame_id'] = first
        if 'first_seen_time_s' in actor:
            actor['first_seen_time_s'] = frames[first]['time_s']
        evidence = [dict(status='observed', frame_id=fid, time_s=s['time_s'], reason=s['features'])
                    for fid, s in sorted(sights.items(), key=lambda p:p[1]['time_s'])]
        actor['evidence'] = (actor.get('evidence', []) if aid not in split_ids else []) + evidence
        if not actor.get('hypothesized_lane') and sights[first]['lane_id'] is not None:
            actor['hypothesized_lane'] = sights[first]['lane_id']
        replacements[aid] = actor; key_ids[key] = aid
        if vehicle['actor_id'] is not None:
            updates[aid] = {k:actor[k] for k in ('identity_description', 'first_seen_frame_id')}
    for aid, actor in replacements.items():
        for collection in (result_registry, result_world['actor_hypotheses']):
            target = next((a for a in collection if a['actor_id'] == aid), None)
            if target is None:
                collection.append(copy.deepcopy(actor))
            else:
                # Preserve World-only known fields such as road_reference.
                target.update(copy.deepcopy(actor))
    baseline = copy.deepcopy(registry)
    for actor in baseline:
        actor.update(updates.get(actor['actor_id'], {}))
    validate_registry(baseline, result_registry)
    result = dict(world=result_world, registry=result_registry, resolved_issue_ids=sorted(resolved),
                excluded_issue_ids=sorted(excluded), vehicle_ids=key_ids,
                remaining_issue_ids=sorted(set(supplied)-set(resolved)-set(excluded)
                    -{r['issue_id'] for r in observation_only}))
    if packet.get('scope_recovery'):
        from _lib.contracts.census_history import parent
        prior = parent(packet)
        if prior:
            result['remaining_issue_ids'] = sorted(set(result['remaining_issue_ids']) |
                (set(prior['result']['remaining_issue_ids']) - set(supplied)))
    if packet['revision'] in SCOPE_REVISIONS:
        result.update(observation_only=observation_only,
                      revoked_scope_issue_ids=packet.get('scope_revocations', []))
    if packet['revision'] in MERGE_REVISIONS:
        result['canonical_actor_ids'] = aliases
        from _lib.contracts.census_identity import resolve_questions
        result['resolved_identity_question_ids']=resolve_questions(
            original_world,result_world,packet,response,aliases)
    if packet['revision'] in INFORMATION_REVISIONS:
        from _lib.contracts.census_information import apply_updates
        apply_updates(packet, response, original_registry, result)
    if relevance is not None:
        result.update(relevance=relevance, pending_distinctions=deferred_distinctions,
                      identity_verification='pending_scope' if deferred_distinctions else 'passed')
    return result


def preserve_world_revision(saved, before, after):
    """Finite post-Behavior carry rule; never re-certify identity or scope.

    Existing road/lane entities, Actors and questions stay exact. Only local
    junction connections and appended parking areas may change. A separation
    or observation-scope conclusion needs its own adjudication, not this rule.
    """
    from _lib.contracts.census_history import rounds
    from _lib.contracts.census_scope import active
    def blocked(message):
        error = ContractError(message, rule_id='identity.census.world_revision')
        error.code = 'world_census_readjudication_required'
        error.diagnostics = dict(rule_id=error.rule_id, stage='post_behavior_world_readjudication',
                                retry_kind='new_evidence', automatic_retry=False, message=message)
        raise error
    if before != saved['result']['world']:
        fail('World revision baseline differs from census proof')
    if (active(saved) or saved['result']['remaining_issue_ids']
            or any(r['result']['excluded_issue_ids'] for r in rounds(saved))):
        blocked('World revision requires census scope re-adjudication')
    expected = copy.deepcopy(before)
    expected['evidence_epoch'] = before['evidence_epoch'] + 1
    allowed_evidence = set()
    old_junctions = before['world'].get('junctions', [])
    new_junctions = after['world'].get('junctions', [])
    if len(old_junctions) != len(new_junctions):
        blocked('World revision changes census road entities')
    for index, (old, new) in enumerate(zip(old_junctions, new_junctions)):
        if old.get('lane_connections') != new.get('lane_connections'):
            expected['world']['junctions'][index]['lane_connections'] = copy.deepcopy(new.get('lane_connections'))
            allowed_evidence.add(f'world.junctions[{index}].lane_connections')
    old_areas = before['world']['road'].get('roadside_areas', [])
    new_areas = after['world']['road'].get('roadside_areas', [])
    if new_areas != old_areas:
        if len(new_areas) <= len(old_areas) or new_areas[:len(old_areas)] != old_areas:
            blocked('World revision changes existing census roadside facts')
        actors = {a['actor_id'] for a in saved['result']['registry']}
        if any(not set(area.get('occupied_by', [])) <= actors for area in new_areas[len(old_areas):]):
            blocked('World revision parking area restores an unproved identity')
        expected['world']['road']['roadside_areas'] = copy.deepcopy(new_areas)
        allowed_evidence.add('world.road.roadside_areas[-]')
    for key in allowed_evidence:
        if key in after.get('field_evidence', {}):
            expected.setdefault('field_evidence', {})[key] = copy.deepcopy(after['field_evidence'][key])
    if after != expected:
        blocked('World revision changes protected census facts; re-adjudication required')


def proof(case):
    from _lib.runtime.fact_versions import (ROOT as FACT_ROOT, current_facts,
        reviewed_world, verified_reconciliation_lane_points)
    case = Path(case)
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    path = (view.directory/'census_adjudication.accepted.json' if view else
            case/'state/census_adjudication.accepted.json')
    if view and view.census is None and read_json(path) == {}:
        if view.reconciliation.get('census_adjudication'):
            fail('Committed census certificate is missing')
        return None
    if not path.is_file():
        receipt = case/'state/reconciliation.json'
        if receipt.is_file() and (view.reconciliation if view else raw_read_json(receipt)).get('census_adjudication'):
            fail('Committed census certificate is missing')
        return None
    from _lib.contracts.census_history import replay
    saved = replay(read_json(path)); result = saved['result']
    from _lib.world.post_behavior import proof as revision_proof
    revision = revision_proof(case, census=saved)
    current_world = revision['world'] if revision else result['world']
    for name in ('world', 'registry'):
        actual = ((view.world if name == 'world' else view.registry) if view else
                  raw_read_json(case/'state'/('world_context.json' if name=='world' else 'actor_registry.json')))
        expected = current_world if name == 'world' else result[name]
        if view and actual != expected:
            # A later proved question review may change only the effective World;
            # the Census receipt remains bound to its historical World snapshot.
            if (name == 'world' and reviewed_world(case, view)) or \
                    verified_reconciliation_lane_points(case, view):
                expected = actual
        if actual != expected:
            fail('Census certificate no longer matches ' + name)
    if not view and not (case/'state/reconciliation.json').is_file():
        fail('Census reconciliation receipt is missing')
    receipt = view.reconciliation if view else raw_read_json(case/'state/reconciliation.json')
    point_handoff = (view is not None and
                     verified_reconciliation_lane_points(case, view))
    if (receipt.get('census_adjudication') != saved['request']['request_sha256']
            or receipt.get('world') != (view.world if point_handoff else current_world)
            or receipt.get('batch_sha256') != {p.name:sha256(p) for p in (Path(case)/'batches').glob('batch_*.json')}):
        fail('Census reconciliation receipt changed')
    if (saved['request'].get('parent_certificate') and
            receipt.get('remaining_census_issue_ids') != result['remaining_issue_ids']):
        fail('Census reconciliation remaining issues changed')
    return dict(saved, result=result)


def summary(result):
    value = {key:copy.deepcopy(result[key]) for key in ('vehicle_ids','resolved_issue_ids',
            'excluded_issue_ids','remaining_issue_ids')}
    if 'canonical_actor_ids' in result:
        value['canonical_actor_ids'] = copy.deepcopy(result['canonical_actor_ids'])
        value['resolved_identity_question_ids'] = copy.deepcopy(result['resolved_identity_question_ids'])
    if 'observation_only' in result:
        value.update(observation_only=copy.deepcopy(result['observation_only']),
                     revoked_scope_issue_ids=result['revoked_scope_issue_ids'])
    for key in ('relevance', 'pending_distinctions', 'identity_verification'):
        if key in result:
            value[key] = copy.deepcopy(result[key])
    if 'information_updates' in result:
        value['information_updates'] = copy.deepcopy(result['information_updates'])
    return value


def validate_behavior(case, behavior):
    saved = proof(case)
    if not saved:
        return
    from _lib.contracts.census_history import rounds
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    versioned = (Path(case)/FACT_ROOT/'active.json').is_file()
    view = current_facts(case) if versioned else None
    for item in rounds(saved):
        if view is None:
            _validate_behavior_round(item, behavior, current_registry=saved['result']['registry'])
        else:
            _validate_behavior_round(_effective_round(item, view), behavior,
                                     current_registry=view.registry)
    if view is not None:
        by_id = {actor['actor_id']: actor for actor in behavior['actors']}
        for claim in view.claims.values():
            if claim['field'] != 'position_ego_m':
                continue
            actor = by_id.get(claim['actor_id'])
            point = next((row for row in actor.get('trajectory', [])
                          if row.get('frame_id') == claim['frame_id']
                          and row.get('time_s') == claim['time_s']), None) if actor else None
            if point is None or point.get('position_ego_m') != claim['value']:
                fail('Behavior changed effective metric position: ' + claim['observation_anchor_id'])


def _effective_round(item, view):
    """Project current claims into a private validation copy, never a certificate."""
    projected = copy.deepcopy(item)
    response = projected['response']
    if projected['request'].get('mode') == 'layout_and_census':
        lane_map = projected['result']['world']['world']['metadata'][
            'lane_layout_review']['adjudication']['lane_id_map']
        response = normalize_confirmed_layout_sightings(
            response, raw_read_json(projected['request']['registry_path']), lane_map)
        projected['response'] = response
        projected['request']['mode'] = 'census_after_layout'
    vehicle_ids = projected['result']['vehicle_ids']
    split = []
    for vehicle in response['vehicles']:
        if vehicle.get('role') == 'comparison':
            split.append(vehicle)
            continue
        for sight in vehicle['sightings']:
            anchor = f"certificate:{item['sha256']}:{vehicle['key']}:{sight['frame_id']}"
            lane = view.claims.get(f'{anchor}:actor_lane')
            identity = view.claims.get(f'{anchor}:actor_identity')
            if lane is None or identity is None:
                fail('Effective Census sighting is missing from fact version')
            key = f"{vehicle['key']}@{sight['frame_id']}"
            row = copy.deepcopy(vehicle)
            row['key'] = key
            row['sightings'] = [dict(sight, lane_id=lane['value'])]
            split.append(row)
            vehicle_ids[key] = identity['value']
    response['vehicles'] = split
    return projected


def normalize_confirmed_layout_sightings(response, registry, lane_map):
    """Translate only pre-layout lanes inside an existing Actor's confirmed window."""
    translated = copy.deepcopy(response)
    anchored = {row['actor_id']: row for row in registry}
    for vehicle in translated['vehicles']:
        actor = anchored.get(vehicle.get('actor_id'))
        if not actor:
            continue
        old_lane = actor.get('hypothesized_lane')
        new_lane = lane_map.get(old_lane)
        if not new_lane or new_lane == old_lane:
            continue
        for sight in vehicle['sightings']:
            if (sight['lane_id'] == old_lane
                    and sight['time_s'] <= actor.get('last_seen_time_s', -1)):
                sight['lane_id'] = new_lane
    return translated


def _validate_behavior_round(saved, behavior, *, current_registry=None):
    result, packet, response = saved['result'], saved['request'], saved['response']
    if packet.get('mode') == 'layout_and_census':
        lane_map = result['world']['world']['metadata']['lane_layout_review']['adjudication']['lane_id_map']
        response = normalize_confirmed_layout_sightings(
            response, raw_read_json(packet['registry_path']), lane_map)
    by_id = {a['actor_id']: a for a in behavior['actors']}
    aliases=set(result.get('canonical_actor_ids', {}))
    def stale_reference(node):
        if isinstance(node,str): return node in aliases
        if isinstance(node,list): return any(stale_reference(v) for v in node)
        if isinstance(node,dict): return any(k in aliases or stale_reference(v) for k,v in node.items())
        return False
    if aliases and stale_reference(behavior):
        fail('Behavior restored a superseded duplicate identity')
    for vehicle in response['vehicles']:
        if vehicle.get('role') == 'comparison':
            continue
        aid = result['vehicle_ids'][vehicle['key']]
        actor = by_id.get(aid)
        if actor is None:
            fail('Behavior lost a reviewed census Actor: ' + aid)
        expected = next(a for a in (current_registry or result['registry']) if a['actor_id']==aid)
        for key in ('actor_type', 'identity_description', 'color', 'vehicle_class'):
            if key in expected and actor.get(key) != expected[key]:
                fail('Behavior restored superseded census identity: ' + aid)
        points = {p.get('frame_id'):p for p in actor.get('trajectory', [])}
        for sight in vehicle['sightings']:
            point = points.get(sight['frame_id'])
            if (point is None or point['time_s'] != sight['time_s']
                    or sight['lane_id'] is not None and point.get('lane_id') != sight['lane_id']):
                error = ContractError('Behavior omitted or changed reviewed census sighting: ' + aid,
                                      rule_id='identity.census.adjudication')
                error.diagnostics = dict(rule_id=error.rule_id, actor_id=aid,
                    frame_id=sight['frame_id'], expected_time_s=sight['time_s'],
                    actual_time_s=point.get('time_s') if point else None,
                    expected_lane_id=sight['lane_id'],
                    actual_lane_id=point.get('lane_id') if point else None,
                    retry_kind='new_evidence')
                raise error
    discharged = set(result['resolved_issue_ids']+result['excluded_issue_ids'])
    def references(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from references(value)
        elif isinstance(node, list):
            for value in node:
                yield from references(value)
        elif isinstance(node, str):
            yield node
    conflicts=(discharged & set(references(behavior.get('semantic_limitations', [])))) | (
        set(result['excluded_issue_ids']) & set(references([
            behavior.get('events', []), behavior.get('executor_primitives', [])])))
    if conflicts:
        from _lib.world.gaps import IdentityEvidenceGap
        raise IdentityEvidenceGap([dict(rule_id='identity.census.scope_conflict',owner='world',
            issue_id=iid,actor_ids=[],evidence_frame_ids=next(r['evidence_frame_ids'] for r in response['bindings'] if r['issue_id']==iid),
            impact_domains=['actor_identity'],message='Behavior challenges an accepted census identity/scope decision',
            retry_kind='new_evidence') for iid in sorted(conflicts)])


def behavior_prompt(case, *, read_view=None):
    saved = proof(case)
    if not saved:
        return ''
    scope = ('v2 中仅 role=binding 的车辆产生裁定后的身份/目击要求；role=comparison 只用于身份比较，'
             '不能据其 sightings 提前 first_seen 或额外编造轨迹。'
             if saved['request']['revision'] in FOCUSED_REVISIONS else '')
    if saved['request']['revision'] in MERGE_REVISIONS:
        scope += ('result.canonical_actor_ids 是已验收的旧 ID 到规范 ID 映射。原始 Batch 保留历史 ID，'
                  '解释其观察时必须用该映射；车辆、轨迹、事件和动作只能引用正式 Registry 的规范 ID。')
    if saved['request']['revision'] in INFORMATION_REVISIONS:
        scope += ('information_updates 保留登记快照、后续证据和已验收纠正；'
                  '首次登记和历史凭证只用于追溯，当前外观字段使用最新正式 Registry，'
                  '不得恢复已纠正的旧值，所有历史已验收目击与身份区分仍有效。')
    from _lib.contracts.census_history import certificate_paths
    history = ('' if read_view else ''.join(
        f'\n同时读取不可变历史裁定：{path}。' for path in sorted(certificate_paths(saved))))
    if saved['request']['revision'] in SCOPE_REVISIONS:
        scope += ('observation_only 是待本轮逐 issue 复查的范围授权，身份仍未知；'
                  '它不创建 Actor，也不允许删掉 Registry 车辆。按本轮 census_scope_review 合同复查。')
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts
    versioned = (Path(case)/FACT_ROOT/'active.json').is_file()
    current_note = ''
    if versioned:
        view = current_facts(case)
        current_note = (f'\n当前有效事实版本为 {view.version_id}；读取 {view.directory/"claims.json"}。'
                        '若与历史证书冲突，以当前有效 claim 为准；历史 claim 仅供追溯。')
    from _lib.behavior import census_read_view
    compact = bool(read_view and census_read_view.revision_for(case) == census_read_view.COMPACT_REVISION)
    reading = (f'\n已验收 census 裁定：{Path(case)/ACCEPTED}。读取证书 request/response/result。'
               if read_view is None else
               f'\n读取已冻结的 Census 来源处置视图 {read_view}：unresolved_sources 按原 source_id 列出'
               '正式 resolved/excluded、真正 remaining/unresolved_source 与独立的 preliminary_relevance。'
               '只对 remaining/unresolved_source 且与核心场景相关或需要动态判断的观察作新裁决；'
               'background_candidate 只是初判，不能作为正式排除证明。已正式处置的来源沿 census_proof 指针继承，'
               'shared_road_proofs 仅复用相同正式道路隔离证据，各来源仍须有独立观察帧、时间、对象关系和绑定。'
               '不得把道路证据写成逐帧车辆目击；Ego、目标车道车辆、交互和跨帧运动继续完整处理。'
               '源证明过期、对象不符或出现交互反证时报告缺口。按 source_id/census_proof 定向回查原证书、Batch 和帧。'
               if compact else
               f'\n读取已冻结的 Census 精简阅读视图 {read_view}：逐轮核对 request.records、'
               'response.bindings/vehicles/relevance、result、pending_issues 和 unresolved_sources；'
               '若读取工具截断，按行续读到视图末尾。'
               '未决来源原文及状态仍在 behavior_input.request.json 的 evidence_timeline.unresolved。'
               'unresolved_sources 中 census_issue_id 为 null 表示没有可验证的精确车辆 issue 关联，'
               '不得自行猜测它属于哪个 Actor 或 issue。视图的 sources 保存原证书、Batch、有效事实版本和哈希；'
               '遇到冲突、缺项或需要细节时，按已有 issue_id、source_id 及 JSON pointer 定向回查完整来源。'
               '不能验证来源或映射时报告缺口，不把精简视图当作完整证据。')
    return (reading + history + current_note +
            '原始 Batch 是历史观察，不得按已推翻的关联恢复误合并。沿用 Registry 的修订身份，'
            + scope + '覆盖上述绑定车辆 sightings 中每个真实帧及已确认车道；未知米制量保持未知。'
            '背景排除仅适用于 excluded_issue_ids 对应的证据对象，不能扩展到其他车辆。'
            '若发现事件/动作依赖排除对象，或新证据冲突，报告具体证书 issue_id，不能静默丢弃。')
