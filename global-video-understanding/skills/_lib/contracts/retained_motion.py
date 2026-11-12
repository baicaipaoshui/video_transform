"""Source-bound retention and execution-only completion of an unseen tail.

This policy does not establish irrelevance, identity links or observed motion.
Its deliberately bounded recognizer declines unfamiliar/mixed evidence.
"""
import copy
import re
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.unused_association import mentions
from _lib.runtime.case_core import read_json, atomic_json, sha256

KEY = 'retained_unobserved_motion'
REVISION = 'retained-unobserved-motion.v1'
ADOPTION = 'state/retained_unobserved_motion.adoption.json'
PERMISSIONS = {'vehicle_speed_policy_default', 'vehicle_position_policy_default',
               'trajectory_continuation_policy_default'}


class RetainedMotionBlocked(ValueError):
    code = 'execution_assumption_infeasible'
    rule_id = 'motion.retained_unobserved_infeasible'

    def __init__(self, message):
        super().__init__(message)
        self.diagnostics = dict(rule_id=self.rule_id, retry_kind='policy_change',
                                owner='delivery', message=message)


def enabled(case):
    case = Path(case)
    if read_json(case/'state/execution_policy.json').get(KEY) == REVISION:
        return True
    if not (case/ADOPTION).is_file():
        return False
    from _lib.contracts.execution_assumptions import SOURCES, fail
    value = read_json(case/ADOPTION)
    if (value.get('revision') != REVISION
            or value.get('case_id') != read_json(case/'case.json')['case_id']
            or value.get('sha256') != digest({k:v for k,v in value.items() if k != 'sha256'})
            or not set(SOURCES) <= set(value.get('source_hashes', {}))
            or any(not (case/p).is_file() or sha256(case/p) != h
                   for p,h in value.get('source_hashes', {}).items())):
        fail('Retained unseen-motion adoption changed')
    return True


def adopt(case):
    """Explicit opt-in before Behavior; never changes existing policies/budgets."""
    from _lib.contracts import execution_assumptions as assumptions
    from _lib.runtime.bounded_recovery import require_terminal
    case = Path(case)
    if enabled(case):
        return read_json(case/ADOPTION) if (case/ADOPTION).is_file() else None
    state = read_json(case/'state/progress.json')
    require_terminal(state)
    if (state.get('attempts', {}).get('behavior', 0)
            or any(s.get('stage') == 'behavior' for s in state.get('sessions', []))
            or any((case/p).exists() for p in ('state/behavior.json', 'state/behavior.candidate.json',
                'state/behavior_scope_review.accepted.json', 'final/analysis.json'))):
        assumptions.fail('Retained unseen-motion adoption requires unattempted Behavior')
    assumptions.adopt(case)
    names = list(assumptions.SOURCES)
    if (case/assumptions.ADOPTION).is_file():
        names.append(assumptions.ADOPTION)
    body = dict(revision=REVISION, case_id=read_json(case/'case.json')['case_id'],
                source_hashes={p:sha256(case/p) for p in names})
    value = dict(body, sha256=digest(body))
    atomic_json(case/ADOPTION, value)
    return value


def pure_tail(item, aid):
    """A pure missing tail, never unknown braking/interaction/identity/action."""
    text = str(item.get('description', item.get('note', ''))).lower()
    return (item.get('actor_ids') == [aid]
        and bool(item.get('impact_domains'))
        and set(item['impact_domains']) <= {'actor_motion', 'executor_primitive', 'speed', 'position'}
        and item.get('subject_field') in (None, 'actor_motion')
        and not any(item.get(k) for k in ('world_paths', 'related_world_paths', 'affected_event_ids',
            'affected_actor_ids', 'scope_binding', 'identity_binding', 'census_issue_ids',
            'event_id', 'event_ids', 'involved_actor_ids', 'localization'))
        and set(re.findall(r'\b(?:actor_\d+|ego)\b', text)) <= {aid}
        and not re.search(r'brak|accelerat|decelerat|overtak|collis|interact|yield|cut.?in|lane.?chang|turn|避让|刹车|碰撞|变道|超车|交互', text)
        and bool(re.search(r'motion|运动|行驶', text))
        and bool(re.search(r'unknown|unobserved|not seen|未知|未观察|不可见', text))
        and bool(re.search(r'after|later|tail|leaving|离开|后续|之后', text)))


def inherited_tail(item, aid):
    """Typed restatement of a frozen unknown; no positive prose keyword test."""
    if not pure_tail(dict(item, description='Motion after leaving the view is unknown.'), aid):
        return False
    text = str(item.get('description', '')).lower()
    return (set(re.findall(r'\b(?:actor_\d+|ego)\b', text)) <= {aid}
        and not re.search(r'brak|accelerat|decelerat|overtak|collis|yield|cut.?in|lane.?chang|turn|'
                          r'another|different vehicle|identity.*(?:unknown|uncertain)|另一辆|身份.*(?:未知|不明)|'
                          r'避让|刹车|碰撞|变道|超车', text))


def bounded_actor_local_motion_limit(item, aid):
    """A mixed single-Actor limit may preserve appearance/lane unknowns.

    The retained-motion owner completes only the missing motion.  The known
    lane is checked independently and appearance remains unknown in Analysis.
    """
    domains = set(item.get('impact_domains', []))
    return (item.get('actor_ids') == [aid]
            and 'actor_motion' in domains
            and domains <= {'actor_motion', 'actor_lane', 'appearance'}
            and item.get('execution_effect') == 'bounded_uncertainty'
            and not any(item.get(k) for k in ('world_paths', 'related_world_paths', 'event_ids',
                'affected_event_ids', 'identity_binding', 'scope_binding', 'census_issue_ids',
                'affected_actor_ids', 'involved_actor_ids', 'event_id', 'actor_id', 'localization')))


def distant_description(text):
    return bool(re.search(r'远端|远距|远处|far|distant', str(text), re.I))


def same_direction_lane_ids(road):
    """Reuse the exact legacy lane-count adapter used by semantic planning."""
    from _lib.contracts.semantic_motion import _implicit_ego_lane_ids
    implicit = _implicit_ego_lane_ids(road)
    return {lane['lane_id'] for lane in road.get('lanes', [])
            if lane.get('travel_direction') == 'same_as_ego'
            or (lane.get('travel_direction') in (None, 'unknown')
                and lane.get('lane_id') in implicit)}


def select(case, saved, behavior, pending, frames):
    if not enabled(case):
        return {}
    from _lib.contracts import bounded_execution
    inherited = bounded_execution.enabled(case)
    from _lib.contracts.conversion_policy import build
    parameters = read_json(Path(case)/'case.json')['parameters']
    policy = build(parameters, '0'*64, 'highway')
    if not PERMISSIONS <= set(policy['allowed_degradations']):
        return {}
    world = saved['result']['world']; scene = world['world']
    roads = scene.get('road', {}).get('roads', [])
    if (scene.get('scene_type') != 'highway' or scene.get('junctions') or len(roads) != 1
            or (roads[0].get('road_geometry') or {}).get('shape_type') not in (None, 'straight')):
        return {}
    lanes = {l['lane_id']:l for l in scene['road']['lanes']}
    same_direction_lanes = same_direction_lane_ids(scene['road'])
    actors = {a['actor_id']:a for a in behavior.get('actors', [])}
    result = {}
    for registered in saved['result']['registry']:
        aid = registered['actor_id']; actor = actors.get(aid, {})
        anchors = [e for e in registered.get('evidence', []) if e.get('status') == 'observed']
        points = actor.get('trajectory', [])
        lane = registered.get('hypothesized_lane')
        description = registered.get('identity_description', '')
        if (aid == 'ego' or registered.get('actor_type') != 'vehicle'
                or actor.get('actor_type') != 'vehicle' or actor.get('is_static') is not False
                or len(points) != 1 or not anchors
                or len({e['frame_id'] for e in anchors}) != 1
                or points[0].get('time_s') != 0 or any(e.get('time_s') != 0 for e in anchors)
                or points[0].get('frame_id') != registered.get('first_seen_frame_id')
                or {e['frame_id'] for e in anchors} != {points[0].get('frame_id')}
                or frames.get(points[0].get('frame_id'), {}).get('time_s') != 0
                or lane not in same_direction_lanes
                or points[0].get('lane_id') != lane
                or not inherited and actor.get('identity_description') != description
                or not re.search(r'alongside ego.*(?:first frame|initial)|首帧.*(?:并行|并排)', description.lower())
                or re.search(r'\b(?:not|maybe|possibly|uncertain|unknown)\b|不确定|可能|并非', description.lower())
                or not inherited and points[0].get('detail') not in (None, '', description, *(e.get('reason') for e in anchors))
                or (actor.get('direction_evidence') or {}).get('value') not in (None, 'same_as_ego')
                or any(aid in r.get('candidate_actor_ids', []) for r in pending.values())):
            continue
        if inherited and any(not inherited_tail(dict(actor_ids=[aid],impact_domains=['actor_motion'],
                description=text),aid) for text in
                (re.sub(r'\bego\b', '', str(actor.get('identity_description','')), flags=re.I),
                 re.sub(r'\bego\b', '', str(points[0].get('detail','')), flags=re.I))):
            continue
        if inherited and any(re.search(
                r'not (?:alongside|beside)|(?:ahead of|behind|following|approaching) (?:the )?ego|'
                r'ego (?:is )?(?:ahead|behind)|不.*(?:并行|并排)|(?:自车|主车).*(?:前方|后方)|'
                r'跟随|接近|远离|交互', str(text).lower()) for text in
                (actor.get('identity_description', ''), points[0].get('detail', ''))):
            # A changed initial relation is new evidence, not an identity paraphrase.
            continue
        # Newly supplied relational fields are not explained by the old anchor.
        if set(points[0]) - {'frame_id','time_s','lane_id','road_id','ordinal_from_right',
                             'position_ego_m','speed_mps','detail'}:
            continue
        claim = actor.get('execution_scope_evidence') or {}
        if re.search(r'brak|accelerat|decelerat|overtak|collis|yield|cut.?in|lane.?chang|turn|'
                     r'not alongside|避让|刹车|碰撞|变道|超车', str(claim.get('reason', '')).lower()):
            continue
        dependencies = [behavior.get(k, []) for k in
            ('events', 'video_fidelity', 'challenge_resolutions')]
        dependencies.append([p for p in behavior.get('executor_primitives', [])
                             if not (p.get('type') == 'initial_state' and p.get('actor_id') == aid)])
        dependencies += [a for a in behavior.get('actors', []) if a['actor_id'] != aid]
        if mentions(dependencies, {aid}):
            continue
        uncertainties = [q for k in ('questions_to_verify', 'ambiguities') for q in world.get(k, [])
                         if mentions(q, {aid}) and q.get('resolution') == 'unresolved']
        limits = [q for q in behavior.get('semantic_limitations', []) if mentions(q, {aid})]
        if (not uncertainties or any(q.get('owner') != 'behavior' or not pure_tail(q, aid) for q in uncertainties)
                or any(not (inherited_tail(q, aid) if inherited else pure_tail(q, aid)) for q in limits)):
            continue
        result[aid] = dict(revision=REVISION, actor_id=aid, lane_id=lane,
            observed_frame_id=points[0]['frame_id'], observed_time_s=0,
            registry=copy.deepcopy(registered), actor_sha256=digest(actor),
            uncertainties=copy.deepcopy(uncertainties), limitations=copy.deepcopy(limits),
            parameters_sha256=digest(parameters), policy_permissions=sorted(PERMISSIONS),
            assumption='Retain the confirmed vehicle; unseen tail stays in the observed lane with bounded constant-speed execution approximation. No observed interaction or hidden action is asserted.')
        if inherited:
            result[aid]['description_authority'] = 'registry_source'
            result[aid]['submitted_description'] = actor.get('identity_description')
    return result


def bounded_records(case, saved, behavior, pending, frames, review):
    """Retain confirmed single-frame vehicles without inventing identity links.

    Unlike the legacy alongside-tail rule this records a simulation envelope,
    not an observed centre position. All source limits remain in the Behavior.
    """
    from _lib.contracts import bounded_execution, census_relevance, behavior_handoff
    from _lib.contracts.execution_gaps import authorized_scale
    from _lib.contracts.conversion_policy import build, default_completion
    if not bounded_execution.enabled(case):
        return {}
    world = saved['result']['world']; scene = world['world']
    roads = scene.get('road', {}).get('roads', [])
    if scene.get('scene_type') != 'highway' or scene.get('junctions') or len(roads) != 1:
        return {}
    policy = build(read_json(Path(case)/'case.json')['parameters'], '0'*64, 'highway',
                   default_completion=default_completion(case))
    if not PERMISSIONS <= set(policy['allowed_degradations']):
        return {}
    lanes = {l['lane_id']:l for l in scene['road']['lanes']}
    same_direction_lanes = same_direction_lane_ids(scene['road'])
    rows = {r['issue_id']:r for r in (review or {}).get('decisions', [])}
    views = census_relevance.grouped_views(behavior, pending, rows)
    choices = []
    # Only a complete original scope proof may discharge an identity limit.
    for iid, observation in pending.items():
        if iid not in rows:
            continue
        checked = census_relevance.validate(case, views.get(iid, behavior), rows[iid],
            observation, frames, world, registry=saved['result']['registry'])
        if checked:
            choices.append(checked)
    receipt = dict(revision=census_relevance.EXECUTION, choices=choices)
    probe = Path(case)/'state/probe.json'
    if not probe.is_file():
        return {}  # No new envelope can be bound without the source horizon.
    duration = read_json(probe)['probe']['duration_s']
    result = {}
    registered = {a['actor_id']:a for a in saved['result']['registry']}
    def unknown_motion(item, aid):
        # These new envelopes own absent motion, not positive prose facts.
        # Keep the legacy inherited-restatement contract unchanged.
        text = item.get('description', item.get('note', ''))
        return (inherited_tail(dict(item, description=text), aid)
            and bool(re.search(r'unknown|unobserved|not seen|未知|未观察|未记录|不可见|'
                r'无法确认|无法判断|无法确定|不确定|不清楚|未确认', text, re.I))
            and not re.search(r'\d+(?:\.\d+)?\s*(?:m/s|km/h|米|公里|千米|m\b)', text, re.I))
    for actor in behavior.get('actors', []):
        aid = actor['actor_id']; source = registered.get(aid, {})
        points = actor.get('trajectory', []); claim = actor.get('execution_scope_evidence') or {}
        anchors = [e for e in source.get('evidence', []) if e.get('status') == 'observed']
        if (aid == 'ego' or actor.get('actor_type') != 'vehicle' or actor.get('is_static') is not False
                or source.get('actor_type') != 'vehicle' or len(points) != 1 or not anchors
                or actor.get('identity_description') != source.get('identity_description')
                or {e.get('frame_id') for e in anchors} != {points[0].get('frame_id')}
                or points[0].get('frame_id') != source.get('first_seen_frame_id')
                or points[0].get('time_s') != frames.get(points[0].get('frame_id'), {}).get('time_s')
                or claim.get('kind') not in {'edge_fragment', 'distant_transient'}
                or re.search(r'brak|accelerat|decelerat|yield|cut.?in|lane.?chang|turn|'
                    r'避让|刹车|加速|减速|碰撞|变道|超车', points[0].get('detail',''), re.I)
                or (actor.get('direction_evidence') or {}).get('value') not in {None, 'same_as_ego'}):
            continue
        # Reuse the full frame-bound claim validator, without using its omission.
        behavior_handoff.transient_actors(world, dict(behavior, actors=[actor]), list(frames.values()), choices)
        pure_questions = {q['ambiguity_id'] for q in world.get('ambiguities', [])
                          if q.get('owner') == 'behavior' and unknown_motion(q, aid)}
        challenges = [q for q in behavior.get('challenge_resolutions', []) if not (
            q.get('challenge_id') in pure_questions and q.get('resolution') == 'resolved'
            and q.get('execution_effect') == 'retain_actor_state'
            and unknown_motion(dict(actor_ids=[aid], impact_domains=['actor_motion'],
                description=q.get('decision','')+' '+q.get('consistency','')), aid))]
        dependencies = [behavior.get(k, []) for k in ('events','video_fidelity')]
        dependencies.append(challenges)
        dependencies += [[p for p in behavior.get('executor_primitives', [])
                          if not (p.get('type') == 'initial_state' and p.get('actor_id') == aid)],
                         [a for a in behavior['actors'] if a['actor_id'] != aid]]
        if mentions(dependencies, {aid}):
            continue
        limits = [q for q in behavior.get('semantic_limitations', []) if mentions(q, {aid})]
        unresolved = [q for key in ('questions_to_verify','ambiguities') for q in world.get(key, [])
                      if q.get('resolution') == 'unresolved' and mentions(q, {aid})]
        def allowed(item):
            return (authorized_scale(case, item) or census_relevance.observation_issue_disposed(item, receipt)
                    or unknown_motion(item, aid)
                    or bounded_actor_local_motion_limit(item, aid)
                    or item.get('actor_ids') == [aid] and item.get('impact_domains') == ['actor_lane']
                    and item.get('execution_effect') == 'bounded_uncertainty'
                    and not any(item.get(k) for k in ('world_paths','related_world_paths','event_ids',
                        'affected_event_ids','identity_binding','scope_binding','census_issue_ids',
                        'affected_actor_ids','involved_actor_ids','event_id','actor_id','localization')))
        if any(not allowed(q) for q in [*limits, *unresolved]):
            continue
        point = points[0]; lane = point.get('lane_id'); assumed_lane = lane is None
        if source.get('hypothesized_lane') is not None and lane != source['hypothesized_lane']:
            continue  # Do not turn a known lane conflict into a free choice.
        if assumed_lane:
            if ('vehicle_lane_policy_default' not in policy['allowed_degradations']
                    or point.get('road_id') != roads[0]['road_id']
                    or point.get('ordinal_from_right') is not None
                    or type((point.get('position_ego_m') or {}).get('y')) in (int,float)):
                continue
            # Same road-order preference as Converter's existing unknown-lane
            # fallback. It is a choice, not a visual lane determination.
            candidates = [lid for lid in roads[0]['lane_ids'] if lid in same_direction_lanes]
            if not candidates:
                continue
            lane = candidates[0]
        else:
            candidates = [lane]
        if lane not in same_direction_lanes:
            continue
        text = source.get('identity_description', '')
        kind = ('near_side' if point['time_s'] == 0 and claim['kind'] == 'edge_fragment'
                and re.search(r'旁侧|并行|并排|alongside|beside', text, re.I)
                else 'remote' if claim['kind'] == 'distant_transient'
                and distant_description(text) else None)
        if kind is None:
            continue
        motion_limits = [q for q in limits if (q.get('actor_ids') == [aid]
                         and q.get('impact_domains') == ['actor_motion'])
                         or bounded_actor_local_motion_limit(q, aid)]
        if not motion_limits:
            continue
        result[aid] = dict(actor_id=aid, actor_sha256=digest(actor), registry=copy.deepcopy(source),
            observed_frame_id=point['frame_id'], observed_time_s=point['time_s'],
            selected_lane_id=lane, candidate_lane_ids=candidates, lane_assumed=assumed_lane,
            kind=kind, limitations=copy.deepcopy(motion_limits), uncertainties=copy.deepcopy(unresolved),
            unobserved_prefix_s=[0, point['time_s']], unobserved_tail_s=[point['time_s'], duration],
            identity_association_used=False,
            assumption=('Confirmed vehicle retained. Unknown prefix/tail use bounded constant-speed simulation; '
                'no early identity association or hidden action is asserted. '
                + ('Lane chosen by the existing road-order fallback, not observed. ' if assumed_lane else '')
                + ('Initial centre placed near ego as an explicit body-fragment approximation.' if kind == 'near_side'
                   else 'Keep a simulation separation of at least 150m throughout the window; this is not a measured distance.')))
        if kind == 'near_side' and re.search(r'随后落后出画|随后.*落后', point.get('detail','')):
            result[aid]['relative_motion'] = 'falling_behind'
            result[aid]['relative_motion_source'] = point['detail']
            result[aid]['assumption'] += ' Preserve the stated falling-behind trend; exact exit time is unknown.'
    return result


def records(analysis):
    q = analysis.get('quality_report', {})
    scope = q.get('execution_actor_scope') or q.get('offline_execution_actor_scope') or {}
    return ((scope.get('execution_dispositions') or {}).get('execution_assumptions') or {}).get(KEY, {})


def request_fields(analysis, evidence):
    """Only called after the full source/scope certificate has been replayed."""
    retained = records(analysis)
    if not retained:
        return {}
    actors = {a['actor_id']:a for a in analysis.get('actors', [])}
    owned = {}
    for aid, record in sorted(retained.items()):
        actor = actors.get(aid)
        if (not actor or len(actor['trajectory']) != 1
                or actor['trajectory'][0].get('lane_id') != record['lane_id']
                or actor['trajectory'][0].get('time_s') != 0
                or actor.get('identity_description') != record.get('submitted_description',record['registry']['identity_description'])):
            raise ValueError('Retained unseen-motion actor/anchor changed: ' + aid)
        source = '/actors/' + aid + '/identity_description'
        inherited = record.get('description_authority') == 'registry_source'
        for row in evidence:
            if row['source'] == source:
                row['time_s'] = 0
                owned[source] = dict(kind='no_motion' if inherited else 'alongside', actor_id=aid)
            elif row['source'].startswith('/actors/' + aid + '/trajectory/'):
                kind = 'alongside' if not inherited and re.search(r'alongside|并行|并排', row['text'].lower()) else 'no_motion'
                owned[row['source']] = dict(kind=kind, actor_id=aid)
            elif row['source'].endswith('/description') and row['actor_ids'] == [aid]:
                if row['text'] in {r['description'] for r in record['limitations']}:
                    owned[row['source']] = dict(kind='unobserved', actor_id=aid)
        if source not in owned:
            raise ValueError('Retained unseen-motion initial relation evidence missing')
        if inherited:
            path = f'/execution_assumptions/{KEY}/{aid}/registry/identity_description'
            evidence.append(dict(source=path,text=record['registry']['identity_description'],actor_ids=[aid],time_s=0))
            owned[path] = dict(kind='alongside',actor_id=aid)
        for i, item in enumerate(record['uncertainties']):
            path = f'/execution_assumptions/{KEY}/{aid}/uncertainties/{i}'
            evidence.append(dict(source=path, text=item.get('note', item.get('description', '')),
                                 actor_ids=[aid], time_s=None))
            owned[path] = dict(kind='unobserved', actor_id=aid)
    from dataclasses import asdict
    from _lib.behavior.lane_change import from_analysis
    spec = from_analysis(analysis)
    lane_change = asdict(spec) if spec else None
    if lane_change and lane_change.get('timing_assumption') is None:
        lane_change.pop('timing_assumption', None)  # Preserve frozen pre-policy requests.
    return {KEY: dict(revision=REVISION, actors=copy.deepcopy(retained), sources=owned,
                     lane_change=lane_change)}


def complete_relations(request, coverage, relations):
    program = request.get(KEY)
    if not program:
        return coverage, relations
    coverage = copy.deepcopy(coverage); relations = copy.deepcopy(relations)
    owned = program['sources']; sources = {e['source']:e for e in request['evidence']}
    for row in coverage:
        if row.get('source') in owned and row.get('status') == 'identity_unresolved':
            raise ValueError('Retained unseen-motion cannot repair an identity conflict')
    for row in relations:
        if {row.get('actor_id'), row.get('reference_actor_id')} & set(program['actors']):
            # No relation may extrapolate the single initial observation.
            aid = row.get('actor_id')
            expected = (aid in program['actors'] and row.get('reference_actor_id') == 'ego'
                and row.get('type') == 'alongside' and row.get('time_interval_s') == [0, 0]
                and bool(row.get('evidence')) and all(
                    owned.get(e.get('source')) == dict(kind='alongside', actor_id=aid)
                    and isinstance(e.get('quote'), str) and bool(e['quote'])
                    and e['quote'] in sources[e['source']]['text'] for e in row['evidence']))
            if not expected:
                raise ValueError('Retained unseen-motion cannot overwrite a new temporal relation')
    relations = [r for r in relations if r.get('actor_id') not in program['actors']]
    coverage = [r for r in coverage if r.get('source') not in owned]
    for source, row in sorted(owned.items()):
        coverage.append(dict(source=source, status='used' if row['kind']=='alongside' else row['kind'],
            reason=REVISION + ': source-bound initial observation or explicitly unobserved tail; not a noninteraction proof.'))
    for aid in sorted(program['actors']):
        relation = initial_relation(aid, sources, owned)
        if relation not in relations:
            relations.append(relation)
    return coverage, relations


def initial_relation(aid, sources, owned):
    return dict(type='alongside', actor_id=aid, reference_actor_id='ego', time_interval_s=[0, 0],
                evidence=[dict(source=s, quote=sources[s]['text']) for s,r in sorted(owned.items())
                          if r == dict(kind='alongside', actor_id=aid)])


def complete_plans(request, plans, relations):
    """Finite deterministic choices, changing only the owned unseen actor metrics."""
    program = request.get(KEY)
    if not program:
        return plans
    from _lib.contracts import semantic_motion as motion
    actors = {a['actor_id']:a for a in request['eligible']}
    owned = set(program['actors'])
    if not owned <= set(actors):
        raise ValueError('Retained unseen-motion vehicle cannot be omitted from conversion')
    other = [copy.deepcopy(p) for p in plans if p.get('actor_id') not in owned]
    profiles = motion.validate_plans(request, other, partial=True)
    if set(profiles) != set(actors)-owned:
        return other  # Other normal work is incomplete; never invent its plans.
    # A defect in somebody else's model plan is not deterministic tail
    # infeasibility; preserve the ordinary content-error classification.
    other_request = dict(request, eligible=[a for a in request['eligible'] if a['actor_id'] not in owned])
    motion.check_relations(other_request, [r for r in relations
        if not {r['actor_id'],r['reference_actor_id']} & owned], profiles)
    motion.check_lane_clearance(other_request, profiles)
    base_speed = profiles['ego'][0][3]*3.6
    # A bounded Cartesian search keeps previously chosen vehicles fixed; no
    # relaxation of source facts, established relations or major maneuvers.
    def search(index, chosen):
        if index == len(owned):
            try:
                complete = motion.validate_plans(request, chosen)
                motion.check_relations(request, relations, complete)
                motion.check_lane_clearance(request, complete)
                check_changing_clearance(request, complete)
            except ValueError:
                return None
            return chosen
        aid = sorted(owned)[index]; actor = actors[aid]; point = actor['trajectory'][0]
        x = motion.number((point.get('position_ego_m') or {}).get('x'))
        speed = motion.number(point.get('speed_mps'))
        condition = request['initial_conditions'].get(aid) or {}
        if x is None:
            x = motion.number((condition.get('position_ego_m') or {}).get('x'))
        known_speed = speed*3.6 if speed is not None else motion.number(condition.get('speed_kmh'))
        for v in ([known_speed] if known_speed is not None else [base_speed, base_speed-5, base_speed+5, base_speed-10, base_speed+10]):
            for offset in ([x] if x is not None else [0., -1., 1., -2., 2.]):
                plan = dict(actor_id=aid, initial_x_m=offset,
                    speed_knots=[dict(time_s=0, speed_kmh=v), dict(time_s=request['duration_s'], speed_kmh=v)],
                    assumption=program['actors'][aid]['assumption'] + f' Initial offset={offset}m; speed={v}km/h; policy={REVISION}.')
                found = search(index+1, [*chosen, plan])
                if found is not None:
                    return found
        return None
    # Bound work as well as numerical choices. More simultaneous unknown tails
    # require a separate joint policy, never exponential unbounded retries.
    if len(owned) > 2:
        raise RetainedMotionBlocked('Retained unseen-motion bounded planner supports at most two vehicles')
    result = search(0, other)
    if result is None:
        raise RetainedMotionBlocked('Retained unseen-motion has no feasible bounded continuation; confirmed actors/actions preserved')
    return result


def check_changing_clearance(request, profiles):
    """Conservative longitudinal protection in either lane of the main action.

    The existing final target continuous lateral envelope remains mandatory.
    This extra planning guard may reject a feasible solution; it never certifies
    an observed interaction or moves the changing vehicle to obtain clearance.
    """
    from _lib.contracts import semantic_motion as motion
    program = request[KEY]; spec = program['lane_change']
    if not spec:
        return
    actors = {a['actor_id']:a for a in request['eligible']}
    for aid, record in program['actors'].items():
        lane = record['lane_id']
        if lane not in {spec['source_lane_id'], spec['target_lane_id']}:
            continue
        lo = 0 if lane == spec['source_lane_id'] else spec['start_s']
        hi = request['duration_s'] if lane == spec['target_lane_id'] else spec['end_s']
        first, second = profiles[aid], profiles[spec['actor_id']]
        times = motion.critical_times(first, second, lo, hi)
        gaps = [motion.state(first,t)[0]-motion.state(second,t)[0] for t in times]
        half = (motion.length(actors[aid])+motion.length(actors[spec['actor_id']]))/2+motion.POLICY['clearance_m']
        if min(gaps) <= half and max(gaps) >= -half:
            raise ValueError('Retained unseen-motion conflicts with the unchanged main lane change')
