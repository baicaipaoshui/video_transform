# 生命周期与恢复协议

## 新 v3 Analysis 文字取舍与合成 Target

新 Case 冻结 `analysis_reconciliation=analysis-reconciliation.v1` 和 `synthetic_target=synthetic-target.v1`；旧 Case 不隐式采用。Builder 仍拥有 World/Behavior 的事实组装。只有多条未决观察的同物体文字、同帧结构字段冲突、事件/轨迹冲突、事件所需 Actor 遗漏或无法从逐帧证据确定的事件车道摘要，才形成小型文字请求；无问题跳过模型。`json-coverter` 在一次普通文字任务中逐项给出沿用、来源支持的缺值派生或保持未知，并可提议临时执行关联。编排器验证冻结请求哈希、精确引文、字段白名单与完整覆盖后保存取舍回执；失败则保留未知。正式身份、原始观察和锁定字段不由此任务改写。独立 description fallback 仅在回执仍绑定同一 Analysis、且两条签名目击的帧/时间/车道和区分锚点一致时，才复用一辆临时执行代表车。

正式 Converter 与原校验先运行。新 v3 Case 若在终态仍无 Target，独立 `synthetic_delivery/target.json` 提供一条固定直路和一辆自车，验证四个 MELA section、引用及下游路网格式；manifest 标记 `synthetic_unverified`、列出省略的 Actor/事件和原失败。它不是原视频复现，不写回 Analysis，也不使严格 Case 获得正式发布状态。成功的既有 Case 不触发合成兜底；已有冻结 Case 不迁移。

## Description B 的执行与未建模来源

新建 B 投影在现有 `projection_receipt.json` 冻结 `description-source-coverage.v1`。先复验完整 Analysis 的原来源合同，再逐项核对执行 Actor、既有证据排除与未建模风险：参与执行者保留原轨迹；排除者重放原 Census/Scope 凭证；无法定位的非 Ego 车辆只有全部原始帧、时间及 Batch 来源引用完整、无已知执行位置、无核心事件或必要交互依赖时，才可留在现有 `unmodeled_observations`。原始 `participant`、车道未知和限制不改，潜在 Ego 交互及碰撞均标为未验证。缺少来源索引的旧数据不据此获得新的风险省略许可。来源审计视图只在 B 的来源/运动复验内使用，不传给 ConversionWork、Converter 或 Target；原 Core 来源覆盖与身份验收不变。无此回执版本的既有冻结投影按原逻辑回放，不覆盖、补签或迁移。

准备、重启和 `check` 均从冻结来源重新构建同一投影与风险记录。`core_action_omission`、必要车辆或来源绑定的相对运动约束遗漏只留阻断草稿，不能准备为合格 Ego 场景或发布成功 Target。已经进入 ConversionWork 的数值、运动或碰撞失败不能通过自动 minimum scene 删除车辆规避。原 `body_progress`、未完成超越、原数值与连续碰撞检查继续执行；全部检查通过才原子发布已有等级 `unverified`，并披露 `collision_scope=modeled_vehicles_only` 及周边交通未完整复现。JSON 存在本身不代表验收，不能冒充 `core_scene_approximate accepted`、严格完成或来源无关的 `synthetic_unverified`。

## v2 Batch 格式与有限修复

新建 v2 Case 冻结 `state/v2_batch_policy.json`（`v2-batch-contract.v1`）。候选提示从原 `batch.schema.json` 派生，仅排除程序注入的 session_id；observed 观察条目必须直接定位，多帧观察逐帧输出，不把 v3 的嵌套证据例外用于 v2。正式 Schema、事实和验收保持不变。

格式失败记录 JSON Pointer、Schema 路径、失败规则及要求，修复提示不先回显庞大错误对象。同一字段/规则错误允许一次修复，第二次相同错误后停止；每个 Batch 累计最多提交3次（含取消或传输失败），显式恢复不清零累计次数或错误记录。有新字段修复进展仍受总次数限制，已接纳检查点优先复用。无此策略文件的既有 Case 不迁移、不改变冻结提示或预算。

## v3 核心动作遗漏恢复

`core_scene.motion_continuity` 命中后保留严格失败与原始 Behavior，直接进入归档的独立 description fallback；此错误不追加视频理解 Behavior 重试。Python 只把同帧、同时间、已绑定到注册 Actor 的观察归入车道约束；旧 Behavior 车道与 detail 单列为争议，不作为模型回答或下游轨迹的车道证据。已确认的两个端点构成不可删除的变道及观察时间包络，World 首帧保留为更早佐证，包络不表示精确越线时刻。缺少独立车道观察的中间帧保持未知；原变道合同无法执行的起点前未知采样仅进入 Analysis 不确定性说明和审计记录。独立 Analysis 的 ego 首帧车道只在原始 World 与独立 Batch 一致时修订，后续延续注明未验证仿真假设。原 json-coverter 的归档文字任务最多一次，候选逐字段验收；终态失败且没有活动或未决任务时，Python 用确认端点继续准备 Analysis，再进入原 ConversionWork 两阶段和 Converter。仍无法表达时可检查既有 minimum_scene；若核心变道未恢复，只保存阻断的诊断候选，不发布为有效 Ego 场景。来源改变和合同损坏仍阻塞。已有独立输出不被覆盖；显式 `--description-fallback prepare|resume|check --description-root /absolute/semantic_revision_v1` 将三个动作绑定同一目录，该目录发布记录分别报告 `target_generated` 与 `core_event_fidelity`。

## v2 独立最小场景兜底

新建 v2 Case 冻结 `state/v2_minimum_policy.json`（`v2-minimum-scene.v1`）。重复格式错误、有限次数耗尽或内容/确定性转换停止后，入口以实际 Case ID 自动尝试一次独立交付；显式 `--description-fallback off` 禁用。活动任务、受理未明和未对账的本地超时不触发兜底。旧 Case 不迁移；可对已停止旧 Case 显式调用 description fallback prepare/resume/check，在隔离目录只读复用来源。

适配器优先复用已有 Analysis，保留核心动作及其对象；缺少 Analysis 时，复用已保存 Global 的结构化道路/自车事实，或原短路口编译器。无法可靠表达的主要动作、核心反证或道路缺口保存 draft.json，不能改成直行成功。外围车辆、群体和未定交互进入省略及风险记录；原 Case、回答、Registry 和图片不改。数值由原 Converter 投影补全及有限规划器生成，已知数值保持，默认值与执行续接明确标注为仿真假设。

原 Converter 在独立目录执行；复验原 Target Schema、引用、来源投影、完整时间运动解码、原数值计划、同车道连续间隔及适用的原变道碰撞检查。仅全部通过后原子发布 `description_fallback_deliveries/<case_id>/v2_minimum_scene/output/target.json` 与 manifest.json，状态 unverified，不宣称严格或核心近似 accepted。失败只留 draft.json 和被拒候选，不发布成功文件。快照和检查点绑定全部原 JSON、视频及证据帧哈希；重复运行复用冻结计划与已通过输出，不新增模型调用或第二轮转换尝试。检查时原检查器仍会做确定性来源重放。

## 保留车辆与未观察尾段近似

`retained-unobserved-motion.v1` 是独立、显式冻结的执行许可，复用 `execution-assumptions.v1` 来源凭证。仅接收已验收 Registry 的普通同向车辆：唯一确认锚点为 0 秒、Behavior 保留该帧/时间/已知车道/身份描述，原文明确首帧与 ego 并行，且无待查身份关联、新观察、额外动作/交互/其他车辆/保真依赖；World 必须有仅该车的 Behavior-owned 纯未观察尾段疑点。有限词汇无法识别的描述继续原规则，不能把所有 `executor_primitive` 未知放行。新提交的不同描述、反证、混合限制不被旧证据覆盖。

凭证记录原 Registry、原疑点、原限制、输入摘要和许可；范围接收跳过重复的单帧相关性证明，但任何过滤不得删掉该车。原始 Behavior/World 不改；执行副本中精确匹配的尾段限制可标为 bounded_uncertainty。首帧并行只约束 0 秒，后续不推断真实加减速或变道。最终来源校验重新计算同一凭证。

`conversion_relations` 由程序覆盖精确绑定来源：首帧并行、未观察尾段；不全局改写 `motion_unresolved`，身份未知及新时序关系仍拒绝。`conversion_semantic_gap` 在其他车辆计划完整后生成该车尾段：已知位置/速度优先，否则在首帧并行范围内尝试纵向偏移 0、-1、1、-2、2 米，速度尝试已接受 ego 初速、该速度±5、±10 km/h，按固定顺序选择通过现有关系和连续同车道检查的组合；最多两个这样的车辆。主变道源/目标车道另作保守纵向检查，最终 target 仍执行原连续横向碰撞包络验收。其他车辆计划与主动作不为该假设改写；不能借普通前方车距默认值覆盖并行证据。

原模型 save 不改写，程序派生的覆盖和计划在最终决策中保存，检查点/重启重新计算，补点不带伪造观察帧。所有数值及保真损失写入执行回执和报告。无可行有限方案以 `execution_assumption_infeasible` 停止，不发起模型修复、不重置预算。其他模型方案本身的错误保持原分类。

旧 Case 保留冻结语义；仅首次 Behavior 前可显式运行 `adopt_execution_assumptions.py --case-dir ... --retain-unobserved-motion`，绑定原 case、策略、World、Registry 和 census，不覆盖旧记录或调用模型。仅接通该规则不等于真实视频跑通：缺少实际 Behavior 或其余正常运动回答时，离线局部检查不得称为 target 交付。

## 自车相关性与观察级裁定

新 Case 冻结 `world-census-adjudication.v6`、`behavior-census-scope.v4`、`census-execution.v2`。旧冻结合同不变。v6 World closure 同次输出 `relevance`，逐条覆盖未决观察和已供帧中已登记车辆：retain/background_candidate/unknown，附源帧和理由；这是初步范围意见，不是排除或身份结论。已有车辆缺失的同帧区分记入 `pending_distinctions`，`identity_verification=pending_scope`；新身份去重、正向关联、反证、合并/拆分、原锚点和已知道路冲突仍执行原门禁。

新 Case 的 `census-lane-position-evidence.v1` 要求 Census 首次确认车道或改变物理走廊时，给出该原帧左右边界见证。同走廊的持续观察复用已知锚点，空车道保留未知。`ego-lane-observations.v1` 在 World/Batch 的现有证据行记录显式 Ego 单帧候选，编排器只为尚无有效 point 的走廊变化端点调用原定点裁决。每 Case 最多冻结一份含此类候选的复核请求，合计至多 12 张原帧，沿用原任务预算。`ego-lane-point-handoff.v1` 将通过复验的点交给 Behavior 来源和运动连续性验收，普通与 Core 候选都须保留确认端点。仅有文字或证据不足时保留原来源与未知，历史请求按原冻结策略复验。

正常 Behavior 同次 `census_scope_review` 对每条未决观察和待查区分回答。`irrelevant` 需要覆盖完整冻结采样窗口的逐帧位置/路径关系/交互依据，以及明确的 identity/path/interaction dependency=false；单凭远小、未列事件或无法辨认不足以排除。对象仅须在源帧可见，其他帧可证明道路上下文，不虚构连续可见。`unrelated_observation` 只结束该条未决观察的身份追查，即使其候选 Actor 有已知车道也不一刀切阻断；不删候选 Actor，也不改变其后续相关证据。`isolated_opposing` 要求逐帧反向、物理隔离、不可达自车道路，且不与 World 冲突。

已登记对象的待查区分只能由源帧两车特征的 `distinct` 证明，或被比较对象的隔离对向证明解除；后者还要求 Behavior 中一致的 background/direction_evidence 且无执行依赖。未知/相关而无身份依据继续停止，不能将 pending 视为 passed。所有正向证明仍验收；范围待查不授予合并权限。Behavior 后发现依赖或证据变更会使范围凭证失效，交付前须重新裁定。普通 Batch 未决身份沿现有有预算的 World 恢复；只有待查同帧区分而证据仍不足时保留诊断，不给未知任务额外额度。

范围凭证绑定完整 Behavior、原 census 证书、观察/帧和依据，投影、Builder、Converter 与离线重放共同验证。`observation_excluded` 不进入候选 Actor 删除集合，仅可闭合准确绑定该观察的 bounded_uncertainty，带 Actor/事件/道路依赖的限制不随之删除。第一次 Behavior 前允许范围待查，正式交付必须完成检查。

首次 Behavior/accepted census 之前的 v4 外观冻结或同帧区分失败，可用原 `--retry-failed --resume-world-closure-session SESSION` 显式采用 v6。独立 `relevance_adoption` 记录绑定旧请求、失败候选及哈希，保留全部原帧、Batch、布局输入和策略文件；不会自动修补旧回答。这是范围语义升级，与历史 v5 information_adoption 区分。须确认最近旧任务远端完成、无活动或受理未明任务；只允许一次独立合同修复任务，普通 used=2/2 不清零、不自动退款。重复命令复用已有任务，未收到回执的提交保持待对账，不重复提交。新 v5/v6 初次闭合使用 reserved→dispatching→submitted 持久绑定，明确未提交才可释放本次预留；旧无绑定账目不猜测删除。

## 登记与后续信息更新

v5 提供以下登记更新能力，v6 继承这些能力并按下节增加观察级范围裁定。普通已有车辆不再由模型重写 `identity_description/color/vehicle_class`，Python 从冻结 Registry 继承；新目击写 `sightings` 并在验收后累积到证据。原请求的 `before/registry.json` 和每轮不可变凭证保留历史登记，当前 Registry 是已验收信息的视图。

已有 binding 车辆可通过 `corrections` 显式纠正描述、颜色、车型。每项绑定准确旧值、新值、原因、首见帧及至少一个更晚目击；必须先通过完整身份关联、候选排除和全部提供过渡帧检查。纠正不能修改 ID、道路、车道或首见，不能丢弃已知事实为 unknown，comparison 无纠正权。合并/拆分仍走专用证明，不使用普通纠正。凭证 `information_updates` 保存登记快照与修改依据；Behavior 使用最新 Registry 的字段，同时继续满足全部历史目击与区分证明。自由文字解释不等于程序独立核实了图像真值。

正向关联的反证可用 `subject_sighting_frame_ids` 引用同一车辆的逐帧观察；Python 展开后执行原校验。未知引用、无正向绑定的引用、缺失同帧车辆区分仍拒绝，不自动填充身份结论。缺项反馈列出每个车辆、帧与缺少的 ID。v1–v4 冻结请求保持原语义；普通重试不隐式改写旧请求或历史候选，旧 v4 Case 可通过下述显式合同升级采用 v6；历史 v5 请求仍按原语义重放。

## 已登记车辆的早期身份关联

新 Case 冻结 `actor_identity_review=actor-identity-link.v1`。路由检查 World 的未决身份问题和 Behavior 的身份局限；绑定单一已登记车辆且只涉及身份/相对运动的缺口，可复用 actor-motion review 入口的 `identity_link` 模式。Worker 由 global-video-understanding 承担，按 [身份复核提示](../../behavior-understanding/references/identity-link-prompt.md) 查看最多 12 张哈希绑定 JPEG。json-coverter 仍只读取已验收文字，不承担视觉身份判断。

第一版仅支持保持 actor_id、actor_type、identity_description 不变，将已有车辆 first_seen_frame_id 提前。确认需要更早、原首次、更晚至少三帧，覆盖所提供的中间帧，并区分同帧其他已登记车辆。`different_vehicle` 或 `insufficient_evidence` 保存终止结果，输入不变时不再次提交；不自动新增、合并、拆分车辆。未绑定对象、混合其他事实的缺口、超过窗口预算的证据请求继续 needs_review。

`same_vehicle` 通过专用合同后，编排器写入可恢复提交日志，成对修订 World/Registry 并递增 evidence_epoch。普通 validate_registry 的身份锁保持不变。旧 Batch 原文件不改写，reconciliation 回执与身份凭证仅取代本次批准的关联。补充帧只进入凭证绑定的 Behavior 证据并集，不扩充历史 World 全局索引。先归档并退役旧 Behavior 输入、候选、语义方案、target 和检查，再使用原 Behavior 及新凭证重建；旧样本、已知数值/车道、其他车辆、事件和无关局限必须保留。新 analysis/target 必须重新通过全部门禁。

身份复核和 Behavior 重建分别计入原有两次额外模型任务预算，不另设恢复预算。已完成 Case 经显式入口创建派生 Case，原 Case 不变；派生 Case 继承历史任务和已消耗预算，不能借派生重置额度。子 Case 显式采用文字运动方案版本，旧版语义任务已经执行时，新语义任务仍按历史预算规则计费，可能因剩余额度不足停止。恢复命令复用同一派生 Case；中断提交先恢复，不再调用模型。

## 逐帧车辆清点与误合并修订

新 Case 冻结 `vehicle_census=vehicle-census.v1` 和 `actor_identity_split=actor-identity-split.v1`。普通 Batch 的私有 response_schema 要求 vehicle_census，公开持久化 Batch 中该字段可选以兼容旧合同。覆盖实际提供的每一帧：vehicles 先列独立物理车辆（不拆连体车头/挂车），再关联 existing/new/unresolved。代码验证帧/时间、唯一对象、同帧已确认身份不重复、新车登记及首次出现一致；它不根据像素保证车辆清点完整。未决对应记录作为结构化身份缺口，Behavior 提交前停止，不允许空 semantic_conflicts 将其隐去。独立 lane-layout-review.v1 不增设身份任务；单帧 Batch 只清点该帧，不扩展窗口。

旧冻结 `census_admission=vehicle-census-admission.v2` 的 Case 保留原规则。existing/new 是编号在本批前序 Registry 中的来源；新编号须在实际首次帧声明 new，后续帧 existing 写法可确定性转为 new。未决候选允许引用本批已合法登记且不晚于当前时刻的新增编号。旧编号在冻结首次帧之前的未经裁定关联退回 unresolved；不修改 Registry、观察描述、车道或数值。原候选快照及逐项差异通过现有 candidate_snapshot/diagnostics 保存。普通候选、提交前 precheck 和失败归档恢复共用此入口；历史已提交且未冻结 v2 的 Batch 保持原首次出现解释，不重写旧 policy/request/manifest。规范化前同帧重复身份仍拒绝，不能借退回未决隐藏错误。归档恢复先核对 Case 内路径、文件名哈希及既有完整原始快照，复用 World 归档恢复的绑定规则；篡改内容不能作为新的原始回答验收。归档重验失败记录源/请求/World/Registry/manifest/索引/代码指纹及具体行项，预算耗尽信封保留这次失败；各 Batch 在检查及提交之前记录实际 substage。模型额度、活动任务对账和新任务策略不变。

新 Case 冻结 `census_admission=vehicle-census-admission.v3`，沿用 v2 合同与无新增字段的 Batch 输出。World 提示将不可变 identity_description 限定为首次目击外观；入库规范化把显式提到较晚 frame_id 的后续描述从该不可变字段撤下，保留可分离的首次目击描述，无法安全分离时只保留结构化首见属性。原候选及差异保存在诊断中；自由文字未标帧的隐含连续性仍不能靠代码验证。Batch 入库时，若两辆已登记同色同类车曾同帧出现，而某 existing 关联距该编号上一次已验收清点超过 1.5 秒且没有受理的中间目击，则将该清点退回 unresolved，候选包含原编号及可混淆的共现编号。首见帧、先前已验收的清点和当前较早的未退回清点可作为时间锚点；Registry 自由文字及未验收 evidence 不能证明连续性。同步撤回该编号本批依赖被退回帧的新增 evidence、last_seen 及其他可变属性更新，保留原始 candidate 和修复记录。如果前序冻结 Registry 已经把同一待定帧写成观察证据，Batch 不覆盖它，非重试地停在身份所有者复核边界。此规则是需裁决风险筛查，不证明未触发的关联在视觉上必然正确；未决仍由既有 world_closure 取证与裁决，证据/任务预算不增加。旧冻结 Case、请求与检查点保持原验收语义。

受控入口复用 `--review-actor-motion ACTOR --actor-review-mode identity_split`。不自动调用额外模型；新未完成 Case 需已冻结拆分策略，已完成父 Case 创建或复用显式派生 Case，父产物不改写。用户明确要求复核时可检查未主动报告的身份错误，但请求不等于已确认拆分。每个修订最多处理一个旧 Actor 拆成两辆；旧编号保留原首次出现对象，新编号由代码分配。至少一个同帧共现证据、每车至少两帧，完整覆盖请求帧，区分其他已登记车辆。选取已有 probe 的开头四帧和该身份所有绑定证据/旧轨迹，最多12帧，不重新解码、不静默删减。多对象歧义、涉及已验收事件、需要同步修改已知车道/数值或超出预算的范围停止，不能用拆分绕过其他事实门禁。

split_confirmed 回答的两组 sightings 取代旧混合身份描述/证据；旧证据保留在哈希绑定 before 快照，原轨迹每个样本必须精确分配给一辆车。复用身份提交日志原子恢复 World/Registry、递增 epoch、退役下游并重建 Behavior；其他车辆与原事件、未解决局限必须保留，已知样本数值/车道不得改变。Behavior 逐车覆盖修订凭证中的可见帧。后续 json-coverter 仍仅依据文字生成执行近似。insufficient_evidence 持久化并停止，不重复提交相同输入。

身份修订不重置预算；任务计费沿用已有恢复策略，余额不足时保持明确阻塞。旧 identity_link 合同保持原能力，不将其 different_vehicle 自动当作拆分授权。已冻结请求、旧 Batch、已完成 Case 不隐式升级。

## 执行协议

### 已有 World closure 的 census 联合裁定

新 Case 在 fact baseline 后、首次 Behavior 前运行主动事实对账。可定位的同 Actor／同帧车道争议使用现有 Actor 原文时间线生成 `diagnostics/question_review/alignment-*.request.json`，绑定原始及有效 World、Batch 原文、物理边界、原帧与哈希；需要视觉判断时复用 `world_closure` 任务及其预算、远端受理对账和恢复。Python 只接纳请求内的确认或改判并发布局部 fact version，随后重新对账。证据不足、未绑定的基础事实、来源或版本变动继续阻断；不由 Reconciliation 生成变道事件，也不因无新证据重复请求。

新建联合请求按冻结 Registry 类型附加身份选择说明：`existing_individual_actor_ids` 仅包含本次可比较的个体车辆，`vehicle_group_context_ids` 仅作群组上下文。群组成员关系不能编码为 existing 个体身份，群组多帧 sightings 不能证明成员连续性；无法确认个体时沿原 Schema 保留 unresolved，不要求猜测新 Actor。证据计划、原同帧独立性验收及已有群组规范化条件不变；已冻结请求原样复用，不补写新说明。

核心近似在没有可采用的 Behavior 候选时，原样返回 World、Batch 或 World closure 的失败阶段、错误详情及预算记录，不进入 Behavior 归档恢复。失败 Behavior 的既有恢复入口不变；此错误传播不增加重试额度，也不重新提交模型任务。

新 Case 冻结 `world_census_adjudication=world-census-adjudication.v4`。已触发的布局 closure 携带未决 census 时，在同一次 world_closure 调用使用私有外层合同。v4 在没有布局需求、census 未决且尚无 accepted 时，也可在首次 Behavior 前条件性调用同一 World 裁决入口；这不是每个 Case 的固定额外调用。已有 partial accepted 仍走原显式续接。旧布局 request/candidate 与编译器保持内层合同。v1/v2/v3 已冻结外层请求继续按原 Schema、图片和验收规则执行。

v2 在打包前生成 `census-evidence.v1`：保留布局原证据、每条未决原帧、已有身份的防重复登记锚点；关联目标范围为本次记录候选并集，无候选记录还允许比较 Registry 身份。每条关联保留其源帧至目标首次帧（提前关联另含更晚目击）的已有全局/Batch 证据及已登记目击；用已有 probe 填补超过 1.5 秒的检查间隔。早于所有候选首次出现的记录另检查最早锚点前一张 probe，不再固定追加开头四帧。无候选的范围排除还保留视频首末帧。相同图片复用但每条 issue 独立回答；不基于图像差分或文字相似度判定身份。

证据计划在图片压缩前冻结，编译与凭证重放从哈希绑定的原输入重算，不能通过删图片降低正向关联要求。最多 12 张，必要图片缺失用 `identity.census.evidence_missing`，必需集合超限用 `identity.census.evidence_budget_exceeded` 并报告数量/上限；均不提交模型。1.5 秒是检查排程约束，不是连续身份保证，遮挡或其他不足仍 unresolved。图片打包复用现有分辨率、质量、单图与总量预算，并计入请求内容和 Schema 的估算。

v2 的 `candidate_refutations` 用源对象跟踪和已有身份锚点之间的同帧不同车证据否定候选；改绑还必须满足目标的冻结 links。独立新车须区分全部 `comparison_actor_ids`，避免将已有车重复登记。`role=comparison` 仅用于上述比较，必须保留已有身份描述和原首次锚点，不写入 Registry、不增加 Behavior 轨迹要求；`role=binding` 才进入原编译提交。不能用 identity_split 代替候选纠错，无法证明的部分保留未决。

v3 继续复用 v2 证据计划，增加两项约束：`evidence_scopes` 分别列出 layout/census 允许引用的图片，内层布局完整 decision Schema 和合法 frame_id 枚举进入外层响应 Schema；`identity_groups` 表达已有登记的重复身份证明。组内每个原首次帧/时刻/已知车道必须匹配，完整覆盖冻结 links 和已提供过渡帧，不得有同帧不同对象、已解决身份事实或已知结构字段冲突。编译器选择最早首次帧、其次 ID 顺序作为规范身份，保留道路/静态性和证据，生成别名凭证；普通身份锁不放宽。只有结构化、纯身份、被证明完整覆盖的未知问题才能解除；运动或路网问题不随合并解除。Behavior 的 Actor 与事件引用均不得恢复旧别名。

合并成员以当前已验收 Registry 为准，允许 Batch 后续登记的车辆尚未出现在初始 World 中。World 存在的同名记录作为额外事实约束；规范身份仅在 Registry 中存在时，经完整合并证明后从该记录构造 World 条目并保留组内已知事实。不得假定两份身份集合相同，也不能绕过缺锚点、同帧不同对象或已知事实冲突。未参与合并的普通已有身份仍保留冻结描述与外观；失败诊断给出 Actor、字段路径及冻结原值，交原内容修复流程处理。

新 v3 Case 已生成联合外层请求时，首次 world_closure 计计划内正常任务，内容修复才消耗共享两次恢复额度；布局单独 closure 与冻结 v1/v2 保持原计费规则。增加计划内任务不会减少真实模型费用。归档 candidate 先校验请求、文件哈希与源输入并尝试确定性验收，通过时无需模型任务。

历史 v1 Case 如果尚无外层请求、world_closure 尝试/session/candidate 或受理未明提交，并且旧选帧超限，普通正式重试可创建 v2 私有外层请求，记录 `contract_upgrade=pre_submission_evidence_budget`。不改旧 execution_policy、Batch、内层布局请求或预算。已有外层请求绝不原地升级；已完成 closure 仍走下面的显式原 session 续接入口。

逐项覆盖 records，支持 existing/new/excluded_opposing/unresolved。新 Actor ID 由编译器分配；新车至少两次目击，同车提前关联需早期、原首次、更晚和提供的完整过渡帧；误合并仅一旧身份拆两车，各至少两次且同帧共现，保留旧首次对象。明确区分同帧既有车辆，禁止把不同对象绑定一个身份。范围排除仅无候选对象、物理隔离对向且无交互，覆盖请求首末与相关帧；道路侧别或交互不明保留未决，不使用描述关键词排除。合法已知道路/车道等字段不丢失，未知尺度不补造。

复用 lane_layout.commit.json 验证旧新 checkpoint 后提交 World、Registry、reconciliation 和 `state/census_adjudication.accepted.json`。凭证可重放编译，绑定全部 Batch、图片及输入快照；部分未决保存已验收成果，Behavior 仍停止。凭证进入 Behavior 哈希输入、补充帧和提示；候选及交付缓存复验身份、目击和排除依赖。原 Batch 与普通身份锁保持不变。

`--case-id ID --retry-failed --resume-world-closure-session SESSION` 显式恢复首次 Behavior 前的 identity.census.unresolved。只接受最新完成 World closure 的原 session，先核对远端终态、未决提交、source/参数、已完成 reconciliation 和剩余 Case 预算。新请求在 diagnostics/world_census_adjudication/continuation，旧 policy/request/candidate 不覆盖；一次授权仅向原 session 追加一个 task，仍按 world_closure 额外任务消耗原预算，不新设额度。活动/受理不明任务先对账，不重复提交。有效 candidate/journal 优先恢复；保留部分未决不代表交付成功。不能组合其他策略迁移或转换恢复，已完成 Case 不走此入口。

已有部分 accepted census 时，同一显式入口只处理累计凭证验证后的全部剩余 issue。父凭证原样归档到 `diagnostics/world_census_adjudication/certificates/<凭证摘要>.json`，新请求位于 `continuations/<父凭证摘要>/`，绑定 `parent_certificate` 的不可变路径及文件哈希；沿用父请求合同版本，不改旧 policy 或请求。原始 Batch 保持不变，剩余候选通过已验收别名映射到规范身份，既有裁定的对象绑定和目击加入 confirmed 约束。不得重答已解决/排除项、拆分或替换已验收身份、撤销已知身份属性、车道或分离证明。新 Actor 不复用历史退役 ID。

凭证重放逐轮验证父输出与子输入，只有链尾与当前 World/Registry/reconciliation 比对；accepted 文件仅可由声明并验证了当前父凭证的 journal 推进，历史答案不覆盖。Behavior 门禁、轨迹/排除复验、证据图片和输入哈希消费整条链。每轮授权保留 planned_attempt，一旦已提交只能恢复该任务或候选，retry_epoch 不授权同轮再次提交。再次部分接纳后须显式授权下一轮，继续消耗原两次恢复额度；无剩余问题、已有 Behavior 候选/输入冻结/正式结果时不允许新轮。旧单轮凭证与旧 continuation 路径保持兼容，旧合同修复的独立额度不用于普通剩余问题续接。

同一显式续接入口也支持历史 v1/v2 合同修复：最新任务必须是该原 session 的 World closure invalid_output，远端已 completed，首次 Behavior/输入冻结/身份提交之前，无活动或受理未明任务；完整复验冻结归档输出，并只接受布局外来帧引用或替代身份缺少候选否定这一已知合同缺口。新 v3 请求保存于 contract_repair，绑定旧 request/candidate 哈希。最多一次新 task，单独记录 world_contract_repair_reservations，原普通恢复预算和任务历史原样保留。重复授权、崩溃恢复不重复发任务，失败后返回 world_contract_repair_budget_exhausted，不自动再开 session。此入口是一次真实额外模型调用，不是预算退款；不能保证图片本身足够裁定。其他格式或证据错误不获得该额度。

续接发送前仍原子持久化 consumed。只有实际 submit 明确未受理，才在同一授权文件中原子保存本次拒绝证据并恢复 authorized；每次发送清除上一发送的拒绝证据，超时、部分任务回执及会话不匹配不得恢复授权。429 沿用公共调度器最多三次发送尝试，共用一条预约；明确拒绝后退出只释放该请求/原 task/session 绑定的预约，合同修复只处理独立账本。预约后、发送前中断可复用同轮预约，retry_epoch 不产生新额度。task 回执与对应预约在同一次 progress 写入中关联；新发送标记存在但 task 回执未落盘时按 submission_unresolved 停止，候选文件不能证明 producer 已结束。旧 consumed 标记不依据缺少 task ID 自动退款。

相同显式命令可恢复已授权但尚未发送或明确被拒绝的一轮，无需重新满足新轮的 census 停止状态；仍验证原 session、冻结请求、当前输入、父凭证、首次 Behavior 边界及远端终态。已记录的任务复用原 task 对账。联合提交在新增模型预约前检查 reconciliation 回执及 Batch 绑定，首次布局调用使用传入结果；已有提交 journal 先恢复。缺失原回执且无可恢复 journal 时提前停止，不新建回执快照、不自动补造原结果。

生产仅保留标准 v3 和显式 v2。`efficient_v1` 已下线：新建、恢复及模型调度在写 Case/启动模型前拒绝，错误码为 execution_policy_retired。仅保留历史参数解码与拒绝门禁用于审计；旧运行模块、对照基准和协议测试已移除，不构成生产入口。旧 Case 不迁移、不清空预算；改用标准流程须新建 Case。

本文件是阶段路由、任务租约和预算的维护入口。业务判断分别由 World/Behavior Worker 及阶段门禁负责。

## 状态与入口

新请求默认标准 v3；显式 workflow_version=v2 才进入兼容路径。quality_profile/contract_revision 已停止支持。指定 case-id 且参数对象为空时继承其已存参数，不迁移合同。正常 v3 顺序：World → 0–4 个串行 Batch → reconciliation/World readiness → Behavior → build → capability/conversion/projection → report/publish。

World readiness 在线使用观察检查：未决事实保留到 Behavior/analysis，不单独触发前置 closure；结构、身份、引用和明确冲突仍须通过。Builder 汇总 World 与 Behavior 执行缺口；有缺口时写 final/analysis.json 后以 needs_review 结束，diagnostics.analysis 记录路径和哈希，generation_status=not_generated 指 target 未交付。严格 readiness 入口和已有活动 closure 沿用原协议；观察模式不允许绕过未对账任务。

phase 用于恢复；substage 标识 reconciling、behavior、building、capability_gate、projection_gate、render_verification；旧两任务 World 的 planning 内初始任务另记 diagnostics.initial_world_task，不扩展持久状态 Schema。新冻结 visual-answers.v2 的基础模型任务数为 N+2（一次 World、N 个 Batch、一次 Behavior），仅旧冻结 visual-answers.v1 为 N+3；均不含按路由需要的独立复核或额外修复。closure/readjudication 只由已记录问题触发。已完成且四份 artifact 与 conversion 哈希有效的 Case 不因代码或提示词指纹变化重跑；新版重新理解使用 --new。

## 租约与预算

新建 v3 Case 冻结 semantic_compilation=visual-answers.v2。单次 world 任务同时回答道路、环境、Actor 与未决问题；Python 在现有 admission 前生成明确可推导的 ID、引用、时间和问题区间。完整稳定直路可用紧凑道路观察，缺证或复杂道路在同次回答内用原 World 结构保留 unknown/minimum，不额外启动 world_topology。回答绑定 case、帧索引、源图和执行策略哈希。语义冲突停止，只有既有有界字段修复可消耗恢复预算；活动任务与受理不明提交仍按同一租约恢复。旧冻结 v1 保留原来的两任务、两次 topology 尝试及预算语义，不升级、不重写。私有回答格式不替换公共 Schema。细节见 [语义编译合同](../../world-understanding/references/semantic-compilation.md)。

### World 观察交接（新 Case：world-admission.v1）

初始 `visual-answers.v2` Full World 在 admission 前使用 `initial-lane-numbering.v1` 整理编号，仅适用于带 `lane-layout-review.v1` 的单一道路 `road_001`、无路口 highway。已有车道名必须是完整连续集合；每条车道的已声明位置锚点、相邻左右边界及同向边界链必须唯一一致，证据帧须属于冻结请求。Python 按这些结构化位置同时重命名车道定义、道路清单、锚点、车辆/标线等已支持引用及描述中的显式 lane ID token，保持实体数组位置、物理边界、Actor ID、数量、unknown/minimum 与未决问题不变。不从自然语言推断位置，也不补造缺失车道。缺证、冲突、多道路、路口或未识别的引用字段不整理，继续原有严格校验。

原始候选字节继续保存于 `candidate_snapshots`；`world_compilation.*.json` 的 `lane_numbering` 记录版本、旧新 ID 映射、逐字段差异和边界/帧依据。恢复同一初始候选复用现有归档重验入口，通过则无需新模型任务。已提交 World、Batch 合并、closure 与下游历史引用不经过此初始整理，不隐式迁移。

路口成员共用 World Schema 的对象格式。`world-junction-membership.v1` 只把旧字符串包装为 road_id 对象，不推断 lane_ids；转换记录输入/输出哈希和逐项差异。没有 active 修补包时，恢复可读取最后一个 invalid_output session 绑定的隔离候选，核对 Case 内路径、文件名哈希及完整 candidate_snapshot，再用当前适配器/validator 复验。通过后原子提交并保留失败历史及预算；仍有冲突则继续原有有界修补/同指纹停止。活动或受理不明任务先对账，完成的 Case 不走此恢复路径。

新建 Case 在 execution_policy.json 冻结 world_admission_revision=world-admission.v1；缺少该标记的旧 Case 不迁移。业务 Schema、ID、排序和两次额外预算保持不变。World/Batch 合并/closure 提交复用观察验收：只检查声明的结构、身份、引用、方向和关系自洽，不要求路臂数量、出口数量、进出成员或连接达到执行完整程度。执行依赖由既有路口复核及独立投影处理。

Python 按 World Schema 的版本化别名映射归一 impact_domains，或按明确 subject_field 补齐缺失值；不解析描述文字，不删除未知枚举或执行影响，保留原稿及逐字段日志。Schema 错误一次完整报告。格式修复使用 world-field-repair.v1 任务包和最小补丁，输出 Schema 由权威文件装入任务包；完整 World 只由 Python 读取及合并。旧补丁的 notes 只经 world-patch-notes.v1 转入审计，不进入 World。任务包上限 32 KiB/4 张直接证据帧，同父对象错误不可拆分，独立字段组可拆分。越权/结构迁移/无进展停止，同指纹跨重启及 retry 去重；指纹包含 Schema 与适配器。活动任务对账、已有候选及哈希绑定的原始基线复验先于新提交；复验通过不增加模型预算、不删除失败历史。工具权限当前只有提示词约束，QwenPaw chat API 未暴露单任务文件 ACL，任务包明确记录 prompt_only，不宣称隔离已实施。

初始 World 尚未提交时，`world.directionality_conflict` 可使用同一 `world-field-repair.v1` 定向请求：仅支持 `one_way=true` 与已绑定未知的 `opposite_direction_lanes=null` 冲突，唯一允许修改的路径是 `/world/road/one_way`。对向数量须有专用 unresolved 项和 unknown 字段证据，方向须有 observed 帧证据；关联隔离信息、未决项和证据作为只读上下文，全部绑定图片必须可用，继续遵守 4 帧/32 KiB 上限。模型按图片判断整条道路的方向属性，不从 null 推断对向道路存在；证据不足返回 needs_evidence，需改其他事实返回 blocked_by_patch_contract。原始数量、问题、身份和其他字段不得修改。请求去重包含关联字段、未知绑定、帧及校验实现版本，单纯改写理由或重排证据不构成新输入。已有停止记录保留；显式 retry 在当前实现下复验归档候选，必要的修复仍消耗原有剩余额度。已提交 World、closure、其他事实冲突不因此获得修改权限；补丁继续通过原有白名单、哈希及完整 admission 校验。

初始 visual-answers.v2 编译支持单帧问题：verification_window.start_s == end_s 且问题 evidence 唯一绑定该时刻的索引帧时，保存编译器元数据 single-frame-review.v1；仅限静态问题域，时序/身份问题仍需正长度窗口。单帧问题从重复宽 critical_intervals 的 question_ids 中移出，其他问题/歧义及原始候选不变。Batch 单独分配唯一帧，同一帧上的问题可以合并；不自动扩大窗口或补相邻帧。单帧组、普通窗口和独立布局复核共同占用原有最多4个 Batch，超限明确停止。已冻结 manifest 不迁移；已提交 World 不重新编译。

采用观察合同及 ego_vehicle_scene.v3/v4 的未冻结计划，先按 Batch 协议的 `batch.isolated_opposing_count.v1` 排除有物理隔离证据的纯对向车道数量复核，再计算单帧组和普通窗口预算。排除凭据写入 manifest；未知事实保留，执行阶段仍按实际 Behavior 复核依赖。剩余任务超预算时报告 `stage=batch_planning`、单帧组数、布局批次数和普通批次需求，普通重试不意味着重新调用 World。

新 Case 冻结 `batch_evidence_coverage=question-bound-coverage.v2`，在图片打包与 manifest 冻结之前检查分配问题/歧义的跨 Batch 引用帧覆盖。复用已解码索引和原选帧函数，保持窗口边界；在普通 Batch 中重新分配重复帧，补上其他分配批次尚未提供的有效比较帧。以每个问题的全部已分配 Batch 的证据并集判断覆盖，未采用 batch-evidence-capacity.v1 时每批仍最多4帧、总计最多4批；新版普通批的有界扩展见后文。单帧和独立布局批次不扩展；独立盲布局复核的首末帧为必选，关联 World 字段的重复目击按时间跨度选取剩余名额，不将其完整历史作为单批必需证据，选帧清单及缺失引用留在 coverage 记录中。其他问题仍要求全部可用结构化引用覆盖；未知帧或帧时间不符保留明确缺口，不猜替代。调整后仍有可用必需引用帧未覆盖或打包丢失受保护证据则停止，不调用模型处理遗漏的证据；额外采样帧仍可按原打包规则缩减。冻结后恢复只读取原 manifest。

初始回答的窗口格式、frame_id、first_seen_frame_id、last_seen_frame_id 和 evidence_frame_ids 逐元素引用在编译前统一汇总。公共扫描器只枚举引用，最终校验仍保留 Registry 历史继承、视频时间与执行时钟的边界。缺省时间从索引补齐，冲突时间不自动覆盖。未知引用仅在冻结请求中存在唯一时间映射或明确的旧 global_NNNN.jpg 文件映射时进入候选确认；不按采样间隔、列表顺序或未提供的探针帧推算，映射冲突/多解仍停止。有明确序号混淆证据时，同时列出无时间但合法的歧义 ID，要求逐位置显式保持或修改。

新生成的初始修复包使用 reference_revision=initial-frame-reference-repair.v2，继续复用 world-field-repair.v1 的操作格式和 baseline_kind=initial-world-answer.v1。仅该新版初始修复包的 output_schema.operations.maxItems 从冻结 allowed_paths 的去重数量推导，每个路径最多操作一次；不再将待核对位置总数与固定32比较。一般 World 字段修复以及已冻结 reference v1 包仍使用原32条 Schema，World 业务 Schema 不变。Python 保存字段类型、候选范围和配对时间；只允许指定叶子路径，数组不得整段替换。包内提供所属事实对象和去重图片，首次/末次出现引用附带冻结请求内相关的更早/更晚帧，操作须引用该时间上下文。模型不能改身份、描述或道路事实；改引用将留下原描述中的旧帧 ID 时返回证据不足，不做全局文字替换。所有配对字段在补丁整体应用后检查，并重新编译、通过完整 admission。

该类初始修复不自动拆包：提交前检查4帧和32 KiB请求，补丁仍最多16 KiB。v2 请求去除祖先/子对象的重复上下文，将内部类型校验元数据留在 Python 侧并通过 validation_issues_sha256 绑定，原事实描述和图片不截断；请求以紧凑 JSON 原子保存，按实际文件字节检查包大小。验收复核私有元数据、模型可见问题及白名单的一致性，并按冻结请求派生 Schema，不能由回答扩展修改范围。超限、无候选、越权、未逐项确认、无进展或证据不足明确停止。每份原始回答基线与冻结请求最多一个额外修复任务，使用既有恢复预算；绑定存于原任务预约账本，与 session/task 回执在同一次 progress 写入关联。有效候选和活动任务恢复先于新提交；已提交/提交结果不明的预约不得重开，明确未受理的提交错误按已有流程释放预约，重启、retry_epoch、检查器指纹或 reference 版本变化均不重置此限制。旧修复包不隐式升级，历史候选和停止记录保留。

新打包图片名称包含源帧 ID（例如 global__frame_0004.jpg），不再只暴露包内序号；已冻结图片、索引、请求和 manifest 不重命名或重写。修复后的原始候选及接受回执另存，原 Case 的业务状态只有正式编排入口可推进。

旧 case 的 World 与 Batch 不重新配对。原 case_20260912T110137901Z_15995bea 的两次 World 仅作为离线回归输入，不重跑、不改终态。测试用合成 Behavior/复核响应验证真实 Builder/Converter/发布路径，不代表视频事实或 MELA 渲染已验证。

新 Case 的 state/execution_policy.json 固定自动路由和预算语义，不新增用户可选版本。World/各 Batch/Behavior 首次任务、按事实选中的首次 preview_review，以及冻结 census v3/v4 且已生成联合请求的首次 world_closure 属正常任务；其余传输恢复、内容修复、布局单独 closure/readjudication 与再次复核共用最多两次额外额度。历史合同的一次显式迁移额度见前述 census 联合裁定协议。额度在提交前持久化，重启与 retry_epoch 不清零；有效候选与检查点复验先于额度检查。旧 Case 无该记录时仍按原预算，不迁移、不重新分类既有预约。

冻结 `intersection_path_contract=intersection_path.v1` 的新 Case 另有一次性结构化闭环预算：一次 `post_behavior_world_readjudication`、一次随 World epoch 更新的 Behavior 重建和一次 intersection review 重建。三者分别持久化计数，不占两次通用恢复额度；任一项相同输入第二次提交均停止。未取得 task ID 的明确提交失败释放对应预约。旧 Case 未冻结该字段时继续使用原复核合同和原预算。

后置 World 裁定冻结旧 World、Registry、reconciliation、原 census 凭证及复核输入，沿用 `world-closure-patch-v1` 编译器。`post-behavior-world-commit.v1` 在原 prepared 文件保存请求/回答及可重放结果，使用既有 `lane_layout.commit.json` 提交；前一笔已完成 journal 绑定后归档。原 census 凭证不改写，receipt 绑定后置 prepared，`proof()` 先重放原 census 再重放修订。首版承接只允许保持全部 Actor、道路/车道实体、问题及旧目击的局部车道连接修改或有证据的停车区域追加；census 有剩余问题、历史排除或有效 observation_only 时不自动承接，提交前停止并要求适用的重新裁定。未覆盖的事实修改同样停止，不能只靠 Actor ID 未变放行。已有独立 actor-identity-link/split 凭证的组合尚未纳入此承接合同，提交前停止，不能使其旧 World 绑定失效。

后置 journal 在恢复入口先完成旧/新检查点验证、依赖归档、事实与回执写入和进度收尾，最后标记 committed。Behavior 输入及旧 Behavior、analysis、转换结果、相关 lane/motion 复核 prepared 一并退役，新 Behavior 冻结后置证明依赖。恢复不重置原任务预算；生产者受理未知或仍运行时继续按既有任务对账阻塞，不因本地证明有效而提交新任务。旧版仅含 world 的 prepared 或缺少冻结基线的请求不能自动补签，也不覆盖其原文件。

state/execution_route.json 保存共享判定的 full/straight_preview/intersection_preview/blocked/unsupported、依据与输入哈希。World 后检查已知执行能力，Behavior 后优先完整执行；高速缺口仅由获准尺度策略覆盖，单四岔路口使用独立关系复核及执行投影，不进入高速直行预览。Builder 使用同一判定函数。首次必要 intersection_review 也是正常任务，后续调用共用两次恢复额度。

fact_contract_revision=observation-execution.v1 只对新 Case 冻结启用。state/observation_plan.json 保存前判、结构化缺口和源哈希；未知尺度/连接不能伪装为 resolved，未知身份不能伪装为冲突。World 保留未知，投影必须通过严格门禁。定向补证按字段路径和关联问题/道路/actor 选择帧与补丁白名单，无相关证据提交前停止。输入不变不重复调用，修改依赖不重置恢复预算。旧冻结请求不原地升级。

新尝试默认使用新 session；下述显式转换会话续接是唯一例外。一个阶段只允许一个活动 task，同一目标 Agent 通过跨 Case 文件锁串行整个 task 生命周期。只有明确 429 提交拒绝可最多重试三次；受理不明持久化 submission_unresolved 并保留预约，不重发。明确本地 Connection refused 使用 backend_unavailable 并释放未受理预约。访问拒绝与明确取消直接停止。

已有 task 连续六次查询失败时先校验候选；没有有效候选则记录 task_status_unavailable，保留原 task_id 待对账。恢复优先查询原任务，不创建新 session 覆盖未决任务。阶段可恢复错误仍受三次业务尝试及 Case 两次额外任务上限限制。

World closure 本地超时后先查询同一远端 task 的终态；确认停止后才按原请求哈希从新到旧复验归档候选。较新候选不合格时，较早但完整合格的证据可沿原 journal 提交，并记录归档来源与失败记录，不新增模型任务或预算。布局回答越出冻结 Actor 范围的走廊回声只在编译副本中忽略，Registry 车辆不删。已冻结 Actor 确认窗口内的旧车道编号按被验收的布局映射解释；窗口外的目击车道冲突仍须明确补证，不能自动覆写。

模型协议错误、服务拒绝和流中断按后端错误分类；仅候选字节实际无法解码才标 invalid_json。缺依赖、IO 和代码错误不能伪装成 Schema 错误消耗模型额度。

## 恢复与输出

验收失败候选移入 diagnostics/invalid_candidates，验收原始字节保存 candidate_snapshots。显式 --case-id --retry-failed 才重新打开失败或需要复核的 Case；阶段尝试可进入新 epoch，Case 恢复额度不清零。World epoch 改变使依赖其结果的后续产物需要复验，不能绕过旧候选绑定。

census 恢复先核对完整冻结 Batch 链，再恢复既有多文件提交 journal、验证 accepted 证书并对账未结束的 World closure producer。初次 closure 没有 continuation active 标记时，也必须完成这些检查；已提交结果的 producer 对账不开放新模型尝试。验证通过后，将仍停留在 planning/batching 的恢复点推进到 aggregating；部分 accepted 结果继续受原 census 未决门禁约束。completed_batches 仅是进度记录，不单独授权跳过证据和提交检查。

存在有效 accepted census 时，v3_batches 仅重验冻结历史，不写当前 Registry、进度或 Batch 请求；历史缺失或不合法直接停止，不隔离、重建或调用模型。尚未完成的 census journal 必须先恢复，缺失或不一致的证书不能退回普通 Batch 发布。没有 census 提交的普通 Batch 接收和恢复保持原流程。

### 分会话转换（conversion-continuation.v1）

新 Case 冻结 `conversion_continuation=conversion-continuation.v1`。正常两个新 session：`conversion_relations` 覆盖全量文字证据并整理带时间关系，`conversion_semantic_gap` 依据验收关系联合规划全部 eligible 车辆。两次均为计划任务；两阶段共用最多一个额外 session，且必须有既有 Case 恢复额度。预算预约账本跨重启/epoch 保留，不重置两次通用恢复额度。旧运动/gap Case 不隐式升级。

私有状态位于 `state/conversion_work/workflow.json`；每个 generation 有不可变 request 和 worker 顺序累积 `save_NNNN.json`。保存发生在同一会话内，不增加模型任务。进程在任务返回或恢复时扫描完整文件，复用共享引用/数值校验，原子提交 accepted 内容及哈希；截断、越权、丢失已保存覆盖项的文件不替换有效检查点。恢复请求携带已验收草稿、尚缺 evidence/actor 清单与校验反馈。第一阶段必须覆盖所有证据；第二阶段必须覆盖所有车辆并通过完整积分、时序关系和碰撞校验，才写原 `conversion_semantic_gap.json` 并调用确定性 Converter。身份/运动未决交回上游，不能通过补会话删除不确定性。

不修改后端 1800s 上限，不在任意时刻打断模型。终态超时或可修补候选错误才可自动接续；状态不明、仍运行、提交受理不明、取消或权限失败停止并保留成果。先确认旧 producer 结束再启动新 session，不同 generation 写不同目录，迟到旧文件不覆盖新结果。进程自身中断后由正常 `--retry-failed` 恢复入口读取检查点；没有后台守护进程自动重启已退出的编排进程。模型尚未落盘的内部思考无法恢复。单任务文件权限仍为提示词约束，不宣称系统 ACL 隔离。

该版本使用自身受控接续，拒绝下述旧转换 `--authorize-transport-retry/--resume-conversion-session` 授权，防止绕过共用一次上限。完成缓存须复验两个阶段的检查点和最终决策绑定。后续身份修订将旧转换检查点一同归档，禁止在新 analysis 上套用旧成果。

### 历史单会话转换恢复

无 continuation marker 的 conversion_semantic_gap 每个 epoch 保持最多一次模型尝试。显式恢复时，若最近一次为 task_deadline_exceeded、deadline_task_resolved=true 且该阶段没有活动任务，可在一次失败后重新开放该阶段；新任务使用原有 Case 恢复额度，任务历史、冻结请求及已验收上游结果保留。尚未对账的超时先由现有生命周期查询原任务，确认结束后再显式恢复；内容校验失败、取消及其他错误不通过此例外重开。有效候选仍优先验收，预算用尽则停止。本规则不延长超时时间、不增加预算，也不放宽转换验收。

普通预算耗尽后，用户可通过同一正式入口显式增加 `--authorize-transport-retry conversion_semantic_gap`，授权一个额外转换任务（仍须 `--case-id --retry-failed`）。此授权只适用于当前失败属于转换阶段、该阶段历史均为失败的 `task_deadline_exceeded` 且冻结 request/analysis 完整的 Case。外层错误阶段允许 building/aggregating/converting/conversion，但必须同时核对最近模型任务属于 conversion_semantic_gap、当前错误是超时或尝试/预算耗尽；不能仅凭外层阶段放行。未对账的超时先查询原 task，只有明确终态失败且后端确认为 timeout/task_deadline_exceeded 才持久化 resolved；运行中、查询失败、取消及其他失败均停止授权。远端已完成则要求普通恢复验收原结果。

用户要求保留原转换会话上下文时，可同时传 `--resume-conversion-session SESSION_ID`。只接受当前 Case 最近一次失败转换任务的 session_id，仍须前述授权及终态对账，不能组合策略迁移。session 选择持久化在授权中；预约后经同一任务租约提交新 task，向后端明确传 --session-id，核验返回会话一致。进度按 task_id 区分尝试，旧失败记录保留。续接不修改冻结提示词合同、输入或候选门禁，也不延长任务时限；未指定此参数仍创建新会话。

转换授权绑定 request 文件、analysis 哈希及既有 task IDs，在下一次任务预约时复核并一次性消费，单独记入 authorized_transport_recovery；普通额度不清零，历史 session 与已验收上游不改写。如果普通额度尚有余额，显式转换授权仍在下一次提交消费，不能留下供后续任务使用。相同未消费授权重复调用返回原记录；已消费后必须出现新的终态超时任务并再次得到用户明确授权，才允许新授权。有效晚到候选优先验收，不消费授权；正式 decision 存在后该授权不能再提交任务。此入口沿用原字段/语义验收、单任务租约及超时，不保证新任务能在时限内完成。原 batch_NNNN 传输恢复规则保持兼容；其他阶段不由本入口新增放行。

终态 done/done_degraded 只声明 analysis.json、report.md、report.docx、target.json 四份产物的路径及 SHA256。needs_review 带具体阻塞及恢复点，placeholder 不进入发布目录。只有 Coordinator 将正式信封转换成用户回复。

标准新 v3 省略 allowed_degradations 时自动包含 Converter 已登记缺值补全，无需手填速度等数值或逐项授权。旧冻结参数仍保持不变。仅因 conversion.fallback_unauthorized 停止的未交付 Case，在用户已有自动补全意图时可经 --case-id --retry-failed --enable-conversion-defaults 恢复；conversion-default-completion.v1 采用记录绑定原参数、执行策略、analysis 与原转换 policy，不修改任何模型冻结输入。recovery_phase 对有效采用记录直接返回 converting，保留模型阶段、预算和原始失败/策略归档；完成产物仍通过完整转换、报告和发布门禁。详见 Delivery protocol.md。

各阶段合同与门禁见 [contracts.md](contracts.md)，旧路径见 [legacy.md](legacy.md)。

## Preview 复核与隔离证据恢复

preview_relations.v1 原地维护。新请求从 behavior/preview_contract.py 生成响应 Schema 与结论规则：confirmed 零修改；corrected 为 1–24 项有效修改；inconclusive 保存诊断、保留观察并进入 needs_review。冻结旧请求允许不带 outcome，但必须经过同一证据/一致性门禁后推导结论。零修改本身不是通过依据。

execution_scope 不按 impact_domains 名称白名单放行。复核必须提交 affected_actor_ids 和空 affected_event_ids；程序验证这些 Actor 全部存在、属于结构化隔离范围排除的 background、关系为 opposite，且没有身份或事件语义依赖。旧冻结请求缺少依赖字段时，只能保守绑定全部“background + opposite + 无自车道路 lane_id”的 Actor，并在新证书记录 legacy_normalizations；不能从理由文本抽取 Actor ID。

未实例化为 Actor 的对向整体观察使用 Behavior semantic_limitation.scope_binding.kind=physically_separated_opposing_observations，绑定 world.road.opposite_direction_lanes、representation=observation_only 和空 affected_event_ids。Builder 验证结构化物理隔离、无跨向事件/动作以及所有 participant 位于自车侧后直接排除该 limitation；Preview 不为不存在的对象创建 affected_actor_ids。已有冻结 Case 仅兼容明确的 lim_opposing_traffic_not_modeled ID，并记录 legacy_normalization，不能从描述文本推断。

preview_review 独立记录用途和局部任务数，底层仍走 global-video-understanding 与同一 Case 全局预算。旧 behavior attempt 计数不代表独立复核次数。复核失败不调用 Builder 覆盖错误阶段。恢复使用候选、冻结依赖和校验代码内容哈希；无变化直接返回具体阻塞，不重复隔离文件或提交任务。

隔离设施缺少结构化证据时，World closure 只允许新增 opposing_separation 和其精确 field_evidence。旧 World/Behavior/证书均先归档。证书迁移只接受这一增量及 epoch 更新，其他事实、观察与帧字节必须一致；提交前重验，prepared 日志支持中断恢复。原请求和旧证书不覆盖，对向数量未知不消除，预算不重置。

只读检查入口：behavior-understanding/scripts/validate_preview.py --case-dir CASE --candidate CANDIDATE。分别输出复核接纳与执行就绪，不提交任何 Case 状态，也不声明真实渲染通过。

### 预览后车道问题复核

已验收 preview 后，若唯一相关字段为自车车道数或首条道路 lane_ids 而仍有 required 未决项，execution_verification 使用一次既有恢复预算发起 World 定向 patch。只允许 resolution 与追加 evidence；须覆盖所有轨迹帧，保留旧证据且不改变任何 World 事实。证据不足停止并保持 needs_review，不重置预算。验收后通过持久 prepared journal 同步 World epoch、Behavior epoch 与预览证书哈希；无需重跑视觉关系复核。默认路长仍为仿真假设，不代表观察范围。

### Preview 语义阻塞与格式重试

新 preview 请求仅携带道路观察投影和去除重复 detail 的 Actor 轨迹，完整 World 留在 hash-bound world_snapshot_path，由 Python 合并时加载。limitation_contract 由同一尺度谓词生成。上游纯尺度未知应显式标记 limitation_kind=absolute_scale；executor_primitive 不再被无条件当作尺度别名。图像复核前的上游不可修改阻塞返回 preview_prerequisite_blocked，不启动模型；复核中的语义冲突返回 PreviewStopped，不走 schema_validation_failed 自动重试。缺字段/无效 JSON 等格式问题仍有原有有界重试。

所有 limitation 必须有复核结论或通过既有 scope_binding 依赖校验；漏项不能得到 accepted。Builder 同时列出道路未决和遗漏 limitation，不因优先输出某个前置问题而隐藏另一个。已冻结请求兼容旧形状，不原地改写；已有活动任务先对账，完成 Case 不隐式重跑。

新复核请求的 required_actor_evidence 明确每辆车的轨迹证据帧，context_frame_ids 仅引用当前 review_frames 作为消失等上下文。错帧返回 owner/actor_id/field_path/expected/actual/retry_kind；局部修复绑定原 candidate 和输入哈希，只允许对应 actor 的 evidence_frame_ids/context_frame_ids/reason。旧冻结请求不接纳新字段。不自动裁剪证据或改写观察来放行。

新请求的格式/漏 actor 错误为 schema_validation_failed，具体规则保存在 rule_id；可复核但无法确认的证据为 preview_evidence_insufficient；显式车道、事件、限制或几何事实冲突为 preview_fact_conflict，retry_kind=fact_conflict，不按格式错误反复请求模型。当前执行能力不足为 unsupported_scene_type，owner 指向 World 或 Behavior。传输故障保留 timeout/model_stream_interrupted 等原码。旧冻结请求保留原 preview_contract_blocked 码，不升级或重写历史信封。

### 自车本车道直接前车一致性

新 preview_relations 请求必须单独给出 ego_lane_lead_actor_id。该对象须为 ahead，且复核后的 lane_id 必须与 ego 相同；否则复核为 inconclusive。自车车道不得用可见机盖左右边缘定位，机盖横跨画面且相机可能偏置；应结合近场标线限定的行驶走廊、跨帧收敛关系和直接前车对齐判断。冻结旧请求继续按其原 response_schema 验收。

### 视频外未来道路不阻塞当前预览

未来道路问题只有在结构化标记 temporal_scope=after_observed_window，并以全部执行帧证明当前窗口车道数、边界连续、无汇入分流及无进入路口时，才从当前预览依赖中排除。预览 duration_s 等于已复核窗口终点，因此该排除不依赖默认道路长度，也不声称视频外道路已知。证据缺失、当前事件/动作、车道变化或非直行单路场景仍阻塞。未知事实原样进入报告和 execution_dependencies 审计。

## 布局编译版本记录（2026-10-04）

新 Case 使用 lane-layout-decision.v6，并在 progress 保存 pipeline_fingerprint_at_creation；成功交付 fingerprint 仍表示交付时版本。新布局任务冻结 Schema、Prompt 内容、validator_revision；旧 Case 不隐式改合同。布局位置由物理边界对编译，确定性恢复规则见 [Reconciliation 协议](../../world-reconciliation/references/protocol.md#decision-v6-与本地复验2026-10-04)。


### census v4 的观察级执行范围

v4 沿用 v3 身份/布局合同，bindings 增加 scope_claim（普通结论必须 null）及 observation_only。唯一新增规则为 stationary_isolated_offroad.v1：无稳定身份的源对象在全部指定帧中可见、静止、车行道外、被物理屏障隔开且无车行道入口；逐帧给位置、静止及屏障视觉依据。必须用已有 candidate_refutations 的源追踪/锚点/共现规则排除全部已登记非 ego 身份，不能以距离、模糊或事件列表缺席替代；反证主体不能同时声明行驶车道。已登记车辆不按此政策删除，原始 Batch 身份仍 unresolved。

scope_plan 固定 [0, probe.duration_s]。仅在既有全局/已接纳 Batch 图片全部覆盖、首末采样距 Case 边界不超过 1.5 s、采样间隔不超过 1.5 s 时允许范围结论。证据计划只在联合集合不超过原 12 张上限时补入已有 probe；补不齐时范围结论不可用，普通身份裁决仍可进行；若可选范围图片造成打包超限，退回原身份必需证据并禁用 scope_claim，不因可选范围证据截断身份裁决。排程只是有限采样支持，不保证未观察时刻绝无交互；遮挡、无法追踪、移动或隔离不明保持 unresolved。图片、响应大小与任务预算不扩大。

经编译的范围结论与身份证明分别记录在原不可变凭证链。前置 census 门禁接受有效 observation_only 以进入 Behavior；交付另须同一次 Behavior 的 census_scope_review 明确覆盖全部有效 issue，绑定当前 census 凭证摘要、全部要求的帧以及 affected_actor_ids/affected_event_ids。confirmed 的 affected 数组为空；遗漏、未知及冲突都不代表确认。该私有字段在正式 Behavior 提交前移除，复查结果绑定规范化 Behavior 摘要保存于内部 state，公开 Behavior Schema 不变。

普通完整 Behavior 已通过其他门禁但复查字段缺失或合法条目覆盖不足时，分类为 behavior_scope_review_incomplete。复用 behavior_repair.request.json，以 behavior-scope-completion.v1 冻结完整规范化基线、census/历史链和证据哈希；仍由原 behavior model_stage 提交新 task/session，共享原尝试次数和两次恢复额度，不新增预算。模型只答 {base_sha256,census_scope_review}，Python 合并后重跑原验收，不允许 replacements 或更改基线。重启先校验冻结请求并对账已有生产者；提交中断复用已归档补答。成功同时保存原格式范围凭证与完整 Behavior 后归档补答请求，下游无新合同。漏答不撤销、不默认确认；格式错误和过期凭证不冒充漏答，只有完整合法的 challenged/unknown 进入原 census_scope_recovery。独立运动/车道以及缺复查的普通 replacements 响应仍按原冻结修复合同停止，不隐式扩大权限。

pending_scope 的 excluded 已知帧覆盖不足也可进入该补答，诊断分别使用 identity.census.scope_proof_frames（逐帧证明）和 identity.census.scope_evidence_frames（证据编号目录），保留 field_path、issue_ids、expected/actual 和 missing_frame_ids。冻结任务包的 scope_output_requirements.excluded_required_frame_ids 与 frames 一致，两个输出字段必须全覆盖。结构错误、重复或未知帧不视为这一覆盖补答；原排除判据不变。预算耗尽时保存补答请求及完整基线后停止，既不提交范围也不调用新任务。

运动复核存在有效范围项时，在原 request 中冻结 scope_context、当前 census/历史链及帧输入，在同一 candidate 的原运动补丁旁提交新的 census_scope_review。复用观察准备、身份/census/范围及保真门禁，在最终规范化 Behavior 上验收复查；prepared.json 保存结果，原诊断目录的 actor-motion-commit.v1 commit.json 绑定请求、候选、结果、范围凭证和验收记录。顺序提交 Behavior、范围凭证并重验，最后写 accepted.json。中断经原 model_stage 对账后重放，只接受旧/新检查点，不覆盖第三种状态、不依赖候选重新裁决、不追加模型调用或预算。旧日志没有范围证据时停止，不自动补签。合法 challenged/unknown 保存完整修复候选并绑定实际运动 task/session，后续 aggregating 恢复复用原 census_scope_recovery；缺失或格式错误不授权撤销。

普通 Behavior 定向修复同样冻结范围上下文并要求同一补丁复查；在线和独立 validator 都先拆 census_scope_review，再验收 replacements，归档基线中的旧私有 review 不继承。所有下游继续消费原范围凭证合同，旧 analysis/转换产物须按新输入重建。旧 preview 的独立写入流程仍不在此提交合同内。

独立 review_actor_lane 的 lane_only / lane_and_position 同样冻结 scope_context、census 链与完整范围帧；保留原 review_frames、allowed_paths、横向估计撤回与初始状态同步。diagnostics/lane_review.prepared.json 的 lane-review-commit.v1 日志同时绑定请求、补丁、最终 Behavior 和新范围凭证。原 model_stage 对账后，按 Behavior、范围凭证、completed 记录顺序提交；中断保持 pending，先恢复提交再 rebuild，既不因旧凭证阻断恢复，也不让 finally 的重建错误掩盖原异常。恢复只接受绑定的旧/新检查点，候选已归档也不追加任务；completed 快速路径重验 Behavior/范围绑定并重建 analysis。合法反证绑定实际车道复核 task/session 后进入现有范围恢复，旧 completed 或旧冻结请求缺少匹配证据时不自动补签。仍使用原 behavior 阶段和一次定向复核额度，不重置预算。

明确 challenged/unknown 的合法回答保存完整 Behavior 到绑定 trigger，按原 lane_layout.commit journal 追加确定性撤销轮；历史身份证明、旧对向排除保持锁定，只有新观察级授权可重开。旧执行产物及 Behavior 输入绑定归档，范围状态按顺序重放，不能累计旧授权永久放行。新的受限 census 任务复用 world_adjudication、原 World session、原预约/两次恢复预算及对账；预算不足仍保留撤销后的现场。首次正常 Behavior 不增加固定调用；反证后的裁决和必要的 Behavior 修复分别计入现有额度，不能承诺任何 Case 都可恢复。

Behavior 修复以完整已保存基线为准，只允许复查明确相关的 Actor/Event、此次 census 为该 issue 正式绑定的 Actor 及这些对象的事件、动作和局限；其他内容保持相同。无法界定修改范围直接停止。新请求正常重冻输入、使用新 Behavior session，不给旧候选重新标注新输入。受理未知不重提，明确未受理按既有授权规则恢复；提交 journal 和已提交生产者在重启时先恢复。

execution_actor_scope 增加 excluded_observations 与 census_scope_binding；它不制造不存在的 Actor ID。已登记对象覆盖仍按原规则检查。Converter 和 target 检查校验同一有效凭证/复查，转换回执绑定 census_scope_sha256；范围变化使旧产物失效。观察级排除标记 done_degraded，报告逐 issue 列范围、依据和帧。行人等被执行投影排除时，相关车辆减速/停车/避让约束仍须保留，不因观察级范围授权解除。

### 未决 census 的 Behavior 范围判断

已冻结 behavior_census_scope=behavior-census-scope.v1 的 Case：有效 v4 census 后，剩余未决可进入正常 Behavior candidate 分析，不再要求先把全部身份确认。World、Registry、原 Batch 和 census 证明保持不变；身份仍 unresolved。census_scope_review 对 pending_scope=true 的项逐条给 excluded/needs_identity/unknown、理由、原帧、受影响 Actor/Event 和 scope_proof；其他已授权 observation_only 项继续原 confirmed/challenged/unknown 合同。正式 Behavior 提交前必须完整覆盖并通过范围验证；unknown 不自动调用新模型、不默认确认，完整候选归档后停止交付。

新建 Case 改冻结 behavior_census_scope=behavior-census-scope.v2 和 execution_scope=ego_vehicle_scene.v4；上段 v1 适用于原冻结 Case。v2 不增加模型输出字段，Python 对完整物理隔离的单车候选也接受 excluded，并在已验收复核后签发与来源绑定的执行裁定。单候选且无同帧硬冲突只作执行身份假设；未知车道候选的省略或指定同向车道代表车须有 Case 输入提示，且无事件/动作/道路依赖。全部相关观察和 Actor 有合法执行处置后，纯 census 身份/车道限制才从执行副本退役；源限制保留。欠缺前提仍阻塞，预算不变。

首版 excluded 只支持 physically_isolated_opposing.v1：本次所有冻结 Behavior 帧均提供绑定位置、对向方向、连续屏障及无进入自车道路通道的证据，原观察帧须看见对象，其他帧可证明道路隔离上下文，不虚构跨帧身份。采样范围覆盖完整 Case，最大间隔 1.5s，且不与已知道路事实或事件/动作依赖冲突。个体 Actor 候选仍需身份裁定；vehicle_group 候选允许逐观察排除，但同一群体的全部未决观察都证明排除且正式 Behavior 提供逐帧对向 background 证据后，执行副本才可排除群体。不能仅以远、小或无事件引用放行。

全部非排除项明确 needs_identity 时，保存完整 Behavior、原凭证和冻结帧的 trigger，复用原 census_scope_recovery、World session、journal 和预算；无需撤销原本未获授权的观察。unknown、漏答或无效证据不授权该恢复。额度不足先停止，保留 candidate/trigger，不退役现场或重置预算；有额度时只允许相关 Actor/Event 和本轮绑定对象的受限 Behavior 重建。正常首次 Behavior 仍按原计划任务记账，其他阶段恢复额度耗尽不将它变成额外任务。

旧 v4 Case 仅在 needs_review/identity.census.unresolved、首次 Behavior 前、全部任务已对账且已有有效 accepted 时，可通过 --case-id CASE --retry-failed --enable-behavior-census-scope 显式采用。state/behavior_census_scope.adoption.json 绑定原 Case/策略和采用时凭证；不修改原冻结 policy、请求、事实或预算，不支持与其他迁移/续接参数合用。采用记录进入 Behavior/修复及恢复请求哈希，范围凭证绑定采用记录摘要。普通旧 Case 重试保持原门禁。

最终 require_resolved、execution_scope、离线预检、Builder、Converter、target 检查及报告统一消费 census_scope_review 凭证。新的观察排除及群体裁剪继续写原 execution_actor_scope/census_scope_binding，按 done_degraded 报告；源观察和身份未决没有被删除或伪装为 resolved。

### execution-gap-recovery.v1

新 Case 冻结此标记，无标记的旧 Case 保持原 mixed-unknown 和全未知车道停止语义，不提供隐式迁移。原 observation、identity、scope 及 Converter Schema 不变。

共享 execution_gaps 只读分类使用原转换参数摘要及有效默认补全授权。范围门禁只豁免允许补全的 speed/position/execution 纯尺度限制；executor_primitive、actor_identity、actor_lane、event/path 或真实事件/动作引用不豁免。源 limitation 和未知数值不改。有效结构的待决范围回答先汇总逐条错误和独立车道/身份限制；内容冲突不能降级成覆盖补答。诊断不是通过证明。

混合 needs_identity/unknown 的完整 review 可启动一次受限恢复，trigger 绑定可处理 issue_ids 与 deferred_issue_ids。World 请求仅包含明确 needs_identity 子集；历史重放检查这个精确子集，result.remaining_issue_ids 同时保留未分派的问题。原 World session 新增一次任务，之后重建 Behavior；不把 unknown 改成 needs_identity，不继承未正式通过的排除建议。提交前原剩余额度至少为2，不足则保留基线及 trigger，不退役依赖；不退款、不重置、不中断活动生产者。已 adjudicated 的同轮不因重复 unknown 再调用模型。

trigger 还绑定原 Behavior 全未知车道的 Actor 清单，必要的同一次 Behavior 重建可复核这些 Actor 的原轨迹 road_id/ordinal_from_right/lane_id/detail，仅此范围。样本、身份、时间、速度和其他事件保持。普通执行路由的 execution.lane_unobserved 在新策略下可交给已有一次 lane_review，再执行最终车道门禁；不能确认则原样停止，未获得新依据不再次调用。

正常 Behavior 或明确授权的车道补丁可返回私有 lane_closures。每项包含 collection/issue_id/actor_id/outcome/evidence_frame_ids/reason，绑定单 Actor actor_lane 限制或纯车道 World 疑问。确认必须所有轨迹帧均有合法 lane_id 及包含原帧编号的 detail，覆盖原问题证据。Python 只闭合 actor_lane，保留混合 actor_motion/actor_identity 等其他影响及原描述。World 原件不变；Behavior 的 resolved_lane 答案由执行投影验证后消费。在线提交与独立候选校验共用受限恢复检查；中断恢复沿用原任务、prepared commit 和范围凭证校验。

### census-evidence.v2：复用后续对照帧

census-evidence.v2 请求按下述冻结规则重放；未标记的历史请求按 census-evidence.v1 重算。每个请求的 evidence_plan.revision 参与摘要和验收，部分裁定后的请求继承父凭证的选帧版本。新 Case 默认版本见下方 v3。

规划先建立与问题顺序无关的可复用集合：来源帧、布局帧、身份锚点及现有身份关联区间内必需的历史/确认帧。候选首次出现之后的已确认目击始终优先；缺少已确认后续目击时，将可复用集合中锚点之后不超过1.5秒的帧与最早后续探测帧作为候选，连同各自关联区间的历史/确认/补桥帧计算新增量。选择新增帧最少者，同数优先复用，再按时间和帧ID稳定排序；不能因复用更晚图片反而引入更多中间帧。所有原始来源、锚点、区间历史证据、确认目击和最大1.5秒间隔继续校验；可选范围证据和12帧上限不变。不能为了凑数量删除必需帧，也不因选择了图片就断言对象可见、同一身份或允许排除。

无新标记的旧 Case，仅 initial 联合请求不存在且旧规划超过12帧时，可以在原正式恢复路径创建v2请求。创建前检查无 world_closure 尝试、会话或任务预留，无联合请求、候选、活动授权、活动/受理未明生产者，无父凭证或其他恢复链。保存 before/evidence_preparation.json 不可变准备快照，并在新请求记录 evidence_plan_upgrade；重放核对快照输入哈希、未提交条件和原v1超限原因，再按v2重算。快照写入后的中断可复用原快照，不能覆盖。此入口不改内层布局请求、旧策略、已验收Batch和恢复预算；已有联合请求不迁移。v2 仍超限时可按下节采用 v3；所有可用版本仍超限则停止。retry_kind=replan_evidence 不保证普通重试必能通过。

### census-evidence.v3：按观察义务规划检查画面

新 Case 默认使用 v3。普通全局抽样目录只表示图片可用，不能冒充某车辆在该帧的观察。必需集合完整保留：每个疑点原帧、所有登记身份锚点、布局证据、World/Registry/已验收 Batch 中全部结构化 frame_id、first_seen_frame_id、last_seen_frame_id 和 evidence_frame_ids 引用。仅引用时间的观察也须按已有时间容差匹配冻结索引并保留对应帧。未知、遮挡、冲突等记录的帧同样保留，不能只保留正向目击。缺失引用直接报 evidence_missing。

在相邻必需帧之间，从完整 probe 索引建立每段不超过1.5秒的检查路径。按补充帧数量最少、同数优先已有全局/Batch 抽样、再按时间顺序的帧ID选择；此计算不读取12帧上限。最终每条身份链必须覆盖其区间内所有必需帧和选定补桥帧；后续对照优先确认目击，否则按v2规则复用。不能证明连续可见仍 unresolved；候选反证、重复身份及范围排除的验收规则不变。

mandatory_frame_ids、补桥用途和逐 issue 关联链进入请求摘要。验证从不可变 before/world、Registry、Batch、probe 及布局输入重算；提交后的当前 World 不能改变历史请求的证据要求。总数超过12仍停止，绝不按预算截断观察。

对从未提交过初次联合请求的 v1/v2 Case，普通 --case-id ... --retry-failed 可在原规划超限时采用下一可行版本，复用上述未提交守卫和准备快照，保留原策略、已完成结果和预算。v1→v3 必须同时重现v1和v2的超限；v2→v3 必须重现v2超限。已有请求直接按冻结版本复用；活动、受理未明或已有闭合尝试不允许迁移。后续凭证链继承已采用的版本。

## Behavior 观察范围补充（behavior-observation-scope-review.v1）

`--case-id ID --retry-failed --review-behavior-scope` 是显式的一次范围补充授权。仅适用于已有 v6 census、完整冻结 Behavior 输入、最新终态 identity_evidence_insufficient 候选且尚无正式 Behavior/analysis 的 Case。原失败回答保持不变；请求、选中 issue、输入哈希及生产任务绑定保存于 diagnostics/behavior_scope_review，请求后不再扩大修改范围。普通预算不退款或清零；专用一任务账本覆盖本次补充，不能用于 World、完整 Behavior 或转换修复。

补充响应只有 base_sha256 和选中 decisions。未选回答与全部事实保持原值。对同向、无物理隔离的观察仍可验收完整 unrelated_observation 证明。可选 candidate_independence 只支持唯一候选的早期观察：须早于 Registry 首见和全部 Behavior 轨迹，精确列出该车所有轨迹帧、声明 observation_used=false，并无独立/混合身份争议。仅精确绑定这条观察的 bounded_uncertainty 身份限制可在执行副本解除；车辆及后续轨迹不裁剪、不提前，不写身份确认。

补充任务验收不等于整个 Behavior 通过。仍有路径/事件/身份依赖时返回原语义门禁，保留补充结果且不发起额外恢复任务。旧 census 恢复请求保留为历史，不在该补充分支自动提交。重复运行重放哈希绑定结果；中断和受理不明沿原 model_stage 对账，禁止第二次提交。交付和离线重放使用同一 scope review 与执行凭证。

额度失败的唯一例外是显式 `--case-id ID --retry-failed --reauthorize-behavior-scope-after-quota FAILED_TASK_ID`。仅接受原 scope 任务的终态 `failed`、已持久化的服务端 token-plan quota exhausted 错误、没有候选/结果或未决任务且原输入哈希仍有效的 Case。额度恢复由用户先确认；入口不联网查询额度。该入口不能和其他恢复/迁移选项合用。

默认第 1 轮授权最多额外一项 `behavior_scope_quota_retry` 任务，单独账本绑定原 task ID、原 session 与原授权快照，使用原请求、原选中 issues 及原验收规则。原任务、授权、普通恢复预算和已完成事实保持不变。重复同一命令只复用该授权/任务/结果，活动或受理未明状态先对账；失败后不自动增加第三项任务，仅更换 task ID 也不能继续授权。超时、协议错误但非明确额度耗尽、候选验收失败不适用；成功后仍可能因剩余身份问题停止。

第一轮额度恢复再次因额度失败且无输出时，用户另行明确授权后，可使用同一入口并加 `--scope-quota-retry-generation 2`，task ID 必须精确匹配第一轮额度恢复的失败任务。第二轮使用独立 `behavior_scope_quota_retry_2` 账本，绑定上一轮失败 session、授权快照及原冻结请求；验收前和重放时检查完整授权链。该轮最多提交一个任务，重复命令或再次失败均不增加名额；不支持 generation 3。普通重试和省略 generation 的旧命令不创建第二轮授权。
## 独立下游执行范围复核

`--case-id ID --retry-failed --review-execution-scope` 仅适用于已采用有限恢复、v6 census、
behavior-handoff.v1、完整冻结输入及终态身份失败候选，尚无正式 Behavior/analysis 的 Case。
它是 `execution_scope_review` 一次任务，沿现有 model_stage、任务回执和总额外任务预算记账；
不退款、不重置旧 World/Behavior 内容额度，不与其他恢复选项组合。重复命令只对账或重放，失败不追加任务。

请求保存在 `state/execution_scope_review.request.json`，冻结原候选、World/Registry/Batch/帧、
待查观察、候选集合和输入摘要。候选从现有执行投影计算，保护 ego、事件/动作引用对象及至少三帧已知车道的轨迹。
新响应只包含请求摘要、Actor 范围判断及逐条观察范围判断。每项须覆盖全部冻结帧、承认已知可见帧，
提供空间和交互依据并证明无身份、路径或交互依赖；不能以远小、短轨迹或缺少事件替代证据。
同帧多候选不合并，全部候选和观察须分别证明无关；保留车辆替代身份、正式 distinction、混合身份、道路或事件依赖仍阻塞。
全局纯绝对尺度限制保留，交原语义运动补全流程处理。

验收结果在 `diagnostics/execution_scope_review/result.json` 绑定唯一生产任务。
原 Behavior/review 字段不改，只向执行凭证附加 `downstream-execution-scope.v1` 证明；历史 census 快照重放不读取新凭证。
接纳、执行投影、构建和转换逐次重验源摘要及证据。仅删除执行副本中的已排除对象及其专属问题，
同步清理这些问题的 interval 和 single_frame_review 索引，原有其他无效索引仍报错。
身份结论继续保持未决，旧恢复请求保留；该分支不自动重新发起 World 身份恢复。
后续正常关系提取、语义运动计划、数值/碰撞校验及严格 target 转换均不跳过。

## 独立核心近似交付中的代表车辆

`--core-scene-approximate` 在构建隔离 Analysis 前，重放 `census_execution` 来源绑定及
`peripheral_uncertainty` 投影约束，复用 `execution_scope.append_representatives` 构造合法
`single_representative`。代表车加入必需执行集合，并在投影回执记录原 issue、帧及车道；
未知速度和米制位置仍为未知。原始 World、Registry、Behavior 和证书不改。
独立验收重放同一构造，要求代表车同时存在于 Analysis、数值计划及 Target，且核心交互依赖完整。

新核心投影收据 `core-approximate-probe.v2` 通过原 `census_scope.validate_review`
校验同次回答、完整覆盖及逐 issue 证明；仅允许将 `needs_evidence` 留给核心范围判断，
不提交严格 scope review。全部 handling 均重放正式处置，不按风险标签放行。
执行副本复用正式 limitation 处置谓词，记录原 limitation 摘要、全部 issue 和处置摘要。
已合法排除的观察可以解除精确的身份关联限制，但不删除其已登记候选 Actor；
同一限制内混有未处置 issue、其他 Actor、事件或道路依赖时保留。
独立验收重新验证来源、整条限制的处置和核心投影；原始观察与独立性证明仍绑定在收据中。
旧 v1 收据按原合同读取，不能通过修改版本号或重签摘要取得新规则授权。

`core-approximate-probe.v3` 的未知车道车辆如需执行排除，可在隔离 Case 中用现有
`review_world_question.py prepare --question-id ... --scope-event-id ... --frame-id ...`
对尚无 World question_id/Census issue_id 的 Behavior 对象限制，现有脚本的
`prepare-observations` 只冻结候选 JSON 指针、原文哈希、有效 World 版本和原帧哈希；
连续性上下文取疑点首帧前一张及两疑点帧之间的冻结证据帧，最多 12 张。
已登记 issue 留给原 Census 入口，结构化对向范围留给原 Behavior 范围验收。
模型任务仍由原 `world_closure` 的 `run_source_observation` 入口和原预算所有者提交；
请求准备不提交模型。World 答案分别报告车辆真实性、身份、核心路径关系与运动证据缺口。
只有经过原任务验收的 `not_vehicle`，或有既有 World/Behavior 隔离证明的
`physically_separated`，才在私有执行副本移除精确绑定且无 Actor、事件、道路依赖的限制；
原候选保留，Core 来源哈希及回放绑定复核文件。未决或核心相关结论保持原
严格/Core/Description 阻断，受理回执本身不修改 Actor、轨迹、事实版本或 Target。
Core 首次投影、恢复重放和独立 Target 验收共用同一 Behavior 准备函数；
副本内的冻结复核请求仍由其原 Case 核验绝对路径和来源哈希，复核只应用一次。
不得因目录复制而漏用已验收凭证，也不得改写请求路径或重签来源来通过验收。
冻结 Census 阅读视图在隔离副本中保留原字节；先按原 Case 验证完整视图，
再核对副本的 Batch、证书、World、Registry、claims 和事实版本清单哈希。
仅目录位置可不同，任一来源、活动版本或视图绑定改变仍阻止交接。
真实回答后的动态交互由原 Behavior 受限恢复复核，
再走原 Converter 与独立 Target 验收。
冻结 World 问题、Ego 事件、Batch 和原帧，再由 World 回答相对道路边界占用、潜在交互及适用时间窗。
仅 `path_separated` 且覆盖核心事件的来源绑定回答可进入 `core_projection_sources`；
车道与后续运动仍保持未知。独立 Target 验收重放该回答、有效事实版本、完整来源和核心运动义务。

共用执行代码变化会产生新的 Converter 实现指纹。已有隔离 Analysis、转换请求和 Target 不隐式迁移；
旧产物的指纹不匹配时，独立验收以 `implementation_version_incompatible` 拒绝，保留冻结及当前指纹。
该结果表示版本不兼容，不表示旧 Target 内容损坏，也不允许重签收历史产物。
新输入使用 `--new --core-scene-approximate` 进入原转换及独立验收流程；严格完成验收规则不变。
原 Behavior 在本地期限超时后，普通接管和显式 `--retry-failed` 均先查询原 task 的远端终态，
核对冻结请求、session 和任务预约。远端仍运行或状态不可得时停止且不派新任务；远端完成则
复验原候选；远端明确因 timeout 失败但候选已落盘时，先将原字节归档并绑定原任务，
再按源绑定与内容门禁处理。其他远端失败按其原因停止。
只有远端明确失败、本次没有候选文件且符合传输恢复预算时，显式重试才可新增一个 Behavior 任务；
不完整或校验失败的已归档候选不能借传输重试绕过内容门禁。
重复接管复用同一归档，不重新计费或创建 Case；原超时与远端错误均保留在诊断中。

## 显式核心场景近似输出

`--case-id ID --retry-failed --core-scene-actors ego ACTOR_ID ...` 只接受明确的核心车辆集合。
首次采用要求原独立范围复核已终态 `invalid_output`，错误为无关证据不足，原回答结构与全部帧覆盖完整，
所有任务已终态、没有受理未明记录，且尚无正式 Behavior/analysis。不能与其他恢复或迁移参数组合。
不是新模型复核，也不是旧严格证明的放宽；原失败任务、`retain/unknown`、dependency 布尔及文字依据保持不变。

唯一新增原子文件 `state/core_scene_scope.accepted.json` 使用 `core-scene-approximation.v1`，
绑定原冻结请求及所有输入、Behavior 与复核生产任务、隔离回答的文件哈希、核心集合及可重放执行凭证。
原冻结策略不覆盖；重复同一命令只复验，换核心集合不静默重新授权。请求写完即能恢复，不依赖另一个可失配的授权账本。
执行处理为 `omitted_by_execution_policy`，身份仍为 `unresolved`，视觉独立性为 `not_proven`。
每次接纳、执行投影、构建、转换及重启都核对；历史 census 快照不能读取当前模式。

在原复核候选内，只允许省略一到两帧的车辆观察。ego、显式核心集合、事件/动作结构引用、
至少三帧已知车道轨迹及已记录核心关系的传递依赖必须保留。身份候选/替代身份/受影响对象跨越保留和省略集合，
正式 distinction、道路或事件依赖不能被消除。部分保留组继续走原门禁，不因其他组可省略就全体放行。
候选稀疏只是该近似策略的适用边界，不是远处目标无关的证据；严格复核的反对意见完整嵌入输出审计。

新模式在旧严格复核前验证并采用原 Behavior；旧未提交 World 恢复保持暂缓，已提交任务和提交日志仍按原逻辑对账。
只裁剪执行副本及其专属问题、索引，原始观察/身份/轨迹不改。正常关系提取、语义运动补全、相对运动、
连续变道碰撞检查、严格 target 转换和交付均保持。输出审计 `quality_report.execution_actor_scope` 标记省略依据；
成功为 `done_degraded`，验证只覆盖被生成的核心场景，不能描述为全场景安全或渲染验证通过。
采用本身不消耗模型额度；后续正常关系/运动模型仍按原预算执行。恢复期间不自动追加范围任务或重置已耗尽额度。

## 已完成范围补充后的早期关联独立性

新 Case 的正常入口冻结 `early_association_independence=early-association-independence.v2`：同次 Behavior 回答先通过完整观察范围/身份结构验收，再由 Python 对其剩余早期关联组执行与 v1 相同的独立性约束。全部成立时，原始 needs_identity、Actor 和轨迹不变，执行裁定记录 `independent_early_association` 及绑定当前 Case/策略/World/Registry/census/帧/Behavior/范围回答的证明。该证明直接随已验收范围记录持久化，不派范围补充任务、不使用 v1 sidecar、不增加恢复额度。条件不成立时仍按原身份门禁处理；损坏或漂移输入报错，不作为可忽略的不适用条件。正式验收、重启复验、投影和转换使用同一证明与首见时间边界。普通旧 Case 不自动采用；已完成交付保持不变。

`--case-id CASE --retry-failed --accept-early-association-independence` 是显式本地验收入口，不能与额度恢复、视觉复核、策略迁移或场景修订组合。仅在已有范围补充完整验收、无活动/受理未明任务、Behavior 尚未提交且因身份不足停在 needs_review 时首次采用。重复命令复验同一凭证，不追加模型任务或预算。通过后恢复正常流水线；后续正常转换可能调用原有文字模型。

`state/early_association_independence.json` 冻结 Case/策略/World/Registry/census/已完成范围请求及结果哈希、原 Behavior/review 摘要、精确 issue/Actor/限制集合、每车首见和轨迹摘要、已验收共现区分。原 needs_identity 与成功 decisions 均不改。限定首见不晚于1.5秒的固定同向车道车辆，全部早期观察必须早于每车首见；纯身份限制不得混入道路/动作依赖，早期自车不能占用候选走廊，不能有初始状态或早期变道/交互依赖。现有转换权限须允许位置、速度和未观察时段补值。任一条件缺失仍停止。

验收、执行投影、analysis 重放共用 `independent_early_association` 裁定。仅完整凭证能解除精确绑定的限制；原观察不作视觉无关判断，不确认团块所含车辆数量。`early_association_bounds` 传递各车确认起点，无时间描述在转换证据中为 `usage: audit_only`，coverage 必须为 `audit_only`，不能据此建立运动关系。涉及受保护车辆的关系不得覆盖确认起点以前；完整0秒运动方案仍需注明执行近似，并保留已知数值、全时域与碰撞门禁。


新 Case 冻结 `batch_evidence_capacity=batch-evidence-capacity.v1`：普通 Batch 默认最多4张；先按原规则跨批分配必需证据，仍有未覆盖引用时才允许相关普通批次扩到最多6张。新增名额只用于结构化必需证据和窗口边界，不增加普通抽样；总批数仍最多4，独立布局仍最多4张、单帧复核仍严格1张。扩展批次的所有证据在每档 JPEG 打包中保留，超出原请求12 MiB或单图2 MiB限制则停止，不删必需帧。选帧、打包、冻结 manifest、Batch 重放和只读交付预检共用容量合同；旧 manifest Schema 文件不改，在 Python 中按冻结策略组合6帧语法和严格用途校验。

不再提供旧 Case 的证据容量升级入口。新 Case 按冻结策略自动使用上述容量规则；已有容量采用回执保留只读校验，历史结果、冻结批次和原始失败记录不改写。
# 有限恢复（bounded-recovery.v1）

新标准 v3 Case 冻结 `bounded_recovery=bounded-recovery.v1` 和 `recovery_limits`：
`transport_retries=2`、`content_repairs=2`、`total_extra_tasks=8`。这些上限只控制新增任务，
不授权跳过事实或执行校验。无标记的历史 Case 沿用原两次规则；其他专用合同的一次性限制仍有效。

旧 Case 的显式有限恢复升级入口已移除，不再创建 `state/bounded_recovery_adoption.json`。已有采用回执仍按原输入哈希和签名读取、校验，不改写旧策略、历史记录或旧补答请求；新 Case 使用创建时冻结的恢复规则。

恢复分类使用结构化错误及实际候选：无拒绝候选的 timeout/stream/gateway/status 故障是传输重试；
已有候选且校验失败归内容修复。受理未明/远端未决先对账，不重复提交；鉴权/额度等终态错误沿原停止规则。
已有身份合并证明通过后，World `field_evidence` 中归一到同一字段路径的历史证据列表完整合并、去除完全重复项；普通键值观测冲突仍拒绝，原始 World、请求和模型回包不改写。
传输重试按阶段及冻结请求最多2次；没有旧请求指纹的历史消耗保守计入。
内容修复按阶段族合并为问题组（Behavior 及其复核共组、World closure及再裁定共组），每组最多2次，
避免更换 issue ID、重启或拆分问题绕过预算。所有原有额外任务及专用授权任务计入总8次，
正常首次阶段不占额外预算。内容修复与传输计数分离，较窄专用合同仍先限制。

恢复凭证链时，历史 Behavior trigger 只使用已复验祖先的 World、Registry 和冻结帧，
不得通过当前 evidence_frames 或自动独立性证明再次读取正在复验的最新凭证。
真实循环、深度上限、哈希及证据校验保持有效；绑定实时文件的旧 v1 独立性 sidecar 不迁移到历史复验。
World 已原子提交且任务终态已确认时，普通 `--case-id ID --retry-failed` 复验后推进原恢复记录，
复用已提交结果，不再授权或提交同一 World 任务，不增加预算消耗；下游 Behavior 仍须正常验收。

Behavior 接纳先整理重复帧索引：仅 `irrelevant` 的 sightings 已完整、无重复、帧和时间合法、
全套证明与依赖校验通过，且旧索引只是合法子集时，才能从 sights 重建 evidence_frame_ids。
缺失真实证明、重复/陌生帧、道路矛盾和身份依赖不修为通过。记录原/新索引与证明哈希。

正式身份争议仍走现有 World census 所有者，开始前检查 World 与 Behavior 两个后续任务的可用预算。
随后受限重建保留原已有车辆轨迹的样本、帧/时间、位置、速度及已知车道，
只可补原未知车道的 lane_id/road_id/ordinal_from_right/detail；已通过的 scope 行原样保留并重验。
不能靠新增或拼接旧车轨迹解除身份争议。

其余范围/执行证据缺口采用 `bounded-behavior-repair.v1`，复用 `state/behavior_repair.request.json`。
包内冻结完整基线、输入哈希、目标 issue_ids、通过行、叶字段 allowed_paths 与车道闭合义务；
模型仅返回基线哈希、所选 scope decisions、受限 replacements 和 lane_closures。
Python 在副本合并并执行全部原校验；不允许改角色、身份、已知事实、时间/速度/位置、事件或直接删改限制。
补未知车道必须给同一点含 frame_id 的边界证据，lane_closures 由原确定性合同验收。
原始候选、每轮基线和旧请求保留。局部通过不是最终交付。

World census 裁定后、新 Behavior 修复任务派发前，程序对已验收的旧/新帧集做只读预检。
若合法新增帧使未涉本轮身份裁定的 Actor 既有范围证据或受保护 scope 决定失效，
使用同一 `bounded-behavior-repair.v1` 包的 `full_evidence_review` 模式：只重审包中列出的
`execution_scope_evidence` 与当前 issue 决定，保留原 Behavior 的身份、轨迹、事件、限制项关联和描述。
旧帧或图像哈希冲突、缺少旧索引及未获 Census 凭证的新增帧直接停止；旧冻结修复请求不切换模式。
完整证据重审仍占原 Behavior 内容恢复额度，使用原候选格式及完整准入，不退款、不追加授权。

只有阻塞集合严格减少才允许继续下一轮证据补丁；换措辞/编号、新增阻塞不算进展。
一般阶段同一结构化错误连续重复也停止。`bounded_recovery_no_progress`、分类/总预算耗尽、
输入漂移或越权补丁停止为 needs_review，记录停止原因和原始校验问题。
中断后先采用已有结果/对账任务；预留名额无回执时不能再次提交。原任务历史不重写。


## 未采用的后续身份关联

新 Case 冻结 `unused_association=unused-association.v1`。普通单候选车辆已有至少两个确认目击，未决观察晚于全部确认目击、无候选反证，且实际 Behavior 保留原锚点、没有采用该观察或依赖它的动作时，Python 可记录 `unadopted_association`，不再要求模型补“无关证明”。接收、执行投影、analysis、转换和最终校验复验同一凭证；仅解除精确绑定的纯身份关联限制，不删除车辆、改变轨迹/动作或补车道。身份仍 unresolved，事实层保留原观察，报告标明跟踪/交互细节可能缺失。这是执行范围选择，不是“该物体不会影响变道”的证明。真实身份反证、混合身份/动作限制、已采用的后续观察、车道或动作缺失仍由原门禁处理。旧 Case 不隐式升级。

旧 v6 Case 仅在 World 凭证有效、从未尝试 Behavior、无未决模型任务且尚无 Behavior/analysis 时，可显式运行本地脚本：

```bash
python skills/video-case-orchestrator/scripts/adopt_unused_associations.py --case-dir /absolute/case/path
```

### 有界仿真假设

新 Case 冻结 `execution_assumptions=execution-assumptions.v1`，与未采用关联互补，所有既有 Case 保持旧策略。已有 v6 World 凭证且从未尝试 Behavior 的 Case，可明确采用：

```bash
python skills/video-case-orchestrator/scripts/adopt_execution_assumptions.py --case-dir /absolute/case/path
```

该命令只增加来源绑定的 adoption sidecar，不改原策略、World、Registry、任务历史或预算，不派模型。使用已有归档回答的离线验证只能在隔离副本执行；没有真实 Behavior 不能宣称 target 跑通。

- 未确认观察：仅已归档的远处微小目标，且无旧候选或全部旧候选已有 World 反证，可采用 `observation_only_by_assumption`。实际 Behavior 不得在轨迹、事件、primitive、保真关系或挑战中采用它；新替代身份、交互依赖和混合限制不自动处理。原始观察、反证和身份 unresolved 留档，不新增车辆，也不合并到已有车辆。这可能漏掉真实车辆及交互，不是视觉无关证明。
- 车道：仅精确三车道同向高速路网、普通车辆至少两个已确认锚点，原 Registry 证据明确中间或中间/右侧范围，Behavior 保留原锚点但车道仍未知时适用。唯一中间车道可复用；多个候选优先已有合法锚点，否则按从右至左顺序取首个。记录全部候选、原证据、所选车道、逐帧替换与 selection 规则。假设锚点间没有额外变道，不延长观察时间。涉及该车的动作、primitive、其他车轨迹引用、video_fidelity、挑战、已知横向位置和混合车道限制仍阻塞假设。
- 接收由程序签发 `behavior_scope_review.accepted.json` 中的 `execution_dispositions.execution_assumptions`，模型不负责伪造证明或选政策标签；同次回答的 unknown/needs_identity 或缺省只在上述程序条件满足时可继续，错误回答仍校验。仅精确绑定的纯观察身份/车道限制或对应单车纯车道限制可从执行副本移到审计记录，其他限制不变。
- 同一凭证可附带 `inherited_opposing` 来源记录：仅高速单道路、无路口、已观察且无争议的护栏分隔，复用已验收 census `excluded_opposing` 的首末及原帧证据；图片变体哈希与时间必须匹配。Registry 每个原始锚点明确在同侧屏障之外，实际 Behavior 未新增锚点、已知位置或同向车道等冲突且无动作依赖，才继承范围，不要求模型重复填 background/direction。群体观察还须单一群体候选、自身明确位于同侧隔离区域、同帧来源凭证及全部待查观察覆盖。身份仍 unresolved，绝不按同帧建立一一身份映射。接收把这些观察的来源加入范围记录；投影与最终复验同一原始凭证和依赖集合，仅移走已隔离背景的纯运动限制。对向背景运动不进入 target，原始车辆/群体证据保留；同向及影响主要动作的车辆、事件和 primitive 不得删改。无标记旧 Case 不采用该规则。
- 执行投影先重验来源和原车辆、轨迹、事件、primitive 的保留约束，再只补记录中的空车道。与 unused-association 的原始保留检查组合，而非覆盖原始回答或重签被修改的事实。允许复用已有对向背景过滤：从原始 Behavior 重新验证逐帧对向证据、background 角色和无依赖条件，群体仍需已有范围验收。事件、primitive、其他车辆引用、保真要求、挑战及混合身份/道路/动作限制涉及的对象不能被过滤；已有未采用关联或车道假设需要保留的对象也不能排除。仅允许这个独立验证的对向子集从执行副本移出，原始观察留档；其他删除、轨迹改写和任何事件/primitive 改写仍拒绝。所有确定性生成、交付和重启均重放同一记录；禁止 Converter 再排除投影后保留的确认车辆。最终 analysis 的车辆集合、事件及轨迹必须与重放结果一致。
- 报告输出“有记录的仿真假设”，完整依据保存在 analysis 的执行范围凭证中，并标记降级。既有结构、引用、主要动作、来源和运动约束继续执行；缺少正常 Behavior、对向群体个体化、未覆盖的身份/动作/道路问题或不可行运动仍可能阻塞交付。

此操作只写 `state/unused_association.adoption.json`，绑定原 Case、策略、World、Registry、census 凭证及已冻结的 Behavior 请求；不改这些文件、不调模型、不重置预算、不推进 Case。后续事实或绑定改变必须停止重新核对，不能用旧凭证继续。已完成 Case 不迁移。

同次 Behavior 的相关范围行可以缺省或保持 unknown/needs_identity；其他行仍完整校验，已给出的反证/替代身份/动作依赖不能被覆盖。程序把处理记录放入现有 `behavior_scope_review.accepted.json.execution_dispositions.unused_association`，不合成模型的 review 答案。投影只从执行副本移除精确关联限制，原始 Behavior/World 不变。保留约束与上文独立验证的对向过滤组合；除此之外，已有排除或转换逻辑若会删掉确认车辆，仍停止。程序凭证损坏、输入漂移或投影保留约束失败归本地程序问题，不触发模型内容修复。

验收分别报告：真实 Case 现有证据的适用性、真实 Behavior 的采用情况、软件夹具的跨阶段验证、真实成功 Case 回归。缺少真实 Behavior 时不得声称当前视频已生成 target。
# 有界执行补全（bounded-execution.v1）

新 Case 冻结策略；旧未开始 Behavior 的 Case 可显式调用 `adopt_execution_assumptions.py --case-dir CASE --bounded-execution`，同时采用既有保留尾段规则。采用记录绑定来源，不改原策略、事实、任务或预算。普通接收入口继承符合条件车辆的 Registry 身份文字，原 candidate 和修正审计保留；该处理不是接受新身份或动作的通道。

投影凭证绑定完整观察和动作，在执行 metadata 中携带同一来源记录；最终 scope 重放比较该记录。边界触及 0 或视频结束的单次相邻变道，原观察时间保持，执行区间仅在包络内留出不超过0.5秒且不超过包络十分之一的直行段，转换回执标记 `bounded_lane_change_timing`。缺失实际变道事件、帧证据或动作冲突仍按原门禁报告。

正常转换先合并来源可明确整理的时刻关系和已有尾段处置，其余来源仍交正常关系任务。完整关系之后，数值阶段在提交模型前做有限联合匀速搜索，最多8辆和20000次候选；全部已保留车辆、已确认值、已有动作及已接受计划不变。程序检查点与普通检查点使用同一输入哈希、原验收器和最终校验，重启重算；已有或未决远端任务不得绕过对账。搜索耗尽为 `motion.bounded_search_incomplete`，不增加模型修复预算、不自动改用无约束默认值，也不宣称不存在其他数值解。
