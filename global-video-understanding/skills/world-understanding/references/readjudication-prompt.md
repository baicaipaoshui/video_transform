Skill: world-understanding
道路坐标与参考线方向（与初始 World 任务共用）：
${coordinate_convention}
对 Case ${case_id} 执行唯一一次 geometry re-adjudication。
只读取冻结 World、有效 Batch 和 ${reconciliation_path}，仅裁决 conflicts=${conflict_ids}；不得改变无关字段。
输出仍须符合 world_context schema，并把 evidence_epoch 精确设为 ${next_epoch}。
只调用一次 write_file 写 ${candidate_path} 后停止；不得创建 subagent。
