# Road/lane 位置映射

World 定义道路和全局唯一 lane_id；Behavior 独立观察对象所在道路和边界后引用该映射，不重新编号。`ordinal_from_right` 是沿该道路行驶方向从最右外边界向左计数的位置，统一从0开始，不能用全局 lane_id 的数字后缀计算。Ego 初始入口锚定 road_001；完整入口从 lane_001 开始，其他道路接续全局编号。

`roads[*].lane_ids` 给出已识别车道的右到左顺序，`road.lanes` 是实体清单，存储顺序没有空间含义。部分车道清单依赖 `lane_knowledge.anchors` 的位置；没有完整清单或位置锚点时，不把数组下标伪装成已测定的道路内序号。明确的数量、锚点与车道归属不能互相矛盾。

Behavior 轨迹可给 `road_id`、`lane_id`、`ordinal_from_right`，三者已知时必须一致；旧格式只给 lane_id 仍可使用，所属道路由 World 唯一引用查询，无需改写原件。路口内部 lane_id=null；道路和序号不能继续冒用入口值。道路已知但车道未知合法，未知不得自动补成0或最右车道。

共享确定性检查在 `_lib/contracts/lane_locations.py`：World readiness、Behavior 观察/执行验收、analysis 和 Converter 私有投影使用相同归属及位置规则。既有冻结模型请求不重写；本规则用于新渲染请求，离线检查不提交或迁移旧 Case。

Builder 只在获准执行投影中补全车道，重编号必须同时映射车辆、连接及其他引用，并在 provenance 保存 source_lane_map。Converter 不按 lane 实体数组重新编号。右侧通行 highway 的 MELA `index` 从中心线向外计数，与 World 的 `ordinal_from_right` 相反：三条同向车道中，源最左的序号2对应目标 `Lane_1`/index 0，源中间的序号1对应 `Lane_2`/index 1，源最右的序号0对应 `Lane_3`/index 2。名称不复用上游全局 lane_id 的数字后缀。独立交付检查核对目标名称、索引及车辆 reference_name。

## 物理边界编译与一致性（2026-10-04）

新 Case 的 lane-layout-decision.v6 将已确认的相邻行驶边界组合编译为道路内零基序号；Worker 不输出重复序号。路侧区域不加入行驶边界链。边界对顺序、相邻性、逐帧共同证据、每条旧车道的唯一对应及全部引用均须验收；格式通过不等于视觉判断正确。部分清单不能因为总数为 exact 就使用数组下标。lane 实体已有 ordinal 必须与道路顺序/锚点一致，实体声明不能单独补造部分清单的证据锚点。

新请求冻结 Schema、Prompt 内容与 validator revision；旧 v2–v5 冻结形状不迁移。事实原件和执行近似继续分层，json-coverter 的既有车距语义任务和策略补全保持原职责。
