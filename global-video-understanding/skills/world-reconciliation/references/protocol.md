# World 协调协议

实现：[_lib/stages/reconciliation.py](../../_lib/stages/reconciliation.py) 调用 [world_readiness.py](../../_lib/world/readiness.py)、[world_closure.py](../../_lib/world/closure.py) 和 v3_contract.reconcile_world。编排进程是正式 World 的唯一提交者。

机械别名、重复元数据、嵌套 evidence 可无损规范化；不能据此推断交通方向、车道数或解决未知。geometry_readjudication 保留既有完整 World candidate 协议，限指定 conflict；closure 使用 world-closure-patch-v1，两者不是同一候选格式。

历史正式 Batch 出现“集合路径 + 单成员对象”时，只有 `roadside_areas` 的 `area_id` 在冻结 World 唯一命中、对象除 provenance 外恰有一个既有叶子发生变化、且叶子替换后 World 仍通过门禁，才在 reconciliation 私有副本中收窄为精确数组成员叶子路径。正式 Batch 文件和哈希保持不变；原始路径、原始值、稳定选择器、规范路径及 provenance 写入 `conflict_normalizations`。任何不唯一或需要解释事实的情况继续 needs_review。

在线调用 `ensure_world_ready(case, execution=False)`：保留结构、引用、拓扑一致性及身份门禁，未决 World 事实不单独阻断 Behavior。默认 `execution=True` 保留严格行为及兼容入口。Builder 自行汇总未决核心事实为执行缺口，以 `quality_report.world_uncertainties` 保留原记录及对应字段证据，生成观察 analysis 后停止 target 交付；不把未决项改成 resolved/non_core。已有活动 closure 沿用冻结问题与完整输入绑定，先对账，不能因切换模式跳过任务。

观察 admission 与 Behavior handoff 不同：初始 World 的聚合数量可以作为观察交给 Batch；reconciliation 后必须有可引用的 road_001 及其已识别 lane 定义。仍只有数量、Ego 道路不存在或该道路没有已识别车道时，`world.handoff.lane_definitions` 以 `world_evidence_insufficient` 停在 aggregating/needs_review，不启动 Behavior，不猜造车道，也不为同一缺证重复提交任务。局部车道清单、未知总数、未知出口连接和未完整观察的其他路臂仍保留，不提升为 exact 或强制三路臂。诊断绑定 World 哈希和 JSON path。

新 Case 的 `reconciliation_alignment=reconciliation-alignment.v1` 在首次冻结 Behavior 请求前复用 Actor 时间线及有效 fact version。确定性检查同 Actor、同帧的结构化车道、原始与有效 World 物理车道及车道映射；带问题绑定且影响基础空间事实的原文语义未核实项阻断请求，并给出原文、来源和定向 World 复核动作。未绑定 Actor、但有明确道路问题与道路 ID 的观察，按道路、事实和问题归组送同一定向 World 入口；请求保留逐条来源、原文、帧及哈希。仅在有效布局的同道路段可见帧解释每条来源后解除该来源阻断；真正矛盾继续停止，遮挡帧不改成直接观察。路肩存在性证据仍不足且道路段已定位时，来源保持未核实，由 Behavior 后的既有执行依赖检查决定 Target 是否受阻；不细分路肩铺装类型。其他原文仍交由已有 Behavior 来源核对，无法定位道路的观察仍待来源绑定，不猜测身份或路段。跨帧位置只形成既有运动义务，不在 Reconciliation 生成事件。旧冻结策略及请求不自动启用新门禁；有效版本或 Batch 来源变化不能复用旧检查。

交付范围以视频实际时间窗内 Ego 的运动及必要道路、车辆交互为准。Reconciliation 复用 `execution_dependencies(world, None)` 初判：已定位的非停车路侧未知，或已有结构化 `after_observed_window` 范围、当前布局与 Ego 走廊及窗边界证据的后续拓扑未知，可保持 `unverified` 并交给 `behavior_dependency_review`，不为这类来源派发重复 World 任务。当前 highway 的完整独立布局、边界和 Ego 初始走廊已接纳时，纯 road_topology 疑问也可先交 Behavior，即使尚无最终时间定位；这只允许读取已确认事实，不证明后续拓扑无关。lane_count、核心边界缺证或真实冲突不走此通道。初判只返回 `deferred`，不证明执行无关；Behavior 生成后按实际轨迹、事件、动作、道路/路侧区域/连接引用执行原最终依赖复核。来源矛盾、核心边界缺证、实际使用的未知连接、超出证据时间窗均不豁免。缺少范围定位时不得从自然语言或已确认车道数自动推断“视频外”或“没有分叉”。已耗尽预算不重置。

同一 World 道路复核允许 `partially_explained`：各来源独立保留 covered、visibility_limited 或 unresolved；混合来源使用 `assessment=partial` 与源内 `components`。每个片段保存原文、事实类别（current_layout、visible_sign、marking_interpretation、future_topology、roadside_structure）、covered/unresolved、帧引用及理由；按序拼接必须精确还原整条原文，禁止漏掉未知片段。确认布局片段须同时引用原帧和已接纳布局帧；可见标志不证明物理分叉，未见后续拓扑不能 covered。部分结果不修改 World，不把整条来源标为 covered；未知仍沿同一依赖门禁判断。Converter 额外长度/连接仅能使用原合同允许且注明来源的执行近似，不能作为视频事实。

visual-answers.v2 的 World 复用及 Batch/Behavior 冻结统一核对原 `world_observation.request.json`、case/帧索引/策略哈希和源图字节。原请求缺失或输入漂移返回 source_changed；不能通过重建请求让已有 World 配对新图片。旧模式不迁移冻结合同。

closure 请求绑定 base、Batch、Schema、相关图像哈希，并包含 issue、allowed_paths、focused_context、coordinate_convention、resolution_values 和 stop_contract。模型只读小包与允许的图像，不重新读取完整 World/Schema。普通及旧冻结布局只允许 1–32 个有合法帧证据的字段 add/replace；新冻结布局使用下面的单一裁决合同，不提交 operations；缺证或权限不足按停止协议返回零操作。

合并在 base 副本执行，保护身份、验证项集合、epoch 和哈希，不允许越权、数组插删或无证据值。无损字典赋值由 base 决定 add/replace。完成后执行 Schema、evidence、registry 和 readiness。needs_evidence 不能被重试或枚举别名变成 resolved；某个补丁应用不等于整题已解决。

World 事实字段、坐标与模型任务模板由 [World Skill](../../world-understanding/SKILL.md) 维护。

## 同向布局自动闭环（新冻结请求）

`lane-layout-review.v1` 把同向数量、右外边界、首帧自车两侧边界/序号及右侧路侧用途分别保存；普通 Batch 不拥有最终布局。独立 Batch 与 World 一致且完整时，确定性解决专用布局问题，不增加模型任务；出现差异、缺关键结构或声明不相容时，编排器自动进入已有 `world_closure`，不在首次发现差异时要求用户补字段。

`lane-layout-closure.v1` 仅允许单 highway 道路、无路口的布局结构单元；使用 [定向裁决模板](../../world-understanding/references/lane-layout-closure-prompt.md)。允许指定 lanes/markings/roadside 集合原子替换及其必要引用，固定其他事实与身份。每个旧物理车道以 lane_id_map 保留，已知尺度不能被删除或覆盖；所有改动有精确路径 evidence，并验收 N+1 边界、数量、顺序、自车位置、道路与 Actor 引用。同向已确认车道必须使用正式方向、类型、序号和左右边界字段；初始 World 仍可保留未知与局部车道。自车位置按观察边界链核对；只有已有 ego Actor 车道引用时才额外交叉检查，不要求虚构 ego 条目。Batch 不直接改 World，但 World 候选若仍与独立 Batch 的 exact 同向数量或已确认自车序号冲突，不得直接提交。

历史 v5 Case 的 `state/execution_policy.json` 固定 `lane_layout_closure=lane-layout-decision.v5`，并把该策略文件纳入 closure 输入哈希。新冻结请求使用[多区域证据裁决模板](../../world-understanding/references/lane-layout-decision-v5-bands-prompt.md)：请求只给 World、Batch 的规范化主张、旧 ID 结构和原始帧，不传旧判断性文字。模型在一次 world_closure 调用中提交 N+1 个行驶车道边界、逐段行驶证据和从最右行驶边界向外的完整 `roadside_intervals` 物理链；只对真正不相容的数值主张提交 claim_disputes。Python 按内外物理边界匹配旧区域，保留对应 ID 和仅在两边界均一致时保留宽度；不能唯一匹配或遗漏旧区域就停止，不按执行时 `paved_shoulder` 映射合并。`minimum=2` 与三条车道相容，路缘/道路外缘登记类型由 kind 推导。推翻数量需两帧和具体区间，推翻自车位置需真实首帧。Python 统一生成 World 的车道、区域、引用和精确路径证据，再按 World/Registry/Admission 门禁验收及原子提交。已有冻结 decision-v5 请求保留[原标量模板](../../world-understanding/references/lane-layout-decision-v5-prompt.md)和原 Schema；冻结 v4/v3/v2/v1 也不迁移。代码能检查帧绑定、区域链和引用，不能代替视觉判断。

采用已有全局额外模型预算（2次）；布局补丁的可定位字段格式错误、边界注册缺项（`world.layout.boundary_registry`）或上述核心冲突最多允许一次新 session 定向修正，持续冲突保留独立证据并停止，不把 Batch 自动提升为 World 真值，也不无限重试。边界链中的物理路缘/道路边缘与漆线一样，必须在 closure 的 `lane_markings` 完整边界注册表中有唯一 `marking_id`；错误诊断列出缺失 ID。旧版本把相同错误持久化为通用 `world.layout.patch` outcome 时，仅在请求哈希不变、尚有该 closure 的一次修正额度时归档旧 outcome 并恢复，保留审计记录。未决对向数量不自动否定已确认同向数量，不能仅见 minimum 一刀切。无证据、无法唯一裁决、越权、无完整修正或预算耗尽仍如实停止，不承诺所有视频无中断。相同冻结请求的明确停止不会重发。修正通过后提交 World、累计 Registry 及 receipt，继续既有 Behavior；原正式 Batch 不改写。提交 journal 使进程中断后的局部写入可幂等补齐，不新增第二套状态机。

## decision-v6 与本地复验（2026-10-04）

历史 v6 Case 冻结 lane-layout-decision.v6；v5 标量/多区域和 v2–v4 仅兼容冻结恢复。模型提交边界对，Python 编译位置并生成初始 ego 主张差异；count 争议仍需直接视觉证据，不削弱旧区域连续性、身份、尺度及原子提交门禁。

普通恢复复用匹配的冻结布局请求，输入哈希漂移明确 source_changed，不用当前模板重写旧请求。失败记录绑定 request/candidate/validator 哈希和结构化路径诊断。终态停止在 validator revision 变化后可对同哈希 candidate_snapshot 本地复验；失败时保存新版本，下一次不重复复验；相同版本不重提。活动任务先走原对账路径，不从存档抢先提交。复验通过沿既有 journal 提交，不重置模型预算。缺证或冲突仍停止，不将本地回放视为真实视频模型验收。

同帧 existing 身份判断只需冻结首次目击作为锚点；跨帧提前/延后身份仍须满足原连续目击与共现反证。布局合并后的旧车道编号仅在登记 Actor 已确认的时间窗内由 Python 映射，正式 census 回答和原始候选保持不变；更晚帧的不同车道判断属于事实分歧，不能当格式差异自动合并。

首次联合 layout/census closure 尚未提交时，`reconciliation.json` 可以不存在：原执行路径的汇总结果只在内存中传递。续跑在校验冻结请求、完整 Batch 集合和当前 World/Registry 与冻结快照一致后，使用同一确定性汇总函数重建待裁定结果；结果必须逐字段匹配已冻结的 layout World base，且冲突仍限原布局裁定范围。重建不写正式回执、不确认未知、不改旧 Batch。新候选通过原身份、道路、证据及输入绑定校验后，才通过原 journal 一并提交 World、Registry、census 证书和回执。已有未完成 journal 优先恢复；已提交的后续轮次缺失回执不能套用首次重建。

一次性合同修复成功后，重复同一续跑命令核验原任务与已提交证书并继续下游，不重新授权或提交修复。受理不明仍保留占用并停止重复提交；普通预算、历史预留与 Batch 结果保持不变。候选提交前再次核对当前 World/Registry，不能覆盖任务运行期间发生的基线变化。

## decision-v7：内部虚构边界纠正

新 Case 默认冻结 `lane-layout-decision.v7`，使用 [v7 模板](../../world-understanding/references/lane-layout-decision-v7-prompt.md)。当旧主张因不存在的内部边界而重复拆分车道时，count 争议以 `absent_boundary_ids` 明确否定内部边界，保留两侧旧边界和至少两帧共同证据；一个连续区间必须解释全部数量差。仅该区间内的连续旧车道可合并，已知属性冲突继续停止。编译同步移除被否定的标线、重建引用及数组证据，并汇总可独立判断的共同帧、数量和映射错误。冻结 v6 及更早合同不迁移，正常恢复和原子提交机制不变。
