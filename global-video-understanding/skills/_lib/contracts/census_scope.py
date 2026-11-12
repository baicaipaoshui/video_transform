"""Bounded observation-level scope decisions; never resolve an identity by policy."""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json, atomic_json, sha256

REVISION = 'world-census-adjudication.v4'
RULE = 'stationary_isolated_offroad.v1'
REVIEW = 'state/behavior_scope_review.accepted.json'
MAX_GAP_S = 1.5


def coverage_frames(case, selected):
    """Add existing images only when the union fits the frozen 12-image ceiling."""
    from _lib.world.actor_facts import frames_for
    case = Path(case)
    decoded = sorted(frames_for(case), key=lambda f:f['time_s'])
    by_id = {f['frame_id']:f for f in decoded}
    required = set(selected) | {f['frame_id'] for f in read_json(case/'state/global_frames.json')['frames']}
    for batch in read_json(case/'manifest.json').get('batches', []):
        if (case/'batches'/f"{batch['batch_id']}.json").is_file():
            required.update(f['frame_id'] for f in batch.get('frames', []))
    required.update((decoded[0]['frame_id'], decoded[-1]['frame_id']))
    while len(required) <= 12:
        ordered = sorted(required, key=lambda fid:by_id[fid]['time_s'])
        gap = next(((a,b) for a,b in zip(ordered,ordered[1:])
                    if by_id[b]['time_s']-by_id[a]['time_s'] > MAX_GAP_S), None)
        if gap is None:
            return required
        first, last = (by_id[fid]['time_s'] for fid in gap)
        choices = [f for f in decoded if first < f['time_s'] <= min(last,first+MAX_GAP_S)]
        if not choices:
            break
        required.add(choices[-1]['frame_id'])
    return set(selected)  # Identity adjudication still works; no scope claim is eligible.


class ScopeReviewBlocked(ContractError):
    code = 'census_scope_review_required'

    def __init__(self, message, issue_ids=()):
        super().__init__(message, rule_id='identity.census.scope_review')
        self.diagnostics = dict(rule_id=self.rule_id, owner='behavior',
            issue_ids=list(issue_ids), retry_kind='scope_adjudication', message=message)


class ScopeReviewIncomplete(ScopeReviewBlocked):
    """An omission, not evidence authorizing census revocation.

    Only the ordinary Behavior owner may promote this to a budgeted completion
    after all other gates pass. Other writers retain their existing stop policy.
    """

    def __init__(self, issue_ids):
        super().__init__('Scope review omits required observation answers', sorted(issue_ids))
        self.diagnostics['retry_kind'] = 'scope_review_completion'


class ScopeReviewCoverageError(ScopeReviewIncomplete):
    """Missing known frames may be supplemented, never treated as exclusion."""

    def __init__(self, issue_id, field, required, actual):
        missing = sorted(set(required) - set(actual))
        label = 'sightings proof' if field == 'scope_proof/sightings' else 'evidence_frame_ids index'
        ScopeReviewBlocked.__init__(self,
            f'Pending census {label} omits frozen frames: {", ".join(missing)}', [issue_id])
        self.rule_id = ('identity.census.scope_proof_frames' if field == 'scope_proof/sightings'
                        else 'identity.census.scope_evidence_frames')
        self.diagnostics.update(rule_id=self.rule_id, field_path='/'+field,
            expected=sorted(required), actual=sorted(actual), missing_frame_ids=missing,
            retry_kind='scope_review_completion')


def schema():
    def obj(fields):
        return dict(type='object', additionalProperties=False,
                    properties=fields, required=list(fields))
    text = dict(type='string', minLength=1)
    sight = obj(dict(frame_id=text, time_s=dict(type='number', minimum=0),
        location_evidence=text, stationary_evidence=text, barrier_evidence=text,
        outside_carriageway=dict(const=True), stationary=dict(const=True),
        physically_isolated=dict(const=True), access_to_carriageway=dict(const=False)))
    return obj(dict(rule_id=dict(const=RULE),
        window_s=dict(type='array', minItems=2, maxItems=2, items=dict(type='number', minimum=0)),
        sightings=dict(type='array', minItems=2, maxItems=12, items=sight)))


def plan(case, frames):
    """Coverage is an inspection schedule, not proof of motion between images."""
    duration = read_json(Path(case)/'state/probe.json')['probe']['duration_s']
    from _lib.world.actor_facts import frames_for
    decoded = sorted(frames_for(case), key=lambda f:f['time_s'])
    ids = {f['frame_id'] for f in frames}
    covered = [f for f in decoded if f['frame_id'] in ids]
    times = [f['time_s'] for f in covered]
    eligible = (len(times) >= 2 and times[0] <= MAX_GAP_S
                and duration-times[-1] <= MAX_GAP_S
                and all(0 < b-a <= MAX_GAP_S for a,b in zip(times,times[1:])))
    # Every accepted Batch/global image in the Case must be inspected as well.
    required = {f['frame_id'] for f in read_json(Path(case)/'state/global_frames.json')['frames']}
    manifest = read_json(Path(case)/'manifest.json')
    for batch in manifest.get('batches', []):
        if (Path(case)/'batches'/f"{batch['batch_id']}.json").is_file():
            required.update(f['frame_id'] for f in batch.get('frames', []))
    eligible &= required <= ids
    return dict(rule_id=RULE, window_s=[0., duration], max_gap_s=MAX_GAP_S,
        eligible=bool(eligible), required_frame_ids=[f['frame_id'] for f in covered])


def validate_claim(packet, row, decision, world):
    from _lib.contracts.census_adjudication import fail
    schedule = packet['scope_plan']; claim = decision['scope_claim']
    if (not schedule['eligible'] or claim is None or decision['vehicle_key'] is not None
            or decision['separation'] is not None or claim['window_s'] != schedule['window_s']):
        fail('Observation-only needs an eligible full-Case isolated-offroad proof')
    frames = {f['frame_id']:f for f in packet['frames']}
    sights = {s['frame_id']:s for s in claim['sightings']}
    required = set(schedule['required_frame_ids']) | {row['frame_id']}
    if (len(sights) != len(claim['sightings']) or set(sights) != required
            or not required <= set(decision['evidence_frame_ids'])
            or any(s['time_s'] != frames[fid]['time_s'] for fid,s in sights.items())):
        fail('Observation-only proof omits required frame/time coverage')
    if any(not s[k].strip() for s in sights.values()
           for k in ('location_evidence','stationary_evidence','barrier_evidence')):
        fail('Observation-only needs explicit per-frame visual reasons')
    # A local observation must be distinguished from every registered vehicle.
    required_actors = {a['actor_id'] for a in packet['actors'] if a['actor_id'] != 'ego'}
    if not required_actors <= {r['actor_id'] for r in decision['candidate_refutations']}:
        fail('Observation-only needs anchored refutations of registered vehicle identities')
    if any(s['lane_id'] is not None for r in decision['candidate_refutations'] for s in r['subject_sightings']):
        fail('Isolated offroad subject cannot simultaneously assert a driving lane')
    # Existing visual refutation validation checks source tracking/co-visibility.
    # No motion coordinates, lane changes or negative event-list inference occur here.
    for collection in ('questions_to_verify', 'ambiguities'):
        for item in world.get(collection, []):
            if row['issue_id'] in item.get('census_issue_ids', []) and item.get('world_paths'):
                fail('Observation-only cannot discharge a road or facility dependency')
    return dict(issue_id=row['issue_id'], batch_id=row['batch_id'],
        frame_id=row['frame_id'], object_id=row['object_id'], rule_id=RULE,
        reason=decision['reason'], evidence_frame_ids=sorted(required),
        window_s=claim['window_s'], proof=copy.deepcopy(claim))


def active(saved):
    from _lib.contracts.census_history import rounds
    result = {}
    for item in rounds(saved):
        for iid in item['result'].get('revoked_scope_issue_ids', []):
            result.pop(iid, None)
        for claim in item['result'].get('observation_only', []):
            result[claim['issue_id']] = dict(claim, certificate_sha256=item['sha256'])
    return result


def context(case):
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    claims = active(saved)
    from _lib.contracts.behavior_census_scope import pending
    claims.update(pending(case, saved))
    return dict(certificate_sha256=saved['sha256'] if saved else None,
                observations=list(claims.values()))


def bind_request(case, packet):
    """Freeze scope and its existing evidence in a bounded repair request."""
    from _lib.contracts.census_adjudication import proof
    from _lib.contracts.census_history import certificate_paths
    from _lib.runtime.fact_versions import census_path
    from _lib.behavior.compile import evidence_frames
    case = Path(case)
    value = context(case)
    if not value['observations']:
        return
    packet['scope_context'] = value
    packet['frames'] = evidence_frames(case)
    if any(o.get('pending_scope') for o in value['observations']):
        packet['scope_output_requirements'] = dict(
            excluded_required_frame_ids=[f['frame_id'] for f in packet['frames']])
    paths = {str(census_path(case)), *certificate_paths(proof(case))}
    from _lib.contracts.behavior_census_scope import ADOPTION
    if (case/ADOPTION).is_file():
        paths.add(str(case/ADOPTION))
    from _lib.contracts.census_execution import HINTS, enabled as execution_enabled
    if execution_enabled(case) and (case/HINTS).is_file():
        paths.add(str(case/HINTS))
    paths.update(str(case/name) for name in ('state/execution_policy.json',
        'state/probe.json', 'state/global_frames.json', 'manifest.json') if (case/name).is_file())
    for frame in packet['frames']:
        for row in [frame, *frame.get('variants', [])]:
            paths.update(row[key] for key in ('path', 'source_path') if row.get(key))
    packet.setdefault('input_hashes', {}).update({str(p):sha256(p) for p in sorted(paths)})


def verify_request(case, packet):
    """Old requests cannot acquire scope authority after model submission."""
    value = context(case)
    if packet.get('scope_context') != value and (value['observations'] or 'scope_context' in packet):
        from _lib.behavior.input_binding import InputChanged
        raise InputChanged('Scope repair request lacks the current frozen scope context')


def split(raw):
    data = copy.deepcopy(raw)
    review = data.pop('census_scope_review', None) if isinstance(data, dict) else None
    return data, review


def validate_confirmation_row(case, behavior, row, claim, frames):
    """Shared admission for an already excluded observation's review row."""
    iid = row['issue_id']
    actor_ids = {a['actor_id'] for a in behavior['actors']}
    event_ids = {e['event_id'] for e in behavior['events']}
    def names_issue(node, iid):
        if isinstance(node, str): return node == iid
        if isinstance(node, list): return any(names_issue(v, iid) for v in node)
        if isinstance(node, dict): return any(names_issue(v, iid) for v in node.values())
        return False
    if (set(row) != {'issue_id','outcome','reason','evidence_frame_ids','affected_actor_ids','affected_event_ids'}
            or row['outcome'] not in {'confirmed','challenged','unknown'}
            or not isinstance(row['reason'], str) or not row['reason'].strip()
            or any(not isinstance(row[k], list) or any(not isinstance(v, str) or not v for v in row[k])
                   or len(set(row[k])) != len(row[k])
                   for k in ('evidence_frame_ids','affected_actor_ids','affected_event_ids'))
            or not set(claim['evidence_frame_ids']) <= set(row['evidence_frame_ids']) <= set(frames)
            or not set(row['affected_actor_ids']) <= actor_ids
            or not set(row['affected_event_ids']) <= event_ids):
        raise ScopeReviewBlocked('Scope review evidence or affected entities invalid', [iid])
    from _lib.contracts.execution_gaps import dependencies as execution_dependencies
    dependencies = execution_dependencies(case, behavior)
    if (row['outcome'] != 'confirmed' or row['affected_actor_ids'] or row['affected_event_ids']
            or names_issue(dependencies, iid)):
        return False
    return True


def validate_review(case, behavior, review, *, saved=None, frames=None, historical=False,
                    allow_pending=False):
    """Require an explicit same-task answer for every active unknown observation.

    allow_pending is for the isolated core consumer: validate every answer and
    return unresolved choices without committing or granting strict admission.
    """
    if historical and (saved is None or frames is None):
        raise ScopeReviewBlocked('Historical scope review requires a replayed ancestor and frozen frames')
    from _lib.contracts.census_adjudication import proof
    saved = saved or proof(case)
    claims = active(saved)
    from _lib.contracts import behavior_census_scope as deferred
    pending = deferred.pending(case, saved)
    claims.update(pending)
    from _lib.contracts import execution_assumptions
    assumptions_enabled = bool(saved) and execution_assumptions.enabled(case)
    if not claims and not assumptions_enabled:
        if review is not None:
            raise ScopeReviewBlocked('Scope review supplied without active observations')
        return None
    from _lib.contracts import unused_association
    automatic = unused_association.enabled(case)
    if review is None:
        if not automatic and not assumptions_enabled:
            raise ScopeReviewIncomplete(claims)
        rows = []
    else:
        if (not isinstance(review, dict) or set(review) != {'certificate_sha256','decisions'}
                or review['certificate_sha256'] != saved['sha256']):
            raise ScopeReviewBlocked('Scope review must bind the current census certificate', claims)
        rows = review['decisions']
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ScopeReviewBlocked('Scope review decisions must be explicit', claims)
    if any(not isinstance(r.get('issue_id'), str) for r in rows):
        raise ScopeReviewBlocked('Scope review requires observation issue IDs', claims)
    by_id = {r.get('issue_id'):r for r in rows}
    if len(by_id) != len(rows) or not set(by_id) <= set(claims):
        raise ScopeReviewBlocked('Scope review must cover every observation exactly once', claims)
    from _lib.behavior.compile import evidence_frames
    frames = {f['frame_id']:f for f in (evidence_frames(case) if frames is None else frames)}
    problems = []
    from _lib.contracts.execution_gaps import enabled as gap_recovery, diagnostics
    failures = []
    excluded = list(active(saved).values())
    from _lib.contracts import census_relevance
    grouped = (census_relevance.grouped_views(behavior, pending, by_id)
               if census_relevance.enabled(case) else {})
    for iid, row in by_id.items():
        if iid in pending:
            try:
                value = deferred.validate_decision(case, grouped.get(iid, behavior), row, pending[iid], frames,
                    world=saved['result']['world'], registry=saved['result']['registry'])
            except ContractError as error:
                if not gap_recovery(case):
                    raise
                failures.append((iid, error))
                continue
            if value:
                excluded.append(value)
            continue
        if not validate_confirmation_row(case, behavior, row, claims[iid], frames):
            problems.append(iid)
    if failures:
        # Keep the strongest error class: a content conflict cannot authorize
        # a coverage-only completion just because an earlier row omitted IDs.
        error = next((e for _,e in failures if not isinstance(e, ScopeReviewIncomplete)), failures[0][1])
        error.diagnostics = dict(getattr(error, 'diagnostics', {}),
            scope_validation_issues=[dict(issue_id=iid, message=str(e),
                details=getattr(e, 'diagnostics', {})) for iid,e in failures],
            execution_gap_issues=diagnostics(case, saved['result']['world'], behavior, rows))
        raise error
    program = (unused_association.build(case, behavior, pending, frames, saved, review)
               if automatic else None)
    assumptions = (execution_assumptions.build(case, behavior, pending, frames, saved, review)
                   if assumptions_enabled else None)
    from _lib.contracts.inherited_opposing import REVISION as INHERITED
    excluded.extend(dict(r['observation'], scope_origin=INHERITED,
                         source_receipt_sha256=assumptions['sha256'])
                    for r in (assumptions or {}).get('observations', {}).values()
                    if r.get('basis') == INHERITED)
    program_ids = (set((program or {}).get('records', {})) |
                   set((assumptions or {}).get('observations', {})))
    # Validate supplied answers before classifying partial coverage as omission.
    # Even a partial challenge cannot authorize revocation of an incomplete review.
    if set(by_id) | program_ids != set(claims):
        error = ScopeReviewIncomplete(set(claims) - set(by_id) - program_ids)
        if program_ids:
            error.diagnostics['program_handled_issue_ids'] = sorted(program_ids)
        raise error
    if problems:
        error = ScopeReviewBlocked('Behavior challenges or cannot confirm scope exclusion', problems)
        error.scope_review = review
        error.behavior = behavior
        raise error
    from _lib.world.gaps import IdentityEvidenceGap
    try:
        execution_dispositions = deferred.blocked(pending, by_id, behavior, review, case=case,
            **({'snapshot':saved, 'frames':frames} if historical else {}),
            **({'program':program} if program else {}),
            **({'assumptions':assumptions} if assumptions else {}),
            **({'allow_pending':True} if allow_pending else {}))
    except IdentityEvidenceGap as error:
        if gap_recovery(case):
            gaps = diagnostics(case, saved['result']['world'], behavior, rows)
            if assumptions:
                from _lib.contracts.census_relevance import observation_issue_disposed
                receipt = error.execution_dispositions
                handled = {r['issue_id'] for r in receipt['choices'] if r['handling'] != 'needs_evidence'}
                limits = [(item.get(key), item) for source, collection, key in (
                    (saved['result']['world'], 'questions_to_verify', 'question_id'),
                    (saved['result']['world'], 'ambiguities', 'ambiguity_id'),
                    (behavior, 'semantic_limitations', 'limitation_id')) for item in source.get(collection, [])]
                disposed = {iid for iid,item in limits if observation_issue_disposed(item, receipt)}
                gaps = [g for g in gaps if not (
                    g.get('rule_id') == 'execution.lane_unobserved' and g.get('actor_id') in assumptions['lanes']
                    or g.get('rule_id') == 'identity.census.scope_pending' and g.get('issue_id') in handled
                    or g.get('rule_id') == 'identity.temporal_binding' and g.get('issue_id') in disposed)]
                error.diagnostics['program_handled_issue_ids'] = sorted(handled)
                error.diagnostics['assumed_lane_actor_ids'] = sorted(assumptions['lanes'])
            error.diagnostics['execution_gap_issues'] = gaps
        raise
    value = dict(certificate_sha256=saved['sha256'], behavior_digest=digest(behavior), review=review)
    if pending:
        value['excluded_observations'] = excluded
        if (Path(case)/deferred.ADOPTION).is_file():
            value['adoption_sha256'] = sha256(Path(case)/deferred.ADOPTION)
    if execution_dispositions and (pending or assumptions):
        value['execution_dispositions'] = execution_dispositions
    if not historical:
        from _lib.behavior.census_read_view import source_coverage
        coverage = source_coverage(case, pending, execution_dispositions)
        if coverage is not None:
            value['census_source_coverage'] = coverage
    return dict(value, sha256=digest(value))


def commit_review(case, value):
    if value:
        atomic_json(Path(case)/REVIEW, value)


def verified_review(case, behavior):
    from _lib.contracts.census_adjudication import proof
    saved = proof(case)
    from _lib.contracts import execution_assumptions
    if not context(case)['observations'] and not (saved and execution_assumptions.enabled(case)):
        return None
    path = Path(case)/REVIEW
    if not path.is_file():
        raise ScopeReviewBlocked('Final delivery requires explicit Behavior scope review', active(saved))
    value = read_json(path); body = dict(value); signature = body.pop('sha256', None)
    if (signature != digest(body) or value['behavior_digest'] != digest(behavior)
            or value['certificate_sha256'] != saved['sha256']
            or validate_review(case, behavior, value['review'], saved=saved) != value):
        raise ScopeReviewBlocked('Behavior scope review changed or belongs to stale inputs')
    return value


def bind_audit(case, behavior, audit):
    """Read-only decoration shared by online projection and offline replay."""
    case = Path(case).resolve()
    checked = verified_review(case, behavior)
    if not checked:
        return None
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, census_path, current_facts
    audit['excluded_observations'] = checked.get('excluded_observations', context(case)['observations'])
    if checked.get('execution_dispositions'):
        audit['execution_dispositions'] = checked['execution_dispositions']
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    sources = (view.world_path, case/'state/behavior.json', view.registry_path) if view else tuple(
        case/name for name in ('state/world_context.json','state/behavior.json','state/actor_registry.json'))
    audit['census_scope_binding'] = dict(certificate_path=str(census_path(case).resolve()),
        certificate_file_sha256=sha256(census_path(case)), review_file_sha256=sha256(case/REVIEW),
        review_sha256=checked['sha256'], source_hashes={str(path.relative_to(case)):sha256(path)
            for path in sources})
    return checked


def validate_analysis(data):
    quality = data.get('quality_report', {})
    audit = quality.get('execution_actor_scope') or quality.get('offline_execution_actor_scope') or {}
    link = audit.get('census_scope_binding')
    if not link:
        if data.get('metadata', {}).get('bounded_execution'):
            from _lib.contracts import core_projection_sources
            if data.get('metadata', {}).get(core_projection_sources.ANALYSIS_BINDING_KEY):
                return core_projection_sources.validate_analysis_binding(data)
            raise ScopeReviewBlocked('Bounded execution metadata lacks its final source binding')
        if audit.get('excluded_observations') or any((audit.get('execution_dispositions') or {}).get(k)
                for k in ('unused_association', 'execution_assumptions')):
            raise ScopeReviewBlocked('Observation exclusions lack their final scope binding')
        return None
    certificate = Path(link['certificate_path'])
    state = next((parent for parent in certificate.parents if parent.name == 'state'), None)
    if state is None:
        raise ScopeReviewBlocked('Invalid census scope certificate path')
    case = state.parent
    from _lib.runtime.fact_versions import census_path, ROOT as FACT_ROOT, current_facts
    if certificate != census_path(case):
        raise ScopeReviewBlocked('Invalid census scope certificate path')
    if (not certificate.is_file() or sha256(certificate) != link['certificate_file_sha256']
            or not (case/REVIEW).is_file() or sha256(case/REVIEW) != link['review_file_sha256']):
        raise ScopeReviewBlocked('Conversion scope certificate or review changed')
    behavior = read_json(case/'state/behavior.json')
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    sources = (view.world_path, case/'state/behavior.json', view.registry_path) if view else tuple(
        case/name for name in ('state/world_context.json','state/behavior.json','state/actor_registry.json'))
    expected_hashes = {str(path.relative_to(case)):sha256(path) for path in sources}
    if link.get('source_hashes') != expected_hashes:
        raise ScopeReviewBlocked('Analysis scope source snapshots changed')
    checked = verified_review(case, behavior)
    if checked is None or checked['sha256'] != link['review_sha256']:
        raise ScopeReviewBlocked('Conversion scope review no longer authorizes exclusions')
    value = context(case)
    observations = checked.get('excluded_observations', value['observations'])
    if audit.get('excluded_observations') != observations:
        raise ScopeReviewBlocked('Analysis observation exclusions differ from the current certificate')
    from _lib.runtime.execution_scope import project
    from _lib.contracts.behavior_census_scope import excluded_groups
    policy = read_json(case/'state/execution_policy.json')
    from _lib.contracts.execution_gaps import enabled as gap_recovery
    from _lib.contracts import behavior_handoff
    from _lib.behavior.compile import evidence_frames
    projected_world, projected, expected_audit = project(read_json(case/'state/world_context.json'), behavior,
        policy_id=policy['execution_scope'], excluded_group_ids=excluded_groups(case, checked),
        handoff_frames=evidence_frames(case) if behavior_handoff.enabled(case) else None,
        lane_resolution_enabled=gap_recovery(case),
        execution_dispositions=checked.get('execution_dispositions'))
    if audit.get('excluded_actors') != expected_audit['excluded_actors']:
        raise ScopeReviewBlocked('Observation scope cannot authorize additional Actor exclusions')
    if audit.get('execution_dispositions') != expected_audit.get('execution_dispositions'):
        raise ScopeReviewBlocked('Observation execution assumptions differ from the current certificate')
    from _lib.contracts.bounded_execution import KEY
    if data.get('metadata', {}).get(KEY) != projected_world['world'].get('metadata', {}).get(KEY):
        raise ScopeReviewBlocked('Bounded execution metadata differs from the replayed source authority')
    ids = [data.get('ego', {}).get('actor_id'), *[a.get('actor_id') for a in data.get('actors', [])]]
    if len(ids) != len(set(ids)) or set(ids) != {a['actor_id'] for a in projected['actors']}:
        raise ScopeReviewBlocked('Observation scope cannot omit or invent registered execution Actors')
    if any(r.get('handling') == 'retained_unresolved' for r in
           (checked.get('execution_dispositions') or {}).get('choices', [])) or any((checked.get('execution_dispositions') or {}).get(k)
           for k in ('unused_association', 'execution_assumptions')):
        from _lib.behavior.compile import validate_behavior_evidence
        delivery, _ = validate_behavior_evidence(projected)
        if data.get('events', []) != delivery.get('events', []):
            raise ScopeReviewBlocked('Unused-association analysis changed accepted actions')
        actual = {a['actor_id']: a for a in [data['ego'], *data.get('actors', [])]}
        if any(actual[a['actor_id']].get('trajectory') != a.get('trajectory')
               for a in delivery['actors']):
            raise ScopeReviewBlocked('Unused-association analysis changed observed trajectories')
    return digest(dict(observations=observations, binding=link))


def prompt(case, *, frozen_context=None, frames=None):
    from _lib.contracts import behavior_census_scope
    value = context(case) if frozen_context is None else frozen_context
    if not value['observations']:
        return ''
    import json
    pending_prompt = ''
    if any(o.get('pending_scope') for o in value['observations']):
        from _lib.behavior.compile import evidence_frames
        records = evidence_frames(case) if frames is None else frames
        pending_prompt = behavior_census_scope.prompt([f['frame_id'] for f in records], case=case)
    return ('\n以下是基础 Schema“不新增根字段”规则的 v4 私有响应例外：'
        '本轮必须在同一 Behavior candidate（包括 replacements 补丁）顶层额外输出且仅额外输出私有 census_scope_review；'
        '针对本轮完整 Behavior 重新复查：补丁请求使用合并结果，补答请求使用冻结基线，不复制旧复查结论。'
        'Python 验收后移除该字段，正式 Behavior Schema 不变。逐条复查以下无身份观察的'
        '原图及完整 Case 范围，不得依据未出现在 events 中推断无关。'
        '不能确认则 unknown；侵入道路、交互、导致减速/避让则 challenged。'
        '不带 pending_scope 标记的项必须有 issue_id、outcome(confirmed/challenged/unknown)、reason、'
        'evidence_frame_ids(覆盖该观察全部指定帧)、affected_actor_ids、affected_event_ids；'
        'confirmed 的 affected 两数组必须为空。遗漏不是确认。'
        'census_scope_review={certificate_sha256,decisions:[上述项]}。输入：'
        + json.dumps(value, ensure_ascii=False)
        + pending_prompt)
