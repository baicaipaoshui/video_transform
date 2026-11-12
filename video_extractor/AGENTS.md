# AGENTS.md — Video Extractor Batch Worker

本 Agent 只处理 `global-video-understanding` 冻结的一个详细 Batch。上游和生命周期所有者均为全局编排器。

- 每个新 session 恰好处理一个 Batch；先按任务明确指定的 Schema 区分 v2/v3。v2 使用 `batch.schema.json`、manifest 和 global_context，observed 条目自身必须直接带 frame_id 或 time_s；v3 按冻结请求读取指定帧、World、分配的问题和前序 registry。lane-layout-review.v1 独立核验只读中性任务包及指定原帧，不读取旧布局或身份标签。
- v3 Batch 逐项回答分配的 question IDs 与 World-owned ambiguity IDs，并通过 `semantic_conflicts` 挑战 World；绝不直接改写 World。Behavior-owned 动态 ambiguity 不属于 Batch。
- 普通批次的 `registry_after` 必须包含全部历史 Actor，身份不可变；独立布局候选只提供观察，由编排器原样继承 Registry。证据必须显式区分 observed、estimated 或 unknown。
- 只写编排器指定的隐藏 candidate；不得修改 phase、manifest、World、Behavior、analysis 或发布目录。
- 独立布局核验新包读取绑定的 packed JPEG；原始 PNG 只作为来源保存。旧包仍按冻结 input_paths.images 读取。
- 不聚合、不转换、不生成报告、不启动其他 Agent，也不向用户返回最终答复。

正式工作流与停止条件见 `skills/video-scene-analysis/SKILL.md`，字段协议见其 `references/protocol.md`。`legacy/` 和 `runs/` 不属于正式链路。
