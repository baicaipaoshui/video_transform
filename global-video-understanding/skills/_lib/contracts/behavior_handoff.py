"""Evidence-bound identity and execution scope handoff for new Cases."""
from pathlib import Path

from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json

KEY = 'behavior_handoff'
REVISION = 'behavior-handoff.v1'


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get(KEY) == REVISION


def validate_identity(row, observation, actors, frames):
    claim = row.get('identity_assessment')
    if claim is None:
        return  # Missing assessment never permits an identity assumption.
    if (not isinstance(claim, dict)
            or set(claim) != {'candidate_status', 'alternative_actor_ids', 'evidence_frame_ids', 'reason'}
            or claim['candidate_status'] not in {'compatible', 'rejected', 'unknown'}
            or not isinstance(claim['reason'], str) or not claim['reason'].strip()
            or any(not isinstance(claim[k], list) or any(not isinstance(v, str) for v in claim[k])
                   or len(set(claim[k])) != len(claim[k])
                   for k in ('alternative_actor_ids', 'evidence_frame_ids'))
            or not set(claim['alternative_actor_ids']) <= set(actors)
            or not set(claim['alternative_actor_ids']) <= set(row['affected_actor_ids'])
            or set(claim['alternative_actor_ids']) & set(observation['candidate_actor_ids'])
            or observation['frame_id'] not in claim['evidence_frame_ids']
            or not set(claim['evidence_frame_ids']) <= set(frames)
            or claim['candidate_status'] == 'compatible' and claim['alternative_actor_ids']
            or row['outcome'] != 'needs_identity'):
        raise ContractError('Invalid structured census identity assessment')


def permits_assumption(row, candidates, behavior):
    claim = row.get('identity_assessment') or {}
    if (claim.get('candidate_status') != 'compatible' or claim.get('alternative_actor_ids')
            or set(row.get('affected_actor_ids', [])) - set(candidates)
            or row.get('affected_event_ids')):
        return False
    return not any(set(item.get('actor_ids', [])) & set(candidates)
                   and 'actor_identity' in item.get('impact_domains', [])
                   and item.get('resolution') not in {'resolved', 'confirmed', 'non_core'}
                   for item in behavior.get('semantic_limitations', []))


def transient_actors(world, behavior, frames, choices=(), *, retained=()):
    """Exclude only explicitly reviewed, non-interacting transient observations."""
    def mentioned_ids(value):
        if isinstance(value, str): return {value}
        if isinstance(value, list): return set().union(*(mentioned_ids(v) for v in value)) if value else set()
        if isinstance(value, dict): return mentioned_ids(list(value.values()))
        return set()
    by_frame = {f['frame_id']: f['time_s'] for f in frames}
    unresolved = {aid for row in choices if row['handling'] in {'needs_evidence', 'assumed_existing', 'retained_unresolved'}
                  for aid in row['candidate_actor_ids']}
    interacting = set()
    for event in behavior.get('events', []):
        interacting.update(mentioned_ids(event))
    for primitive in behavior.get('executor_primitives', []):
        if primitive.get('type') != 'initial_state': interacting.update(mentioned_ids(primitive))
        else: interacting.update(mentioned_ids(primitive) - {primitive.get('actor_id')})
    result = set()
    for actor in behavior['actors']:
        # Caller has replayed the source-bound execution receipt. Retention
        # does not assert all-frame visibility or absence of interaction.
        if actor['actor_id'] in retained:
            continue
        claim = actor.get('execution_scope_evidence')
        if claim is None:
            if (actor['actor_id'] != 'ego' and actor.get('actor_type') == 'vehicle'
                    and actor.get('simulation_role') == 'participant'
                    and len(actor.get('trajectory', [])) == 1):
                raise ContractError('Single-frame participant requires explicit execution relevance evidence',
                                    rule_id='scope.single_frame_relevance', actor_id=actor['actor_id'])
            continue
        aid = actor['actor_id']
        visible = {p['frame_id'] for p in actor['trajectory']}
        if (not isinstance(claim, dict)
                or set(claim) != {'kind', 'checked_frame_ids', 'visible_frame_ids', 'no_interaction', 'reason'}
                or claim['kind'] not in {'edge_fragment', 'distant_transient', 'execution_relevant'}
                or claim['no_interaction'] is not (claim['kind'] != 'execution_relevant')
                or not isinstance(claim['reason'], str) or not claim['reason'].strip()
                or any(not isinstance(claim[k], list) or any(not isinstance(v, str) for v in claim[k])
                       or len(set(claim[k])) != len(claim[k])
                       for k in ('checked_frame_ids', 'visible_frame_ids'))
                or set(claim['checked_frame_ids']) != set(by_frame)
                or set(claim['visible_frame_ids']) != visible
                or not visible or not visible <= set(by_frame)
                or claim['kind'] != 'execution_relevant' and not visible < set(by_frame)
                or claim['kind'] == 'edge_fragment' and len(visible) != 1
                or aid == 'ego' or actor.get('actor_type') != 'vehicle'
                or claim['kind'] != 'execution_relevant' and world['world'].get('scene_type') != 'highway'):
            raise ContractError('Transient Actor scope requires complete frame-bound evidence',
                                rule_id='scope.transient_evidence', actor_id=aid)
        if claim['kind'] == 'execution_relevant':
            continue
        # The proof is insufficient while any physical identity/event dependency
        # remains. Never discard a disputed identity to make the route pass.
        identity = any(aid in mentioned_ids(row) and
                       ('actor_identity' in row.get('impact_domains', [])
                        or bool(row.get('world_paths')))
                       and row.get('resolution') not in {'resolved', 'confirmed', 'non_core'}
                       for rows in (world.get('questions_to_verify', []), world.get('ambiguities', []),
                                    behavior.get('semantic_limitations', [])) for row in rows)
        if aid not in interacting | unresolved and not identity:
            result.add(aid)
    return result


def census_limitation_disposed(item, choices):
    ids = item.get('census_issue_ids')
    if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
            or item.get('actor_ids') or item.get('actor_id') or item.get('affected_actor_ids')
            or item.get('affected_event_ids') or item.get('world_paths') or item.get('scope_binding')
            or item.get('related_world_paths')
            or item.get('execution_effect') != 'bounded_uncertainty'
            or not set(item.get('impact_domains', [])) <= {'actor_identity', 'actor_lane'}):
        return False
    rows = {row['issue_id']: row for row in choices}
    return set(ids) <= set(rows) and all(rows[i]['handling'] in {
        'observation_only_unknown', 'isolated_opposing', 'observation_excluded'} for i in ids)


def validate_execution(case, world, behavior, frames, review):
    """Use the same projection before admission and during route/build/replay."""
    if not enabled(case): return
    from _lib.runtime.execution_scope import project
    from _lib.contracts.actor_identity_revision import issues
    w, b, _ = project(world, behavior,
        policy_id=read_json(Path(case)/'state/execution_policy.json')['execution_scope'],
        handoff_frames=frames, execution_dispositions=(review or {}).get('execution_dispositions'))
    remaining = issues(w, b)
    if remaining:
        error = ContractError('Behavior handoff retains unresolved identity dependencies; '
            'supply evidence-bound census_issue_ids/scope proof or formal identity review: '
            + ', '.join(r['issue_id'] for r in remaining), rule_id='behavior.handoff.identity_dependencies')
        error.diagnostics = dict(rule_id=error.rule_id, issues=remaining,
                                 retry_kind='content_repair', owner='behavior')
        raise error


def prompt(case, frames):
    if not enabled(case): return ''
    import json
    from _lib.contracts import bounded_execution
    prefix = ('\nbounded-execution.v1：已登记身份由程序继承；符合来源规则的首帧车辆会保留并近似未观察尾段，'
        '不要求重复证明不可见期间无交互。保留真实帧、车道和主要动作；新的身份、动作、交互须用结构化证据表达。'
        '未知数值保持未知；变道的观察边界照实记录，勿为满足执行直行段而捏造新帧或改时间。'
        if bounded_execution.enabled(case) else '')
    return (prefix + '\nbehavior-handoff.v1：身份复核和执行范围必须结构化交接。pending 的 needs_identity 项可增加 '
        'identity_assessment={candidate_status:compatible|rejected|unknown,alternative_actor_ids:[],evidence_frame_ids:[],reason:原图依据}。'
        'compatible 只表示没有反证且仍可能是该候选；明确否定旧候选必须 rejected，把替代已有身份同时列入 alternative_actor_ids 和 affected_actor_ids。'
        '仍保持 needs_identity，Python 交还正式身份裁定；不得仅在 reason 中否定候选。'
        '仅涉及未登记 census 观察的身份/车道限制，使用 census_issue_ids 精确绑定该限制覆盖的 issue 子集；不要与已登记车辆的身份争议混写。'
        '每个已登记车辆仍保留观察。高速场景中，单帧画面边缘局部车辆或仅短暂可见的远处车辆，若逐帧确认不构成自车前车、避让、碰撞、变道约束或其他交互，'
        '可以给 actor.execution_scope_evidence={kind:edge_fragment|distant_transient,checked_frame_ids:全部冻结帧,visible_frame_ids:全部真实轨迹帧,no_interaction:true,reason:逐帧可见/消失及无交互依据}，'
        '并设 simulation_role=background。本冻结策略补充旧版同向车辆保留规则：证据完整的上述短暂观察可由 Python 排除。仅远、小、未出现在事件中不足以证明无交互；遮挡导致无法排除交互时不得声明。'
        'Python 验证证据和依赖后仅在执行副本裁剪，原 Actor、车道及局限保留。已确认前车、持续跟踪的相邻车不能使用此规则。'
        '单帧 participant 必须也填写 execution_scope_evidence，kind=execution_relevant、no_interaction=false，reason 解释具体执行依赖；单帧可见本身不构成参与资格。'
        'checked_frame_ids=' + json.dumps([f['frame_id'] for f in frames]) + '。')
