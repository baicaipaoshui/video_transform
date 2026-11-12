---
name: video-scene-analysis
description: 处理一个冻结的视频 Batch，回答分配的问题并提交证据、结构化 World conflict 和累计 Actor registry。
---

# Case Batch Worker

围绕分配问题判断缺口属于图像细节不清还是时序证据缺失：前者按任务包查看高清原帧，后者报告缺失边界。确认停车区域侧别不能同时确认宽度或所属全部道路；确认对向行驶方向不能同时确认对向车道数量。没有新增证据时保留 partial/unknown 并结束本批。

维护者按 [MELA 表达契约](../../../global-video-understanding/skills/references/mela-expression-contract.md) 将字段需求提炼到 Batch 提示词；运行 Worker 无需额外读取契约，不自行抽取新时刻或裁定降级映射。

先按任务明确指定的 Schema 区分 v2/v3；不要把 v3 规则套用到 v2。

v2 明确使用 `batch.schema.json`：读取任务列出的 manifest、global_context、上一已完成 Batch（若存在）、本批 JPEG 和该 Schema，不要求不存在的 v3 请求包。候选根字段为 schema_version、case_id、batch_id、observations、challenges、registry_after；session_id 由程序注入。每个 observed 的 observations[] 条目自身必须直接带真实 frame_id 或 time_s，嵌套 evidence[] 不能代替；多帧观察拆成逐帧条目，不能为总结伪造时间。estimated 自带 confidence，unknown 自带 reason，禁止 kind。只写任务给出的隐藏 candidate，不写正式检查点。

v3 输入以 `case_id`、`batch_id` 定位。第一步必须读取编排器给出的 `state/requests/<batch_id>.json`；只使用其中 `input_paths`，最终只写其中 `output_path`。正式 Batch 位于 `batches/`，不得猜测或访问 `state/<batch_id>.json`。普通模式读取包内冻结 manifest、World、本批图像、分配 question IDs 和上一批 registry；不得读其他帧或自行重建 World。以下布局与输出规则仅适用于 v3。

若 mode=lane-layout-review.v1，先按 [独立布局协议](references/protocol.md#请求与输入) 执行：只读中性任务包及允许原帧，不读取旧 World/Registry；候选遵守包内 response_schema，输出 layout_observation，不生成普通 Batch 根字段或身份 Registry。编排器原样继承 Registry，差异由 World 裁决。下文完整 Registry 输出规则仅用于普通模式。

输出遵循全局 `batch_v3.schema.json`：带 `world_evidence_epoch`、`answered_question_ids`、`answered_ambiguity_ids`、observations、`semantic_conflicts` 和完整 `registry_after`。每个分配项必须回答；observation 用同名 `question_id` 或 `ambiguity_id` 关联证据。确认原 World 只写 observation，只有 proposed value 不同时才写 Conflict；Conflict 不能直接改写 World。

v3 Observation 标记 `observed` 时，可以直接写帧/时间，也可以由其 `evidence[]` 中至少一条已定位的 observed 证据支撑；不得只有空数组或只有 estimated/unknown 子证据。v2 仍须遵守上述直接定位要求。

只写编排器指定的隐藏 candidate，随后立即停止；不得处理下一批、聚合、Behavior、转换、报告或发布。v2 兼容字段与完整证据规则见 [references/protocol.md](references/protocol.md)。
