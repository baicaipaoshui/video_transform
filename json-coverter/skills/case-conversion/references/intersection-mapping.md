# 路口场景格式转换指南

> 通用规则（坐标系 / Maneuver 结构 / ID 层级 / 枚举映射 / 校验方法 / 常见报错）
> 通用规则见同目录 `mapping-rules.md`。本文档只描述路口场景的专属规则。

## 1. 概述

将视觉语言模型产出的 `intersection_scene_analysis.json` 转换为 Mela pydantic
模型可校验的目标格式 JSON。适用于任意信号化/非信号化路口场景。

### 涉及文件

| 文件 | 说明 |
|------|------|
| `media/input/intersection_scene_analysis.json` | 视频理解原始输出（源） |
| `scenes/intersection/convert.py` | 数据驱动转换脚本（含空车道兜底 + direction 映射 + Mela 格式校验） |
| `media/output/intersection_scene_target.json` | 转换后的 Mela 格式 JSON（产出） |

### 运行

```bash
python skills/case-conversion/scripts/scenes/intersection/convert.py
```

## 2. 转换规则

### 2.1 Scene / Road Network

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `road.roads` | `scene.road_network.roads` | 逐条转换，见 2.2 |
| `road.junctions` | `scene.road_network.junctions` | 逐条转换，见 2.4 |
| `road.driving_side` | `road_network.traffic_type` | `"right"` -> `"right_hand_traffic"` |
| 有 junction | `scene.type` | `"intersection"` |

### 2.2 Road -> Segment -> Lanes

源 JSON 中每条 road 包含 `lane_ids` 列表，展开为 Mela 的 Road > Segment > Lanes 三级结构。

**车道生成规则：**

维护位置映射以 [统一位置合同](../../../../global-video-understanding/skills/references/lane-location-contract.md) 为准。源 `lane_id` 是全局唯一引用；目标 index 是道路内从0开始的位置，目标名称末尾从1开始。`road.lanes` 的实体存储顺序不参与编号；Converter 不重新命名源 lane_id，也不改变已有车辆引用。

1. 遍历 `roads[].lane_ids`，按列表顺序生成 Mela lane
2. Lane `index` = 在 `lane_ids` 列表中的位置（0, 1, 2, ...）
3. Lane `name` = `Road_{N}_Seg_1_Lane_{i+1}`（i 为 index）
4. Lane `type` = 源 JSON 的 `lane_type` 字段值
5. Lane `width` = 源 JSON `width_m.value`
6. Lane `direction` = 根据 `road_role` 映射（见 2.3）
7. **车道补全**：先遵守 `lane_knowledge.exact/minimum` 与已有 anchors；已声明四臂中可唯一对应的 Ego 直行出口数量未知且空车道时，按入口数量补齐并记录策略。其他无数量信息的空道路生成一条默认 driving lane；具体边界见 [fallbacks.md](fallbacks.md)。不覆盖已观察的 exact 数量。

**车道标线映射：**

| 源 marking_type | 目标 left/right_lane_marking_type |
|------------------|----------------------------------|
| `"broken_line"` / `"dashed_lane_divider"` | `"dashed"` |
| `null` 或其他 | `"solid"` |

查找方式：源 lane 的 `left_boundary_id` / `right_boundary_id` -> 在 `lane_markings` 数组中查找 -> 取 `marking_type`。

### 2.3 Lane Direction 映射

| 源 road_role | 目标 direction | 说明 |
|-------------|---------------|------|
| `ego_approach` | `"forward"` | 主路驶入，朝向路口 |
| `ego_exit` | `"forward"` | 主路驶出，与驶入同向 |
| `cross_street_incoming` | `"forward"` | 横向道路驶入，朝向路口 |
| `cross_street_outgoing` | `"backward"` | 横向道路驶出，远离路口 |

> **关键：** `cross_street_outgoing` 的 lane direction 必须为 `"backward"`，其余均为 `"forward"`。脚本中由 `decide_direction(role, heading_rad)` 实现：`heading ≈ π/2` 且 `role == "outgoing"` -> `"backward"`。

### 2.4 Junction

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `junction_type` 含 `"intersection"` | `type: "intersection"` | JunctionType 枚举 |
| `incoming_roads` + `outgoing_roads` | `num_connections` | 两者数量之和，最小 3 |
| - | `cardinal_positions` | `["S", "N", "E", "W"]` |
| - | `connection_points: {}` | 留空，model_post_init 自动填充 |

`traffic_signals` 是是否受信号控制的唯一结构事实来源：非空时 Junction 描述写 `signalized=True`，为空时写 `signalized=False`。兼容输入可携带旧 `signalized` 布尔值，但它只能确认对象列表；二者冲突时拒绝转换，不能用缺失字段的 `False` 默认值覆盖已经存在的交通灯。交付投影会独立核对描述状态、交通灯对象数量和 analysis 结构事实。

### 2.5 Vehicle（ego + actors）

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `ego.trajectory[0].speed_mps` | `vehicles[0].timeline[0].action.start_longitudinal_velocity` | m/s × 3.6 = km/h |
| `actors[i].color` | `vehicles[i].color` | 需匹配 Color 枚举（见通用 skill.md） |
| `actors[i].vehicle_class`，其次旧 `subtype` | `vehicles[i].vehicle_class` | 保留支持的 canonical 枚举，否则按统一映射 |
| `ego` | `vehicles[0].is_ego_actor = true` | 有且仅有一个 ego |
| `actors[i].is_static = true` | maneuver type = `"stop"` | 静止车辆用 stop + simulation_time |
| `actors[i].is_static = false` | maneuver type = `"drive_straight"` | 匀速或减速直行 |
| `quality_report.intersection_preview.actor_paths[id].contract = "intersection_lane_change.v1"` | `drive_straight → lane_change → drive_straight` | 单车单次相邻变道；变化段引用目标车道，横向速度由米制位移/证据时长换算为 km/h |
| `actors[i].maneuvers` 含 `"braking"` | acceleration < 0 | 制动减速 |

**车辆定位规则：**
- `reference_name` = 车辆所在车道的 Mela lane name（通过源 `trajectory[0].lane_id` 映射）
- `lateral_specifier` = `"right_of"`, `lateral_value` = `0.0`（在车道上）
- 普通转换中 `is_static=true` 且初始 `lane_id=null` 的路边车，先按 [停车布局策略](fallbacks.md#停车与时长2026-09-19) 获取执行位置；已生成的 `_execution_parking` 优先于普通 lane_id 分支，不因 lane 已补齐而跳过停车映射。
- 右侧路边车参考入口路最右 driving lane，使用 `right_of` 米制偏移；左侧则参考最左 driving lane 并使用 `left_of`
- 默认横向偏移 = 半条参考车道宽 + 半个 2.5 m 停车 shoulder 宽；3.5 m 车道时为 3.0 m
- 路边静止车必须使用零速度 `stop + simulation_time`

普通连续直行采样由 `lib/motion_timeline.py` 合并等加速度片段；全程单段匀速生成一个 traveled_distance 动作。观察轨迹与事件保留，校验器比较完整速度/距离曲线，不要求采样数量等于 timeline 数量。专用变道与 motion_contract 编译保留独立合同。

**路边停车区域：**
- `road.roadside_areas[].area_type=curbside_parking` 不映射成普通 lane
- 无显式 `road_id` 时，停车区映射到 Ego 入口路和其直行 lane connection 指向的出口路
- 左/右区域分别映射为 segment `left_shoulder/right_shoulder`，宽 2.5 m，`type=paved`，description 标明 curbside parking
- 当前 Mela ShoulderType 不接受 `parking`；使用 `paved` 才能通过权威 Pydantic 校验，下游 OpenX 将其生成 shoulder lane

### 2.6 Pedestrian

非车辆默认不进入当前车辆行车执行副本，原观察保留在 analysis，排除 ID 写入 `conversion_result.json.excluded_actor_ids`。只有后续明确扩展为行人交互执行场景时才应用下表的 MELA 行人映射。

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `speed_mps > 0` | maneuver type = `"walk"` | 速度 × 3.6 = km/h |
| `speed_mps = 0` 或 `null` | maneuver type = `"wait"` | 用 simulation_time transition |
| - | `gender`, `age_group`, `top_wear` | 源无此信息，用合理默认值 |

**行人定位（路口专属）：**
- 通过 `position_ego_m.y` 判断左右侧
- Y < 0（右侧）-> `reference = Road_1_Seg_1_Lane_1`, `lateral = "right_of"`, `lateral_value = 1.0`
- Y > 0（左侧）-> `reference = Road_1_Seg_1_Lane_5`, `lateral = "left_of"`, `lateral_value = 1.0`

### 2.7 Environment

| 源字段 | 目标字段 | 映射规则 |
|--------|---------|---------|
| `environment.weather` | `weather` | `"cloudy"` -> `"overcast"`（枚举无 cloudy） |
| `environment.daytime: "day"` | `daytime: "afternoon"` | "day" 不在枚举中 |
| `environment.sun_direction: null` | `sun_position: null` | 阴天无太阳方向 |
| `environment.road_surface` | - | 写入 description |
| - | `topology: "flat"` | 城市道路默认 flat |
| - | `season: "summer"` | 源无季节信息，合理推断 |

信号灯和斑马线对象已被观察但缺少米制位置时，使用 [统一硬编码兜底](fallbacks.md) 的标准位置；对象本身不存在时不凭空添加。

## 3. 下游 OpenX 静态输入检查（正式 v3 交付门禁）

Pydantic 校验通过 ≠ 下游可用。下游 `convert_roads` 对 road network 有额外硬约束。

**最常见报错：`Lane empty for segment Road_N_Seg_M`**

根因：源 JSON 中某些 road 的 `lane_ids` 为空（视频未观测到的横向道路），
转换后 segment 有但 lanes 为空。修复见 2.2 节规则 7。

**实际验证案例：**

源 JSON 中 `road_002`（横向 outgoing）和 `road_004`（横向 incoming）的
`lane_ids=[]`，转换后 `Road_2_Seg_1` 和 `Road_4_Seg_1` 的 `lanes: []`。

修复：按 2.2 规则 7 生成默认 lane，direction 由 `heading_rad` + `road_role` 推断：

| Road | 源 road_role | heading_rad | direction | 生成 lane |
|------|-------------|-------------|-----------|-----------|
| Road_2 | outgoing | π/2 | backward | Road_2_Seg_1_Lane_1 |
| Road_4 | incoming | π/2 | forward | Road_4_Seg_1_Lane_1 |

内部 Scene 脚本单独输出 OpenX 诊断；正式 v3 Wrapper 在提交 target 前将这些静态错误作为阻塞条件。静态通过不代表真实 generator 或渲染已执行。

## 4. 路口场景常见问题

### 4.1 十字路口车道方向错误

**原因：** cross street 的 `travel_direction` 被统一映射为 `"forward"`，但 `cross_street_outgoing` 应为 `"backward"`。

**解决：** 按 2.3 节的 Lane Direction 映射表，`cross_street_outgoing` -> `"backward"`。

### 4.2 Lane empty for segment（下游报错）

**原因：** 源 JSON 中某些 road 的 `lane_ids` 为空数组（视频未观测到的横向道路）。

**解决：** 按 2.2 节规则 7 生成 1 条默认 lane。`direction` 需按 2.3 节 road_role 映射，`cross_street_outgoing` -> `"backward"`。

## 5. 转换脚本结构

`scenes/intersection/convert.py` 的代码组织：

```
1. 导入 lib/ 共享模块（path_setup / enums / builders / validators）
2. 读取源 JSON + 构建查找表（lane_obj_map / road_obj_map / marking_map）
3. 道路/车道: 遍历 roads -> 生成 Road > Segment > Lanes（含空车道兜底）
4. Junction: 从源 junctions 转换
5. 实体: entities + traffic_lights（从源信号灯提取）
6. 车辆: ego + actors（数据驱动，循环生成）
7. 行人: actors 中的行人（数据驱动，循环生成）
8. 环境: environment
9. 输出: 写入 media/output/
10. 校验: Scene 脚本四部分格式校验及 OpenX 诊断；正式 v3 Wrapper 另将静态错误作为交付门禁
```
