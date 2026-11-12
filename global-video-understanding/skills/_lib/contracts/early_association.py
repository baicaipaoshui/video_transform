"""Explicit, replayable execution independence for a bounded early observation group.

This never assigns an anonymous observation to an Actor or proves it irrelevant.
The original census/Behavior answers and the completed scope task remain frozen.
"""
import copy
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'early-association-independence.v1'
AUTOMATIC_REVISION = 'early-association-independence.v2'
POLICY_KEY = 'early_association_independence'
PATH = 'state/early_association_independence.json'
HANDLING = 'independent_early_association'


class IndependenceNotEstablished(ContractError):
    """Valid observations do not establish the bounded independence proof."""


def fail(message):
    raise IndependenceNotEstablished(message, rule_id='identity.early_association.independence')


def automatic_enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get(POLICY_KEY) == AUTOMATIC_REVISION


def automatic(case, behavior, pending, review, frames, world, *, snapshot=None):
    """Derive the normal same-answer receipt without a supplement or sidecar.

    Absence of proof preserves the existing identity stop. Malformed evidence or
    stale census inputs are not eligibility failures and must still propagate.
    The accepted scope receipt stores this value; replay recomputes it exactly.
    """
    if not automatic_enabled(case) or not pending:
        return None
    from _lib.contracts.census_adjudication import proof
    case = Path(case)
    saved = snapshot if snapshot is not None else proof(case)
    groups = []
    for limitation in behavior['semantic_limitations']:
        issue_ids = limitation.get('census_issue_ids') or []
        if not issue_ids or any(iid not in pending for iid in issue_ids):
            continue
        group_pending = {iid: pending[iid] for iid in issue_ids}
        try:
            group = group_candidate_independence(case, behavior, group_pending, review,
                frames, world, saved,
                registry=snapshot['result']['registry'] if snapshot is not None else None)
        except IndependenceNotEstablished:
            continue
        groups.append(group)
    if not groups:
        return None
    groups.sort(key=lambda row: (row['limitation_id'], row['issue_ids']))
    value = dict(revision=AUTOMATIC_REVISION, case_id=case.name,
        behavior_digest=digest(behavior), review_digest=digest(review),
        frames_digest=digest(frames), source_certificate_sha256=saved['sha256'], groups=groups)
    if snapshot is None:
        from _lib.runtime.fact_versions import source_path
        value['source_hashes'] = {name:sha256(source_path(case, Path(name).name)
            if name.startswith('state/') and Path(name).name in {
                'world_context.json', 'actor_registry.json', 'census_adjudication.accepted.json'}
            else case/name) for name in ('case.json','state/execution_policy.json',
            'state/world_context.json','state/actor_registry.json','state/census_adjudication.accepted.json')}
    else:
        value['source_snapshot'] = dict(certificate_sha256=snapshot['sha256'],
            world_digest=digest(world), registry_digest=digest(snapshot['result']['registry']))
    return dict(value, sha256=digest(value))


def prompt(case):
    if not automatic_enabled(case):
        return ''
    return ('\n新 Case 同次验收规则 early-association-independence.v2：对一个或多个已登记车辆首见之前的'
        '早期模糊团块，保持 needs_identity 和 scope_proof=null，不猜身份、不输出虚假 irrelevant。'
        '若原图支持候选兼容且无反证，使用既有 identity_assessment(candidate_status=compatible,'
        'alternative_actor_ids=[],evidence_frame_ids,reason)；不支持则 unknown/rejected。'
        '仅同一早期关联问题的观察，用一个 semantic_limitation 的 census_issue_ids 精确列全，'
        'actor_ids 列出全部候选；确实只有提前关联未知时 impact_domains=[actor_identity]、'
        'execution_effect=bounded_uncertainty。每条对应 decision 的 affected_actor_ids 为该组全部候选，'
        'affected_event_ids 必须如实列出；身份、车道、事件等其他问题单独保留。'
        '所有候选车辆保留，仅以各自确认首见之后的证据给 trajectory；不得补造0秒观测点。'
        'Python 会核验高速无路口、首见<=1.5秒、独立固定同向车道轨迹、共现区分、'
        '早期自车走廊和动作依赖以及冻结近似权限，只有全部通过才解除精确的执行阻塞。'
        '模型不能自称证明通过；原始身份未知始终保留，不以想要执行为由省略其他待查项。')


def mentions(value, ids):
    if isinstance(value, str):
        return value in ids
    if isinstance(value, list):
        return any(mentions(v, ids) for v in value)
    if isinstance(value, dict):
        return any(mentions(v, ids) for v in value.values())
    return False


def group_candidate_independence(case, behavior, pending, review, frames, world, saved, *, registry=None):
    """Derive a certificate only from accepted later anchors and bounded dependencies."""
    from _lib.contracts import census_relevance
    from _lib.contracts.census_history import rounds
    from _lib.contracts.conversion_policy import build
    from _lib.contracts.actors import is_vehicle
    case = Path(case)
    if world['world'].get('scene_type') != 'highway' or world['world'].get('junctions'):
        fail('Early association independence is limited to a junction-free highway')
    policy = read_json(case/'state/execution_policy.json')
    conversion = build(read_json(case/'case.json')['parameters'], '0'*64, 'highway', case.name)
    if (policy.get('conversion_semantic_gap') != 'conversion-semantic-motion.v1'
            or policy.get('conversion_parameters_sha256') != conversion['parameters_sha256']
            or not {'vehicle_position_policy_default', 'vehicle_speed_policy_default',
                    'trajectory_continuation_policy_default'} <= set(conversion['allowed_degradations'])):
        fail('Early association requires frozen position, speed and continuation approximation permission')
    rows = {r['issue_id']: r for r in review['decisions']}
    unresolved = {iid for iid, obs in pending.items()
                  if census_relevance.validate(case, behavior, rows[iid], obs, frames, world, registry=registry) is None}
    actors = {a['actor_id']: a for a in behavior['actors']}
    registry = {a['actor_id']: a for a in (registry if registry is not None else read_json(case/'state/actor_registry.json'))}
    limits = [i for i in behavior['semantic_limitations']
              if set(i.get('census_issue_ids', [])) & unresolved]
    if not unresolved or len(limits) != 1:
        fail('Early association requires one exactly bound unresolved observation group')
    limitation = limits[0]
    ids = set(limitation.get('census_issue_ids', []))
    aids = set(limitation.get('actor_ids', []))
    if (ids != unresolved or len(ids) != len(limitation['census_issue_ids'])
            or not aids or len(aids) != len(limitation.get('actor_ids', []))
            or limitation.get('impact_domains') != ['actor_identity']
            or limitation.get('execution_effect') != 'bounded_uncertainty'
            or not census_relevance.observation_issue_only({k:v for k,v in limitation.items() if k != 'actor_ids'})
            or set().union(*(set(pending[i]['candidate_actor_ids']) for i in ids)) != aids):
        fail('Early association cannot discharge partial, mixed or unbound limitations')
    for iid in ids:
        obs, row = pending[iid], rows[iid]
        assessment = row.get('identity_assessment') or {}
        if (obs.get('distinction') or not obs['candidate_actor_ids'] or row['outcome'] != 'needs_identity'
                or set(row['affected_actor_ids']) != aids or row['affected_event_ids']
                or assessment.get('candidate_status') != 'compatible'
                or assessment.get('alternative_actor_ids') or assessment.get('counterevidence')):
            fail('Early association requires compatible unchanged candidates without counterevidence')
    if saved['result'].get('pending_distinctions'):
        fail('Early association cannot replace a pending Actor distinction')
    source_times = [frames[pending[i]['frame_id']]['time_s'] for i in ids]
    bounds = []
    lanes = {l['lane_id']:l for l in world['world']['road']['lanes']}
    for aid in sorted(aids):
        actor, registered = actors.get(aid), registry.get(aid)
        points = (actor or {}).get('trajectory', [])
        if not is_vehicle(actor) or actor.get('is_static') is not False or not registered or len(points) < 2:
            fail('Early association requires retained individually tracked vehicles')
        first = frames.get(registered['first_seen_frame_id'])
        if (not first or not 0 < first['time_s'] <= 1.5 or max(source_times) >= first['time_s']
                or min(p['time_s'] for p in points) != first['time_s']
                or any(p.get('frame_id') not in frames or p['time_s'] != frames[p['frame_id']]['time_s']
                       or p['time_s'] < first['time_s'] for p in points)
                or mentions(points, ids)):
            fail('Early association cannot supply an accepted trajectory or change first visibility')
        lane_ids = {p.get('lane_id') for p in points}
        if len(lane_ids) != 1 or next(iter(lane_ids)) not in lanes:
            fail('Early association requires a confirmed fixed-lane trajectory')
        lane = lanes[next(iter(lane_ids))]
        if lane.get('travel_direction') not in {'same_as_ego', 'forward'}:
            fail('Early association requires a confirmed same-direction lane')
        ego_prefix = [p for p in actors['ego']['trajectory'] if p['time_s'] <= first['time_s']]
        if (not ego_prefix or min(p['time_s'] for p in ego_prefix) != 0
                or any(not p.get('lane_id') or p['lane_id'] in lane_ids for p in ego_prefix)):
            fail('Early association cannot assume independence inside the early ego corridor')
        for item in behavior['events'] + behavior['executor_primitives']:
            if mentions(item, ids):
                fail('An action explicitly depends on an early observation')
            interval = item.get('time_range_s')
            start = interval[0] if isinstance(interval, list) and interval else item.get('time_s')
            if mentions(item, {aid}):
                if (item.get('type') == 'initial_state' or type(start) not in (int, float)
                        or start < first['time_s'] or any(e.get('actor_id') == aid and
                        (e.get('frame_id') not in frames or frames[e['frame_id']]['time_s'] < first['time_s'])
                        for e in item.get('evidence', []) if isinstance(e, dict))):
                    fail('An initial state or action may depend on early Actor identity')
            if (item.get('event_type', item.get('type')) in {'lane_change','cut_in','collision','evasive_steering'}
                    and (type(start) not in (int, float) or start < first['time_s'])):
                fail('Early maneuver prevents bounded prefix independence')
        bounds.append(dict(actor_id=aid, observed_from_s=first['time_s'],
                           first_seen_frame_id=registered['first_seen_frame_id'],
                           trajectory_sha256=digest(points), lane_id=next(iter(lane_ids))))
    for item in [*world.get('questions_to_verify', []), *world.get('ambiguities', []),
                 *behavior['semantic_limitations']]:
        if item is limitation or item.get('resolution') in {'resolved','confirmed','non_core'}:
            continue
        if mentions(item, ids) or ('actor_identity' in item.get('impact_domains', []) and mentions(item, aids)):
            fail('Separate identity or observation dependency remains unresolved')
        if mentions(item, aids) and (item.get('world_paths') or item.get('related_world_paths')
                or set(item.get('impact_domains', [])) & {'path','event','executor_primitive'}):
            fail('Separate Actor path or action dependency remains unresolved')
    distinctions = []
    for index, aid in enumerate(sorted(aids)):
        for other in sorted(aids)[index+1:]:
            shared = {p['frame_id'] for p in actors[aid]['trajectory']} & {p['frame_id'] for p in actors[other]['trajectory']}
            witnesses = []
            for certificate in rounds(saved):
                for vehicle in certificate['response']['vehicles']:
                    subject = vehicle.get('actor_id')
                    if subject not in {aid, other}:
                        continue
                    for sight in vehicle.get('sightings', []):
                        if (sight['frame_id'] in shared and {aid, other}-{subject} <= set(sight.get('distinct_from_actor_ids', []))):
                            witnesses.append(dict(actor_id=subject, **copy.deepcopy(sight)))
            if not witnesses:
                fail('Later vehicles require accepted co-visible distinction evidence')
            distinctions.append(dict(actor_ids=[aid, other], witnesses=witnesses))
    return dict(issue_ids=sorted(ids), actor_ids=sorted(aids), limitation_id=limitation['limitation_id'],
                limitation_sha256=digest(limitation), observations_sha256=digest([pending[i] for i in sorted(ids)]),
                actors=bounds, distinctions=distinctions, identity_resolution='unresolved',
                prefix_policy='execution_approximation_only', description_usage='audit_only')


def build(case):
    from _lib.runtime import behavior_scope_review as supplement
    from _lib.contracts import census_scope, census_adjudication, behavior_census_scope
    case = Path(case)
    candidate = supplement.completed_candidate(case)
    if candidate is None:
        fail('Early association requires a verified completed scope supplement')
    behavior, review = census_scope.split(candidate)
    packet = supplement.request(case)
    saved = census_adjudication.proof(case)
    pending = behavior_census_scope.pending(case, saved)
    group = group_candidate_independence(case, behavior, pending, review,
        {f['frame_id']:f for f in packet['frames']}, read_json(case/'state/world_context.json'), saved)
    from _lib.runtime.fact_versions import source_path
    value = dict(revision=REVISION, case_id=case.name, behavior_digest=digest(behavior), review_digest=digest(review),
                 source_hashes={name:sha256(source_path(case, Path(name).name)
                     if name.startswith('state/') and Path(name).name in {
                         'world_context.json', 'actor_registry.json', 'census_adjudication.accepted.json'}
                     else case/name) for name in ('case.json','state/execution_policy.json',
                     'state/world_context.json','state/actor_registry.json','state/census_adjudication.accepted.json',
                     supplement.RESULT, supplement.REQUEST)}, groups=[group])
    return dict(value, sha256=digest(value))


def load(case, behavior, review, *, snapshot=None):
    path = Path(case)/PATH
    if not path.is_file():
        return None
    if snapshot is not None:
        # v1 binds live source files and a completed supplement. It cannot be
        # transplanted into an ancestor replay or rebuilt through the live head.
        fail('Legacy early association certificate cannot authorize historical scope recovery')
    value = read_json(path)
    if (value != build(case) or value['behavior_digest'] != digest(behavior)
            or value['review_digest'] != digest(review)):
        fail('Early association certificate or frozen inputs changed')
    return value


def adopt(case):
    """Explicit local recovery; no task, budget or existing output is changed."""
    case = Path(case)
    state = read_json(case/'state/progress.json')
    if (state.get('diagnostics', {}).get('unresolved_submissions')
            or any(s.get('status') in {'submitted','pending','running','submission_unresolved'}
                   or s.get('error_code') in {'task_deadline_exceeded','task_status_unavailable'}
                   and not s.get('deadline_task_resolved') for s in state.get('sessions', []))):
        fail('Reconcile all pending tasks before early association adoption')
    value = build(case)
    path = case/PATH
    if path.is_file():
        if read_json(path) != value:
            fail('Existing early association certificate changed')
        return value
    if (state.get('status') != 'needs_review' or state.get('substage') != 'behavior'
            or (state.get('error') or {}).get('code') != 'identity_evidence_insufficient'
            or any((case/p).exists() for p in ('state/behavior.json','final/analysis.json'))):
        fail('Early association adoption requires an unaccepted Behavior identity stop')
    atomic_json(path, value)
    return value


def disposes(item, receipt):
    proof = (receipt or {}).get('early_association_proof')
    if not proof:
        return False
    choices = {r['issue_id']:r for r in receipt['choices']}
    return any(group['limitation_sha256'] == digest(item) and all(
        choices.get(iid, {}).get('handling') == HANDLING
        and choices[iid].get('independence_sha256') == proof['sha256'] for iid in group['issue_ids'])
        for group in proof['groups'])
