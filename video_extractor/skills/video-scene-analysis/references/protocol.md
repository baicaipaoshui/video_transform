# 冻结批次 Worker 协议

## v2 合同

任务明确指定 `batch.schema.json` 时只使用 v2：读取任务给出的 manifest、global_context、上一已完成 Batch（若存在）、本批 JPEG 和只读 Schema，不要求 v3 的 state/requests 包。候选根字段为 schema_version、case_id、batch_id、observations、challenges、registry_after；session_id 由编排器注入。不要生成 v3 epoch、回答清单或 semantic_conflicts。

v2 observations[] 的每个 observed 条目自身必须直接带本批真实 frame_id 或 time_s；只有嵌套 evidence[] 不符合 v2 Schema。多帧观察拆成逐帧条目，不能为总结补造时刻。estimated 条目必须自带 confidence，unknown 条目必须自带 reason；禁止 kind。修复时保留事实及证据，仅按明确字段路径修正结构。

## v3 合同

v3 的一个工作单元带不可变 `world_evidence_epoch` 和显式 `assigned_question_ids`、`assigned_ambiguity_ids`。新请求的 `response_schema` 是模型候选格式，由正式 `batch_v3.schema.json` 派生并排除 `session_id`；`input_paths.schema` 仅规定编排器注入字段后的正式 Batch。旧冻结请求没有 response_schema 时，候选沿用正式 Schema 除 session_id 外的要求。不得包含重写后的 World。`world_evidence_epoch` 只在根对象填写，从冻结请求复制；单条 `semantic_conflicts` 记录只包含 `conflict_id`、`world_path`、`proposed_value`、`evidence`、`impact_domains`。epoch 过期或回答未分配问题时，编排器拒绝提交检查点。

## 请求与输入

例外：mode=lane-layout-review.v1 的新冻结包执行独立布局核验，唯一格式为其 response_schema，唯一可读媒体为 input_paths.images 列出的图片（新包为打包 JPEG，旧冻结包可能为 PNG），不读旧 World、manifest、Registry 或额外 Schema。按包回传 request_sha256 和 layout_observation，不输出 registry_after/semantic_conflicts/session_id；编排器编译正式 Batch 并原样保留 Registry。此模式覆盖下文普通增量 Batch 的输入/候选要求，不扩张文件访问权限。模板见 [independent-layout-prompt.md](../../../../global-video-understanding/skills/batch-lifecycle/references/independent-layout-prompt.md)。

v3 任务以 `case_id` 和 `batch_id` 定位。先读取编排器给出的 `state/requests/<batch_id>.json`，只使用其 `input_paths` 与 `output_path`；v3 输入为冻结 manifest、World、前序完整 registry、本批图像和 Schema，不自行猜测 global context 或历史路径。v2 使用上节明确列出的输入。

只读取当前批次列出的 JPEG 路径。不得检查 probe PNG，也不得读取其他批次的帧。

## v3 候选输出

向编排器指定的隐藏 candidate 路径写入以下内容：

- Schema 规定的 `contract_version`、匹配的 `case_id` 和 `batch_id`，以及根对象的 `world_evidence_epoch`；
- `answered_question_ids`，有分配歧义时填写 `answered_ambiguity_ids`；
- 具有证据支撑的 `observations`；
- 对 World 原值有反证时写入 `semantic_conflicts`；没有反证则为空数组。确认原值或证据不足写 observation，分别使用 confirmed、partial 或 unknown，不把缺证自动当作冲突；
- 完整的 `registry_after`，而不是增量。

每个 question 只回答其分配的事实。身份题确认同一对象后可回答 confirmed，车道题仍无法辨认时单独回答 partial/unknown。confirmed 必须覆盖该题要求的证据帧和时间窗口；仅确认局部窗口不得称为整题确认。未知不是反证，真正与 World 或其他候选相反的事实须同时通过 semantic_conflicts 提交。原问题捆绑多个事实时仍按整题保留 partial，不能靠说明文字自行删掉它的 impact_domains。

不要设置 `session_id`；编排器会在 schema 校验和原子提交之前注入实际返回的 session ID。

v3 不允许 `challenges` 根字段。`challenges` 仅属于显式 v2 请求使用的旧 `batch.schema.json`；v2 按其冻结请求和 Schema 输出，不套用 v3 的 epoch、回答清单或 conflict 结构。两种版本均不授予 Batch 改写 World 的权力。

## 证据与 registry 门禁

- v3 `observed` 必须在同一对象直接带 `frame_id/time_s`，或由非空 `evidence[]` 中至少一条带 `frame_id/time_s` 的 `observed` 证据支撑。v3 多帧总结优先使用后者，不要为父结论伪造单一时间点。v2 observations[] 的 observed 条目必须直接定位，不能使用此 v3 嵌套例外。
- `estimated` 必须带有 0 到 1 之间的 confidence。
- `unknown` 或 null 必须带有 reason。
- Actor 的 ID、类型、首次出现帧以及身份描述都是不可变的。
- 所有历史 actor 都必须继续保留；重复 ID 和悬空引用都属于无效。

## 停止条件

写完这一个 candidate 后立即停止。不得更新 progress、处理其他 batch、执行聚合、转换、报告或发布。绝不能读取 `runs/` 或已废弃的工作区。

当冻结 response_schema 要求 vehicle_census 时，先独立清点每帧车辆，再关联旧编号；每帧一项，existing/new/unresolved 显式区分，疑似旧身份合并时使用 unresolved 和 candidate_actor_ids。新对象必须进入 registry_after，同帧独立车辆不能共用编号；车头和连接挂车不能拆成两车。普通身份字段仍不可修改，由编排器的受控身份修订入口处理。旧请求不追加该字段，独立布局批次不承担此任务。

binding 的 existing/new 按本批前序 registry 区分。本批新增编号在 first_seen_frame_id 明确写 new，后续确认目击仍写 new；unresolved 可引用前序编号或当前时刻已在本批合法登记的新增编号，不可引用未来登记项。早于旧编号冻结首次出现的疑似目击保留 unresolved，不自行改写或扩展旧身份连续性。编排器只规范有首次登记依据的后续标签，保留原候选及修正记录；未决身份进入现有 World 裁定门禁。
