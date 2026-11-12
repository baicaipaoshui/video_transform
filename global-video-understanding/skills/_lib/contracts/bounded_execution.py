"""Opt-in, source-bound execution approximations; never new visual facts."""
import copy
import re
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import read_json, atomic_json, sha256

KEY = 'bounded_execution'
REVISION = 'bounded-execution.v1'
ADOPTION = 'state/bounded_execution.adoption.json'
PERMISSIONS = {'vehicle_speed_policy_default', 'vehicle_position_policy_default',
               'trajectory_continuation_policy_default'}


def enabled(case):
    case = Path(case)
    policy_path = case/'state/execution_policy.json'
    if policy_path.is_file() and read_json(policy_path).get(KEY) == REVISION:
        return True
    if not (case/ADOPTION).is_file():
        return False
    from _lib.contracts.execution_assumptions import SOURCES, fail
    value = read_json(case/ADOPTION)
    if (value.get('revision') != REVISION or value.get('case_id') != read_json(case/'case.json')['case_id']
            or value.get('sha256') != digest({k:v for k,v in value.items() if k != 'sha256'})
            or not set(SOURCES) <= set(value.get('sources', {}))
            or any(not (case/p).is_file() or sha256(case/p) != h for p,h in value['sources'].items())):
        fail('Bounded execution adoption sources changed')
    return True


def adopt(case):
    """Explicit pre-Behavior adoption; old policies/budgets stay immutable."""
    case = Path(case)
    if enabled(case):
        return read_json(case/ADOPTION) if (case/ADOPTION).exists() else None
    from _lib.contracts import retained_motion, execution_assumptions
    from _lib.runtime.bounded_recovery import require_terminal
    state = read_json(case/'state/progress.json')
    require_terminal(state)
    if (state.get('attempts', {}).get('behavior', 0)
            or any(s.get('stage') == 'behavior' for s in state.get('sessions', []))
            or any((case/p).exists() for p in ('state/behavior.json', 'state/behavior.candidate.json',
                'state/behavior_scope_review.accepted.json', 'final/analysis.json'))):
        execution_assumptions.fail('Bounded execution requires unattempted Behavior')
    retained_motion.adopt(case)
    names = [*execution_assumptions.SOURCES, *[p for p in
        (execution_assumptions.ADOPTION, retained_motion.ADOPTION) if (case/p).exists()]]
    body = dict(revision=REVISION, case_id=read_json(case/'case.json')['case_id'],
                sources={p:sha256(case/p) for p in names})
    value = dict(body, sha256=digest(body))
    atomic_json(case/ADOPTION, value)
    return value


def build(case, saved, behavior, *, pending=None, frames=None, review=None):
    if not enabled(case):
        return None
    from _lib.contracts.conversion_policy import build as policy
    parameters = read_json(Path(case)/'case.json')['parameters']
    allowed = set(policy(parameters, '0'*64, 'highway')['allowed_degradations'])
    if not PERMISSIONS <= allowed:
        return None
    scene = saved['result']['world']['world']
    if scene.get('scene_type') != 'highway' or scene.get('junctions'):
        return None
    record = dict(revision=REVISION, certificate_sha256=saved['sha256'],
        behavior_sha256=digest(behavior), parameters_sha256=digest(parameters),
        adoption_sha256=sha256(Path(case)/ADOPTION) if (Path(case)/ADOPTION).exists() else None,
        permissions=sorted(PERMISSIONS), registry=copy.deepcopy(saved['result']['registry']),
        preferences=copy.deepcopy(parameters.get('fallback_policy', {}).get('straight_preview', {})),
        assumption='Unknown metric values may be planned within existing motion constraints. '
                   'Boundary-touching lane-change timing is an execution approximation, not observed timing.')
    if frames is not None:
        from _lib.contracts.retained_motion import bounded_records
        singles = bounded_records(case, saved, behavior, pending or {}, frames, review)
        if singles:
            record['single_frame_motion'] = singles
    return record


def prepare(source, case, frames):
    """Inherit immutable identity before the existing identity equality gates.

    The raw candidate stays archived. Only a qualified retained-tail actor may
    inherit this field; repairs retain the submitted prose for inspection.
    """
    if not enabled(case):
        return source, []
    from _lib.contracts import census_adjudication, behavior_census_scope, retained_motion
    saved = census_adjudication.proof(case)
    if not saved:
        return source, []
    records = retained_motion.select(case, saved, source,
        behavior_census_scope.pending(case, saved), {f['frame_id']:f for f in frames})
    result = copy.deepcopy(source); changes = []
    for actor in result['actors']:
        record = records.get(actor['actor_id'])
        if not record:
            continue
        value = record['registry']['identity_description']
        if actor.get('identity_description') != value:
            changes.append(dict(action='inherit_registered_identity_description',actor_id=actor['actor_id'],
                before=actor.get('identity_description'),after=value,certificate_sha256=saved['sha256']))
            actor['identity_description'] = value
    return result, changes


def timing(scene, actor_id, source_frame, target_frame, start, end, duration):
    """Only trim a boundary-touching observation envelope, never move witnesses."""
    record = scene.get('metadata', {}).get(KEY)
    if not record or record.get('revision') != REVISION or not PERMISSIONS <= set(record.get('permissions', [])):
        return start, end, None
    if not (0 <= start < end <= duration) or (start > 0 and end < duration):
        return start, end, None
    margin = min(0.5, (end-start)/10)
    lo = start+margin if start == 0 else start
    hi = end-margin if end == duration else end
    return lo, hi, dict(actor_id=actor_id, source_frame_id=source_frame, target_frame_id=target_frame,
        observed_envelope_s=[start,end], execution_interval_s=[lo,hi], revision=REVISION,
        assumption='Exact maneuver timing inside the observed lane-transition envelope is unknown; '
                   'reserve a bounded straight segment at the video boundary. Observed frames are unchanged.')


def request_fields(analysis, request):
    record = analysis.get('metadata', {}).get(KEY)
    if not record:
        return {}
    from _lib.contracts.census_scope import validate_analysis
    if validate_analysis(analysis) is None:
        raise ValueError('Bounded execution requires a replayed source certificate')
    from _lib.behavior.lane_change import from_analysis
    from dataclasses import asdict
    spec = from_analysis(analysis)
    actors = {a['actor_id']:a for a in request['eligible']}
    from _lib.contracts.retained_motion import KEY as retained_key
    retained = set((request.get(retained_key) or {}).get('actors', {}))
    evidence, sources, relations = source_relations(record, actors, request['duration_s'], retained)
    known_sources = {r['source']:r for r in request['evidence']}
    qualitative_points = {(aid, r['time_s']) for r in request['evidence']
                          if r.get('usage') == 'qualitative_only' and r['time_s'] is not None
                          for aid in r['actor_ids'] if aid != 'ego'}
    qualitative_paths = set()
    for row in evidence:
        if any((aid, row['time_s']) in qualitative_points for aid in row['actor_ids']):
            row['usage'] = 'qualitative_only'
            qualitative_paths.add(row['source'])
            sources.pop(row['source'], None)
    relations = [r for r in relations if not any(e['source'] in qualitative_paths for e in r['evidence'])]
    for row in evidence:
        if row['source'] in known_sources and row != known_sources[row['source']]:
            raise ValueError('Source relation differs from its bound Behavior witness')
        if row['source'] not in known_sources:
            request['evidence'].append(row)
    # Pure unknown motion has a typed, source-bound owner. A producer cannot
    # turn it back into an unresolved identity by changing its wording/status.
    unobserved = {}
    for aid, single in record.get('single_frame_motion', {}).items():
        prefixes = [f'/limitations/{i}/' for i, item in enumerate(analysis.get('limitations', []))
                    if item in single['limitations']]
        for row in request['evidence']:
            if row['actor_ids'] == [aid] and any(row['source'].startswith(p) for p in prefixes):
                unobserved[row['source']] = aid
    return {KEY: dict(revision=REVISION, source_record=record, sources=sources,
                     relations=relations, lane_change=asdict(spec) if spec else None,
                     **({'unobserved_sources':unobserved} if unobserved else {}))}


def source_relations(record, actors, duration, retained=()):
    """Pure extraction for both verified requests and explicitly partial audits."""
    # Only explicit, actor-bound witness prose is recognized. Unfamiliar text
    # remains normal relation work; no global no_motion/unknown substitution.
    evidence_rows, sources, relations = [], {}, []
    def needs_motion_review(text):
        # ahead_of does not encode stable following distance or acceleration.
        # Do not mark the whole mixed sentence used after extracting one part.
        return re.search(r'(?:车距|间距).*(?:稳定|保持)|(?:stable|steady|constant) (?:gap|distance)|'
            r'(?:gap|distance).*?(?:stable|steady|constant)|匀速|加速|减速|制动|刹车|accelerat|decelerat|brak', text, re.I)
    for registered in record['registry']:
        aid = registered['actor_id']
        if aid not in actors or aid in retained:
            continue
        first_ahead = None
        ordered = sorted(enumerate(registered.get('evidence', [])),key=lambda row:
                         row[1]['time_s'] if type(row[1].get('time_s')) in (int,float) else float('inf'))
        for i, evidence in ordered:
            text = evidence.get('reason', ''); lower = text.lower()
            t = evidence.get('time_s')
            if evidence.get('status') != 'observed' or type(t) not in (int,float):
                continue
            if needs_motion_review(text):
                continue
            if not 0 <= t <= duration or re.search(r'unknown|unproven|possibly|maybe|\bnot\b|occlud|ego (?:is )?(?:ahead|in front)', lower):
                continue
            # Body-part descriptions at intermediate trailer views do not
            # establish whole-vehicle centre relations.
            kind = ('alongside' if re.search(r'(?:cab|tractor).*?(?:beside|alongside) ego', lower)
                    else 'ahead_of' if re.search(r'ahead[- ](?:right|left|in|near|of ego)|directly ahead', lower)
                    and not re.search(r'beside|alongside', lower)
                    else 'ahead_of' if re.search(r'自车(?:正|左|右)?前方', text)
                    and not re.search(r'不在|并非|可能|未知|并行|旁侧', text) else None)
            if kind is None:
                continue
            path = f'/execution_assumptions/{KEY}/{aid}/evidence/{i}'
            evidence_rows.append(dict(source=path,text=text,actor_ids=[aid],time_s=t))
            sources[path] = dict(kind=kind, actor_id=aid)
            relations.append(dict(type=kind,actor_id=aid,reference_actor_id='ego',time_interval_s=[t,t],
                                  evidence=[dict(source=path,quote=text)]))
            if kind == 'ahead_of':
                if first_ahead and t > first_ahead[0] and re.search(r'ego closes|closer', lower):
                    relations.append(dict(type='approaching',actor_id=aid,reference_actor_id='ego',
                        time_interval_s=[first_ahead[0],t],evidence=[first_ahead[1],dict(source=path,quote=text)]))
                if first_ahead is None:
                    first_ahead = (t,dict(source=path,quote=text))
        # A registered initial description is another time-bound witness.
        # Do not interpret trailer rear/cab sightings as whole-centre passing.
        text = registered.get('identity_description', '')
        points = actors[aid].get('trajectory', []) if isinstance(actors, dict) else []
        # Analysis may prepend a 0s execution point reusing a later frame ID.
        # Bind the prose to the Registry observation time, never that padding.
        first = next((e for e in registered.get('evidence', [])
                      if e.get('status') == 'observed'
                      and e.get('frame_id') == registered.get('first_seen_frame_id')
                      and type(e.get('time_s')) in (int,float)
                      and 0 <= e['time_s'] <= duration), None)
        if (first and re.search(r'自车(?:正|左|右)?前方', text)
                and not re.search(r'不在|并非|可能|未知', text) and not needs_motion_review(text)):
            path = f'/execution_assumptions/{KEY}/{aid}/identity_description'
            evidence_rows.append(dict(source=path,text=text,actor_ids=[aid],time_s=first['time_s']))
            sources[path] = dict(kind='ahead_of',actor_id=aid)
            relations.append(dict(type='ahead_of',actor_id=aid,reference_actor_id='ego',
                time_interval_s=[first['time_s'],first['time_s']],evidence=[dict(source=path,quote=text)]))
            if first_ahead is None or first['time_s'] < first_ahead[0]:
                first_ahead = (first['time_s'],dict(source=path,quote=text))
        for i, point in enumerate(points):
            text = point.get('detail', ''); t = point.get('time_s')
            if type(t) not in (int,float) or not 0 <= t <= duration or not isinstance(text,str):
                continue
            # Only explicit ego-relative clauses, not arbitrary words such as
            # "left/front" inside lane-boundary descriptions or other actors.
            clause = text.split('；')[-1]
            if re.search(r'不在|并非|可能|不确定|无法判断|不能确认', clause) or needs_motion_review(text):
                continue
            kind = ('ahead_of' if re.search(r'自车正前方|本车在前', clause)
                    else 'alongside' if re.search(r'自车并行|驾驶室.*在自车右侧', clause) else None)
            path = f'/actors/{aid}/trajectory/{i}/detail'
            citation = dict(source=path,quote=text)
            if kind:
                evidence_rows.append(dict(source=path,text=text,actor_ids=[aid],time_s=t))
                sources[path] = dict(kind=kind, actor_id=aid)
                relations.append(dict(type=kind,actor_id=aid,reference_actor_id='ego',
                    time_interval_s=[t,t],evidence=[citation]))
                if kind == 'ahead_of' and first_ahead is None:
                    first_ahead = (t,citation)
            if first_ahead and t > first_ahead[0] and re.search(r'车距(?:继续)?缩小|自车接近', clause):
                if path not in sources:
                    evidence_rows.append(dict(source=path,text=text,actor_ids=[aid],time_s=t))
                    sources[path] = dict(kind='approaching',actor_id=aid)
                relations.append(dict(type='approaching',actor_id=aid,reference_actor_id='ego',
                    time_interval_s=[first_ahead[0],t],evidence=[first_ahead[1],citation]))
    return evidence_rows, sources, relations


def complete_relations(request, coverage, relations):
    record = request.get(KEY)
    if not record:
        return coverage, relations
    owned = record['sources']
    unobserved = record.get('unobserved_sources', {})
    for row in coverage:
        if row['source'] in unobserved and row['status'] == 'identity_unresolved':
            raise ValueError('New identity conflict cannot be repaired as unobserved motion')
        if row['source'] in owned and row['status'] in {'identity_unresolved','motion_unresolved'}:
            raise ValueError('New conflict with a bound source relation requires review')
    # Worker proposals citing these sources must agree; they cannot silently
    # replace, extend or invert the program's witnessed point constraints.
    expected = record['relations']
    for row in relations:
        if any(e['source'] in unobserved for e in row['evidence']):
            raise ValueError('Unobserved motion cannot supply an observed temporal relation')
        if any(e['source'] in owned for e in row['evidence']) and row not in expected:
            raise ValueError('Worker relation conflicts with the source-bound point relation')
    result = [copy.deepcopy(r) for r in relations if r not in expected]
    result.extend(copy.deepcopy(expected))
    cover = [copy.deepcopy(r) for r in coverage if r['source'] not in owned and r['source'] not in unobserved]
    cover.extend(dict(source=s,status='used',reason=REVISION+': inherited confirmed witness') for s in sorted(owned))
    cover.extend(dict(source=s,status='unobserved',reason=REVISION+': source-bound unknown prefix/tail')
                 for s in sorted(unobserved))
    from .motion_witnesses import complete
    return complete(request, cover, result)


def check_single_frame_bounds(request, profiles):
    """Simulation envelopes, deliberately distinct from observed relations."""
    from _lib.contracts import semantic_motion as m
    records = request.get(KEY, {}).get('source_record', {}).get('single_frame_motion', {})
    for aid, record in records.items():
        if aid not in profiles or 'ego' not in profiles:
            continue
        first, ego = profiles[aid], profiles['ego']
        if record['kind'] == 'near_side':
            if abs(m.state(first, 0)[0]-m.state(ego, 0)[0]) > 2:
                raise ValueError('Single-frame body-fragment simulation envelope changed')
            if record.get('relative_motion') == 'falling_behind':
                t = request['duration_s']
                if (m.state(first,t)[0]-m.state(ego,t)[0]
                        > m.state(first,0)[0]-m.state(ego,0)[0]-m.POLICY['closing_progress_m']):
                    raise ValueError('Single-frame continuation loses the stated falling-behind trend')
        elif record['kind'] == 'remote':
            times = m.critical_times(first, ego, 0, request['duration_s'])
            if min(m.state(first,t)[0]-m.state(ego,t)[0] for t in times) < 150:
                raise ValueError('Single-frame remote simulation entered the near corridor')


class PlanningIncomplete(ValueError):
    code = 'execution_assumption_infeasible'

    def __init__(self, reason, attempts):
        super().__init__(reason)
        self.diagnostics = dict(rule_id='motion.bounded_search_incomplete', retry_kind='policy_change',
                                owner='delivery', attempts=attempts, message=reason)


def check_clearance(request, profiles):
    """Conservative changing-lane corridor guard; final lateral audit still runs."""
    from _lib.contracts import semantic_motion as m
    check_single_frame_bounds(request, profiles)
    from .motion_witnesses import check
    check(request, profiles)
    spec = request[KEY]['lane_change']
    if not spec or spec['actor_id'] not in profiles:
        return
    actors = {a['actor_id']:a for a in request['eligible']}
    for aid, actor in actors.items():
        if aid == spec['actor_id'] or aid not in profiles:
            continue
        lanes = {p.get('lane_id') for p in actor['trajectory']} - {None}
        if len(lanes) != 1:
            raise ValueError('Bounded planner supports fixed-lane surrounding vehicles only')
        lane = next(iter(lanes))
        if lane not in {spec['source_lane_id'],spec['target_lane_id']}:
            continue
        lo = 0 if lane == spec['source_lane_id'] else spec['start_s']
        hi = request['duration_s'] if lane == spec['target_lane_id'] else spec['end_s']
        first,second = profiles[aid],profiles[spec['actor_id']]
        gaps = [m.state(first,t)[0]-m.state(second,t)[0] for t in m.critical_times(first,second,lo,hi)]
        half = (m.length(actor)+m.length(actors[spec['actor_id']]))/2+m.POLICY['clearance_m']
        if min(gaps) <= half and max(gaps) >= -half:
            raise ValueError('Bounded plan conflicts with the unchanged lane-change corridor')


def plan(request, relations, existing=(), *, max_attempts=20000):
    """Finite joint search, retaining accepted partial plans.

    Legacy requests search constant speeds. Explicit witness requests also
    try a small family of source-timed piecewise profiles.

    Exhaustion means this bounded family found no solution, not proof that the
    scene is impossible. No model task or repair-budget operation occurs here.
    """
    from _lib.contracts import semantic_motion as m, retained_motion
    if KEY not in request:
        return None
    actors = {a['actor_id']:a for a in request['eligible']}
    owned = set((request.get(retained_motion.KEY) or {}).get('actors', {}))
    seed = [copy.deepcopy(p) for p in existing if p['actor_id'] not in owned]
    initial = m.validate_plans(request, seed, partial=True)
    if len(actors) > 8 or 'ego' not in actors:
        raise PlanningIncomplete('Bounded planner supports ego and at most seven surrounding vehicles', 0)
    order = sorted(set(actors)-set(initial)-owned, key=lambda aid:(aid!='ego',aid))
    count = 0

    def candidates(aid, chosen):
        actor = actors[aid]; condition = request['initial_conditions'].get(aid) or {}
        preferred = request[KEY].get('source_record', {}).get('preferences', {})
        default_speed = m.number(preferred.get('ego_speed_kmh')) or 80.
        base = next((p['speed_knots'][0]['speed_kmh'] for p in chosen if p['actor_id']=='ego'),default_speed)
        speeds = [m.number(p.get('speed_mps')) for p in actor['trajectory']]
        speeds = [v*3.6 for v in speeds if v is not None]
        known = m.number(condition.get('speed_kmh'))
        if known is not None:
            speeds.append(known)
        from . import motion_witnesses as witnesses
        if not speeds and aid != 'ego':
            for x, knots, reason in witnesses.candidates(request, aid, base,
                    [20.,30.,50.,75.,100.,150.,200.,300.,400.,15.,25.,40.,10.,0.,-20.,-50.,-100.]):
                yield dict(actor_id=aid,initial_x_m=x,
                    speed_knots=[dict(time_s=t,speed_kmh=v) for t,v in knots],
                    assumption=f'{witnesses.REVISION}: bounded piecewise simulation; '+reason+
                    ' Absolute speed, acceleration, initial distance and unseen continuation are execution assumptions.')
        # Measured differing speeds cannot be represented by this first,
        # deliberately small constant-speed planner. Do not flatten them.
        if speeds and max(speeds)-min(speeds)>1e-6:
            return
        values = speeds[:1] if speeds else ([base,base-20,base+20,base-40] if aid=='ego' else
                     [base,base-5,base+5,base-8,base+8,base-10,base+10,base-20,base+20])
        for speed in values:
            if not 0 <= speed <= m.POLICY['max_speed_kmh']:
                continue
            known_x = m.number((condition.get('position_ego_m') or {}).get('x'))
            anchors = [(m.time(p),m.number((p.get('position_ego_m') or {}).get('x')))
                       for p in actor['trajectory']]
            intercepts = [x-speed/3.6*t for t,x in anchors if x is not None and t is not None]
            offsets = ([known_x] if known_x is not None else intercepts[:1] if intercepts else
                       [0.] if aid=='ego' else [20.,30.,50.,75.,100.,150.,200.,300.,400.,15.,25.,40.,10.,0.,-20.,-50.,-100.])
            if known_x is None and not intercepts and aid != 'ego' and re.search(
                    r'far ahead|vanishing point',actor.get('identity_description','').lower()):
                # A preference only, not an invented measured distance.
                offsets = [200.,300.,400.,150.,100.,75.,50.,*offsets]
            single = request[KEY].get('source_record', {}).get('single_frame_motion', {}).get(aid)
            if single and known_x is None and not intercepts:
                offsets = [0.] if single['kind'] == 'near_side' else [200.,300.,400.,150.]
            for x in offsets:
                yield dict(actor_id=aid,initial_x_m=x,speed_knots=[dict(time_s=0,speed_kmh=speed),
                    dict(time_s=request['duration_s'],speed_kmh=speed)],
                    assumption=f'{REVISION}: bounded constant-speed simulation, initial_x={x}m, speed={speed}km/h; '
                               'unknown prefix/tail and metric values are assumptions, not observed motion.'
                               + (' '+single['assumption'] if single else ''))

    def search(index, chosen):
        nonlocal count
        if count >= max_attempts:
            return None
        if index == len(order):
            count += 1
            try:
                plans = retained_motion.complete_plans(request, chosen, relations)
                profiles = m.validate_plans(request, plans)
                m.check_relations(request, relations, profiles)
                m.check_lane_clearance(request, profiles)
                check_clearance(request, profiles)
                return plans
            except ValueError:
                return None
        aid = order[index]
        for candidate in candidates(aid, chosen):
            count += 1
            if count >= max_attempts:
                return None
            trial = [*chosen,candidate]
            try:
                profiles = m.validate_plans(request, trial, partial=True)
                subset = dict(request,eligible=[a for a in request['eligible'] if a['actor_id'] in profiles])
                m.check_relations(subset,[r for r in relations if
                    {r['actor_id'],r['reference_actor_id']} <= set(profiles)],profiles)
                m.check_lane_clearance(subset,profiles)
                check_clearance(subset,profiles)
            except ValueError:
                continue
            result = search(index+1,trial)
            if result is not None:
                return result
        return None

    result = search(0,seed)
    if result is None:
        raise PlanningIncomplete('No solution found in the bounded motion search; '
                                 'actors, observations and main action preserved',count)
    return result
