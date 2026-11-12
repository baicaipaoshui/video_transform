"""Derive redundant executor initial states from evidence-backed trajectories."""
import copy
import math
import hashlib
import json
from pathlib import Path

from _lib.contracts.actors import is_vehicle


def preview_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def normalize_empty_optional_census_refs(source):
    """Remove an empty, optional issue index without changing a limitation claim.

    The schema permits absence but requires at least one ID when the index is
    present.  An empty index binds no census issue, just like absence; nonempty
    indexes and all limitation facts remain under the original contracts.
    """
    if not isinstance(source, dict) or not isinstance(source.get('semantic_limitations'), list):
        return source, []
    changed = [index for index, item in enumerate(source['semantic_limitations'])
               if isinstance(item, dict) and item.get('census_issue_ids', object()) == []]
    if not changed:
        return source, []
    result = copy.deepcopy(source)
    for index in changed:
        del result['semantic_limitations'][index]['census_issue_ids']
    return result, [dict(
        rule_id='behavior.optional_empty_census_refs.v1',
        basis='behavior.schema.json: semantic limitation census_issue_ids is optional; '
              'when present it must be nonempty; [] and absence bind no issue',
        hash_encoding='sha256(canonical JSON: sorted keys, UTF-8, compact separators)',
        before_sha256=preview_digest(source), after_sha256=preview_digest(result),
        changed_fields=[f'/semantic_limitations/{index}/census_issue_ids' for index in changed],
        action='remove_empty_optional_index')]


def normalize_unresolved_lane_closure_notes(source, world):
    """Keep a World question unresolved when a full Behavior echoes that answer.

    ``lane_closures`` belongs to bounded repair packets, not full Behavior.  An
    explicit unknown cannot advance the frozen World fact; retain the submitted
    note in the repair audit and leave the World question untouched.
    """
    if not isinstance(source, dict) or 'lane_closures' not in source:
        return source, []
    from _lib.contracts.v3 import ContractError
    questions = {q['question_id']: q for q in (world or {}).get('questions_to_verify', [])}
    notes = source['lane_closures']
    if not isinstance(notes, list) or not notes:
        raise ContractError('Full Behavior lane_closures must contain only frozen unknown questions')
    for note in notes:
        if not isinstance(note, dict) or set(note) != {
                'collection', 'issue_id', 'actor_id', 'outcome', 'evidence_frame_ids', 'reason'}:
            raise ContractError('Full Behavior lane_closures has an unsupported claim')
        question = questions.get(note['issue_id'])
        if (note['collection'] != 'questions_to_verify' or note['outcome'] != 'unknown'
                or question is None or question.get('resolution') != 'unresolved'
                or note['actor_id'] not in question.get('actor_ids', [])
                or not isinstance(note['reason'], str) or not note['reason'].strip()
                or note['evidence_frame_ids'] != [e['frame_id'] for e in question.get('evidence', [])
                    if e.get('status') == 'observed']):
            raise ContractError('Full Behavior lane_closures cannot change a World lane fact')
    normalized = copy.deepcopy(source)
    normalized.pop('lane_closures')
    return normalized, [dict(action='retained_unknown_world_lane_question',
                             source_claim=copy.deepcopy(note)) for note in notes]


def preview_observations(actors):
    return [{k: copy.deepcopy(a.get(k)) for k in ('actor_id','identity_description','is_static',
            'simulation_role','trajectory')} for a in actors]


def _evidence_domain(path):
    """Map an Actor-local evidence path to a structured limitation domain."""
    if any(part in {'speed_mps', 'speed_kmh'} for part in path):
        return 'speed'
    return None


def _unknown_nodes(value, path=()):
    if isinstance(value, dict):
        if value.get('status') == 'unknown' and not value.get('reason'):
            yield path, value
        for key, child in value.items():
            yield from _unknown_nodes(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _unknown_nodes(child, path + (index,))


def materialize_inherited_unknown_reasons(source):
    """Expand actor/domain-bound semantic limitations into an analysis-ready copy.

    Behavior stays compact: repeated unknown measurements may be covered by one
    actor/domain-bound semantic limitation.  Published analysis remains
    self-contained by carrying a short, resolvable reason at every evidence
    leaf.  No status, value, identity, trajectory, or limitation is changed.
    """
    result = copy.deepcopy(source)
    bindings = []
    limitations = [item for item in result.get('semantic_limitations', [])
                   if isinstance(item, dict)]
    for actor_index, actor in enumerate(result.get('actors', [])):
        if not isinstance(actor, dict) or not isinstance(actor.get('actor_id'), str):
            continue
        actor_id = actor['actor_id']
        for local_path, node in _unknown_nodes(actor):
            domain = _evidence_domain(local_path)
            if domain is None:
                continue
            matches = [item for item in limitations
                       if isinstance(item.get('actor_ids'), list)
                       and actor_id in item['actor_ids']
                       and isinstance(item.get('impact_domains'), list)
                       and domain in item['impact_domains']
                       and isinstance(item.get('limitation_id'), str)
                       and item['limitation_id'].strip()
                       and isinstance(item.get('description'), str)
                       and item['description'].strip()]
            if not matches:
                continue
            limitation_ids = sorted({item['limitation_id'] for item in matches})
            node['reason'] = ('Inherited from semantic limitation '
                              + limitation_ids[0] + '.' if len(limitation_ids) == 1
                              else 'Inherited from semantic limitations ' + ', '.join(limitation_ids) + '.')
            path = ('actors', actor_index) + local_path + ('reason',)
            bindings.append({
                'path': '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1')
                                       for part in path),
                'actor_id': actor_id,
                'impact_domain': domain,
                **({'limitation_id': limitation_ids[0]} if len(limitation_ids) == 1
                   else {'limitation_ids': limitation_ids}),
            })
    return result, bindings


def normalize_optional_color_claims(source):
    """Withdraw unsupported color estimates without inventing confidence."""
    result = copy.deepcopy(source)
    repairs = []
    for index, actor in enumerate(result.get('actors', [])):
        if not isinstance(actor, dict):
            continue
        color = actor.get('color')
        if not isinstance(color, dict):
            continue
        if color.get('status') == 'unknown':
            if isinstance(color.get('reason'), str) and color['reason'].strip():
                continue
            original = copy.deepcopy(color)
            actor['color'] = {
                'status': 'unknown', 'value': None,
                'reason': 'The source did not establish this optional appearance detail.',
            }
            repairs.append({
                'action': 'complete_optional_color_unknown_reason',
                'rule_id': 'appearance.unknown_reason_completed',
                'actor_id': actor.get('actor_id'),
                'field_path': f'/actors/{index}/color',
                'source_claim': original,
                'admitted_value': copy.deepcopy(actor['color']),
                'execution_effect': 'non_blocking',
            })
            continue
        if color.get('status') != 'estimated':
            continue
        confidence = color.get('confidence')
        valid_confidence = (type(confidence) in (int, float)
                            and math.isfinite(confidence) and 0 <= confidence <= 1)
        valid_reason = isinstance(color.get('reason'), str) and bool(color['reason'].strip())
        if valid_confidence and valid_reason:
            continue
        original = copy.deepcopy(color)
        actor['color'] = {
            'status': 'unknown', 'value': None,
            'reason': 'Estimated color lacked valid confidence or reason; the appearance claim was withdrawn.',
        }
        repairs.append({
            'action': 'withdraw_invalid_optional_color_estimate',
            'rule_id': 'appearance.invalid_estimate_withdrawn',
            'actor_id': actor.get('actor_id'),
            'field_path': f'/actors/{index}/color',
            'source_claim': original,
            'admitted_value': copy.deepcopy(actor['color']),
            'execution_effect': 'non_blocking',
        })
    return result, repairs


def validate_behavior_evidence(data):
    """Validate speed and optional appearance claims at the Behavior boundary."""
    materialized, bindings = materialize_inherited_unknown_reasons(data)
    from _lib.runtime.case_core import validate_evidence, EvidenceValidationError
    try:
        for actor_index, actor in enumerate(materialized.get('actors', [])):
            if not isinstance(actor, dict):
                continue
            if (isinstance(actor.get('color'), dict)
                    and actor['color'].get('status') == 'estimated'):
                validate_evidence(actor['color'], ('actors', actor_index, 'color'))
            for point_index, point in enumerate(actor.get('trajectory', [])):
                if not isinstance(point, dict) or not isinstance(point.get('speed_mps'), dict):
                    continue
                validate_evidence(
                    point['speed_mps'],
                    ('actors', actor_index, 'trajectory', point_index, 'speed_mps'),
                    point.get('frame_id'), point.get('time_s', point.get('timestamp_s')))
    except EvidenceValidationError as error:
        from _lib.contracts.v3 import ContractError
        path = tuple(error.absolute_path)
        actor_id = None
        if len(path) > 1 and path[0] == 'actors' and isinstance(path[1], int):
            actors = data.get('actors', [])
            if path[1] < len(actors) and isinstance(actors[path[1]], dict):
                actor_id = actors[path[1]].get('actor_id')
        detail = str(error).rsplit(': ', 1)[-1]
        rule_id = {
            'unknown reason missing': 'evidence.unknown_reason_missing',
            'observed evidence missing': 'evidence.observed_binding_missing',
            'estimated confidence missing': 'evidence.estimated_confidence_missing',
            'confidence outside 0..1': 'evidence.confidence_out_of_range',
        }.get(detail, 'evidence.contract')
        failure = ContractError(str(error), rule_id=rule_id, actor_id=actor_id)
        failure.path = path
        failure.absolute_path = path
        failure.diagnostics = {
            'rule_id': rule_id,
            'field_path': '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1')
                                         for part in path),
            'actor_id': actor_id,
            'retry_kind': 'content_repair',
        }
        raise failure from error
    return materialized, bindings


def restore_registry_actor_evidence(source, registry, *, collection='actors'):
    """Restore provenance dropped while copying frozen Actor identity into Behavior.

    Registry owns identity attributes such as ``vehicle_class``. A Behavior
    candidate may repeat the exact frozen value/status while accidentally
    omitting its structured frame binding. In that one unambiguous case, copy
    only the missing provenance fields from Registry; never infer a frame from
    prose, change a value/status, or manufacture evidence for a new claim.
    """
    result = copy.deepcopy(source)
    repairs = []
    frozen = {row.get('actor_id'): row for row in registry if isinstance(row, dict)}

    def bound(value):
        if not isinstance(value, dict):
            return False
        if value.get('frame_id') or value.get('time_s') is not None:
            return True
        evidence = value.get('evidence')
        return isinstance(evidence, list) and any(
            isinstance(item, dict) and item.get('status') == 'observed'
            and (item.get('frame_id') or item.get('time_s') is not None)
            for item in evidence)

    for index, actor in enumerate(result.get(collection, [])):
        if not isinstance(actor, dict):
            continue
        registry_actor = frozen.get(actor.get('actor_id'))
        if not isinstance(registry_actor, dict):
            continue
        for field in ('vehicle_class',):
            current = actor.get(field)
            original = registry_actor.get(field)
            if (not isinstance(current, dict) or not isinstance(original, dict)
                    or current.get('status') != 'observed'
                    or original.get('status') != 'observed'
                    or current.get('value') != original.get('value')
                    or bound(current) or not bound(original)):
                continue
            copied = []
            for key in ('frame_id', 'time_s', 'evidence'):
                if key not in current and key in original:
                    current[key] = copy.deepcopy(original[key])
                    copied.append(key)
            if copied:
                repairs.append({
                    'action': 'restore_frozen_actor_evidence',
                    'actor_id': actor['actor_id'],
                    'path': f'{collection}.{index}.{field}',
                    'source': 'state/actor_registry.json',
                    'copied_fields': copied,
                })
    return result, repairs


def preview_scale_limitation(item):
    """Absolute scale can be synthesized; identity, events and paths cannot."""
    from _lib.contracts.execution_gaps import scale_limitation
    return scale_limitation(item)


def preview_limitation_issues(world, behavior, review):
    """Report all independent blockers, including omitted frozen limitations."""
    if not review or review.get('status') != 'accepted': return []
    facts=review.get('relations', {})
    claims={item['limitation_id']:item for item in facts.get('non_blocking_limitations', [])}
    excluded={item['limitation_id'] for item in observation_scope_exclusions(world,behavior)}
    issues=[]
    for item in behavior['semantic_limitations']:
        lid=item['limitation_id']
        if lid in excluded: continue
        claim=claims.get(lid)
        if (claim is None or (item.get('execution_effect')=='blocks_execution' and
                not (claim.get('kind')=='absolute_scale' and preview_scale_limitation(item)))):
            issues.append({'rule_id':'execution.preview_limitation_blocked','limitation_id':lid,
                'message':('Unreviewed limitation: ' if claim is None else 'Blocking limitation: ')+lid,
                'required_change':'Provide evidence-backed upstream classification and a complete bound review; do not omit the limitation.'})
    return issues


def observation_scope_exclusions(world, behavior):
    """Return limitations proven outside execution without inventing Actors.

    New candidates carry a structured scope_binding.  The single legacy ID is
    retained for already-frozen Cases that used the original documented name;
    it is accepted only after the same World/Behavior proof and is surfaced in
    the audit rather than written back into Behavior.
    """
    from _lib.world.readiness import ego_carriageway_execution_scope
    scope = ego_carriageway_execution_scope(world, behavior)
    if not scope or scope.get('opposing_physically_separated') is not True:
        return []
    expected = {
        'kind': 'physically_separated_opposing_observations',
        'world_paths': ['world.road.opposite_direction_lanes'],
        'representation': 'observation_only',
        'affected_event_ids': [],
    }
    results = []
    for item in behavior.get('semantic_limitations', []):
        binding = item.get('scope_binding')
        legacy = (binding is None
                  and item.get('limitation_id') == 'lim_opposing_traffic_not_modeled'
                  and set(item.get('impact_domains', [])) == {'actors', 'events'})
        if binding != expected and not legacy:
            continue
        if item.get('execution_effect') != 'bounded_uncertainty':
            continue
        results.append({
            'limitation_id': item['limitation_id'],
            'decision': 'excluded',
            'kind': expected['kind'],
            'world_paths': list(expected['world_paths']),
            'affected_actor_ids': [],
            'affected_event_ids': [],
            'reason': ('Structured physical separation and ego-carriageway-only execution prove '
                       'that uninstantiated opposing observations do not affect this target.'),
            'legacy_normalization': legacy,
        })
    return results


def preview_collision_check(conditions, envelopes, duration):
    """Continuous closed-interval overlap for constant velocity rectangles."""
    from _lib.contracts.v3 import ContractError
    ids = sorted(conditions)
    for i, aid in enumerate(ids):
        a = conditions[aid]; pa = a['position_ego_m']
        for bid in ids[i+1:]:
            b = conditions[bid]; pb = b['position_ego_m']
            if abs(pa['y']-pb['y']) > (envelopes[aid]['width_m']+envelopes[bid]['width_m'])/2:
                continue
            half = (envelopes[aid]['length_m']+envelopes[bid]['length_m'])/2
            start = pb['x']-pa['x']
            end = start + (b['speed_kmh']['value']-a['speed_kmh']['value'])/3.6*duration
            if min(start,end) <= half and max(start,end) >= -half:
                raise ContractError(f'preview collision envelope overlap: {aid}/{bid}')
    return True


def lane_change_preview_collision_check(conditions, envelopes, duration, spec, centres, anchor):
    """Continuous rectangle-envelope check for constant-speed preview motion."""
    from _lib.contracts.v3 import ContractError
    changing = conditions[spec.actor_id]
    y0 = centres[spec.source_lane_id] - anchor
    y1 = centres[spec.target_lane_id] - anchor

    def lateral(time_s):
        if time_s <= spec.start_s: return y0
        if time_s >= spec.end_s: return y1
        return y0 + (y1-y0) * (time_s-spec.start_s) / (spec.end_s-spec.start_s)

    for aid, other in conditions.items():
        if aid == spec.actor_id: continue
        half_width=(envelopes[spec.actor_id]['width_m']+envelopes[aid]['width_m'])/2
        half_length=(envelopes[spec.actor_id]['length_m']+envelopes[aid]['length_m'])/2
        other_y=other['position_ego_m']['y']; cuts=[0.0,spec.start_s,spec.end_s,duration]
        for lo,hi in zip(cuts,cuts[1:]):
            if hi<=lo: continue
            a,b=lateral(lo)-other_y,lateral(hi)-other_y
            intervals=[lo,hi]
            if abs(b-a)>1e-12:
                for boundary in (-half_width,half_width):
                    t=lo+(boundary-a)*(hi-lo)/(b-a)
                    if lo<t<hi: intervals.append(t)
            intervals=sorted(set(intervals))
            for start,end in zip(intervals,intervals[1:]):
                if abs(lateral((start+end)/2)-other_y)>half_width+1e-9: continue
                def dx(t):
                    return (changing['position_ego_m']['x']-other['position_ego_m']['x']+
                            (changing['speed_kmh']['value']-other['speed_kmh']['value'])/3.6*t)
                if min(dx(start),dx(end))<=half_length and max(dx(start),dx(end))>=-half_length:
                    raise ContractError(f'preview collision envelope overlap: {spec.actor_id}/{aid}')
    return True


def build_straight_preview(world, behavior, policy, review):
    """Execution-only projection for straight motion or one bounded lane change."""
    from _lib.contracts.v3 import ContractError
    def require(ok, message):
        if not ok: raise ContractError('preview: '+message)
    require(isinstance(review,dict) and review.get('status') == 'accepted', 'accepted relation review required')
    all_actors = behavior['actors']; facts = review['relations']
    from _lib.behavior.lane_change import derive as derive_lane_change
    duration = max((p.get('time_s', p.get('timestamp_s', 0))
                    for a in all_actors for p in a.get('trajectory', [])), default=0)
    lane_change = derive_lane_change(world, behavior, duration)
    require(review.get('observation_sha256') == preview_digest(preview_observations(all_actors)), 'observation binding changed')
    require(review.get('world_sha256') == preview_digest(world), 'World binding changed')
    if lane_change is None:
        require(not behavior['events'] and not any(p['type'] != 'initial_state' for p in behavior['executor_primitives']), 'events/actions are unsupported')
        require(facts.get('event_state') == 'none', 'event uncertainty')
    else:
        require(facts.get('event_state') == 'present', 'lane-change event must be confirmed by review')
    require(facts.get('motion_conflicts') == 'none', 'motion uncertainty')
    scope_exclusions = observation_scope_exclusions(world, behavior)
    covered = set(facts.get('non_blocking_limitation_ids', []))
    covered.update(item['limitation_id'] for item in scope_exclusions)
    require(covered <= {l['limitation_id'] for l in behavior['semantic_limitations']}, 'unknown limitation ID')
    for item in behavior['semantic_limitations']:
        require(item['limitation_id'] in covered and
                (item.get('execution_effect') != 'blocks_execution' or preview_scale_limitation(item)),
                'unreviewed or blocking limitation: '+item['limitation_id'])
    from _lib.world.readiness import world_execution_issues, world_uncertainties
    require(not world_execution_issues(world_uncertainties(world), world, behavior,
                {'fallback_policy':policy}), 'World execution dependencies remain unresolved')
    scene = world['world']; road = scene['road']; roads = road.get('roads', [])
    require(scene['scene_type']=='highway' and len(roads)==1 and not scene.get('junctions'), 'single highway road required')
    r = roads[0]; geom = r.get('road_geometry', {})
    require(geom.get('shape_type')=='straight' and facts.get('shape_type')=='straight', 'explicit straight geometry required')
    from _lib.world.readiness import ego_carriageway_execution_scope
    divided_scope = ego_carriageway_execution_scope(world, behavior)
    require((road.get('one_way') is True or divided_scope is not None)
            and r.get('traffic_direction','forward')=='forward'
            and abs(r.get('heading_rad',999)) < 1e-9, 'forward isolated ego-carriageway alignment required')
    require(not any(geom.get(k) not in (None,0) for k in ('curvature','radius_m','road_radius')), 'curvature unsupported')
    lane_ids = facts.get('lane_order_right_to_left', [])
    require(lane_ids == r['lane_ids'] and len(set(lane_ids))==len(lane_ids), 'lane order does not match World')
    lanes = {l['lane_id']: l for l in road['lanes']}
    widths = {}; width_sources = {}
    for lid in lane_ids:
        lane = lanes[lid]
        require(lane.get('travel_direction','forward')=='forward', 'oncoming lane unsupported')
        value = lane.get('width_m'); scalar = value.get('value') if isinstance(value,dict) else value
        if scalar is None:
            scalar = policy['driving_lane_width_m']; source = 'driving_lane_width_policy_default'
        else: source = 'world.road.lanes.'+lid+'.width_m'
        require(type(scalar) in (int,float) and math.isfinite(scalar) and scalar>0, 'invalid lane width')
        widths[lid] = scalar; width_sources[lid] = {'width_m':scalar,'source':source}
    centres = {}; edge = 0
    for lid in lane_ids:
        centres[lid] = edge + widths[lid]/2; edge += widths[lid]
    actors = [a for a in all_actors if a.get('simulation_role') == 'participant']
    by_id = {a['actor_id']:a for a in actors}
    require(all(a.get('simulation_role')=='participant' and not a['is_static'] and
                (a['actor_type'] in {'car','van','vehicle','truck','suv','sedan'} or
                 (a['actor_id']=='ego' and a['actor_type']=='unknown')) for a in actors), 'only moving participant vehicles supported')
    order = facts.get('lead_order_near_to_far', [])
    require(len(order)==len(set(order)) and set(order)==set(by_id)-{'ego'}, 'complete confirmed lead order required')
    submitted_relations = {a['actor_id']:a for a in facts.get('actors',[])}
    require(set(submitted_relations)=={a['actor_id'] for a in all_actors}, 'relation actor set mismatch')
    for actor in all_actors:
        if actor.get('simulation_role') == 'background':
            require(divided_scope is not None and
                    submitted_relations[actor['actor_id']]['direction'] == 'opposite',
                    'background exclusion requires reviewed separated opposing traffic')
    relations = {aid:submitted_relations[aid] for aid in by_id}
    start,end = facts['time_range_s']; require(start==0 and end>0, 'window must start at frozen origin')
    config = policy['straight_preview']; defaults=[]; conditions={}; envelopes={}
    def number(value):
        if isinstance(value,dict):
            if value.get('status') in {'unknown','placeholder'}: return None
            value=value.get('value')
        return value if type(value) in (int,float) and math.isfinite(value) else None
    def default(aid, field, value, method='policy_default'):
        defaults.append({'field':f'simulation_scenarios[0].initial_conditions.{aid}.{field}',
            'observed_value':None,'effective_value':value,'value_origin':'policy_default',
            'policy_id':'straight_preview_policy_default','policy_version':config['version'],
            'method':method,'reason':'Explicitly authorized relation-preserving preview; not measured.',
            'review_sha256':preview_digest(review),'lane_width_sources':copy.deepcopy(width_sources)})
        return value
    ego = by_id['ego']; ego_lane = ego['trajectory'][0].get('lane_id')
    require(ego_lane in centres, 'unknown ego lane')
    ego_offset=0.0 if config['ego_at_lane_center'] else number(ego['trajectory'][0].get('lane_center_offset_m'))
    require(ego_offset is not None, 'ego-to-lane-centre offset unavailable')
    anchor = centres[ego_lane]+ego_offset
    for aid in ['ego', *order]:
        a=by_id[aid]; points=a['trajectory']; rel=relations[aid]; first=points[0]
        require([p.get('time_s',p.get('timestamp_s')) for p in points]==facts['sample_times_s'], 'actor time coverage mismatch')
        changing = lane_change is not None and aid == lane_change.actor_id
        if changing:
            require(rel['lane_id'] == lane_change.source_lane_id,
                    'changing Actor relation must name its initial source lane')
            require(rel['direction']=='same_direction' and rel['lane_motion']=='changing',
                    'lane-change relation is inconsistent')
        else:
            require(all(p.get('lane_id')==rel['lane_id'] and p.get('lane_id') in centres for p in points), 'lane conflict or unknown')
            require(rel['direction']=='same_direction' and rel['lane_motion']=='keeping', 'unsupported direction/lane motion')
        require(aid=='ego' or (rel['relative_position']=='ahead' and rel['relative_motion'] in {'steady','receding','approaching'}), 'confirmed steady, receding or approaching lead actors required')
        pos=first.get('position_ego_m',{}); x=number(pos.get('x')); y=number(pos.get('y'))
        v=number(first.get('speed_mps')); speed_origin='observed'
        if v is not None:
            speed_origin=first['speed_mps'].get('status','observed') if isinstance(first['speed_mps'],dict) else 'observed'
            v*=3.6
        else:
            if aid == 'ego':
                assumed_speed = config['ego_speed_kmh']
            else:
                assumed_speed = conditions['ego']['speed_kmh']['value']
                if rel['relative_motion'] == 'receding':
                    assumed_speed += config['receding_delta_kmh']
                elif rel['relative_motion'] == 'approaching':
                    # Reuse the configured relative-speed magnitude in the closing direction.
                    assumed_speed -= config['receding_delta_kmh']
            v=default(aid,'speed_kmh',assumed_speed)
            speed_origin='policy_default'
        require(v>0, 'moving participant has nonpositive speed')
        if aid=='ego':
            require(x in (None,0) and y in (None,0), 'ego origin conflicts with frozen frame')
            x=y=0.0
            if config['ego_at_lane_center']: default(aid,'lane_center_placement',True,'ego_at_lane_center')
        else:
            if x is None: x=default(aid,'position_ego_m.x',config['lead_distance_m']+order.index(aid)*config['distance_step_m'])
            if y is None:
                require(config['actors_at_lane_center'], 'actor lateral position unknown')
                y=default(aid,'position_ego_m.y',centres[rel['lane_id']]-anchor,'derived_from_lane_center')
        offset=y-(centres[rel['lane_id']]-anchor)
        require(abs(offset)<widths[rel['lane_id']]/2, 'position outside assigned lane')
        conditions[aid]={'lane_id':rel['lane_id'],'position_ego_m':{'x':x,'y':y},
            'speed_kmh':{'value':v,'status':speed_origin},'lane_center_offset_m':offset}
        envelopes[aid]={'length_m':max(config['envelope_length_m'],number(a.get('length_m')) or 0),
                        'width_m':max(config['envelope_width_m'],number(a.get('width_m')) or 0)}
        # Every retained numeric observation must agree with the execution projection.
        for p in points:
            t=p.get('time_s',p.get('timestamp_s')); pp=p.get('position_ego_m',{})
            for key,expected in (('x',x+v/3.6*t),('y',y)):
                observed=number(pp.get(key))
                require(observed is None or abs(observed-expected)<=0.05, f'{aid} retained {key} conflicts with preview')
            observed=number(p.get('speed_mps'))
            require(observed is None or abs(observed*3.6-v)<=0.05, f'{aid} speed varies/conflicts')
    for aid in order:
        actor_speed = conditions[aid]['speed_kmh']['value']
        ego_speed = conditions['ego']['speed_kmh']['value']
        if relations[aid]['relative_motion'] == 'steady':
            require(abs(actor_speed-ego_speed) <= 1e-9, f'{aid} steady gap conflicts with retained speed')
        elif relations[aid]['relative_motion'] == 'approaching':
            require(actor_speed<ego_speed, f'{aid} approaching trend violated')
        else:
            require(actor_speed>ego_speed, f'{aid} receding trend violated')
    for near,far in zip(['ego',*order],order):
        a,b=conditions[near],conditions[far]
        for t in (0,end):
            require(b['position_ego_m']['x']+b['speed_kmh']['value']/3.6*t >
                    a['position_ego_m']['x']+a['speed_kmh']['value']/3.6*t, 'lead order violated')
    if lane_change is None:
        preview_collision_check(conditions,envelopes,end)
    else:
        lane_change_preview_collision_check(conditions, envelopes, end, lane_change, centres, anchor)
    length=number(geom.get('visible_length_m')) or policy['highway_length_m']
    require(all(c['position_ego_m']['x']+c['speed_kmh']['value']/3.6*end+envelopes[aid]['length_m']/2<=length
                for aid,c in conditions.items()), 'preview extends past supported road')
    triggers = ([] if lane_change is None else [{
        'type':'lane_change', 'actor_id':lane_change.actor_id,
        'event_id':lane_change.event_id, 'target_lane_id':lane_change.target_lane_id,
        'trigger_time_s':lane_change.start_s,
    }])
    return {'scenario_id':'scenario_001','description':'Relation-preserving simulation preview; metric values include authorized policy assumptions.',
            'involved_actor_ids':['ego',*order],'initial_conditions':conditions,
            'trigger_events':triggers, 'duration_s':end}, defaults, envelopes


def validate_preview_analysis(analysis):
    """Recompute the execution projection; a quality flag alone grants nothing."""
    from _lib.contracts.v3 import ContractError, fallback_policy, gate_degradations
    quality=analysis.get('quality_report',{}); contract=quality.get('straight_preview')
    if not contract: return
    policy=fallback_policy(contract['policy'])
    if not policy['straight_preview']['enabled']: raise ContractError('preview policy disabled')
    gate_degradations(quality)
    actors=copy.deepcopy([analysis['ego'],*analysis['actors']])
    for a in actors: a['actor_type']=a.get('source_actor_type',a['actor_type'])
    behavior={'actors':actors,'events':analysis['events'],'executor_primitives':[],
              'semantic_limitations':analysis.get('limitations',[])}
    scenario,defaults,envelopes=build_straight_preview(contract['source_world'],behavior,policy,contract['review'])
    if analysis['simulation_scenarios']!=[scenario] or contract['envelopes']!=envelopes:
        raise ContractError('preview execution projection was modified')
    recorded=[d for d in quality['applied_defaults'] if d['policy_id']=='straight_preview_policy_default']
    if recorded!=defaults: raise ContractError('preview default provenance differs')
    original=contract['source_world']['world']['road']
    if [r['lane_ids'] for r in analysis['road']['roads']]!=[r['lane_ids'] for r in original['roads']]:
        raise ContractError('preview target road lane order changed')
    source_lanes={l['lane_id']:l for l in original['lanes']}
    for lane in analysis['road']['lanes']:
        value=source_lanes[lane['lane_id']].get('width_m')
        value=value.get('value') if isinstance(value,dict) else value
        if lane['width_m']!=(value if value is not None else policy['driving_lane_width_m']):
            raise ContractError('preview lane width changed')
    return True


def evidence_frames(case, *, include_census=True):
    """Read one verified evidence union for prompts, adoption and offline checks.

    A frame ID denotes source time; packed image variants keep separate paths
    and hashes. Only committed Batches can extend the global evidence set.
    This function is read-only, including when called from offline validation.
    """
    from _lib.runtime.case_core import read_json, sha256
    from _lib.contracts.v3 import ContractError
    case = Path(case)
    global_path = case / 'state/global_frames.json'
    records = read_json(global_path).get('frames', []) if global_path.is_file() else []
    sources = [('global', row) for row in records]
    from _lib.runtime.identity_revision import supplemental_frames
    sources.extend(('identity_link', row) for row in supplemental_frames(case))
    from _lib.contracts.census_adjudication import proof as census_proof
    reviewed = census_proof(case) if include_census else None
    if reviewed:
        from _lib.contracts.census_history import rounds
        sources.extend(('census_adjudication', row) for item in rounds(reviewed)
                       for row in item['request']['frames'])
    manifest_path = case / 'manifest.json'
    if manifest_path.is_file():
        for spec in read_json(manifest_path).get('batches', []):
            if (case / 'batches' / f"{spec['batch_id']}.json").is_file():
                sources.extend((spec['batch_id'], row) for row in spec.get('frames', []))
    index = {}
    for owner, row in sources:
        frame_id, time_s = row.get('frame_id'), row.get('time_s')
        if not isinstance(frame_id, str) or type(time_s) not in (int, float) or not math.isfinite(time_s) or time_s < 0:
            raise ContractError('invalid evidence frame ID/time')
        if row.get('sha256'):
            path = Path(row.get('path', ''))
            if not path.is_file() or sha256(path) != row['sha256']:
                raise ContractError(f'evidence image hash mismatch: {frame_id}')
        if owner != 'global' and (not row.get('sha256') or not row.get('path')):
            raise ContractError(f'Batch evidence image binding missing: {frame_id}')
        current = index.setdefault(frame_id, {**copy.deepcopy(row), 'variants': []})
        if current['time_s'] != time_s:
            raise ContractError(f'evidence frame has conflicting source times: {frame_id}')
        variant = {key: row[key] for key in ('path', 'sha256', 'source_path') if key in row}
        variant['source'] = owner
        if variant not in current['variants']:
            current['variants'].append(variant)
    return sorted(index.values(), key=lambda row: (row['time_s'], row['frame_id']))


def write_evidence_index(case):
    """Materialize the derived index only in the online lifecycle."""
    from _lib.runtime.case_core import atomic_json, read_json
    path = Path(case) / 'state/behavior_frames.json'
    payload = {'frames': evidence_frames(case)}
    if not path.is_file() or read_json(path) != payload:
        atomic_json(path, payload)
    return path


def complete_estimated_horizon(source, duration_s):
    """Add audited execution boundary samples for estimated participants only.

    Original frame-bound samples are preserved. This is an execution forecast,
    never promoted to observed video evidence.
    """
    result=copy.deepcopy(source); repairs=[]; extended=[]
    if type(duration_s) not in (int,float) or not math.isfinite(duration_s) or duration_s <= 0:
        return result, repairs
    for actor in result.get('actors',[]):
        points=actor.get('trajectory') if isinstance(actor,dict) else None
        if (actor.get('simulation_role')!='participant' or not is_vehicle(actor)
                or actor.get('is_static') or not isinstance(points,list) or not points):
            continue
        rows=[]
        for point in points:
            speed=point.get('speed_mps') if isinstance(point,dict) else None
            t=point.get('time_s',point.get('timestamp_s')) if isinstance(point,dict) else None
            value=speed.get('value') if isinstance(speed,dict) else None
            position=point.get('position_ego_m') if isinstance(point,dict) else None
            if (not isinstance(speed,dict) or speed.get('status')!='estimated' or
                    type(t) not in (int,float) or type(value) not in (int,float) or value<0 or
                    not isinstance(position,dict) or type(position.get('x')) not in (int,float) or
                    type(position.get('y')) not in (int,float)):
                rows=[]; break
            rows.append((float(t),float(value),point))
        if not rows or rows[0][0] < 0 or rows[-1][0] > duration_s or any(
                b[0] <= a[0] for a,b in zip(rows,rows[1:])):
            continue
        actor_repairs=[]
        def boundary(point,time_s,x,side):
            speed=copy.deepcopy(point['speed_mps']); confidence=speed.get('confidence',0.0)
            position=copy.deepcopy(point['position_ego_m']); position.update({
                'x':x,'status':'estimated','confidence':confidence,
                'reason':f'Execution-only constant-speed {side} from nearest estimated source sample; not observed.'})
            return {'time_s':time_s,'lane_id':point.get('lane_id'),'position_ego_m':position,
                    'speed_mps':speed,'execution_extension':True,
                    'detail':f'Execution-only horizon {side}; no source frame claimed.'}
        if rows[0][0] > 0:
            t,v,point=rows[0]; derived=boundary(point,0.0,float(point['position_ego_m']['x'])-v*t,'backcast')
            points.insert(0,derived); actor_repairs.append({'side':'start','from_s':t,'to_s':0.0})
        if rows[-1][0] < duration_s:
            t,v,point=rows[-1]; derived=boundary(point,float(duration_s),
                float(point['position_ego_m']['x'])+v*(duration_s-t),'forecast')
            points.append(derived); actor_repairs.append({'side':'end','from_s':t,'to_s':float(duration_s)})
        if actor_repairs:
            extended.append(actor['actor_id'])
            repairs.append({'actor_id':actor['actor_id'],'action':'complete_estimated_execution_horizon',
                            'extensions':actor_repairs})
    if extended:
        limitations=result.setdefault('semantic_limitations',[])
        base='limitation_execution_horizon_extension'; ids={x.get('limitation_id') for x in limitations if isinstance(x,dict)}
        limitation_id=base; suffix=2
        while limitation_id in ids: limitation_id=f'{base}_{suffix}'; suffix+=1
        limitations.append({'limitation_id':limitation_id,
            'description':('Execution-only constant-speed boundary extension for estimated participant tracks: '
                           + ', '.join(extended) + '. Original frame-bound samples are unchanged.'),
            'impact_domains':['actor_motion'],'execution_effect':'bounded_uncertainty'})
    return result, repairs


def validate_compiled_behavior(data, world, registry, frames=None, *, execution=True,
                               allow_execution_extensions=False, allow_pending_identity=False):
    """One consumer contract shared by online adoption and offline replay."""
    from _lib.contracts.v3 import ContractError, validate_behavior
    validate_behavior(data, world["evidence_epoch"], world, execution=execution,
                      allow_pending_identity=allow_pending_identity)
    validate_behavior_evidence(data)
    crossing_ids = {item.get('event_id') for item in data['executor_primitives']
                    if item.get('type') in {'cross_roadway', 'intersection_entry', 'ego_intersection_entry', 'vehicle_intersection_entry'}}
    for event in data['events']:
        if event['event_type'] not in {'pedestrian_crossing', 'cross_roadway', 'intersection_entry',
                'ego_intersection_entry', 'vehicle_intersection_entry', 'ego_intersection_entry_and_crossing'} and event['event_id'] not in crossing_ids:
            continue
        from _lib.contracts.v3 import BehaviorSemanticBlocked
        index = {item['frame_id']: item for item in (frames or [])}
        if not index:
            raise BehaviorSemanticBlocked('crossing source frame index missing')
        for evidence in event['evidence']:
            if not isinstance(evidence, dict) or 'boundary_state' not in evidence:
                continue
            frame = index.get(evidence.get('frame_id'))
            if frame is None or frame.get('time_s') != evidence.get('time_s'):
                raise ContractError('crossing evidence frame/time differs from source index')
    if frames is not None:
        from _lib.contracts.frame_evidence import validate_behavior_frames
        validate_behavior_frames(data, frames, registry,
                                 allow_execution_extensions=allow_execution_extensions)
    actors = {item["actor_id"]: item for item in data["actors"]}
    if len({item['actor_id'] for item in registry}) != len(registry):
        raise ContractError('duplicate registry actor identity', rule_id='identity.conflict')
    if {item["actor_id"] for item in registry} - set(actors):
        raise ContractError("Behavior omitted registry actors")
    for source in registry:
        current = actors[source["actor_id"]]
        if (current.get("actor_type") != source.get("actor_type")
                or current.get("identity_description") != source.get("identity_description")):
            raise ContractError(f"Behavior changed immutable actor identity: {source['actor_id']}",
                                rule_id='identity.conflict', actor_id=source['actor_id'])
    road = world.get("world", {}).get("road", {})
    if road.get("roads") and road.get("lanes"):
        validate_lane_references(data, world)
        if not execution:
            return data
        from _lib.world.topology import validate_actor_routes
        try:
            validate_actor_routes(world['world'], data['actors'])
            if world['world'].get('scene_type') == 'intersection':
                from _lib.behavior.motion import check_samples
                for actor in data['actors']:
                    if actor.get('simulation_role') == 'participant' and is_vehicle(actor):
                        check_samples(world['world'], actor)
        except ValueError as error:
            failure = ContractError(str(error), rule_id=getattr(error,'rule_id',None),
                                    actor_id=getattr(error,'actor_id',None))
            if getattr(error, 'code', None):
                failure.code = error.code
            raise failure from error
    return data


def validate_lane_references(data, world):
    """Observation gaps cannot legalize references to nonexistent lanes."""
    from _lib.contracts.v3 import ContractError
    road = world.get('world', {}).get('road', {})
    if not road.get('roads') or not road.get('lanes'): return
    known = {item['lane_id'] for item in road['lanes']}
    used = {point['lane_id'] for actor in data['actors'] for point in actor['trajectory']
            if point.get('lane_id') is not None}
    used.update(item['lane_id'] for item in data['executor_primitives'] if item.get('lane_id') is not None)
    if used - known:
        raise ContractError(f"Behavior references unknown World lanes: {sorted(used - known)}")


def derive_initial_states(source, *, sync_actor_ids=()):
    result=copy.deepcopy(source)
    if not isinstance(result,dict) or not isinstance(result.get('actors'),list):
        return result
    primitives=result.get('executor_primitives')
    if not isinstance(primitives,list): return result
    existing={p.get('actor_id') for p in primitives if isinstance(p,dict) and p.get('type')=='initial_state'}
    ids={p.get('primitive_id') for p in primitives if isinstance(p,dict)}
    for actor in result['actors']:
        if not isinstance(actor,dict) or actor.get('simulation_role')!='participant': continue
        actor_id=actor.get('actor_id')
        if not isinstance(actor_id,str): continue
        trajectory=actor.get('trajectory')
        if not isinstance(trajectory,list) or not trajectory or not isinstance(trajectory[0],dict): continue
        point=trajectory[0]; position=point.get('position_ego_m'); speed=point.get('speed_mps')
        if (not isinstance(position, dict) or 'y' not in position) or (actor_id in sync_actor_ids and not point.get('lane_id')):
            # A derived executable initial state must not retain a disproved lane.
            primitives[:] = [p for p in primitives if not (p.get('actor_id') == actor_id and p.get('type') == 'initial_state')]
            continue
        value=speed.get('value') if isinstance(speed,dict) else speed
        if isinstance(speed,dict) and speed.get('status') in {'unknown','placeholder'}: continue
        if not isinstance(position,dict) or not point.get('lane_id'): continue
        numbers=(position.get('x'),position.get('y'),value)
        if not all(type(n) in (int,float) and math.isfinite(n) for n in numbers) or value<0: continue
        converted=copy.deepcopy(speed)
        if isinstance(converted,dict): converted['value']=value*3.6
        else: converted=value*3.6
        if actor_id in existing:
            if not point.get('execution_extension') and actor_id not in sync_actor_ids: continue
            primitive=next(p for p in primitives if isinstance(p,dict) and p.get('type')=='initial_state'
                           and p.get('actor_id')==actor_id)
            if actor_id in sync_actor_ids and not point.get('execution_extension'):
                # Lane review owns lateral fields only; never rewrite speed/x/time.
                primitive['lane_id'] = point['lane_id']
                primitive.setdefault('position_ego_m', {})['y'] = position['y']
                for key in ('status', 'confidence', 'reason'):
                    if key in position:
                        primitive['position_ego_m'][key] = copy.deepcopy(position[key])
            else:
                primitive.update({'lane_id':point['lane_id'],'position_ego_m':copy.deepcopy(position),
                                  'speed_kmh':converted})
            continue
        primitive_id='initial_'+actor_id
        while primitive_id in ids: primitive_id+='_' 
        primitives.append({'primitive_id':primitive_id,'actor_id':actor_id,'type':'initial_state',
                           'lane_id':point['lane_id'],'position_ego_m':copy.deepcopy(position),'speed_kmh':converted})
        ids.add(primitive_id); existing.add(actor_id)
    return result


def lane_position_report(data, world):
    """Conservative ordering check, not lane localization or visual validation.

    Only explicit straight, forward-aligned single roads are supported. Ego's
    actual lateral position is the reference, never an assumed lane centre.
    """
    rows, issues = [], []
    scene = world.get('world', {})
    road = scene.get('road', {})
    roads = road.get('roads', [])
    ego = next((a for a in data.get('actors', []) if a.get('actor_id') == 'ego'), {})
    ego_points = {p.get('time_s', p.get('timestamp_s')): p for p in ego.get('trajectory', [])}
    reason = None
    if scene.get('scene_type') != 'highway' or scene.get('junctions') or len(roads) != 1:
        reason = 'requires_single_straight_road'
    entry = roads[0] if len(roads) == 1 else {}
    geometry = entry.get('road_geometry') or {}
    curvature = geometry.get('curvature', {})
    curvature = curvature.get('value') if isinstance(curvature, dict) else curvature
    if (geometry.get('shape_type', entry.get('shape_type')) != 'straight'
            or curvature not in (None, 0, 'straight')
            or any(geometry.get(k) is not None for k in ('radius_m', 'road_radius'))):
        reason = reason or 'straight_geometry_not_explicit'
    heading = entry.get('heading_rad')
    if type(heading) not in (float, int) or not math.isfinite(heading) or abs(heading) > 1e-9:
        reason = reason or 'road_not_aligned_with_frozen_x'
    lane_ids = entry.get('lane_ids', [])
    lanes = {l.get('lane_id'): l for l in road.get('lanes', [])}
    # v3 lane numbering is right-to-left within each road; do not infer it
    # from a prose description, role='ego lane', or lane width defaults.
    if (not lane_ids or any(not isinstance(k, str) or not k.startswith('lane_') or
            not k[5:].isdigit() for k in lane_ids) or
            lane_ids != sorted(set(lane_ids), key=lambda k: int(k[5:])) or
            any(k not in lanes or lanes[k].get('road_id') != entry.get('road_id') or
                lanes[k].get('travel_direction', 'forward') != 'forward' for k in lane_ids)):
        reason = reason or 'lane_order_or_direction_unknown'
    if road.get('one_way') is not True and any(lanes.get(k, {}).get('travel_direction') != 'forward' for k in lane_ids):
        reason = reason or 'lane_direction_not_explicit'
    for ai, actor in enumerate(data.get('actors', [])):
        if actor.get('actor_id') == 'ego' or not is_vehicle(actor):
            continue
        changing = len({p.get('lane_id') for p in actor.get('trajectory', [])}) > 1 or len(
            {p.get('lane_id') for p in ego.get('trajectory', [])}) > 1
        for pi, point in enumerate(actor.get('trajectory', [])):
            t = point.get('time_s', point.get('timestamp_s'))
            row = {'actor_id': actor['actor_id'], 'time_s': t,
                   'status': 'not_checked', 'reason': reason}
            rows.append(row)
            if reason: continue
            if actor.get('trajectory_coordinate_system') == 'road_local' or ego.get('trajectory_coordinate_system') == 'road_local':
                row['reason'] = 'road_local_coordinates_not_supported'; continue
            other = ego_points.get(t)
            if not other or changing or point.get('execution_extension') or other.get('execution_extension'):
                row['reason'] = 'no_matching_observation_or_lane_change'; continue
            lane, ego_lane = point.get('lane_id'), other.get('lane_id')
            y, ey = point.get('position_ego_m', {}).get('y'), other.get('position_ego_m', {}).get('y')
            if lane not in lane_ids or ego_lane not in lane_ids or not all(
                    type(v) in (int, float) and math.isfinite(v) for v in (y, ey)):
                row['reason'] = 'lane_or_lateral_position_unknown'; continue
            if lane == ego_lane:
                row['reason'] = 'same_lane_has_no_centre_constraint'; continue
            delta = y - ey
            order = lane_ids.index(lane) - lane_ids.index(ego_lane)
            if abs(delta) <= 1e-6:
                row['reason'] = 'lateral_order_indeterminate'; continue
            row.update(status='conflict' if delta * order < 0 else 'consistent', reason='lateral_order_only')
            if row['status'] == 'conflict':
                issues.append({'rule_id': 'motion.lane_position_conflict',
                    'actor_id': actor['actor_id'], 'time_s': t,
                    'paths': [f'/actors/{ai}/trajectory/{pi}/lane_id',
                              f'/actors/{ai}/trajectory/{pi}/position_ego_m/y'],
                    'message': f"actor {actor['actor_id']} at {t}: lane order contradicts lateral position relative to ego; observations retained"})
    return {'scope': 'straight_aligned_road_lateral_order_only', 'samples': rows, 'issues': issues}


def lateral_position_issues(data):
    """A missing lateral observation is not an executable position."""
    issues = []
    for ai, actor in enumerate(data.get('actors', [])):
        # Existing collective observations are report-only, not individual positions.
        if actor.get('actor_type') in {'parked_vehicles', 'vehicle_group', 'pedestrian_group'}:
            continue
        for pi, point in enumerate(actor.get('trajectory', [])):
            if 'y' not in point.get('position_ego_m', {}):
                t = point.get('time_s', point.get('timestamp_s'))
                issues.append({'rule_id':'execution.lateral_position_unknown',
                    'actor_id':actor['actor_id'], 'time_s':t,
                    'paths':[f'/actors/{ai}/trajectory/{pi}/position_ego_m/y'],
                    'message':f"actor {actor['actor_id']} at {t}: lateral position unavailable; observation retained, execution blocked"})
    return issues


def execution_issues(data, world, registry, frames=None, *, allow_execution_extensions=False):
    """Observation acceptance is hard; execution gaps are reported separately."""
    from _lib.contracts.v3 import ContractError
    validate_compiled_behavior(data, world, registry, frames, execution=False,
                               allow_execution_extensions=allow_execution_extensions)
    issues = []
    try:
        validate_compiled_behavior(data, world, registry, frames,
                                   allow_execution_extensions=allow_execution_extensions)
    except ContractError as error:
        issues.append({'rule_id':error.rule_id or 'execution.contract',
                       'actor_id':error.actor_id, 'message':str(error)})
    for actor in data['actors']:
        points = actor['trajectory']
        if any(point.get('lane_id') is None for point in points):
            issues.append({'rule_id':'execution.lane_unknown', 'actor_id':actor['actor_id'],
                           'message':f"actor {actor['actor_id']}: lane mapping unknown; observation retained"})
        # A constant-speed straight observation has an unambiguous numeric
        # consistency check even when its direction is negative. Do not infer
        # an acceleration profile or repair the coordinates.
        for first, last in zip(points, points[1:]):
            def scalar(value):
                return value.get('value') if isinstance(value,dict) else value
            a, b = first.get('position_ego_m',{}), last.get('position_ego_m',{})
            values = [first.get('time_s'),last.get('time_s'),a.get('x'),b.get('x'),
                      a.get('y'),b.get('y'),scalar(first.get('speed_mps')),scalar(last.get('speed_mps'))]
            if not all(type(x) in (int,float) and math.isfinite(x) for x in values):
                continue
            t0,t1,x0,x1,y0,y1,v0,v1 = values
            if t1>t0 and v0==v1 and y0==y1:
                distance = v0*(t1-t0)
                if abs(abs(x1-x0)-distance)>max(0.1,distance*0.02):
                    issues.append({'rule_id':'motion.speed_time_position','actor_id':actor['actor_id'],
                        'message':f"actor {actor['actor_id']}: constant-speed displacement conflict at {t0}..{t1}; source samples unchanged"})
                    break
    issues.extend(lateral_position_issues(data))
    issues.extend(lane_position_report(data, world)['issues'])
    return issues


def prepare_behavior_candidate(source, frames, registry=(), world=None, *, case=None):
    """Shared observation preparation for online, candidate and offline checks.

    Preserve submitted positions, speeds and sample coverage. Execution-only
    horizon completion belongs to prepare_behavior_execution, never admission.
    """
    from _lib.contracts.parking import normalize_unconfirmed_area_references
    from _lib.contracts.frame_evidence import validate_behavior_frames
    from _lib.behavior.lane_change import normalize_event_intervals
    from _lib.behavior.semantic import materialize
    from _lib.world.semantic import enabled
    # Repair only representation that has one lossless interpretation. A missing
    # challenge list is empty only when the frozen World requires no decisions.
    before = source
    changed = []
    if isinstance(source, dict):
        ambiguities = world.get('ambiguities') if isinstance(world, dict) else None
        no_required_challenges = (isinstance(ambiguities, list)
            and all(isinstance(item, dict) for item in ambiguities) and not any(
            item.get('owner') == 'behavior' and item.get('resolution') == 'unresolved'
            for item in ambiguities))
        actors = source.get('actors')
        wrapped = [index
                   for index, actor in enumerate(actors) if isinstance(actor, dict)
                   and isinstance(actor.get('trajectory'), dict)
                   and set(actor['trajectory']) == {'samples'}
                   and isinstance(actor['trajectory']['samples'], list)] if isinstance(actors, list) else []
        if ('challenge_resolutions' not in source and no_required_challenges) or wrapped:
            source = copy.deepcopy(source)
            if 'challenge_resolutions' not in source and no_required_challenges:
                source['challenge_resolutions'] = []
                changed.append('/challenge_resolutions')
            for index in wrapped:
                source['actors'][index]['trajectory'] = source['actors'][index]['trajectory']['samples']
                changed.append(f'/actors/{index}/trajectory')
    shape_repairs = [dict(
        rule_id='behavior.lossless_shape.v1', action='restore_lossless_behavior_shape',
        basis='behavior.schema.json and frozen World challenge ownership; exact samples wrapper only',
        hash_encoding='sha256(canonical JSON: sorted keys, UTF-8, compact separators)',
        before_sha256=preview_digest(before), after_sha256=preview_digest(source),
        changed_fields=changed)] if changed else []
    source, expression_repairs = normalize_empty_optional_census_refs(source)
    source, lane_note_repairs = normalize_unresolved_lane_closure_notes(source, world)
    source_reviews = []
    if case:
        from _lib.runtime.question_review import apply_source_observation_reviews
        source, source_reviews = apply_source_observation_reviews(case, source)
    source, semantic_repairs = materialize(source, frames, world,
                                          active=bool(case and enabled(case)))
    semantic_repairs = shape_repairs + expression_repairs + lane_note_repairs + source_reviews + semantic_repairs
    if case:
        from _lib.contracts.bounded_execution import prepare
        source, inherited_repairs = prepare(source, case, frames)
        semantic_repairs.extend(inherited_repairs)
    source, color_repairs = normalize_optional_color_claims(source)
    source, evidence_repairs = restore_registry_actor_evidence(source, registry)
    validate_behavior_frames(source, frames, registry)
    data = source
    repairs = []
    if isinstance(world, dict):
        data, parking_repairs = normalize_unconfirmed_area_references(
            data, world.get('world', {}).get('road', {}).get('roadside_areas', []))
        repairs.extend(parking_repairs)
    data, interval_repairs = normalize_event_intervals(data)
    return derive_initial_states(data), semantic_repairs + color_repairs + evidence_repairs + repairs + interval_repairs


def prepare_behavior_execution(source, frames, registry, world, *, case=None):
    """Build a private strict-check copy only after accepting the observations.

    No speed integration rewrites submitted coordinates to hide a conflict.
    Generated boundary points carry execution_extension and no observed frame.
    This helper neither commits a Case nor authorizes target-generation defaults.
    """
    from _lib.contracts.v3 import ContractError
    data, repairs = prepare_behavior_candidate(source, frames, registry, world, case=case)
    findings = execution_issues(data, world, registry, frames)
    for issue in findings:
        if issue['rule_id'] in {'motion.speed_time_position', 'motion.lane_position_conflict'}:
            raise ContractError(issue['message'], rule_id=issue['rule_id'], actor_id=issue['actor_id'])
    duration = max((item['time_s'] for item in frames), default=0)
    data, horizon_repairs = complete_estimated_horizon(data, duration)
    return derive_initial_states(data), repairs + horizon_repairs
