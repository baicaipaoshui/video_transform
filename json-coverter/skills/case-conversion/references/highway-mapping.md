# 高速公路追尾场景格式转换指南

> 通用规则（坐标系 / Maneuver 结构 / ID 层级 / 枚举映射 / 校验方法 / 常见报错）
> 通用规则见同目录 `mapping-rules.md`。本文档只描述高速公路场景的专属规则。

## 1. 概述

将视觉语言模型产出的 `crash_scene_analysis.json`（高速公路追尾事故视频理解
输出）转换为 Mela pydantic 模型可校验的目标格式 JSON。适用于任意高速公路
同向多车道直线路段场景。

### 涉及文件

| 文件 | 说明 |
|------|------|
| `media/input/crash_scene_analysis.json` | 视频理解原始输出（源） |
| `scenes/highway/convert.py` | 数据驱动转换脚本（含空车道兜底 + direction 映射 + Mela 格式校验） |
| `media/output/crash_scene_target.json` | 转换后的 Mela 格式 JSON（产出） |

### 运行

```bash
python skills/case-conversion/scripts/scenes/highway/convert.py
```

## 2. 转换规则

### 2.1 Scene / Road Network

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `road.roads` | `scene.road_network.roads` | 平行的同向/对向 carriageway 合并为一条 OpenX 逻辑 highway road，见 2.2 |
| `road.junctions` | `scene.road_network.junctions` | 高速公路无路口 -> `[]` |
| `road.driving_side` | `road_network.traffic_type` | `"right"` -> `"right_hand_traffic"` |
| 无 junction | `scene.type` | `"highway"` |

> **关键：** `scene.type` 必须为 `"highway"`（非 `"intersection"`），
> 否则 RoadNetwork 会要求至少 1 个 junction。

### 2.2 Road -> Segment -> Lanes

源 JSON 中每条 road 表示可观测 carriageway。高速场景转换时按道路顺序展平其 `lane_ids`，合并为一条 Mela Road > Segment > Lanes 结构；同向车道为 `forward`，对向 carriageway 车道为 `backward`。这是当前 `mela-openx-generator` 的兼容契约，可避免其按每条 road 重置 `seg_idx` 后误用第一条 road 的车道数。

**同向车道生成规则（右侧通行）：** World 的 `lane_ids` 从最右侧向中心线排列，MELA Lane `index` 从中心线向外计数，因此转换时逐条道路反转该列表。源 ID 和车辆引用一起映射，不改 World/Behavior 事实。

1. 从中心线向最右外边界遍历 `roads[].lane_ids`
2. Lane `index` = 反转后列表中的位置（0, 1, 2, ...）
3. Lane `name` = `Road_{N}_Seg_1_Lane_{i+1}`（i 为 index）
4. Lane `type` = `"driving"`（高速公路车道均为 driving）
5. Lane `width` = 源 JSON `width_m.value`
6. Lane `direction`：来源 road 为 `same_as_ego` 时是 `"forward"`，为 `opposite_to_ego` 时是 `"backward"`
7. 空车道兜底同路口场景规则

**车道标线映射：**

| 源 marking_type | 目标 left/right_lane_marking_type |
|------------------|----------------------------------|
| `"dashed"` / `"broken_line"` / `"dashed_lane_divider"` | `"dashed"` |
| `"solid"` 或其他 | `"solid"` |

> 高速公路场景源 JSON 直接使用 `"dashed"` / `"solid"`，
> 与 Mela LaneMarking 枚举值一致。

analysis 同时允许 ID 式（`marking_id` + lane boundary ID）和关系式
（`between` / `side`）标线；两者由共享 normalization 层统一解析。类型字段可为
`type` 或 `marking_type`，如 `dashed_white`、`dashed_line` 也映射为 `dashed`。

### 2.3 Lane Direction 映射

| 源 travel_direction | 目标 direction | 说明 |
|---------------------|---------------|------|
| `same_as_ego` | `"forward"` | 同向车道 |
| `oncoming` / `opposite_to_ego` | `"backward"` | 对向 carriageway 车道 |

> 分析层允许把护栏分隔的 carriageway 表达为多条 road；目标层会合成一条逻辑 highway road，并保留 lane direction。

### 2.4 Junction

高速公路无路口 -> `junctions: []`。

### 2.5 Vehicle（ego + actors）

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `ego.trajectory[0].speed_mps` | `vehicles[0].timeline[0].action.start_longitudinal_velocity` | m/s × 3.6 = km/h |
| `actors[i].color` | `vehicles[i].color` | 需匹配 Color 枚举；组合色取主色 |
| `actors[i].actor_type` | `vehicles[i].vehicle_class` | `"van"` -> `"van"`, `"car"` -> `"sedan"` |
| `ego` | `vehicles[0].is_ego_actor = true` | 有且仅有一个 ego |
| trigger 前匀速 | maneuver[0] type = `"drive_straight"` | 距离 = 初始速度 × trigger 时间 |
| ego 避让 | maneuver[1] type = `"turn"` | 由 `evasive_steering` 的方向、持续时间和横向位移计算 |
| actor 失控横摆 | maneuver[1] type = `"turn"` | 由 `loss_of_control_onset` 的方向和旋转速率计算 |

**车辆定位规则：**
- `reference_name` = 车辆所在车道的 Mela lane name（通过源 `trajectory[0].lane_id` 映射）
- `longitudinal_specifier` = `"ahead_of"`, `longitudinal_value` = 源 `position_ego_m.x`（车辆距道路起点的纵向距离）
- `lateral_specifier` = `"right_of"`, `lateral_value` = `0.0`, `lateral_unit` = `"lane"`
- 若自然语言轨迹尚未包含数值字段，使用首个 `simulation_scenarios[].initial_conditions` 中的 `lane_id`、`speed_kmh` 和 `ego.following_gap_m`。主前车由 `involved_actor_ids` 确定，不得通过车型描述中的颜色关键词猜测。
- 主场景参与车辆缺少车道、位置或速度时，按 [fallbacks.md](fallbacks.md) 生成固定执行值并写入转换回执；非关键环境车辆保留在 analysis，但不写入 target。

**主场景车辆 maneuver 设计：**

| 车辆 | Maneuver 1 | 后续 Maneuver |
|------|-------------------|-------------------|
| Ego | 按初始速度直行至 Ego trigger | 有 `evasive_steering` 时，根据其参数计算转向；不得无证据生成 `post-impact` |
| 主前车 | 按初始速度直行至自身 trigger | 有 `loss_of_control_onset` 时，根据旋转方向、旋转速率和剩余时长计算横摆 |
| 其他车辆 | 仅在初始位置、车道、速度完整时生成 | 根据结构化轨迹计算；不完整时不进入 target |

> **规则：** 仅第一个 maneuver 的 action 有 `start_position`，后续为 `null`。
> `decelerate` 类型要求 `acceleration < 0` 且 `start > end`。
> `turn` 类型无特定 maneuver 校验，但 action 校验仍检查加速度符号。

### 2.6 Pedestrian

高速公路追尾场景通常无行人。如有，按通用规则处理。

### 2.7 Environment

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `weather: "sunny"` | `weather: "clear"` | 枚举无 sunny |
| `daytime: "day"` | `daytime: "morning"` | "day" 不在枚举中；结合 lighting 时间戳推断 |
| `road_surface` | - | 写入 description |
| - | `sun_position: "in_front_of_ego"` | 上午晴天，ego 向前行驶 |
| - | `topology: "flat"` | 高速公路默认 flat |
| - | `season: "summer"` | 源无季节信息，合理推断 |

## 3. 下游 OpenX 静态输入检查（正式 v3 交付门禁）

与路口场景相同，下游 `convert_roads` 对 road network 有额外硬约束。内部 Scene 脚本仅输出诊断；正式 v3 Wrapper 在提交 target 前阻塞静态错误。静态通过不代表已执行真实 generator 或渲染。
高速公路场景因无路口，主要关注：

- 每个 segment 至少 1 条 lane（空车道兜底）
- lane `direction` 必须合法（`forward` / `backward` / `bidirectional`）
- lane 必须有 `name` 和 `id`
- segment 必须有 `length`
- 不允许多条 road 在相同 segment index 上具有不同车道数；该形状会触发当前 generator 的 `seg_idx` 索引缺陷。highway converter 通过合并平行 carriageway 消除此形状，validator 同时设置拒绝门禁。

## 4. 高速公路场景常见问题

### 4.1 scene.type 设为 intersection 导致 junction 校验失败

**原因：** RoadNetwork 的 `validate_after_assembly` 要求 intersection 场景至少 1 个 junction。

**解决：** 高速公路场景 `scene.type` 必须为 `"highway"`。

### 4.2 对向车道 direction 错误

**原因：** 未按来源 carriageway 区分方向。

**解决：** `same_as_ego` -> `"forward"`；`opposite_to_ego` -> `"backward"`。

### 4.3 关键车辆缺少可执行初始状态

**原因：** 主场景参与车辆只有自然语言轨迹，没有可解析的车道、位置或速度。

**解决：** Converter 优先读取 `trajectory[0]` 或主场景 `initial_conditions`；缺口按统一硬编码规则补齐并在 `conversion_result.json.fallbacks` 中指出字段路径和值。

## 5. 转换脚本结构

`scenes/highway/convert.py` 的代码组织：

```
1. 导入 lib/ 共享模块（path_setup / enums / builders / validators）
2. 读取源 JSON + 构建查找表（lane_obj_map / road_obj_map / marking_map）
3. 道路/车道: 合并平行 carriageway -> 生成一条 Road > Segment > Lanes（按来源设置 forward/backward）
4. 实体: entities（高速公路无信号灯/标志/人行道）
5. 车辆: 从结构化初始条件和 trigger 计算 ego/主前车 maneuver；只转换具备完整可执行状态的环境车辆
6. 环境: environment（sunny->clear, day->morning）
7. 输出: 写入 media/output/
8. 校验: Scene 脚本四部分格式校验及 OpenX 诊断；正式 v3 Wrapper 另将静态错误作为交付门禁
```
