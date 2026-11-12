Skill: world-understanding
Case ${case_id}，World 同向布局定向裁决。只读冻结任务包 ${request_path} 及 focused_context.evidence_frames 的 review_path 原始图片，禁止读取完整 World、Batch、Schema、其他帧和历史 session。
先独立看原帧，再比较 World 与独立 Batch；Batch 是挑战，不是真值。遵循 focused_context.scope_rule、value_schemas、allowed_paths 和 lane_layout_contract；只写 world-closure-patch-v1 小补丁，复制 request_sha256/base_sha256，只允许指定路径的 add/replace，每项带实际帧证据。
若 Batch 与原 World 对同向数量或自车右起序号不一致，特别是 Batch 报三条/自车中间而原 World 只确认至少两条时，必须重新逐段检查自车右侧虚线外到真实道路右缘之间的完整走廊。右侧没有车辆或停车格，不能单独证明该走廊不是行车道；也不要把路侧停车用途与行车道数量混为一项。若仍否定 Batch，逐字段 reason 必须说明具体相反证据；现有帧无法唯一裁决时返回 needs_evidence，不能仅重复原 World 的说法。程序会对与独立 Batch 仍冲突的补丁做一次有界定向重看；持续冲突不会自动交付。
把同向数量、N+1边界链、车道定义、标线、路侧用途及初始 Actor 车道引用作为一个一致单元修正。不改环境、道路几何、已知米制数值、Actor 身份、原问题或动态行为，不把对向未知转成同向未知。边界标识必须对应实际 lane_markings 及 lane 左右边界。
本闭合合同把 `/world/road/lane_markings` 用作完整车道边界注册表，不只登记漆线：`boundaries_right_to_left` 的每个 ID 都必须有唯一 `marking_id` 条目，物理路缘、道路边缘也不例外。例如边界链使用 `curb_right_edge` 时，必须在替换后的完整 lane_markings 数组中登记 `marking_id=curb_right_edge`，并为 `/world/road/lane_markings` 提交精确路径 observed field_evidence。输出前逐项检查 `set(boundaries_right_to_left) <= set(lane_markings[*].marking_id)`；不能只让 lane 的左右边界引用一个未注册 ID。
完整布局的每条同向行驶车道必须写 lane_id、road_id、lane_type=driving、travel_direction=forward 或 same_as_ego、ordinal_from_right、right_boundary、left_boundary；不得使用 right_boundary_id 或 left_boundary_id 别名。
根 lane_id_map 覆盖每个旧物理车道、一一映射到修正后的全局 lane ID；全局编号仍从右向左连续。同步受影响 Actor 引用而不重建身份。每个修改事实还须写指定精确路径的 observed field_evidence，不能用道路容器证据冒充每个改动。确认原布局合法，但仍须提交完整边界证明并关闭布局复核问题，空 operations 不算通过。
缺证或权限不足按 stop_contract 返回零操作和具体原因，不补造车道、不做无进展重复。只写一次 ${candidate_path} 后停止，不创建 subagent。
