本次冻结的 visual-answers 修订启用了机械字段补全，以下规则只减少重复表示，不减少视觉取证或身份门禁：

- trajectory 点给实际 frame_id 时，可以省略重复的 time_s，Python 按允许帧索引的真实 PTS 补全。给了 time_s 或 timestamp_s 时必须与该帧一致；不根据文件名或帧序号猜时间。
- 已看清所属 road_id 与 ordinal_from_right 时，可以省略重复的 lane_id，Python 查 World 的唯一位置映射补全。两者必须是当前时刻独立观察的结果，不能把首帧位置沿用全程。看不清写 lane_id=null；脚本不会把 null 改成某车道。路口内部不套入口车道编号。
- 显式字段相互矛盾会被拒绝，不会挑一个覆盖另一个。没有有效帧或位置锚点不会得到默认时间/车道。
- 其他 Schema 字段、事件参与者、身份连续性、证据和 epoch 仍按原合同输出。速度等未知数值继续按已绑定 semantic_limitation 表达；不得用执行默认值伪装观察。reason 只写可核验依据，不要求长篇推理过程。
