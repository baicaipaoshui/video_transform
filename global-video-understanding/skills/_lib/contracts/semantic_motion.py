"""Text-grounded execution approximations; never observation-field corrections.

The conversion_semantic_gap artifact carries this revision. Its validators
also serve the optional versioned two-session conversion workflow.
"""
import copy
import math

from _lib.contracts.conversion_policy import digest
from _lib.contracts.actors import is_vehicle, vehicle_class_hint

REVISION = 'conversion-semantic-motion.v1'
POLICY = {'max_speed_kmh': 160.0, 'max_acceleration_mps2': 4.0,
          'max_initial_offset_m': 500.0, 'alongside_tolerance_m': 2.0,
          'clearance_m': 1.0, 'closing_progress_m': 1.0,
          'lengths_m': {'truck': 12.0, 'bus': 12.0, 'tractor': 12.0,
                        'van': 6.0, 'suv': 5.0, 'sedan': 5.0, 'default': 5.0}}
RELATIONS = {'ahead_of', 'alongside', 'approaching', 'overtakes'}


def _implicit_ego_lane_ids(road):
    """Recognize the legacy exact same-direction lane representation.

    Some accepted World answers predate per-lane ``travel_direction`` but
    still bind one road's complete modeled lane list to
    ``ego_direction_lanes``.  This is a representation adapter only: an
    explicit direction always wins and incomplete/multi-road topology gains
    no inference.
    """
    lanes = road.get('lanes', [])
    roads = road.get('roads', [])
    if (not isinstance(lanes, list) or not isinstance(roads, list)
            or len(roads) != 1 or not isinstance(roads[0], dict)):
        return set()
    lane_ids = roads[0].get('lane_ids')
    known = [lane.get('lane_id') for lane in lanes if isinstance(lane, dict)]
    if (not isinstance(lane_ids, list) or not lane_ids
            or any(not isinstance(lane_id, str) for lane_id in lane_ids)
            or len(lane_ids) != len(set(lane_ids))
            or len(known) != len(lanes) or set(lane_ids) != set(known)
            or road.get('ego_direction_lanes') != len(lane_ids)):
        return set()
    return set(lane_ids)


def number(value):
    if isinstance(value, dict):
        value = None if value.get('status') == 'unknown' else value.get('value')
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def time(point):
    return number(point.get('time_s', point.get('timestamp_s')))


def observation_only_background_ids(analysis):
    """Single-frame, unreferenced v4 background vehicles need no motion plan."""
    scope = (analysis.get('quality_report') or {}).get('execution_actor_scope') or {}
    if scope.get('policy_id') != 'ego_vehicle_scene.v4':
        return set()
    dependencies = [*analysis.get('events', []), *analysis.get('executor_primitives', []),
                    *analysis.get('simulation_scenarios', [])]
    limitations = analysis.get('limitations', [])
    result = set()
    for actor in analysis.get('actors', []):
        aid = actor.get('actor_id')
        points = actor.get('trajectory') or []
        if (not isinstance(aid, str) or not is_vehicle(actor)
                or actor.get('simulation_role') != 'background'
                or actor.get('is_static') is not False or len(points) != 1
                or time(points[0]) != 0 or points[0].get('lane_id') is not None
                or any(aid in str(dependency) for dependency in dependencies)):
            continue
        if any(item.get('actor_ids') == [aid]
               and item.get('impact_domains') == ['actor_motion']
               and item.get('execution_effect') == 'bounded_uncertainty'
               for item in limitations if isinstance(item, dict)):
            result.add(aid)
    return result


def eligible(analysis):
    if (analysis.get('scene_type') != 'highway' or analysis.get('junctions')
            or analysis.get('quality_report', {}).get('straight_preview')):
        return []
    road = analysis.get('road', {})
    lanes = {p['lane_id']: p for p in road.get('lanes', [])}
    implicit_ego_lanes = _implicit_ego_lane_ids(road)
    singles = analysis.get('metadata', {}).get('bounded_execution', {}).get('single_frame_motion', {})
    result = []
    for actor in [analysis['ego'], *analysis.get('actors', [])]:
        aid = actor['actor_id']
        points = actor.get('trajectory') or []
        if (not is_vehicle(actor)
                or actor.get('is_static') is not False
                or actor.get('provenance', {}).get('source') == 'user_requested_scenery' or not points):
            continue
        actor = copy.deepcopy(actor)
        single = singles.get(aid)
        if single and single['lane_assumed']:
            for point in actor['trajectory']:
                if point.get('lane_id') is None:
                    point['lane_id'] = single['selected_lane_id']
            points = actor['trajectory']
        directions = {(lanes[p['lane_id']].get('travel_direction')
                       if lanes[p['lane_id']].get('travel_direction') not in (None, 'unknown')
                       else 'same_as_ego' if p['lane_id'] in implicit_ego_lanes else None)
                      for p in points if p.get('lane_id') in lanes}
        if aid != 'ego' and directions != {'same_as_ego'}:
            continue
        result.append(copy.deepcopy(actor))
    return result if len(result) > 1 else []


def early_association_bounds(analysis):
    quality = analysis.get('quality_report', {})
    scope = quality.get('execution_actor_scope') or quality.get('offline_execution_actor_scope') or {}
    proof = (scope.get('execution_dispositions') or {}).get('early_association_proof')
    return {a['actor_id']: a['observed_from_s'] for g in (proof or {}).get('groups', []) for a in g['actors']}


def evidence(analysis, actors):
    """Bound text locations, including uncertainty; no image paths or pixels."""
    from _lib.behavior.source_revision import KEY, QUALITATIVE, REVISION as SOURCE_REVISION, SUMMARY
    rows = []
    bounds = early_association_bounds(analysis)
    qualitative = {}
    for limit in analysis.get('limitations', []):
        record = limit.get(QUALITATIVE)
        if record:
            if (record.get('revision') != SOURCE_REVISION or record.get('representation') != 'qualitative_only'
                    or record.get('completion') != 'unconfirmed'):
                raise ValueError('Invalid qualitative source revision')
            qualitative[(record['actor_id'], record['frame_id'])] = record
    consumed = set()
    def add(path, value, ids, when=None):
        if isinstance(value, str) and value.strip():
            rows.append({'source': path, 'text': value, 'actor_ids': ids, 'time_s': when})
            if when is None and set(ids) & set(bounds):
                rows[-1]['usage'] = 'audit_only'
        elif isinstance(value, dict):
            for key, child in value.items():
                add(path + '/' + key, child, value.get('actor_ids', ids), when)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                add(path + '/' + str(index), child, ids, when)
    for actor in actors:
        aid = actor['actor_id']
        add('/actors/' + aid + '/identity_description', actor.get('identity_description'), [aid])
        if (aid == 'ego' and actor.get('identity_description') == SUMMARY
                and any(KEY in limit for limit in analysis.get('limitations', []))):
            rows[-1]['usage'] = 'audit_only'
        for index, point in enumerate(actor.get('trajectory', [])):
            detail_path = f'/actors/{aid}/trajectory/{index}/detail'
            add(detail_path, point.get('detail'), [aid], time(point))
            record = qualitative.get((aid, point.get('frame_id')))
            if record:
                if (time(point) != record['time_s'] or digest(point.get('detail', '')) != record['behavior_detail_sha256']
                        or record['source_observation_id'] not in point.get('source_observation_ids', [])):
                    raise ValueError('Qualitative source lost its unchanged Actor/frame witness')
                if rows and rows[-1]['source'] == detail_path:
                    rows[-1]['usage'] = 'qualitative_only'
                consumed.add((aid, point['frame_id']))
    if consumed != set(qualitative):
        raise ValueError('Qualitative interaction Actor or source point was omitted')
    for index, event in enumerate(analysis.get('events', [])):
        add(f'/events/{index}/description', event.get('description', event.get('detail')),
            event.get('involved_actor_ids', []))
    for index, item in enumerate(analysis.get('limitations', [])):
        if KEY in item or QUALITATIVE in item:
            add(f'/limitations/{index}/description', item['description'], item['actor_ids'])
            rows[-1]['usage'] = 'qualitative_only' if QUALITATIVE in item else 'audit_only'
            for j, source in enumerate(item.get(QUALITATIVE, {}).get('sources', [])):
                add(f'/limitations/{index}/{QUALITATIVE}/sources/{j}/text', source['text'],
                    item['actor_ids'], item[QUALITATIVE]['time_s'])
                rows[-1]['usage'] = 'qualitative_only'
            continue
        # In an unverified description projection these generated execution
        # gaps remain in Analysis/request.limitations for the Converter. They
        # are not observations of an Actor relation for this text stage.
        nonrelation_gap = (analysis.get('metadata', {}).get('description_fallback')
            and isinstance(item, dict)
            and str(item.get('limitation_id', '')).startswith('execution_gap_')
            and item.get('impact_domains') == ['executor_primitive']
            and not any(item.get(key) for key in ('actor_ids', 'actor_id', 'affected_actor_ids',
                'affected_event_ids', 'census_issue_ids', 'world_paths', 'related_world_paths'))
            and not any(aid in item.get('description', '') for aid in
                        ('ego', *(actor['actor_id'] for actor in actors))))
        if not nonrelation_gap:
            add('/limitations/' + str(index), item, [])
    add('/quality_report/limitations', analysis.get('quality_report', {}).get('limitations', []), [])
    return rows


def request(analysis, analysis_sha256, *, witness_revision=None):
    quality = analysis.get('quality_report', {})
    scope = quality.get('execution_actor_scope') or quality.get('offline_execution_actor_scope') or {}
    if any((scope.get('execution_dispositions') or {}).get(k)
           for k in ('unused_association', 'execution_assumptions')):
        from _lib.contracts.census_scope import validate_analysis
        validate_analysis(analysis)
    bounds = early_association_bounds(analysis)
    if bounds:
        from _lib.contracts.census_scope import validate_analysis
        if validate_analysis(analysis) is None:
            raise ValueError('Early association motion bounds require a verified scope binding')
        quality = analysis['quality_report']
        scope = quality.get('execution_actor_scope') or quality['offline_execution_actor_scope']
        proof = scope['execution_dispositions']['early_association_proof']
        actors = {a['actor_id']: a for a in analysis.get('actors', [])}
        for group in proof['groups']:
            for actor in group['actors']:
                if digest(actors.get(actor['actor_id'], {}).get('trajectory')) != actor['trajectory_sha256']:
                    raise ValueError('Early association cannot replace an accepted Actor trajectory')
    actors = eligible(analysis)
    unsupported = {'turn', 'cut_in', 'collision', 'loss_of_control', 'evasive_steering'}
    if actors and any(event.get('event_type') in unsupported for event in analysis.get('events', [])):
        raise ValueError('semantic motion cannot replace an unsupported observed maneuver')
    actor_ids = {p['actor_id'] for p in actors}
    blockers = []
    for index, item in enumerate(analysis.get('limitations', [])):
        if not isinstance(item, dict) or 'actor_identity' not in item.get('impact_domains', []):
            continue
        bound = set(item.get('actor_ids', [])) | ({item['actor_id']} if item.get('actor_id') else set())
        if actor_ids and (not bound or actor_ids & bound):
            blockers.append({'rule_id': 'identity.temporal_binding', 'owner': 'behavior',
                             'actor_ids': sorted(bound), 'field_path': f'/limitations/{index}',
                             'message': item.get('description', 'Unresolved temporal identity')})
    if blockers:
        from _lib.world.gaps import IdentityEvidenceGap
        raise IdentityEvidenceGap(blockers)
    from _lib.behavior.lane_change import from_analysis
    spec = from_analysis(analysis) if actors else None
    duration = number(analysis.get('metadata', {}).get('duration_s'))
    if actors and (duration is None or duration <= 0):
        raise ValueError('semantic motion requires the source duration')
    body = {'revision': REVISION, 'analysis_sha256': analysis_sha256,
            'duration_s': duration, 'eligible': actors,
            'evidence': evidence(analysis, actors), 'policy': copy.deepcopy(POLICY),
            'events': copy.deepcopy(analysis.get('events', [])),
            'limitations': copy.deepcopy(analysis.get('limitations', [])),
            'initial_conditions': copy.deepcopy((analysis.get('simulation_scenarios') or [{}])[0]
                                               .get('initial_conditions', {})),
            'constant_speed_actor_ids': [spec.actor_id] if spec else []}
    if bounds:
        body['early_association_bounds'] = bounds
    from _lib.contracts import retained_motion
    body.update(retained_motion.request_fields(analysis, body['evidence']))
    from _lib.contracts import bounded_execution
    body.update(bounded_execution.request_fields(analysis, body))
    if witness_revision is not None:
        from . import motion_witnesses as witnesses
        if witness_revision != witnesses.REVISION:
            raise ValueError('Unsupported or unbound motion witness policy')
        body['policy'][witnesses.POLICY_KEY] = witness_revision
        if bounded_execution.KEY in body:
            body[witnesses.KEY] = witnesses.build(analysis, body)
        elif analysis.get('metadata', {}).get('description_fallback'):
            body[witnesses.KEY] = witnesses.description_body(analysis, body)
            if not body[witnesses.KEY]['constraints']:
                raise ValueError('Description body witness requires two bound body-part observations')
        else:
            raise ValueError('Unsupported or unbound motion witness policy')
    return {**body, 'request_sha256': digest(body)}


def segments(plan):
    x = plan['initial_x_m']
    result = []
    for left, right in zip(plan['speed_knots'], plan['speed_knots'][1:]):
        start, end = left['time_s'], right['time_s']
        v, w = left['speed_kmh'] / 3.6, right['speed_kmh'] / 3.6
        acceleration = (w-v)/(end-start)
        result.append((start, end, x, v, acceleration))
        x += (v+w)*(end-start)/2
    return result


def state(profile, t):
    for start, end, x, v, a in profile:
        if start-1e-9 <= t <= end+1e-9:
            dt = t-start
            return x+v*dt+a*dt*dt/2, v+a*dt
    raise ValueError('semantic motion time outside plan')


def critical_times(first, second, left, right):
    times = sorted({left, right, *(t for s in first+second for t in s[:2] if left < t < right)})
    result = set(times)
    for start, end in zip(times, times[1:]):
        dv0 = state(first, start)[1]-state(second, start)[1]
        dv1 = state(first, end)[1]-state(second, end)[1]
        if dv0*dv1 < 0:
            result.add(start + (end-start)*(-dv0)/(dv1-dv0))
    return sorted(result)


def length(actor):
    return POLICY['lengths_m'].get(vehicle_class_hint(actor), POLICY['lengths_m']['default'])


def check_lane_clearance(request_value, profiles):
    """Continuous envelope for fixed-lane pairs; changing actors use lane_change audit."""
    fixed = []
    for actor in request_value['eligible']:
        lanes = {p.get('lane_id') for p in actor['trajectory'] if p.get('lane_id')}
        if len(lanes) == 1:
            fixed.append((actor, next(iter(lanes))))
    for index, (actor, lane) in enumerate(fixed):
        for other, other_lane in fixed[index+1:]:
            if lane != other_lane:
                continue
            first, second = profiles[actor['actor_id']], profiles[other['actor_id']]
            times = critical_times(first, second, 0, request_value['duration_s'])
            gaps = [state(first, t)[0]-state(second, t)[0] for t in times]
            half = (length(actor)+length(other))/2
            if min(gaps) <= half and max(gaps) >= -half:
                raise ValueError('semantic motion same-lane collision: ' + actor['actor_id'] + '/' + other['actor_id'])


def check_relations(request_value, relations, profiles):
    actors = {p['actor_id']: p for p in request_value['eligible']}
    rows = []
    for row in relations:
        aid, other = row['actor_id'], row['reference_actor_id']
        left, right = row['time_interval_s']
        first, second = profiles[aid], profiles[other]
        times = critical_times(first, second, left, right)
        gaps = [state(first, t)[0]-state(second, t)[0] for t in times]
        half = (length(actors[aid])+length(actors[other]))/2
        kind = row['type']
        passed = (min(gaps) >= half+POLICY['clearance_m'] if kind == 'ahead_of' else
                  max(abs(gap) for gap in gaps) <= half+POLICY['alongside_tolerance_m'] if kind == 'alongside' else
                  gaps[0] <= half+POLICY['alongside_tolerance_m'] and gaps[-1] >= half+POLICY['clearance_m']
                  and gaps[-1]-gaps[0] >= POLICY['closing_progress_m'] if kind == 'overtakes' else
                  abs(gaps[0])-abs(gaps[-1]) >= POLICY['closing_progress_m']
                  and gaps[0]*gaps[-1] >= 0)
        if not passed:
            raise ValueError(f'semantic motion relation failed: {aid}/{other}/{kind}')
        rows.append({'actor_id': aid, 'reference_actor_id': other, 'type': kind,
                     'time_interval_s': [left, right], 'critical_times_s': times,
                     'relative_positions_m': gaps, 'passed': True})
    return rows


def _quotes(items, sources):
    if not isinstance(items, list) or not items:
        raise ValueError('semantic motion requires text citations')
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'source', 'quote'}
                or item.get('source') not in sources or not isinstance(item.get('quote'), str)
                or not item['quote'] or item['quote'] not in sources[item['source']]['text']):
            raise ValueError('semantic motion fabricated or unbound citation')


def validate(analysis, analysis_sha256, decision):
    from . import motion_witnesses as witnesses
    expected = request(analysis, analysis_sha256,
                       witness_revision=(decision.get('policy') or {}).get(witnesses.POLICY_KEY))
    fields = {'revision', 'request_sha256', 'analysis_sha256', 'candidate_sha256',
              'status', 'plans', 'relations', 'coverage', 'policy'}
    if (not isinstance(decision, dict) or set(decision) != fields
            or decision['revision'] != REVISION or decision['status'] != 'accepted'
            or decision['request_sha256'] != expected['request_sha256']
            or decision['analysis_sha256'] != analysis_sha256 or decision['policy'] != expected['policy']):
        raise ValueError('semantic motion frozen request/decision mismatch')
    signature = decision['candidate_sha256']
    if not isinstance(signature, str) or len(signature) != 64 or any(c not in '0123456789abcdef' for c in signature):
        raise ValueError('semantic motion candidate hash missing')
    profiles = validate_plans(expected, decision['plans'])
    from _lib.contracts import bounded_execution
    if bounded_execution.KEY in expected:
        coverage, relations = bounded_execution.complete_relations(expected, decision['coverage'], decision['relations'])
        # Other program completion has a stable final ordering below.
        from _lib.contracts import retained_motion
        coverage, relations = retained_motion.complete_relations(expected, coverage, relations)
        if coverage != decision['coverage'] or relations != decision['relations']:
            raise ValueError('Bounded execution source relations changed')
        bounded_execution.check_single_frame_bounds(expected, profiles)
        # The planner uses a conservative corridor filter while searching.
        # Final delivery keeps the existing continuous lateral collision audit;
        # that search heuristic must not reject an otherwise valid supplied plan.
    from _lib.contracts import retained_motion
    if expected.get(retained_motion.KEY):
        coverage, relations = retained_motion.complete_relations(expected, decision['coverage'], decision['relations'])
        if coverage != decision['coverage'] or relations != decision['relations']:
            raise ValueError('Retained unseen-motion program coverage changed')
        if retained_motion.complete_plans(expected, decision['plans'], relations) != decision['plans']:
            raise ValueError('Retained unseen-motion program plan changed')
    validate_relations(expected, decision['coverage'], decision['relations'],
                       observation_only_ids=observation_only_background_ids(analysis))
    check_relations(expected, decision['relations'], profiles)
    check_lane_clearance(expected, profiles)
    witnesses.check(expected, profiles)
    return decision


def validate_plans(expected, plans, *, partial=False):
    """Share known-value and numeric gates with durable partial drafts."""
    actors = {p['actor_id']: p for p in expected['eligible']}
    if (not isinstance(plans, list)
            or any(not isinstance(p, dict) for p in plans)
            or any(not isinstance(p.get('actor_id'), str) for p in plans)
            or len({p.get('actor_id') for p in plans}) != len(plans)
            or not {p.get('actor_id') for p in plans} <= set(actors)
            or not partial and {p.get('actor_id') for p in plans} != set(actors)):
        raise ValueError('semantic motion must cover every retained eligible actor exactly once')
    profiles = {}
    for plan in plans:
        if set(plan) != {'actor_id', 'initial_x_m', 'speed_knots', 'assumption'} or not isinstance(plan['assumption'], str) or not plan['assumption'].strip():
            raise ValueError('semantic motion plan must label execution assumptions')
        aid = plan['actor_id']
        x = number(plan['initial_x_m'])
        knots = plan['speed_knots']
        if x is None or abs(x) > POLICY['max_initial_offset_m'] or aid == 'ego' and x != 0:
            raise ValueError('semantic motion initial position outside policy')
        if not isinstance(knots, list) or not 2 <= len(knots) <= 32:
            raise ValueError('semantic motion speed knots invalid')
        previous = -1
        for knot in knots:
            if not isinstance(knot, dict) or set(knot) != {'time_s', 'speed_kmh'}:
                raise ValueError('semantic motion invalid speed knot')
            t, v = number(knot['time_s']), number(knot['speed_kmh'])
            if t is None or v is None or not previous < t <= expected['duration_s'] or not 0 <= v <= POLICY['max_speed_kmh']:
                raise ValueError('semantic motion speed/time outside policy')
            previous = t
        if knots[0]['time_s'] != 0 or knots[-1]['time_s'] != expected['duration_s']:
            raise ValueError('semantic motion must cover full duration, including unseen prefix/tail')
        profile = profiles[aid] = segments(plan)
        if any(abs(p[4]) > POLICY['max_acceleration_mps2'] for p in profile):
            raise ValueError('semantic motion acceleration outside policy')
        if aid in expected['constant_speed_actor_ids'] and len({p['speed_kmh'] for p in knots}) != 1:
            raise ValueError('semantic motion lane changer requires constant speed')
        for point in actors[aid]['trajectory']:
            actual_x, actual_v = state(profile, time(point))
            known_x = number((point.get('position_ego_m') or {}).get('x'))
            known_v = number(point.get('speed_mps'))
            if (known_x is not None and abs(actual_x-known_x) > 1e-4
                    or known_v is not None and abs(actual_v-known_v) > 1e-4):
                raise ValueError('semantic motion changes known source position/speed: ' + aid)
        condition = expected['initial_conditions'].get(aid) or {}
        for observed, actual in ((number((condition.get('position_ego_m') or {}).get('x')), x),
                                 (number(condition.get('speed_kmh')), knots[0]['speed_kmh'])):
            if observed is not None and abs(observed-actual) > 1e-4:
                raise ValueError('semantic motion changes known initial conditions: ' + aid)
    return profiles


def validate_relations(expected, coverage, relations, *, partial=False, observation_only_ids=()):
    """Validate citations/coverage independently of numerical motion solving."""
    from . import motion_witnesses as witnesses
    actors = {p['actor_id']: p for p in expected['eligible']}
    sources = {p['source']: p for p in expected['evidence']}
    source_witnesses = expected.get('policy', {}).get(witnesses.POLICY_KEY) == witnesses.REVISION
    overtake_events = [(index, event) for index, event in enumerate(expected.get('events', []))
                       if event.get('event_type') == 'overtake'] if source_witnesses else []

    def source_x(actor, t):
        points = [p for p in actor['trajectory'] if time(p) == t
                  and isinstance(p.get('frame_id'), str) and p['frame_id']]
        value = (points[0].get('position_ego_m') or {}).get('x') if len(points) == 1 else None
        # A frozen unknown/assumption wrapper is not an observed coordinate.
        return number(value) if type(value) in (int, float) else None

    def matching_event(row):
        left, right = row['time_interval_s']
        pair = {row['actor_id'], row['reference_actor_id']}
        return [(index, event) for index, event in overtake_events
                if set(event.get('involved_actor_ids', [])) == pair
                and isinstance(event.get('time_range_s'), list)
                and len(event['time_range_s']) == 2
                and left <= event['time_range_s'][1]
                and event['time_range_s'][0] <= right <= event['time_range_s'][1]]
    if (not isinstance(coverage, list) or any(not isinstance(p, dict) for p in coverage)
            or any(not isinstance(p.get('source'), str) for p in coverage)
            or len({p['source'] for p in coverage}) != len(coverage)
            or not {p['source'] for p in coverage} <= set(sources)
            or not partial and {p['source'] for p in coverage} != set(sources)):
        raise ValueError('semantic motion must review every supplied text item')
    for item in coverage:
        if set(item) != {'source', 'status', 'reason'} or not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ValueError('semantic motion coverage requires a reason')
        allowed = {'used', 'no_motion', 'unobserved'}
        receipt = expected.get(witnesses.KEY, {}).get('receipts', {}).get(item['source'])
        if receipt is not None:
            receipt_status = ('qualitative_only' if sources[item['source']].get('usage') == 'qualitative_only'
                              else 'program_verified')
            if item != dict(source=item['source'],status=receipt_status,reason=digest(receipt)):
                raise ValueError('Program witness receipt changed')
            allowed.add(receipt_status)
        if partial:
            allowed |= {'identity_unresolved', 'motion_unresolved'}
        source = sources[item['source']]
        if source.get('usage') == 'audit_only':
            if item['status'] != 'audit_only':
                raise ValueError('Early identity prose is audit-only, not observed motion evidence')
            continue
        if source.get('usage') == 'qualitative_only':
            if (item['status'] != 'qualitative_only'
                    and not (item['status'] == 'program_verified' and receipt is not None)):
                raise ValueError('Body-part source must remain an explicit qualitative constraint')
            continue
        observation_only_motion = (item['status'] == 'motion_unresolved'
            and source['source'].startswith('/limitations/')
            and source['source'].endswith('/description')
            and bool(source['actor_ids'])
            and set(source['actor_ids']) <= set(observation_only_ids)
            and not set(source['actor_ids']) & set(actors))
        if item['status'] not in allowed and not observation_only_motion:
            raise ValueError('semantic motion unresolved identity or motion requires upstream review')
    if not isinstance(relations, list):
        raise ValueError('semantic motion relations must be an array')
    cited = set()
    for row in relations:
        if not isinstance(row, dict) or set(row) != {'type', 'actor_id', 'reference_actor_id', 'time_interval_s', 'evidence'}:
            raise ValueError('semantic motion relation fields invalid')
        if row['type'] not in RELATIONS or row['actor_id'] not in actors or row['reference_actor_id'] not in actors or row['actor_id'] == row['reference_actor_id']:
            raise ValueError('semantic motion relation actor/type invalid')
        interval = row['time_interval_s']
        if not isinstance(interval, list) or len(interval) != 2 or any(number(t) is None for t in interval) or not 0 <= interval[0] <= interval[1] <= expected['duration_s']:
            raise ValueError('semantic motion relation interval invalid')
        bounds = expected.get('early_association_bounds', {})
        if interval[0] < max(bounds.get(row['actor_id'], 0), bounds.get(row['reference_actor_id'], 0)):
            raise ValueError('Early association prefix has no observed Actor relation')
        if row['type'] in {'approaching', 'overtakes'} and interval[0] == interval[1]:
            raise ValueError('semantic motion progress requires a time interval')
        _quotes(row['evidence'], sources)
        if source_witnesses and row['type'] == 'overtakes':
            subject, reference = actors[row['actor_id']], actors[row['reference_actor_id']]
            values = [source_x(actor, t) for t in interval for actor in (subject, reference)]
            if not matching_event(row) or any(value is None for value in values):
                raise ValueError('semantic motion overtake completion lacks frozen source evidence')
            start_gap, end_gap = values[0]-values[1], values[2]-values[3]
            half = (length(subject)+length(reference))/2
            if (start_gap > half+POLICY['alongside_tolerance_m']
                    or end_gap < half+POLICY['clearance_m']
                    or end_gap-start_gap < POLICY['closing_progress_m']):
                raise ValueError('semantic motion overtake completion contradicts frozen source positions')
        for citation in row['evidence']:
            source = sources[citation['source']]
            if source.get('usage') == 'audit_only':
                raise ValueError('Early identity prose cannot establish a motion relation')
            if source.get('usage') == 'qualitative_only':
                raise ValueError('Body-part qualitative source cannot establish whole-vehicle motion')
            if source['actor_ids'] and not {row['actor_id'], row['reference_actor_id']} & set(source['actor_ids']):
                raise ValueError('semantic motion citation belongs to another actor')
            if source['time_s'] is not None and not interval[0] <= source['time_s'] <= interval[1]:
                raise ValueError('semantic motion citation outside relation time')
            cited.add(citation['source'])
    program = {p['source'] for p in coverage if p['status'] == 'program_verified'}
    if cited - program != {p['source'] for p in coverage if p['status'] == 'used'}:
        raise ValueError('semantic motion used evidence must bind an executable relation')
    if source_witnesses and not partial:
        for index, event in overtake_events:
            source = f'/events/{index}/description'
            if source not in sources:
                raise ValueError('semantic motion overtake event lacks a citable source description')
            if not any(row['type'] in {'approaching', 'alongside', 'overtakes'}
                       and any(citation['source'] == source for citation in row['evidence'])
                       and any(i == index for i, _ in matching_event(row))
                       for row in relations):
                raise ValueError('semantic motion cannot silently discard a source overtake event')
    return coverage, relations


def candidate_to_decision(analysis, analysis_sha256, candidate, candidate_sha256, *, witness_revision=None):
    if not isinstance(candidate, dict) or set(candidate) != {'revision', 'request_sha256', 'plans', 'relations', 'coverage'}:
        raise ValueError('semantic motion model response invalid; unresolved facts need upstream review')
    from _lib.contracts import retained_motion
    from _lib.contracts import bounded_execution
    expected = request(analysis, analysis_sha256, witness_revision=witness_revision)
    if analysis.get('metadata', {}).get(bounded_execution.KEY):
        candidate = copy.deepcopy(candidate)
        candidate['coverage'], candidate['relations'] = bounded_execution.complete_relations(
            expected, candidate['coverage'], candidate['relations'])
    if retained_motion.records(analysis):
        candidate = copy.deepcopy(candidate)
        candidate['coverage'], candidate['relations'] = retained_motion.complete_relations(
            expected, candidate['coverage'], candidate['relations'])
        candidate['plans'] = retained_motion.complete_plans(expected, candidate['plans'], candidate['relations'])
    decision = dict(candidate, analysis_sha256=analysis_sha256, candidate_sha256=candidate_sha256,
                    status='accepted', policy=copy.deepcopy(expected['policy']))
    return validate(analysis, analysis_sha256, decision)


def project(projection, decision):
    """Populate only missing metrics on the private copy, with per-field receipts."""
    plans = {p['actor_id']: p for p in decision['plans']}
    bounds = early_association_bounds(projection.data)
    for actor in [projection.data['ego'], *projection.data.get('actors', [])]:
        aid = actor['actor_id']
        if aid not in plans:
            continue
        plan = plans[aid]
        profile = segments(plan)
        path = 'ego' if aid == 'ego' else f'actors[{aid}]'
        points = actor['trajectory']
        single = projection.data.get('metadata', {}).get('bounded_execution', {}).get('single_frame_motion', {}).get(aid)
        if single and single['lane_assumed']:
            for index, point in enumerate(points):
                if point.get('lane_id') is None:
                    projection.set(point, 'lane_id', single['selected_lane_id'],
                        f'{path}.trajectory[{index}].lane_id', 'bounded_single_frame_lane_assumption')
                    projection.fallbacks[-1].update(assumption=single['assumption'],
                        observed_lane_id=None, candidate_lane_ids=single['candidate_lane_ids'])
        # Synthetic boundary samples carry no observed frame or description.
        for t, template in ((0, points[0]), (profile[-1][1], points[-1])):
            if not any(time(p) == t for p in points):
                point = {k: copy.deepcopy(template[k]) for k in ('lane_id', 'road_id', 'ordinal_from_right') if k in template}
                point['time_s'] = t
                points.append(point)
                projection.fallbacks.append({'field': path+'.trajectory', 'value': point.copy(),
                                             'reason': 'semantic_motion_unobserved_boundary',
                                             'assumption': plan['assumption']})
                if aid in bounds and t < bounds[aid]:
                    projection.fallbacks[-1].update(observed_from_s=bounds[aid],
                        unobserved_prefix_s=[0, bounds[aid]], identity_association_used=False)
        points.sort(key=time)
        for index, point in enumerate(points):
            x, v = state(profile, time(point))
            if not isinstance(point.get('position_ego_m'), dict):
                point['position_ego_m'] = {}
            for owner, key, value, field in ((point, 'speed_mps', v, f'{path}.trajectory[{index}].speed_mps'),
                                            (point.setdefault('position_ego_m', {}), 'x', x, f'{path}.trajectory[{index}].position_ego_m.x')):
                if number(owner.get(key)) is None:
                    projection.set(owner, key, value, field, 'semantic_motion_execution_approximation')
                    projection.fallbacks[-1].update(assumption=plan['assumption'], request_sha256=decision['request_sha256'])
    projection.data['_semantic_motion'] = copy.deepcopy(decision)


def audit(analysis, target, decision):
    from _lib.behavior.lane_change import actor_name
    from _lib.delivery.motion_check import decode
    from . import motion_witnesses as witnesses
    expected = request(analysis, decision['analysis_sha256'],
                       witness_revision=decision['policy'].get(witnesses.POLICY_KEY))
    vehicles = {v['name']: v for v in target['scenario']['vehicles']}
    actual = {}
    for plan in decision['plans']:
        aid = plan['actor_id']
        actual[aid] = decode(vehicles[actor_name(aid)], expected['duration_s'])
        predicted = segments(plan)
        times = critical_times(actual[aid], predicted, 0, expected['duration_s'])
        for t in times:
            if any(abs(a-b) > 1e-4 for a, b in zip(state(actual[aid], t), state(predicted, t))):
                raise ValueError('target lost semantic motion plan: ' + aid)
    check_lane_clearance(expected, actual)
    witness_checks = witnesses.check(expected, actual, target=target, analysis=analysis)
    return {'status': 'passed', 'verification': 'execution_approximation_checked',
            'render_verification': 'not_executed',
            **({'source_witnesses': witness_checks} if witnesses.KEY in expected else {}),
            'relations': check_relations(expected, decision['relations'], actual)}
