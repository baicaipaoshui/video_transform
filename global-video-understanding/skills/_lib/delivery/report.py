#!/usr/bin/env python3
"""Generate Markdown and DOCX from exactly the same validated analysis."""
import argparse
import json
import os
import tempfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt


def observation_reason(observation):
    if observation.get('reason'):
        return observation['reason']
    return f"结构化证明 {observation['proof']['rule_id']}（逐帧依据保存在 analysis.json）"


def sections(data, conversion=None, checks=None, delivery_status=None):
    quality=data.get("quality_report",{}); event_lines=[]
    for event in data.get("events",[]):
        time_range=event.get("time_range") or {}
        time_range_s=event.get("time_range_s") or []
        if isinstance(time_range_s,dict):
            time_range_s=[time_range_s.get("start_s",time_range_s.get("start")),
                          time_range_s.get("end_s",time_range_s.get("end"))]
        event_type=event.get("type",event.get("event_type","unknown"))
        start=time_range.get("start_s",event.get(
            "start_s",time_range_s[0] if len(time_range_s) == 2 else "?"))
        end=time_range.get("end_s",event.get(
            "end_s",time_range_s[1] if len(time_range_s) == 2 else "?"))
        line=f"- {event.get('event_id','event')}: {event_type} ({start}–{end} s)"
        if event.get("description"): line+=f" — {event['description']}"
        event_lines.append(line)
    actor_lines=[]
    for actor in data.get("actors",[]): actor_lines.append(f"- {actor.get('actor_id','unknown')}: {actor.get('actor_type','unknown')} — {actor.get('identity_description',actor.get('subtype',''))}")
    confidence=quality.get("overall_confidence",(quality.get("global_hypothesis_assessment") or {}).get("confidence","unknown"))
    limitations=data.get("limitations",quality.get("limitations",[]))
    result = [("场景概况",[f"场景类型：{data.get('scene_type')}",f"总体置信度：{confidence}"]),
            ("关键事件",event_lines or ["未识别到可确认事件。"]),("交通参与者",actor_lines or ["未识别到可确认参与者。"]),
            ("局限性",[f"- {item}" for item in limitations] or ["无额外局限性记录。"]) ]
    if quality.get('batch_field_fallbacks'):
        result.append(('观察字段降级', [
            '- {actor_id} 的车型声明 {claim} 缺少车型字段自身的视频位置；观察车型记为 unknown，车辆及其他已确认事实保留。候选 SHA256：{digest}。'.format(
                actor_id=row['actor_id'],
                claim=json.dumps(row['source_claim'].get('value'), ensure_ascii=False),
                digest=row['candidate_sha256'])
            for row in quality['batch_field_fallbacks']]))
    if quality.get("motion_validation"):
        report=quality["motion_validation"]
        result.append(("事件时间验收",[
            f"- {item['event_id']}: 视频区间 {item.get('evidence_interval_s',item.get('visibility'))}；target 预测 {item.get('predicted_s')} s；区间外偏差 {item.get('outside_interval_s',0)} s"
            for item in report["events"]]+["本地运动预测通过；真实 MELA 渲染：not_executed。首次动作在首个仿真步触发，预览编码时间另需核对。 "]))
    delivered = (conversion is not None and conversion.get('status') in {'done', 'done_degraded'}
                 and checks is not None and checks.get('references') == 'passed')
    if delivered:
        formal_status = delivery_status or conversion['status']
        result.append(('执行状态', [
            '交付级别：' + ('来源约束降级成功' if formal_status == 'done_degraded' else '正式成功') + '。',
            f"观察数据执行字段完整性：{'未齐全（保留 unknown）' if quality.get('execution_ready') is False else '见 analysis 质量记录'}。",
            f"target.json 已生成并通过源合同检查；转换状态：{conversion['status']}。",
            f"真实 runner/render：{checks.get('render_verification', 'not_executed')}；源合同通过不代表真实渲染通过。",
            f"analysis SHA256：{conversion.get('input_sha256')}；target SHA256：{conversion.get('target_sha256')}。",
        ]))
        fallbacks = conversion.get('fallbacks', [])
        result.append(('实际执行补值', [
            f"共 {len(fallbacks)} 项；执行投影 {len(conversion.get('execution_fallbacks', []))} 项，场景转换 {len(conversion.get('scene_fallbacks', []))} 项。",
            f"策略 SHA256：{conversion.get('policy_sha256', '旧合同，无独立策略哈希')}。补值不是视频测量值，也未回写观察。",
            *[f"- {row['field']}: {json.dumps(row['value'], ensure_ascii=False, sort_keys=True)}；原值={json.dumps(row.get('observed_value'), ensure_ascii=False, sort_keys=True)}；策略={','.join(row.get('policy_ids', [row.get('policy_id', 'legacy')]))}；原因={row['reason']}"
              for row in fallbacks]]))
    elif quality.get('execution_ready') is False and quality.get('execution_projection'):
        result.append(('执行状态', ['观察数据仍有 unknown；未提供已核验的转换回执，不能据此判断 target 是否已生成。']))
    elif quality.get('execution_ready') is False:
        from _lib.delivery.diagnostics import execution_blocked_message
        result.append(('执行状态', [execution_blocked_message(quality.get('execution_issues', []))]))
    if quality.get('execution_projection') == 'analysis_values_then_hardcoded_fallbacks':
        result.append(('执行转换说明', [
            'target.json 优先采用 analysis.json 中可执行的值；新合同补值由冻结策略和逐项授权控制，旧合同保留原转换规则。',
            '这些执行值不反向修改视频观察；实际补值以本报告转换回执明细为准。',
            '真实 MELA/OpenX 生成与 esmini 渲染仍需在具备 runner 的环境单独验证。']))
    semantic_gap = (conversion or {}).get('semantic_gap')
    from _lib.contracts.bounded_execution import KEY
    if data.get('metadata', {}).get(KEY):
        from _lib.behavior.lane_change import from_analysis
        spec = from_analysis(data)
        rows = ['已确认车辆、观察帧与主要动作保持；未知距离和速度可由有界程序规划。数值不是视频测量，未观察时段可能与真实运动不同。']
        if spec and spec.timing_assumption:
            rows.append('变道观察时间包络与仿真动作时间分开记录：' + str(spec.timing_assumption))
        for aid, record in data['metadata'][KEY].get('single_frame_motion', {}).items():
            rows.append(f"- {aid}：确认锚点 {record['observed_frame_id']} / {record['observed_time_s']} s；"
                f"执行车道 {record['selected_lane_id']}（{'仿真假设，原车道未知' if record['lane_assumed'] else '原观察车道'}）；"
                f"未知前段 {record['unobserved_prefix_s']} s，未知尾段 {record['unobserved_tail_s']} s。"
                f"未采用早期身份关联；{record['assumption']}")
        result.append(('有界执行近似', rows))
    from _lib.contracts.semantic_motion import REVISION as motion_revision
    if semantic_gap and semantic_gap.get('revision') == motion_revision:
        from _lib.contracts import semantic_motion, motion_witnesses
        witness_revision = semantic_gap['policy'].get(motion_witnesses.POLICY_KEY)
        if witness_revision:
            request = semantic_motion.request(data, semantic_gap['analysis_sha256'], witness_revision=witness_revision)
            record = request[motion_witnesses.KEY]
            result.append(('逐帧证据与执行取舍', [
                '原始回答保留。以下摘要解释仅在执行时采用更具体的车道/逐帧证据；整车是否超越完成仍未证实。',
                *[f"- 来源 {row['source']}；原文：{row['original']}；执行取舍：{row['execution']}"
                  for row in record['choices']],
                *[f"- {row['actor_id']}：{row['type']}；依据与仿真假设：{json.dumps(row,ensure_ascii=False,sort_keys=True)}"
                  for row in record['constraints']],
                f"程序处理记录 {len(record['receipts'])} 项；自动缺值说明须通过最终实际执行投影复验。",
            ]))
        result.append(('文字运动执行近似', [
            '关系来自冻结文字；下列米数、速度、车辆长度及未观测时段的延续均为执行近似，不是视频测量。',
            *[f"- {p['actor_id']}：初始纵向位置 {p['initial_x_m']} m；速度节点 {p['speed_knots']}；{p['assumption']}"
              for p in semantic_gap['plans']],
            *[f"- {p['actor_id']} / {p['reference_actor_id']}：{p['type']}，{p['time_interval_s']} s"
              for p in semantic_gap['relations']],
            f"target 关系检查：{(checks or {}).get('semantic_motion', {}).get('status', 'not_executed')}；真实渲染未验证。",
        ]))
    elif semantic_gap:
        if semantic_gap['status'] == 'accepted':
            chosen = [f"- {row['actor_id']}：{row['category']} → "
                      f"{str(semantic_gap['gap_values_m'][row['category']]) + ' m' if row['category'] != 'unknown' else '获准默认值'}；"
                      f"首帧依据：{row.get('evidence_quote') or '；'.join(row.get('evidence_quotes', [])) or row.get('visual_evidence') or '无法判断'}"
                      for row in semantic_gap['decisions']]
            result.append(('语义车距执行选择', [
                ('档位来自已验收 analysis 的首帧文字及冻结首帧图像；米数是仿真策略值，不是视频测量。'
                 if semantic_gap.get('frames') else
                 '档位来自已验收 analysis 的首帧文字；米数是仿真策略值，不是视频测量。'),
                *chosen]))
        else:
            result.append(('语义车距执行选择', [
                '模型判断未采用；距离按已授权的 Converter 默认值生成。',
                f"原因：{semantic_gap['reason']}。"]))
    if delivered and any(len({row.get('state') for row in signal.get('state_timeline', [])
                if isinstance(row, dict)} - {None, 'unknown'}) > 1
           for junction in data.get('junctions', [])
           for signal in junction.get('traffic_signals', [])):
        result.append(('灯色执行近似', [
            '观察包含灯色变化；是否采用单灯色执行近似及具体值，以实际转换回执为准。',
            'analysis.json 保留原始灯色时间线；原值和替换记录见 conversion_result.json.fallbacks。此替换为 done_degraded，不保证灯色时序保真。']))
    if quality.get('straight_preview'):
        result.append(('近似仿真说明', [
            '视频观察与执行参数分别保存；未知速度和位置由显式启用的策略生成，不是视频测量值。',
            '已复核的 steady 使用等速保持车距；receding/approaching 分别使用正/负速度差并校验整个窗口的碰撞包络。真实 MELA 渲染未由此证明。',
            *[f"- {item['field']}: {item['effective_value']} ({item['policy_id']})"
              for item in quality.get('applied_defaults', [])
              if item.get('policy_id') == 'straight_preview_policy_default']]))
    if quality.get('intersection_preview'):
        projection=quality['intersection_preview']
        result.append(('路口近似与来源', [
            'done_degraded：原始 World/Behavior 保留在来源记录中；以下数值属于执行投影，不是已验证观察。',
            '停车区域以 paved shoulder 有损表达；平面化不复现下坡、局部停车区长度或遮挡。render_verification=not_executed。',
            *[f"- {p['field_path']}: {p['value']}；原值={p['observed_value']}；{p['policy_id']}；{p['reason']}"
              for p in projection['provenance']],
            *[f"- 未执行背景 {p['actor_id']}: {p['reason']}" for p in projection['background_exclusions']]]))
    revision=quality.get('scene_revision')
    if revision:
        corrections=revision['corrections'];layout=corrections.get('highway_layout')
        lines=[f"来源：用户确认；原因：{corrections['reason']}",
            f"父 analysis SHA256：{corrections['parent_analysis_sha256']}；原始观察保留在修订证书中。",
            '这是用户修订的执行场景，不代表模型独立视觉复核或真实渲染通过。']
        if layout:
            lines.extend([f"同向行车道：{layout['driving_lane_count']} 条，按道路方向从右向左编号；停车带不计入行车道数量。",
                f"自车：{data['ego']['trajectory'][0]['lane_id']}；右侧停车带：{layout['right_parking_area']}（target 以 paved shoulder 表达，米制宽度由策略补齐）。",
                *[f"- {event['event_id']}：{event.get('source_lane_id')} → {event.get('target_lane_id')}；原时间区间 {event.get('time_range_s')} s。"
                    for event in data.get('events',[]) if event.get('event_type')=='lane_change']])
        result.append(('用户场景修订',lines))
    plan=quality.get('execution_plan')
    if plan:
        result.append(('执行计划', [f"策略：{plan['revision']}；计划 SHA256：{plan['sha256']}",
            f"执行对象：{', '.join(plan['execution_actor_ids'])}；观察时间范围：{plan['time_range_s']} s",
            '精确观察保持硬约束；估计值参与拟合；默认值和排除项保留在 analysis.json 的 quality_report.execution_plan。']))
    scope=quality.get('execution_actor_scope') or quality.get('offline_execution_actor_scope')
    unused=((scope or {}).get('execution_dispositions') or {}).get('unused_association')
    assumptions=((scope or {}).get('execution_dispositions') or {}).get('execution_assumptions')
    peripheral=((scope or {}).get('execution_dispositions') or {}).get('peripheral_uncertainty')
    retained_unresolved=[row for row in ((scope or {}).get('execution_dispositions') or {}).get('choices', [])
                         if row.get('handling') == 'retained_unresolved']
    if retained_unresolved:
        result.append(('身份未决但保留的车辆观察', [
            '这些来源观察仍未完成身份确认；执行仅沿用已登记车辆的真实观察轨迹。Target 可信度仍以来源、道路、运动及转换验收为准。',
            *[f"- {row['issue_id']}：执行对象 {row['actor_id']}，源帧 {row['observation_frame_id']}；身份 unresolved；未新增车辆或轨迹。"
              for row in retained_unresolved],
            '原始来源、视觉区分和交互依据保存在 analysis.json 的 execution_dispositions。']))
    if peripheral:
        result.append(('逐帧观察的仿真假设', [
            '以下处置不确认原始身份；未建模的远处轮廓可能损失交通细节，末帧车辆的车道和运动是仿真假设。',
            *[f"- {iid}：{row['handling']}；{row.get('actor_id', '未建模')}；{row.get('assumption', row.get('fidelity_loss', ''))}"
              for iid,row in sorted(peripheral['records'].items())],
            *[f"- {aid}：单帧车道假设 {row['lane_id']}；{row['selection']}；原始车道仍未证实。"
              for aid,row in sorted(peripheral.get('lane_assumptions', {}).items())],
            '完整来源与假设保存在 analysis.json 的 quality_report.execution_actor_scope.execution_dispositions.peripheral_uncertainty。']))
    if assumptions:
        result.append(('有记录的仿真假设', [
            '以下选择仅用于执行，原始观察仍未知；已确认车辆、原观察帧和主要动作保留。',
            '未单独建模的目标可能是真实车辆，可能遗漏交通参与者和交互；车道选择可能改变相对走廊关系，不代表视频已证明。',
            *[f"- {r['issue_id']}：观察留档，身份 unresolved，不新增轨迹，也不关联既有车辆。"
              for r in assumptions['observations'].values() if not r.get('basis')],
            *[f"- {r['issue_id']}：继承已验收的护栏隔离范围；个体身份仍 unresolved，没有认定两条观察是同一辆车。"
              for r in assumptions['observations'].values() if r.get('basis') == 'inherited-isolation.v1'],
            *[f"- {aid}：复用上游隔离证据，不模拟对向背景运动；原车辆/群体观察保留，影响主要动作的依赖不得排除。"
              for aid in assumptions.get('inherited_opposing', {})],
            *[f"- {aid}：保留首帧车辆及 {r['lane_id']}；首帧并行只约束 0 秒。后续采用有界匀速、原车道延续假设；"
              '原运动未知保留，不代表已证明无交互。可能损失真实速度变化、车距和离开视野后的交互；数值见转换回执。'
              for aid,r in assumptions.get('retained_unobserved_motion', {}).items()],
            *[f"- {r['actor_id']}：执行车道 {r['selected_lane_id']}；证据允许 {', '.join(r['candidate_lane_ids'])}；"
              f"锚点 {', '.join(r['anchor_frame_ids'])}；选择规则 {r['selection']}；假设锚点间不额外变道。"
              for r in assumptions['lanes'].values()],
            '完整来源、反证和逐帧替换记录保存在 analysis.json 的 quality_report.execution_actor_scope.execution_dispositions.execution_assumptions。']))
    if unused:
        result.append(('未采用的身份关联', [
            '以下后续观察未接入已确认车辆的轨迹；身份仍未知，不代表已证明无关。已确认车辆和动作保留。',
            '可能损失这些后续观察对应的持续跟踪或交互细节；不新增车辆、不补造车道，不将此裁定当作视觉事实。',
            *[f"- {r['issue_id']}：候选 {r['actor_id']}；保留锚点 {', '.join(r['anchor_frame_ids'])}。"
              for r in unused['records'].values()]]))
    if scope and scope['excluded_actors']:
        result.append(('仿真对象范围', [
            '以下对象未纳入此次仿真范围，仅保留观察；这不代表它们都是非车辆。原始身份及轨迹不确定性未被改写。',
            *[f"- {a['actor_id']} ({a['actor_type']}): 不渲染，观察记录保留" for a in scope['excluded_actors']]]))
    if scope and scope.get('excluded_observations'):
        result.append(('观察级执行范围排除', [
            '以下 census 观察的身份仍未知；范围裁定及 Behavior 复查允许仅保留观察，不创建虚假车辆身份。',
            *[f"- {o['issue_id']}；规则：{o['rule_id']}；范围：{o['window_s']} s；依据：{observation_reason(o)}；证据帧：{', '.join(o['evidence_frame_ids'])}"
              for o in scope['excluded_observations']]]))
    if quality.get("usage"):
        usage=quality["usage"]
        result.append(("模型用量",[f"业务任务数：{usage['business_tasks']}",f"输入 token：{usage.get('prompt_tokens')}；输出 token：{usage.get('completion_tokens')}",f"实际模型请求数：{usage.get('model_requests')}；工具调用数：{usage.get('tool_calls')}","None 表示运行时未提供数据，不代表零消耗。 "]))
    return result


def generate(source, markdown, docx, conversion=None, checks=None, target=None, delivery_status=None):
    from _lib.runtime.case_core import read_json, sha256
    if (conversion is None) != (checks is None) or (conversion is not None and target is None):
        raise ValueError('Report requires conversion, target checks and target together')
    receipt = read_json(conversion) if conversion is not None else None
    validation = read_json(checks) if checks is not None else None
    if receipt is not None:
        if receipt.get('input_sha256') != sha256(Path(source)) or receipt.get('target_sha256') != sha256(Path(target)):
            raise ValueError('Report conversion artifact binding mismatch')
        if receipt.get('delivery_checks') != validation or validation.get('references') != 'passed':
            raise ValueError('Report target checks do not match the conversion receipt')
    data=read_json(source); content=sections(data, receipt, validation, delivery_status)
    md=["# 视频场景分析报告",""]
    for title,lines in content: md.extend([f"## {title}","",*lines,""])
    markdown=Path(markdown); docx=Path(docx); markdown.parent.mkdir(parents=True,exist_ok=True)
    fd,temp_md=tempfile.mkstemp(prefix="."+markdown.name,dir=markdown.parent)
    with os.fdopen(fd,"w",encoding="utf-8") as stream: stream.write("\n".join(md)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp_md,markdown)
    directory=os.open(markdown.parent,os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    document=Document(); style=document.styles["Normal"]; style.font.name="SimSun"; style.font.size=Pt(10.5); style._element.rPr.rFonts.set(qn("w:eastAsia"),"SimSun")
    document.add_heading("视频场景分析报告",0)
    for title,lines in content:
        document.add_heading(title,1)
        for line in lines: document.add_paragraph(line)
    fd,temp_docx=tempfile.mkstemp(prefix="."+docx.name,dir=docx.parent); os.close(fd)
    document.save(temp_docx)
    with open(temp_docx,"rb") as stream: os.fsync(stream.fileno())
    os.replace(temp_docx,docx)
    directory=os.open(docx.parent,os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--input",required=True); parser.add_argument("--markdown",required=True); parser.add_argument("--docx",required=True)
    parser.add_argument('--conversion'); parser.add_argument('--checks'); parser.add_argument('--target')
    parser.add_argument('--delivery-status', choices=('done', 'done_degraded'))
    args=parser.parse_args()
    generate(args.input,args.markdown,args.docx,args.conversion,args.checks,args.target,args.delivery_status)


if __name__=="__main__": main()
