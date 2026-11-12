"""Shared, read-only classification for versioned execution-gap recovery."""
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json

REVISION = 'execution-gap-recovery.v1'
KEY = 'execution_gap_recovery'


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get(KEY) == REVISION


def scale_limitation(item):
    """Classify scale only; this does not grant permission to synthesize it."""
    domains = set(item.get('impact_domains', []))
    allowed = {'position', 'speed', 'execution'}
    if item.get('limitation_kind') == 'absolute_scale':
        allowed.add('executor_primitive')
    return bool(domains & {'position', 'speed'}) and domains <= allowed


def authorized_scale(case, item):
    if not enabled(case) or not scale_limitation(item):
        return False
    # A primitive gap can also be an absent action; never exempt it from scope.
    if set(item.get('impact_domains', [])) - {'position', 'speed', 'execution'}:
        return False
    policy = read_json(Path(case)/'state/execution_policy.json')
    from _lib.contracts.conversion_policy import build, default_completion
    record = read_json(Path(case)/'case.json')
    if policy.get('conversion_policy_revision') != 'conversion-policy.v1':
        return False
    # Use the same normalization and explicit permissions as Converter, without
    # freezing a conversion artifact before analysis exists.
    parameters = record['parameters']
    value = build(parameters, '0'*64, 'highway', record.get('case_id'),
                  default_completion=default_completion(case))
    if policy.get('conversion_parameters_sha256') != value['parameters_sha256']:
        raise ContractError('Execution gap permissions differ from frozen parameters')
    needed = {'vehicle_'+d+'_policy_default' for d in ('speed', 'position')
              if d in item['impact_domains']}
    return needed <= set(value['allowed_degradations'])


def dependencies(case, behavior):
    """Known events/actions remain dependencies regardless of missing metrics."""
    return [behavior.get('events', []), behavior.get('executor_primitives', []),
            [r for r in behavior.get('semantic_limitations', [])
             if r.get('execution_effect') == 'blocks_execution' and not authorized_scale(case, r)]]


def diagnostics(case, world, behavior, decisions=()):
    """Collect independent gaps; never accept an invalid candidate or mutate it."""
    from _lib.runtime.routing import unobserved_actor_lanes
    from _lib.contracts.actor_identity_revision import issues
    rows = list(issues(world, behavior)) + unobserved_actor_lanes(world, behavior)
    rows += [dict(rule_id='identity.census.scope_pending', issue_id=r['issue_id'],
                  outcome=r['outcome'], actor_ids=r.get('affected_actor_ids', []))
             for r in decisions if r.get('outcome') in {'unknown', 'needs_identity'}]
    rows += [dict(rule_id='execution.limitation', issue_id=r['limitation_id'],
                  actor_ids=r.get('actor_ids', []), impact_domains=r.get('impact_domains', []))
             for r in behavior.get('semantic_limitations', [])
             if r.get('execution_effect') == 'blocks_execution' and not authorized_scale(case, r)]
    return list({digest(row):row for row in rows}.values())


def prompt(case):
    if not enabled(case):
        return ''
    import json
    world = read_json(Path(case)/'state/world_context.json')
    questions = [dict(issue_id=r.get('question_id', r.get('ambiguity_id')),
                      actor_ids=r.get('actor_ids', []), impact_domains=r.get('impact_domains', []))
                 for collection in ('questions_to_verify','ambiguities') for r in world.get(collection, [])
                 if r.get('resolution') == 'unresolved' and
                 set(r.get('impact_domains', [])) & {'actor_lane','actor_identity'}]
    from _lib.behavior.input_binding import closure_request
    request = closure_request(case)
    if request is not None and 'question_authorization' in request:
        return ('\nexecution-gap-recovery.v1：需要关注的未决身份/车道问题：' +
            json.dumps(questions, ensure_ascii=False) +
            '。当前冻结请求允许提交的车道结案问题：' +
            json.dumps(request['question_authorization'], ensure_ascii=False) +
            '。仅这份清单中的问题可在受限repair的lane_closures中结案；'
            '其余问题保留未知并交上游复核，不得从问题文字推断Actor或目标帧。'
            '证据不足时可以取证；confirmed仍须覆盖目标帧、原问题引用帧及逐帧车道依据。'
            '完整Behavior中的lane_closures只能记录unknown，不能改写World。')
    return ('\nexecution-gap-recovery.v1：同次任务逐项核查下列身份/车道问题：' +
        json.dumps(questions, ensure_ascii=False) +
        '。身份、车道仍不足时明确保留未知；不得通过数值近似、改 simulation_role 或删限制放行。'
        '纯 speed/position 尺度缺失与身份/车道/动作缺口分别列 limitation；米制速度与位置交给 Converter。'
        '可选根字段 lane_closures 为列表，每项={collection:semantic_limitations|questions_to_verify|ambiguities,'
        'issue_id:原ID,actor_id:唯一相关Actor,outcome:confirmed|unknown,evidence_frame_ids:该Actor全部轨迹帧,'
        'reason:逐帧车道边界依据}。只回答含 actor_lane 的单Actor限制或纯 actor_lane 的World问题。'
        'confirmed 必须每个轨迹点有明确车道且detail包含对应frame_id；Python仅闭合车道部分，'
        '其他身份、运动和路径限制保持。World原文件不改。没有证据不得确认。')
