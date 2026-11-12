"""Explain execution blockers without treating them as failed observations."""


def execution_blocked_message(issues):
    categories = set()
    for issue in issues:
        rule = issue.get('rule_id', '')
        message = issue.get('message', '')
        if rule == 'motion.speed_time_position':
            categories.add('执行数值不自洽（并非视频运动矛盾）')
        elif rule == 'world.unresolved_fact':
            paths = ', '.join(issue.get('world_paths', [])) or '未提供字段路径'
            dependency = issue.get('dependency')
            if dependency == 'required':
                categories.add('道路或车道事实未决，当前执行需要：' + paths)
            else:
                categories.add('道路或车道事实未决，执行依赖尚未证明：' + paths)
        elif rule == 'execution.lane_unknown':
            categories.add('道路或车道事实未决：' + (message or '执行车辆车道未知'))
        elif rule == 'execution.lateral_position_unknown' or (
                rule == 'execution.contract' and message.startswith('participant actors missing initial_state primitive:')):
            categories.add('执行初始状态不完整，需核实车道或数值依据')
        else:
            # Preserve specific diagnostics rather than guessing whether an
            # unfamiliar rule is an evidence gap, event conflict or runtime error.
            categories.add(message or rule or '未分类执行问题')
    detail = '；'.join(sorted(categories)) or '需查看 execution_issues'
    return '观察分析已完成并保存 analysis.json；尚未生成仿真目标：' + detail


def failure_explanation(code):
    """Explanations for existing error codes; never infer a missing road fact."""
    return {
        'model_recovery_budget_exhausted': '额外模型任务预算已耗尽；这不表示道路事实已被证明不足。保留原预算与检查点。',
        'model_attempts_exhausted': '模型尝试次数已耗尽；具体原因见 failure_chain，不据此要求确认道路事实。',
        'world_patch_contract_blocked': 'World 初始回答或局部补丁未通过内部契约检查，应按诊断规则修复后恢复。',
        'world_compilation_repairable': 'World 初始回答存在可定向修复的时间或帧引用冲突；使用既有恢复预算提交受限字段修复。',
        'world_closure_no_progress': '定向复核没有减少问题；相同输入不再重复提交。',
        'internal_pipeline_error': '流水线内部错误，应检查程序诊断后恢复。',
        'schema_validation_failed': '模型输出未通过结构或契约校验，应检查具体字段诊断。',
        'world_evidence_insufficient': 'World 定向复核报告证据不足；仅对仍影响执行的具体问题补充证据，详见原始诊断。',
    }.get(code)
