# Mela 仿真场景 JSON 格式转换指南（通用）

> 场景专属规则见 `scenes/{type}/skill.md`（如 `scenes/intersection/skill.md`）。

## 1. 概述

将视觉语言模型产出的视频理解 JSON 转换为 Mela pydantic 模型可校验的目标格式 JSON。

### 转换链路

```
scene_analysis_full_*.json               {type}_scene_target.json
(视频理解输出)            --转换-->       (Mela pydantic 格式)
        |                                      |
   通用结构化 JSON                           四部分: scene / entities /
   (metadata/environment/                    scenario / environment
    road/ego/actors/events/
    simulation_scenarios)
```

### 目录结构

```
skills/
  skill.md              <- 本文件（通用规则 + 标准工作流）
  README.md             <- 目录导航 + 扩展指南
  validate_server.py    <- FastAPI pydantic 校验服务（:8900）
  lib/                  <- 共享代码库（所有场景复用）
    path_setup.py       <- sys.path 注入 + 路径常量
    enums.py            <- 枚举映射表（Color / VehicleClass）
    builders.py         <- uid / mk_pos / mk_orient / mk_action / mk_maneuver / decide_direction
    validators.py       <- pydantic 四部分校验 + 下游 OpenX 校验
  scenes/
    intersection/
      skill.md          <- 路口场景专属规则
      convert.py        <- 路口场景转换脚本
  samples/              <- 正常 Mela 链路产出样例（参考）
  mela-business-logic/  <- 权威 Mela 模型包
    mela_business_logic/scenario_preview/pydantic_classes/
                         <- scene/entities/scenario/environment 格式定义
```

### 输入/产出

| 方向 | 路径 |
|------|------|
| 输入 | `media/input/{type}_scene_analysis.json` |
| 产出 | `media/output/{type}_scene_target.json` |

## 2. 源/目标 JSON 通用结构

### 源 JSON（视频理解输出）

```json
{
  "metadata": { "video_duration_s", "fps", "resolution", ... },
  "environment": { "weather", "daytime", "lighting", "road_surface", ... },
  "road": { "roads": [...], "lanes": [...], "lane_markings": [...], "junctions": [...] },
  "ego": { "trajectory": [{ "timestamp_s", "lane_id", "speed_mps", "position_ego_m", ... }] },
  "actors": [{ "actor_id", "actor_type", "subtype", "color", "is_static", "trajectory": [...], "maneuvers": [...] }],
  "simulation_scenarios": [...]
}
```

### 目标 JSON（Mela pydantic 格式）

```json
{
  "scene":       { "id", "name", "type", "description", "road_network": {...}, "off_road_surface" },
  "entities":    { "id", "scene_id", "assets": [], "traffic_lights": [...], "traffic_signs": [], "crosswalks": [] },
  "scenario":    { "id", "name", "scene_id", "vehicles": [...], "pedestrians": [...], "animals": [] },
  "environment": { "id", "name", "scene_id", "daytime", "sun_position", "weather", "topology", "season", "description" }
}
```

## 3. 通用转换规则

### 3.1 核心原则

- **数据优先**：脚本优先读取源 JSON 的道路、车道和参与者数据；缺失或目标不兼容字段按 [fallbacks.md](fallbacks.md) 的统一硬编码规则补齐
- **车道一视同仁**：所有车道按源 JSON 的 `lane_type` 直接使用，不做 parking/driving 区分或重排序
- **车道顺序**：路口保持已定义的道路映射；右侧通行 highway 将源 JSON `roads[].lane_ids` 的右到左顺序反转为 MELA 从中心线向外的 index 和命名，同时映射所有车辆引用

### 3.2 速度单位转换

```
源: m/s (米/秒)
目标: km/h (公里/小时)
公式: km/h = m/s × 3.6
实现: builders.mps_to_kmh(v)
```

### 3.3 位置坐标系

源 JSON 使用自车坐标系（X 前、Y 左、Z 上），目标 JSON 使用相对参考点的 OrientedPoint。

```
OrientedPoint {
  position: {
    reference_name: "Road_1_Seg_1_Lane_3",   // 参考车道名
    longitudinal_specifier: "ahead_of",       // ahead_of | behind
    longitudinal_value: 0.0,                  // 纵向距离 (m)
    longitudinal_unit: "m",
    lateral_specifier: "right_of",            // left_of | right_of
    lateral_value: 0.0,                       // 横向偏移 (lane 或 m)
    lateral_unit: "lane"                      // "lane" 或 "m"
  },
  orientation: {
    reference_name: "Road_1_Seg_1_Lane_3",
    specifier: "facing_towards",              // facing_towards | facing_away_from
    offset_angle: 0.0
  }
}
```

构造器：`builders.mk_pos()` / `builders.mk_orient()` / `builders.mk_oriented_point()`

### 3.4 Maneuver / Action / Transition 结构

每个 actor 的 timeline 包含一组 maneuver，每个 maneuver 由 action + transition 组成：

```
Maneuver {
  id, name, type, parent_id (actor id),
  action: {
    id, direction, start_position (仅首个 maneuver),
    start_longitudinal_velocity, end_longitudinal_velocity,  // km/h
    start_lateral_velocity, acceleration, turning_angle
  },
  transition: {
    id, type (traveled_distance | simulation_time),
    value, reference_name
  }
}
```

**规则：**
- 仅第一个 maneuver 的 action 有 `start_position`，后续为 `null`
- `start_longitudinal_velocity == end_longitudinal_velocity` 时 `acceleration` 必须为 `0.0`
- `start > end` 时 `acceleration` 必须为负
- `start < end` 时 `acceleration` 必须为正
- `"stop"` 和 `"wait"` 类型必须用 `"simulation_time"` transition，且 `value > 0`
- `"traveled_distance"` transition 的 `value` 必须 `> 0`

构造器：`builders.mk_action()` / `builders.mk_transition()` / `builders.mk_maneuver()`

### 3.5 ID 生成与层级

所有对象需要唯一 UUID。使用 `builders.uid()` 生成。

```
scene.id
  ├── road_network.parent_id = scene.id
  │     ├── road.parent_id = road_network.id
  │     │     └── segment.parent_id = road.id
  │     │           └── lane.parent_id = segment.id
  │     └── junction.parent_id = road_network.id
  ├── entities.scene_id = scene.id
  ├── scenario.scene_id = scene.id
  │     └── vehicle/pedestrian.parent_id = scenario.id
  │           └── maneuver.parent_id = vehicle/pedestrian.id
  └── environment.scene_id = scene.id
```

### 3.6 枚举值映射

**Color 枚举**（`enums.COLOR_MAP`）：

| 源值 | 目标值 |
|------|--------|
| `"red_with_black_roof"` | `"red"` |
| `"dark_gray"` / `"gray"` | `"grey"` |
| `"dark"` | `"black"` |
| `"white"`, `"red"`, `"silver"`, `"black"` | 直接使用 |

**VehicleClass 枚举**（`enums.VCLASS_MAP`）：

| 源 subtype | 目标 vehicle_class |
|-----------|-------------------|
| `"compact_car"`, `"sedan"`, `"coupe"` | `"sedan"` |
| `"minivan"` | `"van"` |
| `"suv"` | `"suv"` |
| `"box_truck"` | `"truck"` |

**ManeuverType 选择**：

| 场景 | maneuver type | transition type |
|------|--------------|----------------|
| 匀速直行 | `"drive_straight"` | `"traveled_distance"` |
| 制动减速 | `"drive_straight"` (accel < 0) | `"traveled_distance"` |
| 静止 | `"stop"` | `"simulation_time"` |
| 行人行走 | `"walk"` | `"traveled_distance"` |
| 行人驻足 | `"wait"` | `"simulation_time"` |

### 3.7 车道标线输入兼容契约

转换器接受以下两种 analysis 表示，并统一解析为每条车道的左右边界：

- ID 式：标线包含 `marking_id`，车道通过 `left_boundary_id` / `right_boundary_id` 引用。
- 关系式：标线通过 `between: [lane_id, lane_id]` 表示相邻车道共享边界，或通过 `side: left|right` 表示 road 的外侧边界；可选 `lane_id` / `road_id` 用于消歧。

标线类型可使用 `type` 或兼容字段 `marking_type`。包含 `dashed` 或 `broken` 的类型映射为 Mela `dashed`，其余类型映射为 `solid`。`roads[].lane_ids` 按从右到左排列，因此相邻两车道间标线是右侧车道的左边界和左侧车道的右边界。

## 4. 通用校验

### 4.1 Pydantic 四部分校验

```python
from validators import validate_data
result = validate_data(target_json)
# {"valid": true, "sections": {"scene": {"valid": true}, ...}}
```

内部逻辑：递归 `id` -> `UID` 重命名 -> 分四部分校验 -> 每部分前清 `BaseClass._registry`。

### 4.2 下游 OpenX 静态输入检查（正式 v3 交付门禁）

```python
from validators import check_downstream_road_network
errors = check_downstream_road_network(target_json)
# 空 = 通过
```

| # | 约束 | 违反时报错 |
|---|------|-----------|
| 1 | `scene.type` 不能为 `"off_road"` | `Off_road not supported` |
| 2 | 每条 road 必须有 `segments` 数组且非空 | IndexError |
| 3 | **每个 segment 必须有 `lanes` 数组且非空** | **`Lane empty for segment {name}`** |
| 4 | 每个 segment 必须有 `length` | KeyError |
| 5 | lane 必须有 `direction` ∈ {`forward`, `backward`, `bidirectional`} | 拓扑错误 |
| 6 | lane 必须有 `name` 和 `id` | KeyError |

### 4.3 校验服务

```bash
# 方式 1: 脚本内自动校验（convert.py 末尾已集成）
python skills/case-conversion/scripts/scenes/{type}/convert.py

# 方式 2: 启动独立校验服务
python skills/case-conversion/scripts/validate_server.py   # 起 :8900
curl.exe -s -X POST http://localhost:8900/validate -F "file=@media/output/{type}_scene_target.json"
```

## 5. 通用常见报错

### 5.1 Duplicate ID Error
**原因：** 多个对象使用了相同的 UUID。**解决：** 确保每个对象调用 `uid()` 生成独立 UUID。

### 5.2 Extra Field Forbidden
**原因：** Mela 模型配置 `extra = "forbid"`。**解决：** 删除多余字段（`bbox_2d_px`, `confidence`, `source` 等）。

### 5.3 id -> UID Alias
**原因：** BaseClass 的 `id` 字段使用 `alias="UID"`。**解决：** `validators.validate_data()` 会自动递归重命名，无需手动处理。

### 5.4 tracking_id: null 校验失败
**原因：** pydantic v2 中 `int = Field(None)` 不接受显式 `null`。**解决：** 已在 `pydantic_classes/scenario/actor.py` 中修复为 `Optional[int]`。

### 5.5 Enum 值不匹配
**原因：** 源 JSON 中的枚举值不在 Mela 模型的枚举中。**解决：** 按第 3.6 节的映射表进行转换。

### 5.6 车道顺序错误
**原因：** 用上游右到左车道序号直接作为 highway 的 MELA 中心线向外序号。**解决：** 按场景合同映射：右侧通行 highway 反转每条同向道路的 `lane_ids` 后编号，并同步更新车辆引用；路口沿用自己的映射规则。

## 6. 标准工作流

```
1. 用户把 3.8max 视频理解 JSON 放入 media/input/
2. 运行 scenes/{type}/convert.py
   -> 读取输入 -> 按 skill 规则映射 -> 写入 media/output/ -> Mela 权威格式校验 + OpenX 兼容诊断
3. 检查校验结果：
   - pydantic 四部分全 ✅ = Mela JSON 转换成功
   - 内部 Scene 脚本仅输出 OpenX 诊断；正式 v3 Wrapper 将静态错误作为提交 target 的阻塞条件
```
