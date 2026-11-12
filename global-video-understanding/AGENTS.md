# AGENTS.md — Global Video Understanding Workspace

尚未冻结转换请求、已启用 bounded_execution/continuation 的 Case，在首次请求 policy 中冻结 `source_witness_revision=motion-witnesses.v1`。已有转换请求无此标记时保持原语义。程序只接管支持的逐句语法、已复验身份/单帧保留凭证和精确匹配的 Builder 缺值记录；陌生句子仍保留未覆盖状态，不能整体标成 no_motion。具体逐帧车身部位与结构车道优先于受限摘要解释，原文、引用和取舍随请求及报告留档；不覆盖正式动作或同等具体证据冲突。稳定车距及车身前移使用有界分段速度近似，规划和最终解码检查同一约束，实际补值逐项复验。所有车辆、主要动作及已知数值保持；全覆盖时复用程序检查点和重启流程，不调用模型。未识别语义、核心证据冲突或有限规划失败仍明确停止。

`bounded-execution.v1` 的单帧保留扩展复用完整来源凭证：同一候选的纯身份限制可绑定多条观察，但每个成员的全帧范围与独立轨迹检查必须全部通过，之后才整体解除。已启用程序保留合同的同向确认车辆不因 transient 背景标签排除；对向隔离过滤和动作依赖检查保持。原始回答不改，最终来源与重启复验同一规则。

已确认单帧车辆具备完整 frame-bound edge_fragment/distant_transient 证明、独立原锚点且只有纯未知运动/车道或获准数值缺失时，可记录单帧执行包络。已知车道保持；未知车道只在联合规划与 Converter 私有执行副本复用同道路顺序默认选择，原 analysis 仍为 null。旁侧局部的初始中心 ±2m、远距目标全窗至少150m均为明确仿真假设，不是视觉测量；已声明落后趋势保持。未观察前段/尾段使用有界匀速，不建立早期身份关联。Python接管精确绑定的纯未知运动来源；新增身份/动作冲突仍拒绝。中文明确自车前方、接近和并行见证可形成原时刻关系；并行车身局部只约束重叠包络，不证明整车完全超越。未识别的文字继续留待正常关系审核，不得一概改为无运动。所有车辆仍进入原数值、车道走廊和最终连续碰撞检查，记录及报告须披露上述假设。

新 Case 冻结 `bounded_execution=bounded-execution.v1`，旧 Case 仅首次 Behavior 前显式 `adopt_execution_assumptions.py --bounded-execution` 采用。复用来源凭证：符合既有保留尾段条件的身份描述由 Python 在不可变身份校验前继承，原回答与修正记录保留；新身份、观察、车道、动作或混合限制不因该规则放行。纯尾段的已绑定 actor/domain 说明不再依赖正向关键词，明显新增动作反证仍拒绝。已验收 Registry 的明确前方、并行和接近文字可生成带帧时刻的关系，不能推广到未经证明的全程；其余文字仍需正常关系回答。仅触及视频边界的已有单次相邻变道，允许在观察包络内留出 `min(0.5s, 包络/10)` 的直行段；原帧、事件和观察时间不改，执行时间另留假设回执。该策略不从两帧自动证明单次变道或生成完整 Behavior。关系齐全后优先有限联合匀速规划（最多8辆含ego、20000次候选），保留已验收计划/已知数值及全部保留车辆；搜索未找到方案不等于数学不可行，不调用模型修复或重置预算。最终来源重放及连续 target 检查仍必须通过。

新 Case 冻结 `retained_unobserved_motion=retained-unobserved-motion.v1`。在已验收身份、0 秒单帧原锚点、同向已知车道和首帧与 ego 并行证据完整，且只有未观察后续运动的纯疑点时，Python 记录“保留＋未观察运动近似”。单帧相关性不要求模型重复证明；所有车辆/主要动作保持，未知事实不改成已观察。转换关系和运动阶段由程序处理凭证列出的精确来源及车辆，仅在执行副本延续原车道、尝试有界匀速数值，保留其他已验收计划及已知值并执行原最终运动校验。新身份/动作/交互/混合依赖仍阻塞；无可行近似不消耗模型修复预算。旧 Case 不隐式启用，可在首次 Behavior 前通过 `adopt_execution_assumptions.py --case-dir ... --retain-unobserved-motion` 追加绑定来源的独立采用记录；不覆盖策略、事实、预算或已完成结果。此规则是有记录的仿真假设，不是无交互证明。

新标准 v3 使用 `bounded-recovery.v1` 分类有限恢复，替代下文历史 Case 的共享两次规则：
传输每冻结请求2次、内容每阶段族2次、全 Case 额外任务8次；更窄专用合同仍有效。
旧 Case 的有限恢复升级入口已移除；新 Case 使用冻结策略，历史采用凭证仅供读取与复验，不改写旧策略或请求。
Behavior 可从完整有效证明整理索引；补丁只能改任务包指定的未知车道/证据，保留轨迹与通过行。
同证据无进展、越权、输入漂移或预算耗尽停止；完整约束以编排 protocol「有限恢复」为准。

新 Case 使用 `world-census-adjudication.v6`、`behavior-census-scope.v4`、`census-execution.v2`：观察级相关性先初判、同次 Behavior 完成范围与待查同帧区分，正式交付前必须验收；过滤观察不删除候选 Actor。v4 可在原观察帧与已登记 Actor 的视觉锚点、真实轨迹和同帧物理区分均具备时，仅在执行凭证保留该观察，身份仍未决；不新增车辆或轨迹，后续道路、运动和 Target 门禁不放宽。登记信息能力继承 v5：普通已有车辆的登记描述、颜色、车型由 Python 继承，模型只补充逐帧观察或提交绑定旧值及首见/后续证据的显式 corrections。全部身份关联、共现区分、合并/拆分证明仍执行；首次登记与历轮凭证保留，Behavior 使用最新已验收字段和全部历史目击。正向反证可引用同一 sightings，展开后执行原门禁。旧冻结请求不隐式升级，详见编排 protocol 的「登记与后续信息更新」。

新 Case 冻结 `behavior_handoff=behavior-handoff.v1`：Behavior 的 pending census 可用 `identity_assessment` 结构化否定候选及提出替代身份；只有明确 compatible、无其他身份/事件依赖时才允许单候选执行假设。反证沿原 census 恢复入口交还 World，替代身份纳入同一冻结证据计划，保留原预算。单帧 participant 必须说明执行相关性；高速短暂边缘/远处车辆只有完整逐帧 `execution_scope_evidence`、无身份或交互依赖才可在执行副本排除。`census_issue_ids` 绑定限制的准确观察子集；已有隔离对向证明与范围裁定在接纳、路由、构建和离线重放共用。原始 Actor/轨迹和旧 Case 策略不改；无证明仍停止。

本工作区承载 Case 编排程序与 World/Behavior 视觉 Worker。**只有 Coordinator 启动的 Python 编排进程拥有 Case 生命周期；模型 session 只拥有当前请求的 candidate。** 最终用户答复由 video-coordinator 发出。

已失败 v6 Behavior 的 `--review-behavior-scope` 只授权一次观察范围补充任务，独立记账并保留原预算和上游。模型仅补选中 decisions；候选身份不明可与执行无关同时成立。同向无隔离的排除仍需全帧 unrelated_observation 证明，单一候选提前目击须额外证明后续轨迹独立；不修改事实或已有 Actor。原回答、未选项和失败历史保留，剩余身份争议仍停止。流程与中断恢复见编排 protocol。

已完成范围补充仍停在 Behavior 身份门禁时，显式 `--accept-early-association-independence` 可本地验收 `early-association-independence.v1`。仅接受高速无路口、首见不晚于1.5秒的多车联合早期关联：全部剩余观察精确绑定同一纯身份限制，后续独立轨迹及已验收共现区分完整，无早期自车走廊/动作/初始状态依赖，且冻结策略允许位置、速度及未观察时段的执行近似。保留原始 needs_identity、全部 Actor、成功回答及任务历史，不派模型、不增加额度；执行副本仅解除精确限制。下游将相关无时间描述标为 audit_only，禁止提前引用后续观察；0秒补点仍须完整运动与碰撞验收。原凭证和请求不覆盖，旧 Case 不自动采用。

上述补充任务明确因 token-plan 额度耗尽失败且无输出时，`--reauthorize-behavior-scope-after-quota FAILED_TASK_ID` 可显式授权同一范围的一次额外任务；绑定原失败历史与输入，独立记账，重复命令不增额。额度须先由用户恢复；超时或受理未明不能借此重试，第二次失败不再追加。原请求、任务历史和预算保留。

用户另行明确授权第二轮额度恢复时，才可加 `--scope-quota-retry-generation 2` 并绑定第一轮额度恢复的失败 task ID。两轮各限一个任务；第二轮保留完整父授权链，旧命令不隐式增加名额，没有第 3 轮授权入口。原证据、历史与普通预算仍不改。

已完成范围补充的只读复验允许下游转换任务正在运行，仍核对原授权链、输入及结果哈希。新授权、预留和派发继续禁止其他未决任务；读取旧结果不能产生新任务或改写下游进度。

新建 Case 冻结 `early_association_independence=early-association-independence.v2`。正常首次 Behavior 验收对同次回答自动计算上述有界早期关联独立性，不依赖范围补充任务、特殊恢复参数或 sidecar；证明存入原 `behavior_scope_review.accepted.json` 的执行裁定，后续投影、转换及重启均重新绑定校验。条件不足仍保留身份阻塞，未知不自动无关；旧无标记 Case 与 v1 显式恢复凭证保持原语义，已完成结果不迁移。

新 Case 冻结 `unused_association=unused-association.v1`。普通单候选车辆已有至少两个确认目击，未决观察晚于全部确认目击、无候选反证，且实际 Behavior 保留原锚点、没有采用该观察或依赖它的动作时，Python 可记录 `unadopted_association`，不再要求模型补“无关证明”。接收、执行投影、analysis、转换和最终校验复验同一凭证；仅解除精确绑定的纯身份关联限制，不删除车辆、改变轨迹/动作或补车道。身份仍 unresolved，事实层保留原观察，报告标明跟踪/交互细节可能缺失。这是执行范围选择，不是“该物体不会影响变道”的证明。真实身份反证、混合身份/动作限制、已采用的后续观察、车道或动作缺失仍由原门禁处理。旧 Case 不隐式升级。

## 模型会话边界

`unused-association.v1` 与 `execution-assumptions.v1` 的车辆保留检查允许复用已有对向背景过滤，但必须从原始来源重新计算允许排除的子集；群体仍需已验收的范围记录。涉及事件、primitive、其他车辆、保真、挑战或混合身份/道路/动作限制，以及假设仍需要的车辆，不允许排除。原始观察完整保留，投影后其余车辆、轨迹、事件和 primitive 不得改写；最终校验重放相同规则，禁止 Converter 继续裁剪保留车辆。不能直接信任模型或审计文件给出的排除列表。

已采用 `execution-assumptions.v1` 时，可由 Python 在同一凭证中记录 `inherited_opposing`：复验上游 census 的完整窗口护栏隔离证据、图片变体、World 分隔事实与 Registry 各原始锚点。群体待查观察必须自身明确位于同侧护栏外、只有该群体候选、同帧已有有效隔离证据，并覆盖该群体全部待查观察；只继承区域范围，不合并物体、不解决个体身份。实际 Behavior 原锚点、描述、未知车道须兼容且没有动作/交互/混合限制依赖；纯 participant 标签或缺少重复方向声明不否定已有证据，明确反证仍阻塞。接收、投影、证书与最终来源复验重新计算范围；原始事实保留，对向背景运动不模拟，报告披露损失。其他单帧参与车辆仍须执行相关性证据。

新 Case 冻结 `execution_assumptions=execution-assumptions.v1`：仅对无已确认身份、远处微小且未被实际 Behavior 采用的观察，记录 `observation_only_by_assumption`；所有旧候选必须为空或已被 World 明确反证，不新增车辆或关联既有车辆。普通车辆有至少两个原始锚点，精确三车道同向路网与原始证据给出中间或中间/右侧范围时，可在执行副本补未知车道；优先已知锚点，否则取证据范围内最右车道。动作、其他车辆引用、保真关系、已知横向位置或混合限制依赖该车时不猜。原始事实不改；除独立验证的既有对向过滤外，原 Actor/事件/primitive 不得删改，仅记录的空车道可补。接收、投影、最终来源复验使用同一凭证；报告披露未建模目标可能缺失、车道和交互可能失真。它不是无关证明或完整视频还原保证。旧 Case 仅在首次 Behavior 前通过 `adopt_execution_assumptions.py --case-dir ...` 显式采用，不追加预算或调用模型。详见编排 protocol。

- world、world_closure、geometry_readjudication 请求使用 skills/world-understanding/SKILL.md。新 Case 的 visual-answers.v2 在单次 world 回答内完成视觉取证和表示编译；world_topology 与 parts 模式仅用于已冻结 visual-answers.v1 Case 的兼容恢复。
- behavior 及定向 Behavior 修复请求使用 skills/behavior-understanding/SKILL.md。
- actor-identity-link.v1 是已有车辆早期关联的受限视觉裁决；模型只写 candidate。World/Registry 的专用身份提交、旧依赖退役和 Behavior 重建由编排器处理；普通身份锁与任务预算不变。
- actor-identity-split.v1 是一个旧身份拆成两辆的受限视觉复核，需共现和跨帧证据；复用身份提交与重建，不修改普通身份锁、不重置预算。
- intersection_review 使用 Behavior Skill 的独立只读关系复核合同；只能补充复核证书，不拥有 World/Behavior 或执行参数的写入权。
- 每次只处理请求指定阶段；不得递归启动流水线、创建 subagent、调用交付阶段或修改 phase、manifest、正式 World/Behavior、analysis、发布目录。
- 只读取当前请求允许的输入，只写指定 candidate。closure/repair 的小任务包限制优先，不因为 Skill 引用而额外读完整 Schema 或历史。
- 静态世界归 World；动态运动及事件归 Behavior；Batch 由外部 video_extractor 每个新 session 处理一个批次。

## 编排进程边界

新 Case 的 observation-execution.v1 事实修订冻结在执行策略中。观察合法性与执行完整性分开；获准的未知路口连接只在 Python 投影补充。身份未知不等于冲突，排除必须有结构化隔离证据和无交互依赖。旧请求、旧预算和旧验收批次不能移到新代码后继续计作同批验收。

已验收观察结果可交付时，连接缺失允许 Converter 使用固定规则在私有执行副本补齐并生成 target，无需为该缺值强制发起 intersection_review。路由保留执行缺口，转换回执记录补齐；原始观察不变。显式 intersection_preview 仍遵守对应的复核合同。

- video-case-orchestrator 是唯一生命周期所有者；batch-lifecycle、world-reconciliation、case-delivery 只在该进程内执行。
- 新模型尝试默认使用新 session；显式 conversion_semantic_gap 受控恢复可续接最近失败 session；显式 `--resume-world-closure-session` 可在首次 Behavior 前续接最近完成的 World closure，处理 census 阻塞。两者仍创建新 task、保留冻结历史和原预算，活动任务先对账。同一目标 Agent 的完整任务生命周期跨 Case 串行。
- 新 Case 的 `world_census_adjudication=world-census-adjudication.v4` 有布局 closure 时在同一次调用处理 census；无布局需求但 census 未决且尚无 accepted 时，在首次 Behavior 前条件性使用现有 world_closure 入口，不增加固定调用。打包前冻结按问题组织的证据计划，最多 12 张，候选否定与替代身份关联分别举证；comparison 车辆只提供比较、不修订 Registry。原布局内层、Batch、普通身份锁、journal 和 Behavior 凭证消费流程复用。v1 已有请求按原合同执行；仅旧选帧超限且外层从未请求/尝试/提交时可在正式重试创建 v2 私有请求并记录升级来源，不改旧 policy 或预算。其他历史 Case 仍用显式续接入口，未决仍停止交付。
- 部分 census 已验收后，显式原 session 续接仅处理累计剩余 issue，绑定不可变父凭证并沿用父合同；历史身份、目击与排除结论持续生效。每轮请求/授权独立，提交 journal 只推进当前父凭证，Behavior 消费整条凭证链；保留原任务预算与远端对账，禁止重答旧结论或自动追加新轮。
- v3 联合请求分别冻结 layout/census 图片引用范围；重复登记仅经完整锚点、过渡和共现排除证明后合并，Behavior 只消费规范身份。首次联合 closure 计正常任务，后续修复共享原两次恢复额度。历史 v1/v2 的已归档特定合同失败可通过显式原 session 入口获得一次独立记账的合同修复任务；不退还或重置原预算，不允许无限续接。
- 候选必须通过所属门禁再原子提交。默认值、simulation、quality、转换、报告和发布均为确定性操作。
- 新 Case 在 analysis 验收后冻结 `conversion_continuation=conversion-continuation.v1`：两个正常文字会话分别完成 `conversion_relations` 和 `conversion_semantic_gap`。会话内多次写入指定 generation 目录的累积 save 文件，Python 验收并保存不可变检查点；终态超时/候选失败最多共用一个额外新会话，且仍受 Case 两次恢复额度限制。覆盖全部同向车辆和证据，最终仍输出 `conversion-semantic-motion.v1`。不读取图像，身份未决交回上游，已知事实不可覆盖。最终编译与运动验收保持确定性，失败不能等速兜底。未冻结 continuation 的旧 Case 保持单会话和旧恢复入口。
- Batch 无最终事实所有权；World 内容快照和前序身份副本固定观察输入，Behavior 使用已验收全局/细节证据的统一索引。analysis 是向 Converter 的主要业务交接。禁止在事实层用模板补造路网或设施位置；新 Case 的 intersection_approximation.v1 仅在独立执行投影应用获准默认值，保留原始事实和逐字段来源。
- 已完成且哈希有效的 Case 不随代码升级隐式重跑；当前无真实 MELA runner，不将 source_contract_validated 描述成渲染通过。

本 Agent 对话运行时不开放 shell。完整视频请求由 Coordinator 启动稳定入口；模型 session 不要求临时开放 shell。Skill 路由、实现与兼容边界见 skills/video-case-orchestrator/references/architecture.md；预算和恢复唯一维护于其 protocol.md。

原先冻结 behavior-census-scope.v1 的 Case：有效 v4 census 剩余观察可进入正常 Behavior candidate，由同次私有 census_scope_review 逐项判断范围。完整隔离对向证据可按 issue 排除，个体候选仍需身份裁定，远小目标不自动排除；正式交付仍要求全部身份或有效范围凭证。群体 Actor 的正式观察保留，只在所有相关观察证明排除且其 Behavior 对向 background 证据有效后投影裁剪。更旧的 Case 仅通过显式 --enable-behavior-census-scope 首次 Behavior 前采用 v1，不修改原策略/事实/预算。完整 needs_identity 可复用原受限 census 恢复，unknown/漏答不授权新裁定。

新建 Case 改冻结 behavior-census-scope.v2 与 ego_vehicle_scene.v4。Behavior 仍只回答 excluded/needs_identity/unknown；Python 在已验收复核后签发执行裁定。完整物理隔离证据可排除单车候选观察；单一候选且无同帧硬冲突可作既有 Actor 的仿真假设，不改首见帧或补造轨迹。远处极小、车道未知且无动作/道路依赖的观察仅保留事实；多候选车辆仅在同次回答明确指出自车右邻走廊、唯一右邻同向车道存在且无依赖时生成执行代表车；源观察和候选 Actor 不改，投影及报告标记 done_degraded。前提不足继续阻塞。v1/v3 冻结 Case 保持旧规则，不隐式迁移。

上述新策略由同一次 Behavior 范围回答后的 Python 执行裁定完成，不暂停等待人工审核。远处极小、车道未知且无事件/道路依赖的 `unknown` 可仅保留观察；多候选 `needs_identity` 仅在现有文字明确指出自车右邻走廊、右邻同向车道唯一且无依赖时生成一辆执行代表车。其余继续阻塞，不猜测身份、车道或动作。

新 Case 的 census v4 可为无稳定身份的观察提交 observation_only，但只接受 stationary_isolated_offroad.v1：全部指定帧可见、静止、车行道外、屏障隔离且无入口，须有全 Case 采样覆盖及对所有已登记非 ego 车辆的锚点共现反证。身份疑问保留；已登记车辆不按此规则裁剪。Behavior 在同一次 candidate 的私有 census_scope_review 中逐 issue 复查，漏答或 unknown 不代表确认。只有绑定有效范围凭证的确认结果可交付。明确冲突允许受限的 Behavior 后 census 续接：追加撤销记录、保存完整 Behavior、退役旧派生产物与输入绑定，复用原 session、journal、原预算及远端对账；仅相关对象及明确关联事件可修订，历史身份证明保持锁定。v1/v2/v3 冻结 Case 不迁移。

新 Case 另冻结 execution_gap_recovery=execution-gap-recovery.v1。范围门禁与转换共享尺度分类；只有冻结转换权限允许的纯 speed/position 缺失可不作为排除依赖，事件、动作、身份及混合路径限制保持。混合 pending census 只将明确 needs_identity 的子集送原 World session；其他 unknown 不获新裁定或排除授权，父凭证继续保留。恢复前须有两次原额度覆盖裁定及 Behavior 重建；同一 Case 不自动重复该恢复轮。首次 Behavior 明确核查车道/身份清单，必要重建同次复核绑定的全未知车道 Actor，保持其他字段。独立全未知车道可进入现有一次车道复核，仍不清楚则停止，不猜车道。lane_closures 私有响应仅闭合有逐帧证据的 actor_lane 分量，其他限制保留；World 只在执行副本消费对应答案。旧请求及无此标记的 Case 保持原停止规则，不隐式采用或补充预算。

`ego_vehicle_scene.v4` 的单帧、车道未知、无事件/执行依赖的 background 车辆，若 analysis 明确记录了仅该车的 bounded actor_motion 不确定性，可保留原观察而不生成其运动方案或目标车辆。转换关系阶段允许该条运动未知继续存在；身份未知、参与执行的车辆以及跨帧车辆仍受原门禁约束。私有转换副本及回执记录排除，原 analysis 不改。

新 Case 冻结 census_evidence_revision=census-evidence.v3。所有 World/Batch 结构化观察引用（含未知和反证）、疑点原帧、Registry 锚点及布局证据都是必需帧；全局图片目录中的普通抽样不自动成为车辆身份事实。先保留全部必需帧，再按不超过1.5秒的间隔规划最少补桥；同数量优先已有抽样，选择与12帧上限无关，超限仍停止。后续对照继续优先已确认目击，否则复用已需帧。每条正向关联仍须覆盖其区间内全部必需帧和补桥帧，否定、去重及范围校验不放宽。v1/v2 已冻结请求仍按原规则重放；仅初次联合请求不存在且无闭合尝试、会话、预留、候选或受理未明任务时，原规划超限可于正式重试采用下一可行版本，绑定不可变准备快照并重验原超限。内层布局请求、Batch、策略文件和预算不改；续接继承父凭证版本。规则只规划查看机会，不证明车辆可见或身份相同。

新 Case 的 batch-evidence-capacity.v1 普通批默认4帧，仅必需证据覆盖不足可扩到6帧；布局/单帧和总4批不变。JPEG打包不删必需帧，保留原字节限制。旧 Case 的容量升级入口已移除；历史采用回执仅供读取与复验，不改写旧冻结批次、结果或失败记录。完整容量与重放规则见编排 protocol。
