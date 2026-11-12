# MELA 表达契约与上游取证映射

维护日期：2026-09-10。适用于本项目 v3 视频 → World/Batch/Behavior → analysis → Converter 的交付路径。本文是维护与取证指引，不新增 JSON 字段，不授予模型执行默认值、改变工作流或发布的权限。

## 来源与适用边界

- 格式：Converter 实际导入的 [模型包](../../../json-coverter/skills/case-conversion/scripts/mela-business-logic/mela_business_logic/scenario_preview/pydantic_classes)。外侧 `scripts/pydantic_classes` 当前具有相同 Python 源码；维护以实际导入包为准。
- 项目能力：[mela-capabilities.v1.json](mela-capabilities.v1.json)；策略配置：[fallback-policy.v1.json](fallback-policy.v1.json)。策略中的值必须由代码显式应用并记录 provenance，不能作为视觉观察答案。
- 后端能力结论来自 2026-09-04 的历史源码快照；原能力审计文档已移除，未固定 commit，也未证明真实回放通过。
- 普通 Preview 与 trajectory 的历史流程说明文档已移除；当前行为以 Schema、Converter、回归测试和真实 runner 证据为准。

格式可表达、后端存在代码分支、项目已接通、真实渲染已验证，是四个不同结论。冲突时按所问层次检查对应代码，不用更宽的后端能力绕过项目门禁。

## 工作流与道路类型

### 车辆外观执行边界

2026-09-11 核查本机 `tmp_mela` 源码快照：`mela2mancera_preview_convert.py` 按 `vehicle_class` 生成车辆 Type，未将车辆 `color` 传入执行初始状态；`scenarios.py` 的非自车 Car 分支固定引用 `car_white.osgb`，van 分支固定引用 `van_red.osgb`。`trajectory_with_objects.py` 的 car/van 资源表也使用这两个固定模型。此结论限定于已检查的本机源码，不代表线上版本或真实回放验证。

当前上游 highway Converter 已将颜色写入 `scenario.vehicles[].color`。因此白色厢式车在第二车道、红色轿车在第一车道时，应保持白色/van/第二车道与红色/sedan/第一车道的对应关系（车型由 Converter 规范映射）。渲染颜色相反不能作为车辆换道、身份互换或上游颜色识别错误的证据。

- 本项目可改范围为现有框架、Skill 和上游适配逻辑；不修改 MELA/OpenX 源码或权威模型包，不应用历史 diagnostics 中的下游补丁。
- 输入缺失或映射错误在上游修复；输入正确而后端忽略颜色时，记录外观执行限制。反复补帧、重跑识别或只改 prompt 无法改变固定资源选择。
- 不用调换车型、actor ID、车道、颜色枚举，或给车辆改名的方式碰取期望颜色；不注入未经验证的模型路径字段。车型还影响尺寸和动力学，不能为颜色牺牲语义。
- 只有在实际服务已有且已验证的配置、模型目录或资源选择接口允许按参与者指定外观时，才可评估无需改源码的适配。当前未验证这样的入口，也未验证 `van_white.osgb`、`car_red.osgb` 可用；不能承诺已能修正颜色。修改生成的 XOSC 或替换资源也不是当前已接通的交付能力。
- 报告分别说明“颜色已写入目标 JSON”“源码使用固定外观模型”“是否真实回放验证”。若目标要求外观完全一致，明确这项要求在当前约束下尚未满足；其他门禁通过不能覆盖该限制。

| 范围 | 当前依据 | 对上游的要求 |
|---|---|---|
| preview_json | 项目清单列出 highway、intersection；Actor 为 vehicle、pedestrian | 按当前 World/Behavior Schema 输出，Converter 负责组装 scene/entities/scenario/environment |
| 直路、路口的当前 Converter | 两个场景入口当前构造 straight segment | 保留真实曲率和连接事实；已知弯道不能为适配入口改成直路 |
| curved、roundabout、t_junction、split/merge | 后端审计记载分支及部分限制，不等于本项目交付支持 | 如实识别，交由能力检查；不能将其强行归类为已支持场景 |
| external_xodr / trajectory | 项目清单为 pending_probe；后端 trajectory 使用外部 XODR 和世界坐标轨迹 | 不自动切换；需要独立路网、坐标与实体映射验证，不能直接发送现有 lane-relative 数据 |
| off_road | 审计中的普通后端转换路径显式拒绝 | 保留事实并交接能力限制；paved shoulder 不能代替任意越野或停车场拓扑 |

## 字段、含义、适用条件与证据

下表目标路径省略数组索引。上游字段名称、未知值写法和证据结构始终服从本阶段 Schema；目标字段不是新增上游字段的许可。

| 目标字段/结构 | 含义与适用场景 | 上游负责人及必要事实 | 取证与缺口处理 |
|---|---|---|---|
| road_network.traffic_type；lanes.direction | 通行侧；相对道路参考线的行驶方向，适用于道路网络 | World：交通方向、分隔和参考线方向 | 看标志、箭头、车流及中央分隔；停车朝向不是合法方向定论；未知不能默认右行或双向 |
| roads/segments/lanes | 道路层次与分向车道；不是车辆数量 | World：稳定 road/lane ID、同向和对向数量、空间关系 | 同向与对向数量分别判定；不可见不等于 0，也不默认 1 |
| segments.shape_type/length/road_radius | 形状、米制长度；后端 curved 需要半径 | World：可见道路形状、几何证据 | 看道路边界变化，不把透视直接当曲率；数值无可靠尺度时保留估计或未知；当前 Converter 不因此获得弯道支持 |
| lanes.width | 车道宽度，米；模型范围 0 < width ≤ 10 | World：车道边界与宽度证据 | 高清图可改善边界识别，但不保证米制尺度；允许的策略值由 Builder 应用 |
| lanes.left/right_lane_marking_type/color | 车道左右边界的类型和颜色 | World：标线及道路/车道锚点 | 优先选清晰原帧，不能将模型或后端默认实线/白色作为观察；复杂标线的转换覆盖需单独核实 |
| segments.left/right_shoulder.type/width | 路侧路肩表面和宽度；paved 是 ShoulderType，不是 LaneType | World：路侧区域性质、所属道路、侧别及宽度 | 路侧停车按下节有损映射；不能因有停放车辆就确认专用停车车道 |
| junctions 与 connection_points | 目标路口连接表达 | World：道路朝向、入口/出口、实际路径所需 lane_connections | 取证覆盖参与者经过的连接；不按道路条数猜完整合法转向网络 |
| Actor 类型及稳定名称 | 决定参与者映射和跨对象引用 | World 建初步身份；Batch 延续 registry；Behavior 跨时刻关联 | 保留真实类别和未知，不能为后端兼容把动物变汽车；类别映射归 Converter |
| action.start_position / OrientedPoint | 以具名对象为参考的纵横向位置和朝向 | Behavior：首点 lane_id、冻结起始自车坐标系中的位置及证据 | 同时核对目标、标线和参考对象；上游 x 前/y 左，目标 reference_name 与 lane/m 单位由代码转换 |
| action 起止速度、acceleration | 目标速度 km/h，加速度 m/s² | Behavior：原视频时刻、speed_mps 与位置序列 | 运动变化需跨时刻证据；速度估计标 estimated；Converter 做 ×3.6，不让模型切换上游单位 |
| timeline/transition | 动作顺序及触发；启用的 type 有 simulation_time、traveled_distance、achieved_speed、end_longitudinal_velocity | Behavior：事件参与者、边界时间与运动证据 | Schema 枚举存在不证明编译链全支持；不输出 TTC 等未启用值；具体编码由代码决定 |
| entities 的位置与类型 | 信号灯、标志、assets 的目标表达 | World：设施身份和位置；动态事件归 Behavior | 读取清晰细节及所属道路；设施可见不证明某车违规；变化灯色不能被单一状态替代后宣称保真 |
| environment | 环境事实及目标兼容字段 | World：天气、路面与可见性 | 保留雨雾等观察；审计快照天气未应用，不通过重复抽帧或改成晴天消除限制 |

## 停车区域 → paved shoulder

精确目标为 `scene.road_network.roads[].segments[].right_shoulder.type = "paved"`（左侧为 `left_shoulder`）。width 为米制正数，完整对象还包含 id、parent_id、name 等，由 Converter 构造。不能写成 `lanes[].type="paved"`。

当前模型的 LaneType 实际包含 `parking`；问题不是整个 MELA 没有 parking 字段，而是本项目能力清单选择 `paved_shoulder` 作为路侧停车表达。对应降级名称为 `parking_as_paved_shoulder`。

1. World 保留“路侧停车区域”的观察语义，按 Schema 记录所属道路、侧别、边界和宽度依据。正式停车专用属性无标志/标线证据时不得过度确认。
2. analysis 使用 `road.roadside_areas` 中 `area_type=curbside_parking`；这不是要求 World 直接生成 analysis。
3. 停车宽度缺失时，普通 v3 Converter 按固定策略在私有执行副本补齐并记录回执；2.5m 是执行默认值，不是视觉测量。2026-09-19 起静态停车布局按车辆包络可显式加宽，见 [fallbacks](../../../json-coverter/skills/case-conversion/references/fallbacks.md)。
4. Converter 映射 shoulder，交付保留表示损失：路侧铺装空间不完整表达停车法规、独立泊位布局或停车操作。

代码依据：[highway Converter](../../../json-coverter/skills/case-conversion/scripts/scenes/highway/convert.py) 的 roadside_widths_by_road/parking_shoulder；[intersection Converter](../../../json-coverter/skills/case-conversion/scripts/scenes/intersection/convert.py) 的 roadside_sides_by_road/mk_parking_shoulder。

上游应尽量明确区域道路归属，缺失时保留问题；Converter 的道路绑定不作为观察证据。当前 shoulder 对象仅表达宽度与表面，不能承诺还原局部停车区起止长度或多个不连续停车区。Behavior 可保留未知停车位置；普通 Converter 使用已授权的确定性静态布景布局，逐车记录策略、道路、侧别、纵向位置，不宣称恢复了视频中的精确停车位置。

## 右侧人行道 → paved shoulder 执行近似

新 Case 若在右侧路缘外观察到连续铺装人行道，World 在既有 `road.roadside_areas` 记录 `type=sidewalk`、所属 `road_id`、`side=right`、不同的内外边界和至少两帧直接证据；局部出入口不能替代整条带。World/analysis 保留 `sidewalk`，不把观察事实改成路肩。仅在 `sidewalk_as_paved_shoulder` 获准时，Converter 的私有执行副本把它映射为 `paved_shoulder` 并按冻结宽度策略补缺，目标输出 `right_shoulder`；回执记录来源类型及宽度默认值。此目标仅表示连续铺装侧带，**不**声称 MELA 中已生成语义准确的人行道或真实渲染已验证。旧冻结 Case 不自动重写。

## 对向车道数量未知

区分“单向道路所以为零”“已确认存在对向道路但数量未知”“方向和数量均未知”。`one_way`描述观察道路是否存在对向交通，物理隔离后的对向交通仍写false。确认存在但数量不可见时，`opposite_direction_lanes`允许为null，但必须同时具有绑定精确world_path的unresolved问题/歧义及status=unknown的field_evidence；不为满足整数类型伪造值。方向标志可以回答方向问题，但不能替代车道数量证据。

| 情况 | 处理 |
|---|---|
| 当前联系表太小 | 在允许索引内看原帧标线、中央分隔和远侧边界 |
| 其他时刻可能显露 | World 将待核事实关联 critical_intervals；Batch 只回答已分配项 |
| 已允许图像仍无法确认 | 保留 partial/unknown 或本阶段对应未知记录；停止无信息增益的重复查看 |
| 影响自车/冲突车辆路径或路网构造 | 交接执行缺口，由代码裁定可否生成 target；不能自行填一条对向车道 |
| 只涉及远处背景 | 如实说明影响范围，由门禁裁定；不得自行删除道路或改标签绕过检查 |

`single_lane_topology_placeholder` 在当前清单中为 unsupported_for_publish。配置出现 placeholder 选项不等于允许把未知车道数量当作已确认事实发布。

## Skill 使用与停止条件

新 Case 的单四岔直行执行近似见 [路口执行策略](intersection-approximation.md)。该策略只授权独立执行投影，不改变上文事实未知值契约，不把single_lane_topology_placeholder提升为一般发布许可。

### 定性运动与近似预览

Behavior 可以保留车道、前后及车距趋势而不给出绝对速度/位置；无尺度依据的速度为 unknown，未知坐标分量省略。相对当前自车的距离写观察说明，不冒充冻结起始坐标。preview_relations.v1 独立复核使用已有 steady/receding/approaching/unknown 枚举。

显式启用 straight_preview 后，当前 Builder 对满足已有单向直路、身份、车道、时间覆盖、顺序及无事件门禁的前车支持 steady、receding 和 approaching：steady 未知速度取自车执行速度，receding 取自车执行速度加配置差值。自车默认 80 km/h、间距及车道中心放置均为可配置仿真假设，逐项记录 policy_default、策略版本和复核绑定；原始轨迹不变。已有数值仍须与投影相符，不能通过删除数值或将矛盾改称 steady 来放行。approaching 的未知前车速度取自车速度减配置差值；非正速度、窗口内顺序反转或连续碰撞包络重叠仍阻断。车道未知和未决事件仍不支持该近似路径。

对于有 observed 方向证据和明确物理中央隔离的单路段 highway，可建立 `ego_carriageway_only` 执行范围。所有 participant 必须始终引用自车侧已定义车道，且没有事件或非初始动作；满足时仅 `world.road.opposite_direction_lanes` 的数量未知不阻塞，原始 one_way=false、对向道路存在、估计值和未决记录全部保留。自车侧车道数、隔离证据、participant 车道或对向交互有缺口时仍阻塞。moving background 观察可以保留在 analysis，但不进入 simulation_scenarios。

这一扩展不将性质未知的右侧条带自动认作车道或路肩，也不迁移既有 Case。观察完成但执行受阻时，诊断应区分数值不自洽、初始状态不完整与道路/事件未决，不能把输出数值冲突称为视频运动矛盾。

- World：以必要事实组织观察；只有可能通过视觉解决的缺口才安排复核窗口。静态世界和动态运动问题分别交给对应所有者。
- Behavior：围绕稳定身份、首点状态、运动与事件边界取证；执行字段缺失不构成补造轨迹的理由。
- Batch：按分配问题定位允许图像；细节不清查看高清变体，时刻缺失如实报告。只提交 candidate，不自行解码视频或调度补帧。
- BatchLifecycle：当前实现按 World critical_intervals 在已解码帧中选择并打包，不承诺自动增加解码频率。新时刻重采样需要另外实现与验证。
- Builder/Converter：负责允许的近似、provenance、单位/枚举映射和能力门禁。格式通过不等于渲染通过。

运行时规则分别位于 [World prompt](../world-understanding/references/prompt.md)、[Behavior prompt](../behavior-understanding/references/prompt.md)、[Batch prompt](../batch-lifecycle/references/worker-prompt.md)，编排器直接加载。本文是维护参考，受限 Worker 不必额外读取全文，closure/repair 不扩大任务包权限。

维护验收案例：右侧停车区应保留停车事实且映射到 right_shoulder；对向数量未知不补 1；单帧横向偏移不升级变道；已知弯道不改直路；天气执行不支持不触发无效补帧。文档检查与模板渲染只能证明指引接入，识别改善需另做实际 Case 验证。

隔离条件的执行依据已收口到 road_geometry.opposing_separation（physically_separated、side）及精确 field_evidence。median/left_boundary 等文字仅保留观察背景，不自动认定隔离。历史复核零修改可在证据一致时接纳；复核接纳不等于执行就绪。
