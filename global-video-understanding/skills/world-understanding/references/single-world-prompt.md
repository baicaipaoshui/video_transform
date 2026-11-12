## 单次 World 回答：visual-answers.v2

Stage: world

只读取冻结请求 ${request_path} 及其 frames 列出的图像，不额外读取完整 World Schema、联系表、未列出的压缩图或历史结果。本节是本次 candidate 唯一输出格式，共享章节只规定视觉事实、身份、问题和证据原则，不增加另一套回答格式。输出遵循请求内 response_schema，原样回传 request_sha256。它是由现有 World Schema 派生的任务格式；Python 编译后才会提交正式 World。道路、环境、设施、Actor 和问题在同一次观察中完成，已有图像足够时不重复打开。没有独立 Topology 或 World parts session，不请求第二次完整 World 来补字段。

在同一份回答中选择以下一种道路表示。无论选哪种，都要同时提交 world.environment、actor_hypotheses、questions_to_verify、ambiguities、critical_intervals 和 field_evidence；空数组仅表示确实没有对应观察/问题，不能用来丢弃对象。Actor 的 actor_id 仍由你依据身份连续性建立，Python不判断或合并物理身份。逐帧清点仅在本次观察内完成，不另写清单或增加输出字段，不新增 Full World 根字段。

1. 完整直路：world.scene_type=highway，在根 road_observation 中回答道路、右到左车道、边界、路侧用途和首帧自车位置；不同时填写 world.road。每项包含 reason 和至少两个真实时刻的 frame_ids，road_context 包含真实首末帧。仅当完整横断面可见、直路、无实体中央隔离、全窗口稳定、无路口进入、同向车道后是连续对向车道且两侧用途都清楚时使用。车道为 N 条，边界必须为 N+1 条；路缘或未划线外缘用 curb/unmarked_edge，内部隔离不套用这种表示。局部出入口用 driveway_apron，不能编为连续路肩。任何判断不确定、存在与这些声明相冲突的问题、需要保留未被该结构容纳的道路设施或有尺度估计时，选第2种。
2. 其他情况：直接在同一份回答的 world.road/world.junctions 使用现有 World 字段，不输出 road_observation。这包括路口、弯道、实体隔离、遮挡、第三条车道未确认、道路结构不稳定和未知路侧用途。使用现有 lane_knowledge.kind=minimum|unknown、anchors、questions_to_verify、ambiguities 和 field_evidence 保留事实；不得以已列出两条车道推导 exact=2。lane_knowledge 只填写 kind、count、anchors、evidence_frame_ids；判断理由写入对应 field_evidence，不在 lane_knowledge 中添加 reason。完整格式仍须引用一致；当前回答就包含全部 World 内容，不先返回 other_scene/insufficient_evidence 等待另一个 session。

右侧若有沿道路连续的白色铺装人行道，即使它位于路缘外，也不能只在 curb_only 的 reason 中提一句。新请求的 response_schema 允许 sidewalk 时，Full World 模式须在 world.road.roadside_areas 写独立的 type=sidewalk 记录，包含 road_id、side=right、与最右行驶车道相接的 boundary_left、独立的 boundary_right 和至少两帧直接 evidence_frame_ids；两条边界都在 lane_markings 登记，layout_observation.right_roadside.type 同时选 sidewalk。若两条边界或连续性无法确认，保留 unknown 并提交问题，不猜测。局部车道出入口或停放车辆不能代替整条连续区域。精简 road_observation 当前不能表达这组边界，遇到这类人行道须选 Full World。旧冻结请求没有 sidewalk 枚举时仍以其 response_schema 为准，不自行添加类型。

精简道路模式下，Python从 road_observation 生成 road_001、从右到左的全局 lane ID、标线/区域引用及道路 field_evidence。模型不用重复写这些字段；不要同时给与生成路径重叠的 field_evidence。该模式仅在你已确认完整道路时编译 exact，不接受 minimum 伪装完整。其他设施/环境的具体证据仍由你提交，不从道路容器证据推断。

仅精简 road_observation 模式下，Actor 可用 hypothesized_ordinal_from_right 表示已观察到的初始位置，Python将它转换为当前完整道路的 lane ID；从0开始，不能按画面横坐标或车辆数量判断。Full World 模式直接使用 hypothesized_lane，不输出 hypothesized_ordinal_from_right；该模式已有 roads[*].lane_ids/lane_knowledge.anchors 负责位置映射。保留边界理由和该Actor的初始帧证据。不确定时用 hypothesized_lane=null 并省略 ordinal，不能让脚本猜位置。若精简模式同时给两种位置，必须一致，null也不会被覆盖。road_observation.ego_initial 仅用于真实首帧的ego；ego在较晚时刻才被记录时不沿用该线索，更不能扩展成全窗口不变道。

两种模式均允许省略 contract_version、evidence_epoch，Python填写冻结元数据。有实际 frame_id 的证据优先省略重复 time_s，由 Python 从冻结索引补齐；已有时间必须等于该帧索引真实PTS，脚本不会覆盖错误值。只使用请求 frames 中的 frame_id，不把 global_0004.jpg 等打包文件序号当原始 Frame ID，不引用仅存在于 probe 目录的未提供帧。

questions_to_verify 的 question_id 可省略，由Python按输出顺序生成未占用编号。问题可附 verification_window={"start_s":起点秒数,"end_s":终点秒数}，Python据此生成 critical_intervals 的 question_ids 关联。保留 critical_intervals 数组（没有另列窗口时为[]）；不必重复编写相同问题窗口。若你自行在其他字段引用某个问题，则明确提供其 question_id；脚本不从问题文字猜引用。world_paths、impact_domains、问题内容、证据和resolution仍由你按现有合同填写。已知Actor相关问题必须绑定actor_ids；未知对象不虚构身份。impact_domains=["actor_lane"] 的问题，只绑定一个已知 actor_ids，并优先写 world.actors.<actor_id>.hypothesized_lane 指向该 Actor 自身的车道事实字段；world.actor_hypotheses.<actor_id>.hypothesized_lane 也可，带序号的 world.actor_hypotheses[<序号>].hypothesized_lane 仅在它确实对应同一 Actor 时才会被 Python 安全换算。hypothesized_lane=null 的未知字段本身就是合法询问对象。不得使用 world.road.lanes 等不含该 Actor 车道事实的泛化路径。

只需确认某张画面中的车道或外观时，verification_window 允许 start_s == end_s，例如 {"start_s":10,"end_s":10}。该时刻必须唯一对应问题 evidence 中的一张冻结索引帧；Python 将它编译为独立单帧复核，不扩大到相邻帧，也不让重复的宽时间段覆盖它。静态问题仅限 actor_lane、appearance、road_topology、lane_count、report；跨帧身份、超车、变化过程和时段稳定性需要多帧，不能用单帧题表达。单帧看不清就保留未知。critical_intervals 本身仍只放正长度时段；单帧绑定由编译器保存，不自行填写 world.metadata.single_frame_review。

初始 Full World 的单一道路 highway 中，位置锚点与相邻边界链一致时，Python 会统一整理 lane ID 及其引用；你仍须准确描述物理边界、从右到左的位置和车辆归属，并使同一物理车道的引用一致。缺失或矛盾的观察不会由编号整理补齐。不要把编号大小当成视觉位置证据。

不生成动态速度、运动轨迹或执行默认值。静态道路尺度只有符合共享尺度证据规则时才在 Full World 中提交 estimated；没有尺度依据保留未知，不能猜填米制距离。道路输出不完整是可接受的观察结果；保留问题给既有Batch取证。每条新增证据必须支持其对应事实，不能因为字段必填就猜值。

当冻结请求包含 layout_review_revision=lane-layout-review.v1，单一道路、无路口的 highway Full World 还应填写根 layout_observation（按 response_schema）。分别声明同向数量、最右外边界是否确认、从右到左的同向物理边界链、真实首帧自车两侧边界及0基序号、独立的右侧路侧用途，并分别附理由与帧证据。数量/外边界确认至少两个真实时刻，自车位置引用真实首帧。最右边界未确认时，不把“自车在最右第一道”当确定事实；同向已看清与对向未知分别保留。边界标识用本回答的物理名称，已确认时对应 World lane_markings.marking_id 及 lane 的 left_boundary/right_boundary 引用，不把已列出的 lane ID 当观察依据。unknown/minimum 是合法观察，不要猜填；编排器会安排独立 Batch 查证，缺口不要求用户手动补字段。未带该 revision 的旧冻结请求不得输出新字段。
