# Workspace Conversion Baseline

本文提炼历史文件 `/Users/baicaipaoshui/Documents/project/JIR/workspaces/json-coverter/skills/skill.md` 及其 highway 规则中已经证明有效的转换契约。旧 workspace 只作为只读质量基线，不得作为当前 Case 的代码、输入、缓存、恢复状态或发布目录。

## 保留的通用规则

- 转换必须由 analysis 数据驱动，不复制旧场景的 UUID、actor、速度或轨迹数值。
- 目标顶层固定为 `scene`、`entities`、`scenario`、`environment`。
- 速度从 m/s 转换为 km/h；位置通过 `OrientedPoint` 引用目标 lane name。
- UUID 与 parent/scene 引用关系必须完整。
- 首个 maneuver 可以带 start position，后续 maneuver 不重复设置；加速度符号必须和速度变化一致。
- Mela/Pydantic 四部分必须分别校验；正式 v3 Wrapper 同时将 road、segment、lane 的 OpenX 静态约束作为交付门禁，内部 Scene 脚本只输出诊断。
- lane 的顺序来自 analysis 的道路顺序和每条 road 的 `lane_ids`，不得按类型私自重排。

## Highway 成功产出形状

旧 crash 成功结果只有一条 Mela highway road。analysis 中的自车 carriageway 车道成为 `forward` lane，对向车道根据 `lane_count_opposite_direction` 追加为同一 segment 下的 `backward` lane。这个形状不会触发当前 `mela-openx-generator` 为每条 road 重置 `seg_idx` 的缺陷。

当前 analysis 允许显式记录多条平行 carriageway road。Converter 必须在目标层把它们合并为一条逻辑 highway road，按来源 road 将 lane 映射为 `forward` 或 `backward`。不得发布多条 road 在相同 segment index 上车道数不同的结构。

## 对旧规则的必要修正

- 旧 highway 文档假设所有源 road 都是同向，实际视频存在护栏分隔的对向 carriageway；当前映射必须识别 `opposite_to_ego`。
- 旧规则的 `check_downstream_road_network()` 只是 OpenX 输入形状的静态检查，不等于真实运行 `mela-openx-generator`。
- 当前 validator 额外拒绝会触发 generator `seg_idx`/`item['direction']` 空引用的跨 road 车道数错位。
- 当前正式入口使用显式 input/output/result 路径、原子提交和 SHA-256 幂等，不使用旧 `media/input`、`media/output` 隐式文件名。

## 验收边界

正式 v3 Case 的验收包含当前 Skill 内四部分权威模型、跨 section 引用、投影和 OpenX 静态输入合同。正式 Wrapper 的 conversion_result.status=done/done_degraded 表示适用的静态门禁已通过；内部 Scene 脚本退出成功不等于发布成功。只有实际运行 mela-openx-generator 并生成目标产物，才可声明真实 generator 通过；不得用静态检查替代该声明。
