# 冻结事实修订 observation-execution.v1

本补充仅用于请求已冻结本修订的新 Case；旧请求仍按原契约执行。你只输出观察事实，不补仿真常量，不创建无证据的车道连接。单四岔路口已知道路方向时，未观测出口数量及直行连接可以保持未知，并用 world_paths、问题 ID 与 evidence 绑定，具体字段按 Schema 使用省略或 null。普通观察交接中的获准连接缺失由 Converter 在私有执行副本中按固定规则补齐并逐项记录，无需仅为该缺值强制请求关系复核；显式 intersection_preview 仍遵循其复核合同。这不表示观察问题已经 resolved。

跨帧身份不确定不是重复身份或明确冲突。需要交接身份疑问时，添加 identity_binding：kind 为 unknown 或 physically_separated_opposing，actor_ids 为涉及的已有 actor ID，evidence_frame_ids 为实际提供的证据帧。后者只能来自明确物理隔离的对向对象，不能由描述文本、未知车道或希望排除对象推断。问题 evidence 保留相应 observed 帧及理由。缺乏隔离证据用 unknown；不能合并、删除或重新命名 actor 来避开门禁。

是否可以排除仍由 Python 结合 Behavior 检查背景角色、整个轨迹窗口及主路径/事件依赖。主参与者身份未知必须补证，不以格式修复反复猜测。道路方向或路口形态未知不能用近似补齐。Agent 负责事实，Python 负责执行策略，Converter 负责 MELA 表达。


道路 lane_ids 是已识别车道清单，不自动表示车道总数。对路口每条 road 可输出 lane_knowledge：kind=exact|minimum|unknown，count 在 exact/minimum 时为整数，unknown 可为 null；anchors 数组记录每条已识别 lane_id、ordinal_from_right（从0起）及 evidence_frame_ids，并给总的 evidence_frame_ids。只看清最右一条时写 minimum/count=1 和位置锚点，不能宣称 exact/count=1。不能凭车流数补出新的观察 lane ID。身份和路侧用途问题尽可能写 actor_ids/world_paths/localization；不按描述文字驱动执行排除。

路口不要求观察输出包含至少三条完整路臂。只声明能够识别的道路；已识别侧向道路但无法识别任何车道时，road.lane_ids 可为空，lane_knowledge 保留 unknown，这不表示道路实际有零条车道。不要为满足执行格式补造道路、车道或连接；必要执行结构由 Converter 在私有副本中固定补齐，观察引用仍必须正确。junction 方向成员的空 lane_ids 仍按其合同表示明确无成员，未知成员应省略或写 null。

针对路口前方车辆，保留右到左的车辆分组和可追踪边界证据。不要把远处右道车辆因透视靠近画面中心而判为自车正前方。路口两侧的未知车道清单不能当作精确单车道总数；在观察资料中区分停车带宽度和行驶车道宽度，明确哪些边缘被货车或画幅遮挡。
