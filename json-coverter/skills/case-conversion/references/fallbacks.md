# Analysis → Target 执行补值

## 新旧合同边界（2026-09-26）

本页历史常量表描述未绑定策略的旧合同。新 v3 Case 使用 `conversion-policy.v1`：通用常量定义位于 `_lib/contracts/conversion_policy.py`，Converter 仅在私有实例读取当前冻结 defaults，不修改全局常量。实际道路长度、车道/停车宽度、天气来自 Case fallback_policy；普通高速速度、纵向间距配置使用 straight_preview 对应数值。标准新请求省略授权清单时自动允许已登记补全；显式限制清单仍受门禁约束。可选 `parameters.conversion_defaults` 逐项覆盖已知默认键，未知键、非法枚举、布尔数值或非有限数值均拒绝。

新建 v3 Case 的未知停车宽度默认执行值为 3.0 m，以容纳当前停车布局支持的卡车净空；这是私有执行近似，不是视频观测值。明确配置更窄宽度或已知停车区域宽度不足时，仍按 `conversion.parking_clearance` 停止。下方 2.5 m 表格只描述未绑定新策略的历史转换合同。

每次补值必须满足 allowed_degradations。既有道路长度/车道宽度/停车宽度授权继续使用；新增类别为 vehicle_speed/position/lane/class/color_policy_default、trajectory_continuation_policy_default、road_topology_policy_default、facility_position_policy_default、signal_state_policy_default、parking_layout_policy_default、environment_daytime/season/topology_policy_default、duration_policy_default。类别采用完整 ID（例如 vehicle_speed_policy_default），不支持通配授权。仅已确定数据的执行场景组装不需额外授权。

新回执每行同时记录 policy_id/policy_ids，未识别的字段路径拒绝；scene-local 硬编码回退也不能绕过冻结策略。已知变化灯色拒绝常量替换，已知几何冲突不允许靠授权覆盖。下文对历史灯色/固定补齐的说明不扩展到新 Case。旧 Case 不隐式迁移；未交付且仅因旧清单缺授权停止的 Case，可经编排器 --enable-conversion-defaults 写独立采用记录后，从原 analysis 恢复转换，不要求用户手填默认数值或逐项确认。

道路方向遵循共享 [坐标合同](../../../../global-video-understanding/skills/references/road-coordinate-convention.json)。World 与 Converter 共用 `_lib/world/topology.py` 的路臂方位计算和冲突校验；已声明同臂冲突返回 `world.topology.arm_overlap`，包含两条道路的字段路径，不属于缺值补齐。Converter 不因方位相同而合并或删除道路。角度规整前后均检查冲突，来源 analysis、Actor、设施和连接引用不被删改来换取通过。

唯一规则：analysis 中存在、有效且受目标枚举支持的值优先；缺失、`null`、`unknown` 或目标不支持的值在 Converter 私有执行副本中使用硬编码值。不得修改来源 analysis。每次实际替换都写入 `conversion_result.json.fallbacks`；不进入执行副本的对象写入 `excluded_actor_ids`。

连接缺失时也按固定规则补齐并生成 target，不以缺少关系复核证书为由停止：取首条入口道路，优先选择朝向相对的出口道路，找不到时取首条出口道路，按双方 lane_ids 顺序逐对建立 straight 连接。规则仅在连接为空时应用；已有连接保留。补齐记录 reason=missing_straight_execution_connection，原始 analysis 中的未知连接仍保持不变。显式启用的其他复核/投影流程保持各自合同。

旧合同常量经 `scripts/lib/execution_fallbacks.py::DEFAULTS` 导入：

| 执行字段 | 硬编码值 |
|---|---:|
| 普通车道宽度 | 3.5 m |
| 停车区域/路肩宽度 | 2.5 m |
| 路口道路长度 | 70 m |
| 高速道路长度 | 300 m |
| 路口速度 | 30 km/h |
| 高速速度 | 80 km/h |
| 首个非 Ego 车辆纵向位置 | Ego 前方 25 m，之后每车增加 15 m |
| 缺省时长 | 5 s |
| Ego / 普通车辆 | sedan / silver |
| 环境 | clear / afternoon / summer / flat |
| 缺失/unknown/变化信号状态 | green（覆盖整个执行时长；变化灯色允许有损替换） |
| 信号灯位置 | x=30 m, y=0 m, z=5 m |
| 斑马线位置 | x=25 m, y=0 m |

普通 observation-execution.v1 交接中，Builder 保留尺度的 estimated/value/confidence/reason/range 和 unknown，不预填路长、车道宽度或停车宽度。Converter 优先使用有效正数；已知负数、零、布尔或非有限尺寸属于错误，不当作缺失兜底。速度时序可用时保留各点，路口时间线按相邻速度差/时间差构造加减速；全部速度未知时使用一致的匀速执行假设，不声称为观察到的加速度。已有显式复核预览继续遵守其冻结策略。

普通 highway 中，非 Ego 动态单体车辆若轨迹全部已知车道只有一个、且没有该 Actor 的 lane_change event/primitive，Converter 可在私有执行副本沿用这一车道：较早和两次已知观测之间的 `null` 车道由同一 Actor 后续确认的车道补齐，并记录 `same_actor_known_lane_execution_assumption`；末次已知观测后的 `null` 车道和媒体结束前的未观察区间使用 `unobserved_horizon_straight_continuation`，缺失速度沿用最近执行速度。为执行起点或终点补出的轨迹点不带 `frame_id`。该规则不解析 detail/limitation 自由文本，不适用于路口、Ego、已有多车道证据或显式换道；原始 analysis 保持不变，实际补齐逐项写入 fallback 回执。若从未确认过该 Actor 的车道，不能以“未观察到变道”把默认车道称为原车道。

高速 Actor 的首个轨迹样本若车道未知，且没有明确初始车道/道路序号约束，执行副本优先取**该 Actor 自己最早的已知车道**作起点，不再直接取道路列表的首车道。回执记录 `same_actor_earliest_known_lane_execution_assumption`、原始 null 和所依据的帧/时间；这只是执行位置假设，不提升为首帧观察。明确初始约束仍优先。投影后逐 Actor 比较源轨迹与执行轨迹的已知车道跳变；补值新增或抹掉跳变时以 `conversion.projection_lane_transition` 停止，不能让假变道通过或伪装成场景能力限制。没有任何同车已知车道时仍受原 `vehicle_lane_policy_default` 授权与回执约束。

Actor 大类与旧类型识别共同消费 `actor_fields.schema.json`，车型优先级为 vehicle_class → 旧 subtype → 旧 actor_type 中的车型提示。特殊车型原名保留在 analysis；目标不支持时回退 sedan，回执包含原值、执行值和原因。旧 car/compact_car 等为显式格式映射，不改变事实身份。

其他固定映射：

- `roadside_parking|parking` → `curbside_parking` → MELA `paved` shoulder；
- `lane_knowledge.exact/minimum` 优先决定执行数量，已识别车道按 anchors 保留 ID 和右起序号，缺槽位补执行车道。仅已声明四臂、Ego 入口和正对面出口可唯一确定且出口 lane_ids 为空、数量未知时，按入口数量补齐同序出口车道，记录 `ego_corridor_same_order_lane_completion`；不覆盖 exact、不由数组下标猜道路对应。其他空车道采用一条 `driving` lane；
- 路口没有可用道路 → 标准四臂路口；只有一至两条道路 → 补齐标准四臂；明确三臂路口保持三臂；
- `car|compact_car|coupe` → `sedan`，`mpv|minivan` → `van`，`box_truck` → `truck`；
- 普通转换保留全部已识别单体车辆，不按 background/participant、静止状态或事件/场景参与名单筛选；在私有 simulation_scenarios 中补入全部这些车辆的执行初态，原始参与名单和观察保持不变；
- 非车辆和群组仍按现有能力边界排除并记入回执，不能将群组补造成确定的个体；显式 straight_preview/intersection_preview 保留其已复核、已绑定的执行范围，不因普通转换规则改变而扩大；
- 已有 `simulation_scenarios`、轨迹数值、车道和速度优先保留，只有缺口才补齐。

已知曲率/弯道不能被直路覆盖，当前转换明确失败。仅未绑定 conversion-policy.v1 的历史合同保留变化灯色转全绿灯的兼容行为，回执记录 `missing_unknown_or_time_varying` 和 `observed_value`，不表示灯色时序保真。新合同始终拒绝已知变化灯色的常量替换；`signal_state_policy_default` 只授权缺值补齐。路口近似投影同样保留 source_world 和逐字段 provenance。

验收顺序为 MELA 四部分模型、跨 section ID/名称引用、OpenX 静态输入合同。通过只说明 `target.json` 可进入已知 `generate(data)` 输入链；真实 `.xodr/.xosc` 生成和 esmini 渲染仍为 `not_executed`，除非实际 runner 给出证据。

## 停车与时长（2026-09-19）

静态车辆缺初始 lane，或已声明 parking_side 时，先执行 `parking_scenery_layout.v1`，再执行通用 Actor 补齐。有明确行车道且未声明停车侧别的停止车辆仍保留原位。布局按 actor_id 稳定排序，优先源道路、侧别和可用纵向位置；否则从道路两端各留 6m 的范围内分配槽位。乘用车占用长度 5m、van 6m、bus/truck/tractor 12m，车间净间距至少 2m。已有 paved shoulder 优先；缺少停车空间时在私有副本增加/加宽 shoulder，容量耗尽时显式延长执行道路，分别记录 `parking_execution_shoulder_default`、`parking_execution_vehicle_clearance` 和 `parking_execution_capacity_extension`。道路延长会改变执行几何，不能称为观察保真。

内部 `_execution_parking` 不进入 Behavior/analysis/target 的公开字段定义。位置通过既有 OrientedPoint 表达：外侧车道 + left_of/right_of + 米制横向偏移；车辆只产生一个零速度 stop，持续至 metadata.duration_s。布局及轨迹投影记录 `static_vehicle_parking_layout`，原始 analysis 不变。独立投影门禁检查 Actor 保留、道路、侧别、paved shoulder、横向中心、纵向范围、停车间距和停止时长。

普通 Builder 从媒体 probe 绑定 metadata.duration_s，last_frame_time_s 单独保留；5s 视频最后一帧为 4.9s 时不截短为 4.9s。Converter 在执行副本将最后速度延续至媒体末尾，并推进位置、移除外推点的 frame_id、记录执行延长。旧 Case 缺少 probe 时沿用既有 metadata/轨迹兜底，不猜测额外帧间隔。

普通路口 timeline 合并等加速度、同一直行路径的冗余采样。明确的非直行车道切换和非 entry 事件边界阻止合并；专用 lane-change/motion_contract 路径仍使用其原有合同。全程单段匀速使用 traveled_distance = speed × duration；多阶段动作保持绝对 simulation_time 边界，stop 保持 simulation_time。单段距离编码的时间与完整速度曲线由本地门禁独立解码核对。该调整不修改 MELA Schema，也不提供显式场景 StopTrigger；真实运行停止时间仍待下游验证。

### 车道、停车绑定和入路口时间修复

`parallel_parking_strip` 与其他已支持停车别名共用 `_lib/contracts/parking.py` 规范化；本次普通转换链路的 Builder、分配器、场景转换和交付校验使用该共享函数。停车分配先解析 `roadside_area_id` 与 `occupied_by`，再使用可选 `parking_side`；明确绑定互相矛盾属于数据错误。没有结构化侧别仍按现有可用区域/槽位确定性分配，逐车记录 `parking_side_policy_default`，不能宣称该侧别已由视频确认。禁止从自由文本提取侧别或用群组推造个体。新 Behavior 在有帧证据时结构化交接区域或侧别；两个字段均可选，旧观察缺失不阻断。

普通路口转换直接消费 `analysis.events` 的 Ego entry 区间，不要求已有 `simulation_scenarios`。优先保留合法 trigger；缺 trigger 取区间交集的中点。Ego 速度补齐后对分段线性速度积分，缺失路长设为初始 s 加进入时刻的位移。使用默认速度时记录 `derived_from_entry_event_and_policy_speed`，不能升级为观察尺度。已知路长且全部源速度未知时，可由路长与时间反算执行匀速，记录 `speed_from_known_geometry_and_entry`；已知运动与几何冲突不能静默覆盖。

停车布局在上述路长确定后执行。若停车容量扩展入口道路，同步平移从该入口起步的运动车辆执行坐标，保持 road_length - initial_s 及车间相对位置，记录 `parking_capacity_entry_origin_shift`；静态布景位置仍由容量布局确定。源 analysis 保持不变，不删单体车辆。该行为是执行坐标/布景近似，不能称为停车位置精确还原。

入口长度调整后，对原始初始位置未知、且原兜底位置越界的动态车辆，按同车道顺序重新分配道路内执行起点：端部留 6 m、车中心间至少 8 m，保留已知坐标和 Ego 占位，记录 `entry_road_bounded_initial_position`。不修改源观察，不覆盖已知越界坐标；已知冲突或确实无可用空间明确报错。

v3 交付 `validate_target` 与兼容 `validate_target_semantics` 的 v3 分支共用 source_constraints 检查：不依赖 simulation_scenarios 的 exact/minimum 数量、已知停车区域/车辆绑定、目标动作覆盖媒体时长、入口车辆起点范围及 Ego entry 区间。旧 v2 不套用 v3 动作时长合同。独立解码目标动作预测进入时刻，回执 `source_constraints.verification=local_prediction_not_rendered`。无相应证据的项目跳过；缺字段使用固定策略并交付 done_degraded，不新增 needs_review 或模型重试。违反已知事实、损坏引用或执行结果自身不一致仍拒绝发布。
