# Batch 协议

Actor 字段遵守 [跨阶段 Actor 合同](../../references/actor-contract.md)。正式 v3 始终提交完整 registry_after；身份字段使用共享类型，已知 hypothesized_lane 在验收时核对 World 引用，null/省略保持未知。不提交增量根字段，不重建或遗漏历史对象。

权威结构：[manifest_v3](../../schemas/manifest_v3.schema.json)、[batch_v3](../../schemas/batch_v3.schema.json)。Python 消费门禁为 v3_contract.validate_batch 和 video_case.validate_evidence/validate_registry。

新请求内嵌由正式 Batch Schema 派生的 `response_schema` 和内容摘要 `response_schema_sha256`。候选格式不含 session_id，新候选自行填写该字段会被拒绝；编排器注入真实 session 后仍按原正式 Schema 验收。已有冻结请求原样保留；没有私有候选 Schema 的旧请求继续使用原注入兼容规则。

任务包 state/requests/batch_NNNN.json 指定 input_paths、assigned_question_ids、assigned_ambiguity_ids、epoch 和 output_path。正式输出是 batches/batch_NNNN.json，模型仅写 batches/.batch_NNNN.candidate.json。state/batch_NNNN.json 不是合法路径。session_id 由编排器注入，矛盾的非空 session_id 仍报错。

每个分配问题/歧义都需要 observation 关联。status=observed|estimated|unknown 表示来源，answer_resolution=confirmed|needs_world_patch|partial|unknown 表示结论。confirmed 必须覆盖整个问题；needs_world_patch 必须关联已应用且覆盖全部 world_paths 的冲突，不能仅凭 observed 自动解决。

semantic_conflicts 的 world_path 与 proposed_value 必须是可直接替换的同形目标。新 candidate 若把 `roadside_areas` 集合路径与单个含 `area_id` 的对象配对，验收前只在 area_id 唯一命中、候选除 provenance 外恰有一个既有叶子发生变化时，确定性收窄为数组成员叶子路径，并把原始路径、值和 provenance 写入修复审计；不唯一、多字段变化、增加未知字段或替换后 World 门禁失败均拒绝。已正式接纳的历史 Batch 不改写字节，由 reconciliation 在内存中应用同一兼容规则并把记录写入正式 reconciliation 信封。

标准 v3 保留完整 registry_after 协议，不迁移为 actor_updates。Actor ID、type、identity_description、first_seen_frame_id 不可变。动态 Behavior-owned ambiguity 可以分配给 Batch 获取局部边界证据，合并不将其标为已裁决，最终裁决仍属于 Behavior。不得让每批生成另一份完整轨迹。

新建 Case 在 execution_policy 冻结 `batch_behavior_owner_routing=batch-behavior-owner-routing.v1`；仅该标记与 `question-bound-coverage.v2`、`vehicle-census.v1` 同时存在且 manifest 尚未冻结时启用答题交接：`owner=behavior`、`subject_field=actor_motion`、绑定已有 Actor 且仅有运动执行影响域的未决歧义，若没有身份、车道、道路字段、关联问题、Census 身份、作用域或主要事件依赖，则只交原 Behavior 回答。同一 Actor 另有未决身份、车道或事件问题时也保守保留 Batch 复核。不能只凭 owner 或“出画”文字过滤；混合依赖仍正常分配。没有标记的旧 Case，包括正在执行 World、尚未冻结 manifest 的 Case，不自动采用新规则。

该规则先完成原窗口、帧选择、跨批证据覆盖、容量扩展和 JPEG 打包，再移除重复答题项。普通 Census 批次、全部帧、窗口、保护帧及 coverage 行保持；coverage 中延后歧义的引用仅解释清点证据来源，不要求 Batch 回答。`packing.evidence_selection.behavior_owner_routing` 保存原分配、保留帧、来源 JSON 指针、原始条目及摘要；`n_zero_blockers` 以 `vehicle_census.evidence` 保留清点义务。World 原条目与 unresolved 不改，Behavior 原有 challenge_resolutions 覆盖验收继续生效。

带该记录的新普通 Batch 任务包携带 `behavior_owner_routing` 与定向 `task`：仅回答实际 assigned 列表，所有图片仍须逐帧 Census；两个 assigned 列表都空时只清点，observations 可以为空。新身份、车道、道路或事件反证仍须如实保留。正式 Batch Schema 和 Census 校验不变，缺帧或回答已延后歧义仍拒绝。旧 manifest 直接返回；无记录的旧任务包和原 worker 提示词原样使用，不在活动任务或恢复时补写该记录。

已有独立身份关联/拆分裁定时，历史 Batch 只读重放，不将其旧 registry_after 写回正式台账。共享 identity_revision.checkpoint_proof 验证裁定前 Registry 与历史链末尾一致、完整 manifest/Batch 哈希、原图与快照、已完成的身份提交及当前 World/Registry/reconciliation。合法裁定后的台账可与最后 Batch 不同；没有有效证明时仍使用原直接比较。未完成提交要求由正式身份恢复入口先恢复，历史重放不补凭证、不生成请求、不提交模型；已有旧请求缺失时按原冻结合同验证可重放历史，不事后伪造输入。无哈希的旧 manifest 可使用有效身份证明绑定的原 World 快照，但不得为有哈希 manifest 补造丢失快照。

新 manifest 保存 `world_sha256`，对应 `checkpoints/world/<hash>.json` 的实际文件哈希；每批请求读取该快照。`state/requests/batch_NNNN.registry.json` 是首批 World 身份或上一已接纳批次的 Registry 副本，请求记录其哈希。每批接纳后更新累计 `state/actor_registry.json`。冻结请求不得在活动任务中改写；恢复复用同一输入。旧 manifest 仅在源 epoch 内容可唯一验证时兼容读取，不事后伪造历史哈希。

冻结与读取 World 快照使用同一个 World 准入合同：启用 `world_admission_revision=world-admission.v1` 时调用共享 admission 校验，不另行要求观察阶段已补齐全部执行道路。新 Case 同时冻结已有 `fact_contract_revision=observation-execution.v1`；身份未知作为未决问题分配补证，重复身份、悬空引用和已知道路/车道矛盾仍拒绝。无该修订的历史 Case 沿用其原规则，普通恢复不补写版本字段。

新冻结的 Batch 先确定分配问题/歧义，再选择帧。除下述有据的规划范围排除及上述 Behavior 答题交接外，每个未决 question 和影响核心执行的 ambiguity 必须至少分配给一个 Batch；模型漏写 `critical_intervals` 绑定时，冻结器只使用该问题已有的结构化 frame/time 引用确定性补建取证窗口；无可用引用时回退到完整观察窗口。该补建只产生调度元数据，不修改 World 事实或将 unresolved 改为 resolved。写 manifest 前必须再校验范围内全部未决问题均已分配或有明确交接，否则停止冻结。

采用 observation-execution.v1 和 ego_vehicle_scene.v3/v4 的 Case，在 manifest 尚未冻结时先执行 `batch.isolated_opposing_count.v1`：单道路无路口 highway、已有车道均属同向道路、有无争议的物理隔离观察时，仅定位到 `world.road.opposite_direction_lanes` 且不绑定 Actor/事件/身份的问题或歧义不占 Batch。混合自车车道、隔离存疑或无隔离的项仍正常分配。manifest 的 `planning_exclusions` 记录 ID、来源字段、隔离证据及原因；World 的未知数量、原问题和 resolution 保留。该记录只排除复核任务，最终仍由 `execution_dependencies` 按实际 Behavior 检查对向侧是否被执行使用，不能据此直接通过交付。已有 manifest 原样重放。

每批四张上限内保留窗口首末见证帧，其余名额先纳入问题 evidence、identity_binding/localization.evidence_frame_ids、绑定 world_paths 的 field_evidence，再纳入结构化 actor_ids 关联 Actor 的证据及首末出现帧，最后补充窗口内部采样。比较所需帧可以位于本批时间窗口之外，但必须来自同一 Case 已解码源索引；不从自然语言猜帧，不将缺失的明确引用替换成附近帧，不自动重解码。`packing.evidence_selection.schedule_revision=unresolved-issue-coverage.v1` 标识该调度规则，并用 `derived_interval_question_ids`/`derived_interval_ambiguity_ids` 记录本批中由冻结器补建时间窗的问题。

独立盲布局复核在尚未冻结 manifest 的 `question-bound-coverage.v2` Case 中使用 `layout-support-sampling.v1`：自动生成问题的首末帧仍是必选；其 `world_paths` 保留为后续裁决的事实绑定，所列 field_evidence 是选帧参考，不要求把每条重复目击都塞入一个四帧 Batch。剩余名额从可用字段证据中按时间均匀选取，优先覆盖最早和最晚的支持帧；选中与未选中帧通过 `available_support_frame_ids`/`sampled_support_frame_ids` 留痕，失效引用仍记入 missing_evidence。独立模型只接收图片和中性问题，不接收原 World 判断；直接问题证据超过四帧时继续停止，不压缩或改写该证据。既有 manifest 原样重放。

packing.evidence_selection 的 selection_revision=question-bound-frames.v1 标识该规则；question_evidence_coverage 逐项记录 required_frame_ids、supplied_frame_ids、has_structured_references 和 missing_evidence。缺失原因区分源帧不存在、帧时刻不匹配、源图片不存在、四张帧预算不足及没有结构化帧引用。该清单只衡量引用帧是否提供，不保证问题在视觉上可解决，也不自动改变 World 的 resolution。

选中的问题/Actor 证据帧和窗口首末帧通过 protected_frame_ids 记录，JPEG 打包不能删去这些帧；无法满足保护约束与传输预算时以 packing_budget_exceeded 停止，不静默降档丢证据。new_source_times/reused_source_times 只记录最终打包帧相对全局索引新增与复用的源时刻。复用高清细节不等于浪费；不声称自动发现所有事件边界。已有 manifest 原样返回，普通恢复不重选帧或改写旧冻结包；需要新证据时新建 Case。

普通取证时长≤2.5 秒最多一批，2.5–5 秒最多两批，长视频最多四批；详细帧不超过十六张。新 Full World 的布局复核可另占一个计划批次，总数仍不超过四批；必要时合并普通窗口，不遗漏其分配问题。冻结后不随恢复重采样；新证据必须通过显式契约变更。零 Batch 仍生成最终 Registry。

新冻结 World 请求的 `layout_review_revision=lane-layout-review.v1` 为单道路无路口 highway Full World 生成中性布局核验问题；已有冻结请求不升级。manifest 对应批次在 `packing.layout_review_revision` 标明独立核验，使用 [独立布局模板](independent-layout-prompt.md)。可读输入只有本 Case 冻结原帧和中性问题，不含旧 World、manifest、Registry、旧车道编号或判断文本；`world_sha256` 和 `previous_registry_sha256` 仅为编排绑定，不能据此读旧结论。访问隔离为 prompt_only，不声称操作系统级沙箱。

独立候选只回传绑定元数据、request_sha256 和结构化 layout_observation；Python 不推断像素，只检查帧引用及声明，将其编译到正式 Batch.observations 并原样携带前序 Registry。它不重建身份、不改 World。观察 unknown/minimum/自相矛盾时仍可保存为待复核证据；结构错误、越界帧或哈希漂移则拒绝。reconciliation 决定是否送 World 定向裁决。

任务、恢复额度与错误重试只遵守 [生命周期协议](../../video-case-orchestrator/references/protocol.md)。

帧证据验收使用 [冻结帧证据合同](../../references/frame-evidence-contract.md)。每批新增观察、冲突证据和新增/改写 Registry 证据只能引用本批 manifest.frames；与冻结前序 Registry 完全相同的历史证据可以原样继承。新候选、存档候选复验、检查点恢复和只读交付预检共用 `_lib/contracts/frame_evidence.py:validate_batch_frames`，正式提交前拒绝不存在的帧与错配时间，不依赖可选 fidelity policy。

验收前仅对一个可证明无损的附加声明做本地撤回：已有 Actor 的前序 Registry 没有 `last_seen_frame_id/last_seen_time_s`，而本批候选新加的 `last_seen_frame_id` 不在本批帧索引中时，删除这对新字段并在 `deterministic_candidate_repairs` 保留原值。Actor、历史证据和其他候选内容不变；新 Actor、已有 last_seen 的改写、首见帧、观察证据及帧时间冲突仍由原门禁拒绝。该处理不增加模型 session，也不将包外帧变成已观察事实。


新 Case 冻结 `batch_evidence_capacity=batch-evidence-capacity.v1`：普通 Batch 默认最多4张；先按原规则跨批分配必需证据，仍有未覆盖引用时才允许相关普通批次扩到最多6张。新增名额只用于结构化必需证据和窗口边界，不增加普通抽样；总批数仍最多4，独立布局仍最多4张、单帧复核仍严格1张。扩展批次的所有证据在每档 JPEG 打包中保留，超出原请求12 MiB或单图2 MiB限制则停止，不删必需帧。选帧、打包、冻结 manifest、Batch 重放和只读交付预检共用容量合同；旧 manifest Schema 文件不改，在 Python 中按冻结策略组合6帧语法和严格用途校验。
