# Behavior 字段与验收

## 局部来源撤出权限

已有 `behavior-source-coverage.v1` 补丁可由新哈希绑定请求附加 `behavior-source-withdrawal.v1` 授权；不升级旧冻结请求。仅允许撤出授权清单内引用缺失或无效、且冻结索引不存在同 Actor/同帧组的 Ego 点。首点、事件证据帧、事件及 primitive 时间端点、相邻已知车道转换两端均锁定。不得混用其他字段替换；其他 Actor、剩余点、事件及 primitive 保持不变。

原候选保持不可变，独立归档补丁、修订结果及包含原哈希、请求哈希、授权、原点、无效引用和理由的凭据。Ego 总述收窄为保留轨迹和事件的引用；撤出点使用现有 `bounded_uncertainty` 局限记录未决，不生成其车道、位置或速度。有关联的其他 Actor 部位观察保留真实来源所有者、原文及定位指针，在 Core Analysis 和 ConversionWork 中作为 `qualitative_only` 承接；不能据此生成整车中心并行或完成超越。独立交付清单公开这些局限，既有来源、变道、身份、运动、数值及 Target 门禁继续重放。

新 Case 的 `behavior-input.v1` 使用内部 sidecar，不增加 Behavior Schema 字段。进入 Behavior 前必须有已完成的 reconciliation；编排器冻结最终 World epoch、World/Registry/Batch/manifest、参数、probe、联合帧索引和实际图片哈希到 `state/behavior_input.request.json`，并在独立 `behavior_input.sessions.json` 中绑定模型 session。提交前、候选验收前及检查点恢复时核对同一快照。同 epoch 内容漂移同样是 `source_changed`，不得重新盖章后接受旧 candidate，也不耗费模型重试去修复输入变化。

明确的 post-Behavior World 裁决可归档旧请求、使旧 Behavior 失效，再开启新请求；普通恢复不自动更新请求。未声明此 revision 的旧 Case 保持原合同。只读离线重建核对现有请求而不创建请求。

仅新建且冻结 `behavior_evidence_timeline=actor-time-evidence.v1` 的 Case，在同一 Behavior 输入请求内附带按 Actor/帧/时间聚合的 Batch 原文索引。索引保留来源 JSON Pointer、Batch 哈希、未决身份和当前已接纳 claim；原文仍是来源主张，不自动变成正式车道或动作。Worker 先读索引，再按需核查完整 Batch 与原帧。可用现有点、事件证据、精确未决说明或既有执行范围证明承接同帧多条观察，不为覆盖补造轨迹点或事件。相关节点的可选 `source_observation_ids` 引用索引组 ID；Python 同时核对当前冻结请求、Actor/帧/事实关系、说明及 motion-obligations。只引用 ID 而没有说明或与已接纳事实冲突不能通过。旧冻结 Case 不自动添加索引或追溯要求；来源漂移需新事实版本及重验。

Actor 身份与车道字段使用 [跨阶段 Actor 合同](../../references/actor-contract.md)，与 World/Batch 共用 Schema 类型；不要求新增 first_seen_frame_id，也不要求多时刻轨迹车道与 World 初步假设始终相同。

权威结构是 [behavior.schema.json](../../schemas/behavior.schema.json)；模型生成规则在 [prompt.md](prompt.md)，定向修复规则在 [repair-prompt.md](repair-prompt.md)。完整消费门禁为 [behavior_compile.py](../../_lib/behavior/compile.py):validate_compiled_behavior，调用 v3_contract.validate_behavior 并检查身份与帧绑定。

道路内位置与全局ID使用 [统一位置合同](../../references/lane-location-contract.md)。轨迹的 road_id/ordinal_from_right 为可选声明；已知值必须与 World lane 归属和位置锚点一致，观察模式同样检查。缺少位置依据时保留 null；不从自然语言 detail 推断后覆盖事实。

路侧静态车辆可选 `roadside_area_id`（World 区域引用）和 `parking_side=left|right|null`，原 Schema 的 required 不变。已知区域归属、occupied_by 与显式侧别必须一致；缺字段保持兼容，交给执行策略。首点 road_id 给道路归属，证据继续使用真实轨迹帧，侧别不根据自由文本机械推断。行驶车道内停车保留 lane_id。

candidate 的 initial_state 可省略，由代码从证据轨迹首点派生。正式检查点必须通过首点与 initial_state 一致性、单位转换、时间顺序、车道引用、参与者和事件证据门禁。独立 validator 在内存派生再校验，不把规范化副本写回 Case。

World/Registry 是冻结输入；Behavior 不得输出道路、junction 或环境。群体身份不能编造个体轨迹；background 不等于 static。bounded_uncertainty 仅允许进入后续具体校验，不是可执行证书。事件缺证时保留未知，并使用协议允许的执行裁决，不能删除 limitation 绕过门禁。

Schema 与 Python 合同尚未完全统一，本次职责重构保留现有语义和 Case 格式。只读验收不等价于 Builder/Converter/真实渲染验证。

执行规划的 Review 补充由 `_lib/runtime/execution_plan.py:prepare_review` 在私有副本中合并，不回写 World/Behavior 或补充证据原件。重叠的 lane_knowledge 必须保持 kind、count 和全部 anchor 身份/ordinal 不变；anchor 按 lane_id 对齐，数组顺序不是事实变化。声明和 anchor 的 evidence_frame_ids 可合并、去重并固定排序。dependency_bindings 的证据同样可合并，但 kind/road_id/side 必须一致；limitation_bindings 的 actor_ids/world_issue_ids 只允许顺序不同，不取并集扩大绑定范围，kind/road_id（包括具有合同语义的 null）仍严格比较。整条记录缺失可按既有授权补充，unknown/null 与确定值不自动互相覆盖。事实或绑定变化返回 execution.review.fact_conflict 与字段路径；合并结果仍须经过 Schema、引用和证据帧可用性门禁，补证不等于事实裁决。

物理隔离对向交通有两种表示。可稳定识别并进入 Registry 的对象可作为 background actor；新执行范围要求运动中的此类 Actor 同时给出逐轨迹帧绑定的 observed `direction_evidence.value=opposite_to_ego`，同向车或方向不明车不得用 background 标签裁剪。无法逐个稳定实例化、仅作为整体观察保留时，semantic_limitation 使用 scope_binding.kind=physically_separated_opposing_observations，并精确绑定 world.road.opposite_direction_lanes。该绑定只是候选声明，Builder 仍需验证隔离证据、参与者车道和事件依赖。Preview 不需要为这类未实例化观察构造不存在的 affected_actor_ids。

观察与执行边界（2026-09-08）：在线接纳保留原始轨迹，仅派生可确定的initial_state，不按估计速度改写位置或外推时段。结构、身份、已声明引用和事件证据仍是硬门禁；明确身份冲突不可重试绕过。执行缺口在Builder写入analysis.quality_report.execution_issues，execution_ready=false时simulation_scenarios为空、target入口拒绝。只读CLI用--observation-only复用该观察检查；默认CLI仍为严格执行验收，不能把两者通过含义混同。

在线、独立 candidate validator、offline_preflight 共用 `_lib/behavior/compile.py:evidence_frames/prepare_behavior_candidate/validate_compiled_behavior`。证据来自 global 索引及已接纳 Batch 的 manifest 帧；检查图片哈希与同一 frame_id 的源时间，保留图像 variants。在线写派生 `state/behavior_frames.json` 供提示及定向修复读取；离线只在内存构建，不改 Case。该索引不是新事实协议。

观察准备统一为 `prepare_behavior_candidate`：按冻结合同补充可唯一确定的时间/车道映射、恢复 Registry 身份证据、处理未确认区域引用、规范事件区间并派生 initial_state。它不按速度积分重写位置，也不回推或外推轨迹采样；显式 null、unknown 与已声明冲突不被执行值覆盖。在线接纳、`--observation-only` 和 offline checkpoint-replay 都使用这个观察副本。各入口仍保留自己的输入绑定、策略及后续门禁，不代表所有验收模式完全相同。

默认严格 candidate CLI 单独调用 `prepare_behavior_execution`，先验证上述观察副本及身份、引用、证据，再检查已知速度/位置和车道/位置冲突；这些冲突直接拒绝，不能靠积分或补点消除。仅无此类冲突时，既有规则可在私有严格检查副本生成带 `execution_extension` 的估计边界点；不提交为 Behavior，不授权 Converter 默认值。未被正式流程使用的旧候选整理器已移除；观察整理只使用上述共享入口。提示词中的部分必填键、类型和枚举由现有 Schema 派生摘要；视觉、证据、单位和跨字段语义仍由对应合同与校验负责。

重复的 Actor 量测 unknown 可由一条结构化 semantic_limitation 统一解释，不要求模型逐点复制相同文字。继承只在 `actor_ids` 精确包含当前 Actor、`impact_domains` 精确包含字段域且匹配结果唯一时成立；局部 reason 优先，多条匹配或无匹配仍拒绝。Behavior 检查点保持原样，Builder 仅在 analysis 交付副本中写入指向 limitation_id 的短 reason，并在 quality_report 记录逐路径绑定；不修改 Schema、status、value、轨迹或原 limitation。

视频观察的帧/时间声明遵守 [冻结帧证据合同](../../references/frame-evidence-contract.md)，适用于全部普通轨迹、事件证据、challenge 的 evidence_frame_ids 及其嵌套观察。正式调用始终传入 evidence_frames 的联合索引，错误在原子提交前停止；frames=None 仅保留无 Case 上下文的结构/语义检查，不代表证据绑定通过。原始 candidate 禁止自声明 execution_extension；只读严格验收先校验原始观察，再在内存生成执行补点，仅此可信副本显式允许执行扩展。offline checkpoint-replay 的观察准备不生成补点，后续执行投影与 Converter 继续遵守各自的授权和记录规则。执行补点没有观察帧；一旦声明 frame_id，仍核对来源。事件区间与执行时钟不作抽帧逐点绑定。

新冻结 census v4 存在 observation_only 时，正常 Behavior 的同一次 candidate 顶层携带私有 census_scope_review（当前凭证摘要及逐 issue 的 confirmed/challenged/unknown、理由、全部证据帧、受影响 Actor/Event）。模型仍只写当前 candidate，不提交范围或身份；Python 移除该字段、验收完整覆盖并绑定正式 Behavior 摘要。遗漏不是确认；明确反证走受限 census 恢复，不按格式错误循环重试。无范围授权的旧请求/Schema 保持不变。

完整 Behavior 通过其他门禁后，缺失复查或合法条目未覆盖全部 issue 可触发 behavior-scope-completion.v1 补答。编排器冻结规范化完整基线、census 凭证链和证据；响应仅 {base_sha256,census_scope_review}，不包含 replacements 或任何 Behavior 字段。Python 校验输入与基线哈希，合并并重新运行原门禁；成功沿用原范围凭证及下游合同。补答共享原 Behavior 尝试和 Case 恢复额度；重复执行复用冻结请求与已有任务，不重新解释旧输入。遗漏/格式错误不能触发 census 撤销；完整且合法的 challenged/unknown 才进入原范围恢复。运动、独立车道和普通 replacements 修复的漏答仍遵循各自冻结合同，不自动扩大为该完整基线补答。

带 pending_scope 的 excluded 回答必须让 sightings 帧集合与 evidence_frame_ids 同时覆盖请求列出的 excluded_required_frame_ids；输入观察的单帧列表仅标识来源。证明漏帧与证据目录漏帧分别报告字段、issue 和缺失编号，普通完整 Behavior 可复用上述补答合同。证明结构错误、重复或未知帧仍按原无效候选处理；补答不自动补齐证据、不撤销范围、不增加额度。unknown/needs_identity 不要求全帧排除证明。

运动复核、独立车道复核和普通 Behavior 定向补丁若请求带 scope_context，必须在同一个补丁 candidate 中提交新的 census_scope_review，针对应用补丁后的完整 Behavior 复查全部有效 issue；不得复制归档基线的私有 review 或仅重绑哈希。请求明确授权读取 base_path 完整基线与 frames，写入仍受原 allowed_paths/修复合同限制。Python 先拆私有字段再应用补丁，按最终规范化 Behavior 签发范围凭证。无有效范围项时沿用原响应；旧请求缺少范围上下文时不得追授新范围权限。

独立 review_actor_lane 的 lane_only / lane_and_position 均遵守此规则；review_frames 仍绑定车道补丁的逐点帧，frames 供完整范围复查。lane_only 不允许模型修改位置/速度，原有横向估计撤回及 initial_state 同步仍由 Python 执行。范围凭证绑定这些确定性处理后的最终 Behavior，confirmed 才能完成提交；challenged/unknown 保存完整候选并交给原范围恢复。

behavior-census-scope.v1 请求可含 pending_scope=true 的未决 census 观察。同次私有 census_scope_review 对这些项用 excluded/needs_identity/unknown；每项额外给 scope_proof，后两种为 null。excluded 仅接受 physically_isolated_opposing.v1 的完整冻结帧位置/方向/屏障/无通道证明，不能以远、小、缺少事件或群体标签作排除依据。个体候选不能被排除；群体候选仍逐观察证明，并在正式群体 Actor 中给 background 与覆盖其全部轨迹帧的 direction_evidence。所有已登记 Actor 在正式 Behavior 中保留，Python 只在执行副本裁剪。原已授权 observation_only 项仍用 confirmed/challenged/unknown，不混用字段。完整未知回答保留候选并停止；明确需身份的完整回答可由原预算恢复机制处理，Worker 不修改 World/Registry 或发起任务。
# 新 Case 的身份与参与范围交接

新建 Case 的 `behavior_census_read_view=behavior-census-read-view.v2` 在冻结请求时把每条原始未绑定来源映射到最终 Census 状态：`resolved`、`excluded`、`remaining` 或没有精确车辆 issue 的 `unresolved_source`。正式前两类必须带可重放的证书、记录及绑定指针；`preliminary_relevance=background_candidate` 只是初判。相同的正式道路隔离证据可在 `shared_road_proofs` 复用，但每个已排除观察保留自己的来源帧、时间、对象与证书绑定，不能由道路证据生成逐帧车辆目击。Behavior 对 remaining 和未绑定来源继续按核心场景及动态依赖审查，Ego、目标车道车辆、交互及跨帧运动义务保持原门禁。验收凭证联合记录正式继承来源与本轮 pending 决策并重放来源覆盖。已有冻结请求沿原阅读视图和验收语义回放。

`world-census-adjudication.v6` / 新 Case 的 `behavior-census-scope.v4` / `census-execution.v2` 将观察与 Actor 分开处理。每条 pending 观察须在同次 Behavior 中给完整范围证明；同帧待查比较另给 distinct 或隔离对向证明，不能借观察过滤跳过。World 的 questions_to_verify、ambiguities 及 Behavior 的 bounded_uncertainty 限制可用非空、唯一的 `census_issue_ids` 精确绑定观察。只有这些 ID 全部已有验收的 observation_excluded 或 retained_unresolved 裁定、影响域仅为 actor_identity/actor_lane，且没有 Actor/identity_binding、道路/定位或事件依赖时，执行副本才关闭相应疑问。retained_unresolved 还须原观察帧与已登记 Actor 的同帧视觉锚点、至少两帧真实轨迹、已知道路位置和所有同帧车辆的区分依据；保留对象及原始来源，不确认身份、不新增轨迹，Target 仍须通过道路与运动验收并标记降级。原 World、Behavior、Batch 及候选 Actor 的有效轨迹和事件不改；未知、漏答、具体车辆身份争议仍阻断。旧执行合同沿用原语义。

isolated_opposing 和 unrelated_observation 都必须符合已知单向道路及对向隔离事实，不能通过更换排除规则名绕过冲突。证据覆盖分别检查 scope_proof/sightings 和 evidence_frame_ids；缺帧报错必须指向实际缺失字段并列出帧 ID，重复或外来帧按内容错误处理，不能当作补帧授权。

观察引用字段定义在独立的 `census_observation_refs.schema.json`，由共享校验器组合到 World 问题结构。原 `world_context.schema.json` 字节保持不变，以保留旧布局请求的冻结哈希。这个扩展只允许带类型的引用字段，不授予旧 Case 新的排除语义；原请求和证据仍逐项验哈希。

冻结 `behavior-handoff.v1` 的请求在同次 Behavior 回答中补充结构化证据。pending census 的 `identity_assessment` 区分 compatible/rejected/unknown，替代身份必须同时列入 `alternative_actor_ids` 与 `affected_actor_ids`；不能只在 reason 中否定原候选。身份修正仍由正式 World 裁定提交。只涉及未登记观察的限制用 `census_issue_ids` 精确绑定，混合车辆身份争议单列。

单帧参与者须有 `execution_scope_evidence` 的 execution_relevant 依据；高速短暂边缘/远处车辆可给 edge_fragment/distant_transient 证据。checked_frame_ids 覆盖完整冻结索引，visible_frame_ids 匹配真实轨迹；后两类必须声明并举证 no_interaction=true。执行投影仅在没有身份、道路或交互依赖时排除，观察保持完整。旧 Case 不启用这些新裁定语义。
## 可选提前关联与执行范围

v6 census 的 `irrelevant` 只表示该观察不参与执行，身份仍可保持 unresolved。`unrelated_observation` 不要求对向或物理隔离，必须逐帧证明不影响自车路径、换道、跟驰、避让或碰撞；远小和事件列表缺席不足以证明无关。

唯一候选的提前目击，可在完整 scope_proof 中追加 `candidate_independence={actor_id,trajectory_frame_ids,observation_used:false,reason}`。候选须有独立的后续已确认轨迹，观察早于 Registry 首见和全部轨迹点，所有轨迹帧必须精确列出；早于已确认轨迹的动作/关系、其他身份争议、混合 Actor/道路/事件限制均阻塞。`affected_actor_ids` 不应仅因为“可能是这个候选”而填入；`identity_assessment` 仍只用于 needs_identity。程序仅在执行副本消除精确绑定该观察的 bounded_uncertainty 限制，不更改原描述、首见帧、轨迹、事件或身份，不删除候选车辆。


`unused-association.v1` 的接收例外由 Python 决定：有独立确认锚点且未在本次轨迹/动作中采用的后续候选关联，可保留 unknown 或缺省该范围行；这不授权遗漏车辆、锚点、主要动作或其他范围问题。模型不输出 program receipt，不把身份未知写成无关；完整规则见编排 protocol「未采用的后续身份关联」。
