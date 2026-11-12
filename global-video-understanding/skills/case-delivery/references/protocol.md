# 确定性交付

## 执行范围版本（2026-09-27）

新 Case 使用 ego_vehicle_scene.v4，继承 v3 的逐样本对向证据与动作依赖检查，并消费 behavior-census-scope.v2 的执行裁定。v3 冻结 Case 继续使用原规则：非车辆沿用既有执行范围；运动中的背景车辆仅在有逐样本原帧绑定的 observed 对向证据、且没有事件或动作依赖时裁剪。未证明对向的运动背景车辆保留；若显式声明对向却缺少逐帧证据，则在 Behavior 边界报错，不靠 background 标签或身份描述文字删除；静止路侧车辆保留。ego_vehicle_scene.v2 的“未被事件引用的 background 均裁剪”仅用于旧冻结 Case，ego_vehicle_scene.v1 保留非车辆排除语义。历史上部分实现未升级 policy_id 就增加了 background 排除；其证书只按 execution_scope.py 中明确登记的 implementation_sha256 兼容，未知历史实现明确停止，不从排除结果猜版本。证书摘要和观察来源仍须校验，旧 Case 不改写。

对向车辆缺少可执行 lane_id 时，裁剪只解除这些车辆的执行车道依赖；原始观察、对向车道数未知及其限制仍保留。`execution_dependencies.decisions[].actor_ids/event_ids` 只记录问题自身的结构化显式引用，不能把执行范围内全部参与者和事件当作该问题的依赖证据；对向车道数是否影响本次仿真由 `ego_carriageway_only` 的车道、边界证据和实际动作范围单独证明。

旧 Case 恢复优先复用输入哈希、策略哈希一致的历史执行范围证书及对应 review；无证书的 v1 使用原始语义。普通复用不迁移执行范围，新建 Case 才使用当前默认版本。publication-verify 按证书的语义版本验收；它仍使用当前 Converter 重放，若后续转换实现存在漂移，必须分别报告，不能仅凭范围证书通过宣称 target 字节复现。

## 新 Case 的私有绑定合同（2026-09-26）

公开 World/Batch/Behavior/analysis Schema 不变。新建 v3 Case 冻结 `conversion_policy_revision=conversion-policy.v1` 和参数摘要；Delivery 在转换前生成按 analysis SHA256 绑定的 `state/conversion_policies/<policy_sha256>.json`，并经 `--policy/--policy-sha256` 传给唯一 Converter 入口。实际默认值来自 Case 的 fallback_policy 与可选 conversion_defaults；省略 `allowed_degradations` 时自动包含 Converter 已登记补全类别，无需用户手填或逐项授权。显式清单仍逐字段限制，缺少授权返回 `conversion_policy_blocked/needs_review`，不触发模型补齐或重试。

旧 Case 未声明此 revision 时继续旧转换规则；普通恢复不增加 marker、不迁移、不重写已发布产物。已冻结旧授权清单且仅因 `conversion.fallback_unauthorized` 阻塞的未交付 Case，可经正式 `--enable-conversion-defaults` 恢复参数采用自动补全。采用记录 `state/conversion_defaults_adoption.json` 绑定 Case、参数、原执行策略、已有 analysis 与原 conversion policy 摘要；不改 case.json/execution_policy.json 或模型冻结输入。原失败回执保存在 diagnostics，原策略归档保留，新 policy 包含 default_completion 和扩展清单；恢复直接进入 converting，离线 replay 使用相同采用记录。已交付 Case、活动任务、输入漂移和其他阻塞拒绝采用。变化灯色和已知事实冲突仍不能被补值覆盖。

报告同时读取 analysis、conversion_result、target_checks 和 target，核对输入/输出哈希和验收回执；分别报告观察完整性、target 生成状态、真实 runner 状态，并列出实际执行补值。缺回执不再从 execution_ready 推断 target 不存在。

已完成普通单道路 highway 可通过 `user_scene_revision.v1.actor_initial_gaps_m` 指定现有运动车辆相对自车起点的中心距。只接受原始首点没有米制纵向值、首帧和车道已绑定、且仍在执行范围内的车辆；距离须大于冻结车辆包络并落在道路范围内。修订写入派生 analysis 的 `simulation_scenarios[0].initial_conditions`，不更改 `actors[].trajectory`；`scene_revision` 证书绑定父 analysis SHA256、修订内容和冻结参数。Converter 使用该初始值构建私有执行副本，目标检查重放转换并验证连续变道碰撞包络。父 Case 及其发布产物保持不变，同一修订复用原派生 Case。

离线工具有两个明确模式：`--mode checkpoint-replay`（默认）重建检查点链路，不能证明原发布字节一致；`--mode publication-verify` 校验四份发布产物哈希，并用原发布 analysis 和冻结转换策略在两个独立进程重放、对比原 target。两种模式都只写临时目录、不发布、不调用模型；真实 render 仍为 not_executed。

checkpoint-replay 与在线 Behavior 接纳共用 `prepare_behavior_candidate` 整理观察，在 Builder 前保留原始位置、速度状态和采样覆盖，不按估计速度重写坐标或自动延长轨迹。观察门禁通过不等于 execution_ready；执行缺口仍传给 Builder，后续私有执行投影与 Converter 分别负责获准的执行处理及 provenance。默认严格 candidate CLI 的 `prepare_behavior_execution` 是独立检查副本，不是离线重建或正式 Behavior 提交入口。

能力检查后进入 conversion 子阶段。Converter 的结构化失败通过回执传递 rule_id、path、related_paths、actor_id（如适用）、input_sha256 和恢复提示，由 Delivery 保留到 progress.error；道路同臂冲突归 World 裁决，不能通过无变化重试或 Converter 删除道路来消除。

输入仅限已验收 World、Behavior、Registry 和参数。Builder 为 v3_contract.build_analysis，质量授权 gate_degradations；转换由 [case-conversion](../../../../json-coverter/skills/case-conversion/SKILL.md) 的 CLI 执行。新建 Case 另冻结 `conversion-continuation.v1`，通过 `conversion_relations` 和 `conversion_semantic_gap` 两个正常文字会话，先解释时序关系，再联合规划数值；每个会话内累积保存，最多共用一个额外接续会话，仍消耗 Case 恢复额度。最终完整决策保持 `conversion-semantic-motion.v1` 和原 CLI 槽位。包含同向背景及晚出现车辆，身份未决交回上游。已知事实、target 时序关系与变道检查保持必需，失败不等速兜底。没有 continuation marker 的旧运动 Case 仍单会话，历史 gap 决策继续原样验证。

Builder 保留 observed、estimated、unknown 和冲突；Converter 在独立执行副本中执行“analysis 可用值优先，否则硬编码兜底”，并把字段和值写入 `conversion_result.json.fallbacks`。Converter 结果不得回写 World、Behavior 或 analysis。历史 Builder 策略仍记录在 applied_defaults；Converter 规则以 [case-conversion fallback](../../../../json-coverter/skills/case-conversion/references/fallbacks.md) 为准。

普通交付及离线重建将 state/probe.json 的媒体 duration_s 传入 Builder，绑定 metadata.duration_s/source_duration_s，并单独保留 last_frame_time_s。观察轨迹仍以真实帧 PTS 为准，媒体末尾的执行延长由 Converter 记录；最后采样时刻不再覆盖可用媒体时长。历史缺 probe 的本地重建兼容既有元数据。专用复核预览继续遵守其冻结时间合同。

观察结果可交付而执行连接缺失时，允许 Converter 在私有副本按固定规则补齐并生成 target；不因为缺少连接而强制进入 intersection_review，也不要求其证书。路由保留原有 execution_issues；补齐写入转换回执，analysis 的未知连接不变。显式复核/近似流程仍遵守各自冻结策略。

observation-execution.v1 的观察交接不要求至少三条完整路臂，也不要求完整的 incoming_roads、outgoing_roads 和 lane_connections。Builder 始终校验已声明道路、车道、成员和连接的引用与事实一致性；执行结构缺失直接记为 world.execution_topology_incomplete，交给 Converter 补齐，不依赖是否存在 unresolved 问题。analysis 待投影时使用观察拓扑校验；补齐后的执行副本仍使用完整拓扑及 target 校验。默认路臂只出现在执行副本和转换回执中，不能回写成 World/analysis 的观察道路。

已声明侧向道路但未观察到其车道配置时，允许保留空 lane_ids，已有车道的 ID、所有权和引用仍须有效；Converter 按现有空车道规则在执行副本补齐。已有三路臂不因侧向车道缺失变为四岔；完整执行模式及未启用观察合同的旧路径仍保留原来的完整性门禁。

依次校验 analysis、能力、转换回执输入/输出哈希，以及 delivery_checks.validate_target 的投影、拓扑和适用的运动约束。report_scene 根据已验收 analysis 生成报告，publish 输出四份文件与 SHA256。已有有效完成 Case 保持不可变。

可安全重复的本地交付操作仅对 EAGAIN/EBUSY/EINTR/ETIMEDOUT 最多重试一次，按 analysis 哈希与阶段去重；不把本地错误转成模型重试。

offline_preflight 在临时目录做只读复验，safe_to_retry_without_model=false、recovery_plan_verification=not_executed。它不发布、不改变正式恢复计划。当前无 MELA runner，render_verification 始终 not_executed，source_contract_validated 不能描述成真实运行通过。旧 video-case-orchestrator/scripts/offline_preflight.py 保留为同一实现的兼容入口。

已提交 Behavior 的范围投影及分析注解复用 execution_scope 的只读入口，保留同一策略、范围凭证和执行裁定。checkpoint-replay 若已有语义运动方案，先比较重建分析与原冻结分析（只允许执行范围实现指纹及其派生摘要变化），再使用原始分析字节及原方案转换；事实、输入绑定或排除范围变化必须重新裁定，不伪造新绑定。publication-verify 同样传入已验收运动方案，执行两次独立进程重放并比较原 target 哈希。

离线先验证原始 Batch 链和冻结 World/前序 Registry/请求/图片，再调用与在线相同的 census proof 校验正式 World、Registry 和 reconciliation。有效 census 可合法合并、新增身份或提前首次目击，最终 Registry 不必等于最后一个历史 Batch。无 census 时使用共享 identity_revision.checkpoint_proof 验证原有独立身份关联/拆分：原 Registry 必须等于已验收历史链末尾，manifest/完整 Batch 集合、原图和快照、提交完成标记及当前正式状态必须与证明一致；不存在合法裁定证明时才使用原直接比较。有后置 World 裁定时继续验证其绑定的 prepared 修订证明。凭证缺失、冻结输入漂移或 journal 未完成均阻塞，离线不恢复、不重写 Batch，也不提交模型任务。

当前 v3 转换回执使用 staged-fallbacks.v1：execution_fallbacks 与 scene_fallbacks 分别记录执行数据补齐和场景生成补齐，fallbacks 仅为两者按顺序合并。Converter 内部提交前验收和最终交付均调用同一 validate_target，传入完整回执；先重算执行副本和记录，再在临时目录重放底层场景转换脚本，核对场景记录及最终 target，同时保留独立语义门禁。两阶段为空也必须核对，记录丢失或损坏不得视为空清单。旧回执只核对完整两阶段合并结果，不按 reason 猜分类、不改写已完成 Case。该临时 JSON 重放不会调用模型或真实 runner。

新 census v4 的无身份观察级范围结论单独进入 execution_actor_scope.excluded_observations，身份保持未知、已登记车辆继续覆盖。交付须消费绑定当前 census 及 Behavior 的明确复查，Converter 回执的 census_scope_sha256 与 target 检查一致；撤销/更改范围后旧回执失效。报告列 issue、规则、完整 Case 范围、理由和证据帧，观察级排除计 done_degraded。

behavior-census-scope.v1 允许未决观察先进入候选分析，正式提交及交付仍验证全部身份或有效范围结论。剩余 census issue 可由绑定当前 Behavior/凭证/采用记录的 physically_isolated_opposing.v1 排除，源 census 仍未决；没有 proof、unknown 或 needs_identity 不能生成 target。群体 Actor 仅在其全部未决观察均证明排除且有匹配的 Behavior 对向 background 证据时，进入执行副本的 excluded_actors；正式 World/Registry/Behavior 保留群体。在线投影、范围证书重放、离线预检和目标映射共用相同规则及 census_scope_sha256，不按类型单独跳过门禁。

behavior-census-scope.v2 保持模型字段不变。Python 对单车和群体使用相同的物理隔离证明；单候选早期观察在没有同帧已登记硬冲突时只作 assumed_existing 执行假设。远处极小、全程无已知动作/道路依赖且候选 Actor 没有车道的 unknown 只保留观察；多候选 needs_identity 仅在同次回答明确指出自车右邻走廊、唯一右邻同向车道存在且无依赖时生成执行代表车。其他未决仍阻塞。已有冻结的 Case 输入提示若存在仍须通过来源哈希与依赖校验，但新 Case 不要求人工填写。执行副本保留来源观察，按 issue 签名裁定，生成代表车或裁剪无依赖 Actor，并将假设/省略写入范围证书和质量报告。不能把仿真假设写回 World、Registry、Behavior，也不能自动升级冻结 v1 Case。
