# World 字段与验收

Actor 身份与车道字段使用 [跨阶段 Actor 合同](../../references/actor-contract.md)，与 Batch/Behavior 共用 Schema 字段类型；初步位置假设仍属于 World，不把动态轨迹并入本对象。

结构的权威文件是 [world_context.schema.json](../../schemas/world_context.schema.json)。初始视觉、几何裁决、局部闭合分别使用本目录的 prompt.md、readjudication-prompt.md、closure-prompt.md，模板占位符由编排器填入，不让模型自行猜测路径。

右侧连续铺装人行道使用既有 `world.road.roadside_areas`：明确 `type=sidewalk`、`road_id`、`side=right`、与最右行驶边界一致的内缘 `boundary_left`、独立外缘 `boundary_right`、至少两帧 `evidence_frame_ids`。若车道布局裁决启用，`right_roadside.type` 也须为 `sidewalk`；`curb_only` 的文字理由不能替代记录。closure 从一次裁决确定性写入这些字段和逐项证据，旧冻结裁决仍按原请求合同验收。执行近似与观察记录分层，见 [MELA 表达契约](../../references/mela-expression-contract.md#右侧人行道--paved-shoulder-执行近似)。

静态事实属于 world；actor_hypotheses 只提供可跨帧识别的初步身份；动态停走问题使用 owner=behavior 与 subject_field=actor_state|actor_motion。impact_domains 仍使用 Schema 的核心 executor_primitive 枚举。核心未知保留问题，并安排有针对性的 Batch。

路口 road 的身份单位是某一侧的物理路臂/路段，不是贯穿路口的整条同名街道。单向街道的接近段与远侧驶出段使用不同 road_id 和全局 lane_id；同一已定义路段跨帧保持 ID。Actor 穿过路口保持 actor_id，但其道路/车道引用可变化。bidirectional 仅表示同一侧路臂内存在分向交通，不能表示一条单向街道在路口前后连续。未知远侧结构保留未知，不为满足此划分而补造观察路段。

路口 `incoming_roads/outgoing_roads` 共用 Schema 的 `junction_membership`：成员为含 road_id 的对象，lane_ids 可省略或为 null（未知），空数组表示已声明无成员。道路和车道必须引用已有定义，方向成员不能靠道路总车道数补齐。`world-junction-membership.v1` 仅将旧字符串引用转成 `{"road_id":原字符串}`，保留次序、重复项及原始候选，逐项审计；不补 lane_ids、不纠正 ID、不掩盖悬空引用或方向冲突。格式错误报告 representation_error，实际不存在的引用才报告 fact_conflict。

坐标规则统一读取 [road-coordinate-convention.json](../../references/road-coordinate-convention.json)，内嵌到初始提示词、geometry_readjudication 提示词和 closure 任务包 coordinate_convention。固定视频起始自车坐标系，x 向前、y 向左；heading 是参考线方向，不能误用物理路臂方位。已声明的不同路臂方位重叠返回 world.topology.arm_overlap / fact_conflict，不作为可忽略 warning；缺失角色或朝向继续保留 unknown。参考线角度与进出角色由 World 裁决，Converter 不通过删除道路或重挂 Actor 来消除冲突。

World → Behavior 交接要求有可引用的道路/车道定义和稳定 ID，不要求观察阶段达到完整执行拓扑。冻结 `world-admission.v1` 的新合同允许不完整但自洽的 World 作为观察检查点，先由 Batch 补证、reconciliation 处理。进入 Behavior 前，`observation_handoff_issues` 要求已有 `road_001`、其非空 `lane_ids` 和 `lanes` 实体，所有已声明引用仍须通过准入校验。若此时仍仅有数量、缺少必要定义，返回 `world_evidence_insufficient`（rule_id=`world.handoff.lane_definitions`，automatic_retry=false）并停止；不能让 Behavior 猜 ID，也不因该缺口自动启动新 closure 任务。未知宽度、远侧/对向车道总数和未观察到的连接继续保留未知，不为满足交接补造事实。

未启用上述准入修订的旧冻结路径仍使用 `normalize_world → validate_world → check_world_shape` 与 `readiness_issues(execution=False)`，按其原 closure、证据和预算规则处理交接缺口。已有活动 closure 必须先按冻结请求对账，不能因当前默认规则改变而遗弃任务或改写请求。获准局部补丁可补齐缺失/空集合，已有非空 roads/lanes 集合不能整体替换；普通恢复不迁移旧 Case。

新合同的初始 World 提交、Batch 冻结/读取快照和 reconciliation 共用 `admission.validate`，校验结构、身份及已声明拓扑的事实一致性；在线调用 `ensure_world_ready(execution=False)` 在准入后检查上述最小观察交接条件，而非统一触发 `readiness_issues` 的 closure 路径。未决 World 事实留在原记录并进入 analysis 的 world_uncertainties，执行缺口由后续 Builder/策略/Converter 门禁判定，不因生成观察 analysis 而自动解决。默认严格检查仍包含未决核心问题。Schema 尚未覆盖全部 Python 语义门禁，因此“结构有效”“可观察交接”和“执行就绪”不能混为一谈。

冻结启用 `world-admission.v1` 的 Case 在正式 World 提交、reconciliation 和候选校验时使用 admission 的同一阻塞判定：representation/schema error 与 fact_conflict 阻塞，degraded_warning 保留在诊断中但不阻塞。重复/非法 lane ID、悬空引用、road/lane 所属冲突及 junction membership 冲突不能降格为提醒。按 `roads` 数组顺序展开 `lane_ids` 必须恰好等于 `lane_001` 至 lane 实体数量对应的连续序列；不通过排序或静默重编号修复。全局错序使用 `world.lane.global_numbering`，在 World 提交前拒绝。actor 的 null lane 假设、未知度量和观察阶段尚未声明的路口关系不等于非法引用，仍保留未知；规则升级不改写已完成 Case。

`heading_rad` 为有限数值或 null，不接受方向字符串；`road_role` 的已知标量仅允许 incoming、outgoing、bidirectional，也允许非空、不重复的同臂 incoming/outgoing 数组。未知用 null 或省略，不使用字符串 unknown。非法字符串在 Schema 边界按 representation_error 拒绝，不猜测或改写角色。观察验收将单元素数组按对应方向、双元素数组按 bidirectional 解释，不改写原始字段或证据。双向同臂的进出 lane_ids 不得重叠，且已声明 travel_direction 必须分别为 backward/forward；单行道路贯穿路口不等于双向同臂，不能以该数组角色绕过这些检查。入口已知但出口未知时保留入口，出口关系省略/null；仅成员车道未知则保留 road_id 并省略/null lane_ids，不复制入口车道。已知冲突不能降为未知以放行。Batch 冻结前复用结构检查。World 可以包含接近道路、路口、出口道路；自车随时间的位置属于 Behavior，不因画面变化复制 World 或拆 Case。

question 的普通帧证据位于 evidence[]，不是根对象 evidence_frame_ids；identity_binding 和 localization 内的 evidence_frame_ids 仍合法，并须满足各自完整必填结构。无稳定 Actor ID 时保留 question.evidence，不虚构身份绑定。

`one_way`描述观察道路是否存在对向交通；中央隔离后的对向交通仍使其为false。`opposite_direction_lanes=null`只表达“确认存在对向道路但数量不可见”，必须绑定精确路径的unresolved问题/歧义和unknown field_evidence；true只能配0或省略，false只能配正整数或上述受约束null。Builder可以将该未知排除在自车侧执行依赖之外，但不能改写World事实。

只读 validate_candidate.py 返回 candidate_validation、handoff_validation 和 handoff_issues；--handoff 要求无未决问题。检查不写原 candidate、不更改 Case，不构成发布授权。

物理隔离证据：对于存在对向交通的道路，在 road_geometry.opposing_separation 写 physically_separated=true/false/null 与 side=left/right/unknown，并在 field_evidence 用精确路径 world.road.roads[索引].road_geometry.opposing_separation 绑定 observed 帧、时间和理由。护栏/中央隔离描述不能替代该结构；证据不足保留 unknown。不要为自车侧预览补造对向车道数量。

问题和 ambiguity 的 `world_paths` 只列真正未决的事实；可选 `related_world_paths` 使用相同 World 路径格式，记录已知关联事实，不作为未决字段。对向车道数量未知时，专用未决项的 `world_paths` 必须逐字且仅为 `["world.road.opposite_direction_lanes"]`；不得把 `opposing_separation` 或其他路径合并进该数组。已观察到的中央分隔放入 `related_world_paths=["world.road.roads[0].road_geometry.opposing_separation"]` 并保留 field_evidence。关联标签不构成执行豁免，程序仍独立核验分隔事实、证据与执行范围；分隔本身未知或冲突时必须另列只绑定该分隔路径的未决问题。
