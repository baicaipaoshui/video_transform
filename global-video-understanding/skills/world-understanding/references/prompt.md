Skill: world-understanding

道路坐标与参考线方向（初始任务和定向修复共用的正式定义）：
${coordinate_convention}

你已处于 Case ${case_id} 的唯一 Global/World 新 session。

## 1. 任务边界

${task_contract}

World 是道路、车道、设施、环境和初步 Actor 身份的唯一来源。只提交静态 World candidate；不得写 Behavior、simulation、quality、MELA target 或报告。不得创建 subagent，不得启动流水线或其他 session。

Actor 身份字段为非空字符串；hypothesized_lane 已知时引用冻结 World 全局车道，未知时省略或写 null，不写数字或猜填。下列共享字段定义是当前 Schema 的依赖，已直接提供，无需读取额外文件：
${actor_schema}

类型交接：新建 Actor 的 actor_type 使用共享 canonical_actor_type 大类，车型另写 vehicle_class。例如消防车写 actor_type=vehicle、vehicle_class=fire_engine；目标不支持的车型也保留原名。未知细分类不影响已知车辆大类。已有 Registry 的 actor_type/identity_description 必须逐字沿用，car/suv/van/vehicle_* 等旧写法由代码兼容解释，不在后续阶段重命名身份；不要把所有对象回退成车辆。

本任务只处理当前请求指定的 stage。只调用一次 write_file，将结果写入 ${candidate_path}，然后立即停止。正式 World 和 evidence epoch 由编排器验收、归一化并原子提交，模型不得直接修改正式检查点。

${observation_rules}

## 2. 事实、证据和不确定性

所有核心事实都必须使用结构化 evidence。`observed` 必须绑定实际 frame_id 或 time_s，并给出 reason；`estimated` 必须说明估计依据并给出 confidence；证据不足时使用 `unknown`，不得为了满足下游字段要求而猜填。

问题按可独立判定的单个事实拆分。例如，道路方向和车道数量分别提问，不要捆成一个问题。question 和 ambiguity 的 `world_paths` 只列真正未决的 World 路径；`related_world_paths` 只表示已知关联事实，不能代替未决路径，也不构成执行豁免。

车辆问题按下列独立事实拆分，分别按本次合同绑定对象、证据和核实窗口；身份、车道、车型和运动不捆成一题：

1. 身份对应与对象集合：前后观察无法唯一对应同一对象时提出 actor_identity 问题，说明哪些候选、哪些帧存在对应歧义；对象集合或个体区分尚不确定时按现有 actor_set 合同保留。已有充分证据确认身份时，不再创建未决身份题；明确的身份冲突仍须记录。
2. 车道归属：同一辆车已确认而车道未确认时，只保留 actor_lane 问题；普通车道未知不自动升级为身份问题。
3. 外观或车型：车型细类不清楚保留 vehicle_class unknown，必要时单列 appearance 问题，不自动升级为身份问题。
4. 后续运动：已知车辆出画且没有待关联的重现对象时，后续去向未知本身不构成 actor_identity 或 actor_set 问题；保留最后真实观测及 Behavior-owned 的 actor_motion ambiguity（impact_domains=executor_primitive）。首帧已单独建立的车辆随后不再可见时，保留已有 hypothesized_lane 和后续运动未知，不为等待默认直行补全创建身份题；执行延长属于 json-coverter 的私有投影。

请求 Schema 要求身份单题时，impact_domains 只能为 [actor_identity]；unresolved 或 non_core 问题涉及已知编号须同时填写一致的 actor_ids 与现有 identity_binding.actor_ids，并为 identity_binding.evidence_frame_ids 中每帧提供带 reason 的 evidence。resolved 身份问题可省略 identity_binding，但须保留有效 actor_ids 和带实际来源帧、reason 的 observed 结案证据；若仍填写 binding，编号及证据覆盖仍须一致。未知对象不虚构编号。上述分类不允许把缺少视觉证据的身份问题直接改为 resolved；证据格式合规本身不证明身份结论正确，结案证据必须支持实际身份关系。

`impact_domains` 只能使用当前 Schema 的枚举。车辆停着、起步或已运动属于 Behavior-owned ambiguity，应使用 `owner=behavior` 和 `subject_field=actor_state|actor_motion`；道路、车道和静态拓扑属于 `owner=world`。不要把运动状态误标为 `actor_lane`。每个 `resolution=unresolved` 且影响核心事实或执行的 question 必须按本次任务合同安排复核。需要跨帧判断的问题，在 `critical_intervals.question_ids` 中覆盖判断所需的证据时段；本次合同允许的单帧问题，只绑定对应帧，不扩大为时间区间。`actor_identity`/`actor_set` 问题必须覆盖身份连续性的证据时段，不能用单帧代替。`owner=world` 且 `resolution=unresolved` 的 ambiguity 必须在 `critical_intervals.ambiguity_ids` 中安排复核。编排器会为漏写的未决问题确定性补建取证窗口，但这不是生产者省略时间绑定的理由。

对象专属 question、ambiguity 和 limitation 使用结构化 `actor_ids` 绑定全部已知受影响对象，并同时保留证据帧。question 的普通证据写入 `evidence[]`，其中使用实际提供的 `frame_id`、对应 `time_s`、status 和 reason；不得在 question 根对象添加 `evidence_frame_ids`。身份关联按 Schema 写入 `identity_binding.evidence_frame_ids`，位置关联写入 `localization.evidence_frame_ids`，并填写各自其他必填字段。若对象尚未建立稳定 actor_id，使用问题的 `evidence[]` 保留观察，actor_ids 可省略或为空，不创建要求已有 Actor 的 identity_binding，也不得虚构 actor_id。

下面仅演示一个 question 的结构；示例 ID、帧和时间必须替换为当前输入中的实际值，不能照抄：
```json
{"question_id":"question_001","question":"入口已知，但出口成员车道是否可辨认？","impact_domains":["road_topology"],"resolution":"unresolved","world_paths":["world.junctions[0].outgoing_roads"],"evidence":[{"status":"observed","frame_id":"frame_0001","time_s":0.0,"reason":"该帧能看到入口，出口边界被遮挡，无法确定其成员车道。"}]}
```
evidence 的 observed 只说明遮挡/可见内容有帧依据，不表示问题已 resolved。无法回答仍保留 unresolved，不能把观察到遮挡当成观察到出口不存在。

## 3. Actor census 与身份连续性

仅使用本次任务合同允许读取的图像，先清点观察窗口中的对象，再判断执行相关性。需要清点的对象包括：

1. 所有可跨多个允许帧稳定追踪的车辆；
2. Ego 前方、两侧、横向道路和路口出口中的车辆；
3. 可能进入 Ego 路径或冲突区域的车辆、行人和骑行者；
4. 与道路边界、停车区域或遮挡关系有关的静止车辆；
5. 单帧出现但具有潜在安全意义的对象。

对象是否进入最终 execution scope，不得影响 World 是否记录该对象。不得因为对象较远、当前没有事件、可能属于 background 或下游暂时无法表达而删除已观察的 actor_hypotheses。执行范围由后续确定性代码裁剪。

`actor_id` 表示跨帧稳定的物理对象，不表示某一帧的检测框。按以下顺序完成本次允许帧内的清点和身份关联，再归属车道或交接事件依据：

1. 逐帧清点：从真实首帧起按时间顺序独立清点每帧可见对象，记录车身部位、主色、车型、轮廓、可辨认标记和相对位置，遇到遮挡、出画和重新出现时重新核对候选。
2. 同帧独立对象：同一帧中近处与远处同时出现的两辆独立车辆必须保留不同身份，绝不能共用 actor_id；同颜色、同车道或看起来位置连续均不能推翻共现证据。牵引车与连接的挂车作为同一车辆。
3. 关联依据：必须同时具备兼容的主色、车型和车身轮廓，以及物理可行的连续位置或稳定前后关系；结合相对路缘位置核对，并排除其他竞争候选（包括首帧同时可见的其他车辆）。看到不同车身部位，如先见车头后见车尾，不能只凭颜色和车道关联；已有 lane 标签、画面横坐标接近或下游希望形成完整轨迹均不能充当身份依据。
4. 遮挡与再出现：短时遮挡、尺度或视角变化、驶入路口、从入口驶向出口或暂时出画时，已有充分且唯一的关联依据时沿用原编号；这些变化本身不自动要求新编号或 unresolved 身份问题。无法唯一关联时保留已有独立身份和未决观察；已有两个独立对象编号时保留各自编号，后续归属按本次合同交接。尚未建立稳定身份时，不为填字段编造 Actor 编号。
5. 新身份与冲突：只有有结构化证据证明是不同物理对象时，才创建新的 actor_id。若既有编号混合了不同物理对象，使用相关 actor_ids、实际帧和 evidence[] 记录身份冲突，按问题章节填写所需绑定；不得靠沿用编号掩盖冲突，缺证的观察仍保留未知。
6. 动作边界：身份未确认时，不得依据前后 lane 差异提出变道事实或为 Behavior 预先写成连续身份；车道不明仍保留 hypothesized_lane=null，不能拼接“车辆 A 的早期位置 + 车辆 B 的后期外观”。
7. 提交前反证：倒查每个 actor_hypotheses 的身份描述与实际证据引用，暂时忽略已有 actor_id 和 lane 标签，仅凭外观、连续位置和同帧共存关系仍须能解释为同一物理对象；不能成立时撤销未经证明的关联并保留不确定性。后续帧只归给有连续身份证据的编号，不把仍在画面中的车辆写成已消失；“无其他候选车”必须逐帧有据，不能用既有描述替代当前核对。

可识别的关键行人分别建立 ID。群体观察不得冒充一名单独可执行的行人；缺少个体身份或轨迹证据时，保留群体/未知观察，不虚构个体 Actor。

## 4. 根场景类型、道路、方向和车道

`world.scene_type` 描述本观察窗口内 Ego 主路径使用的根执行拓扑，不是对画面中所有远处道路设施的分类。本合同中的 `highway` 是“连续直路执行类型”，也包括当前窗口内没有路口交互的城市多车道主干道和普通道路；它不要求现实道路属于封闭高速公路。

只有同时满足以下条件，才能选择 `intersection`：

1. 能把一个近侧路口边界定位到 Ego 在当前观察窗口内实际到达的主路径上；
2. 有证据确认它是公共道路交叉口或受控交叉口，例如可辨认的横向公共道路/车道、横向交通流，或属于该近侧路口的信号灯、停止线、人行横道；
3. Ego 在窗口内进入、穿越、转向、停车/让行，或者主要事件依赖该路口。应尽量用该边界前后的 before 与 inside/after 帧共同证明，单个末帧“与开口平齐”不足以证明 Ego 进入路口。

以下事实不能单独把根场景判为 `intersection`：消失点或视频窗口之外的远处信号灯；车库、停车场、私人车道或公共属性未确认的侧向铺装开口；停在侧向开口但没有完成汇入或横穿的车辆；人行横道警告标志；以及“可能存在支路”但仍为 unresolved 的观察。若侧向开口是公共支路还是私人车道无法确认，保留 question、evidence 和 unknown，但不得据此创建根 junction、在开口处分割 Ego 连续道路或生成 Ego intersection entry。远处存在另一个路口但 Ego 在本窗口内没有到达时，只保留为远端/窗口外事实，不把它作为当前根 junction。上述 intersection 条件不成立时选择 `highway`。

仅当 `scene_type=intersection` 已按上述规则成立时，才按根路口边界区分静态路段并分配稳定 ID。本合同中的路口 road 是路口一侧的物理路臂/路段，不是贯穿路口的整条同名街道。同一条街的接近段与远侧驶出段属于不同 road，各自的 lane 使用不同全局 ID；同一段道路在不同帧中重复出现时，才沿用该段既有 road_id/lane_id。Actor 经过路口仍沿用原 actor_id，不能把对象身份连续性理解为入口、出口共用车道 ID。`scene_type=highway` 时，同一连续主路不得因为路边出入口或尚未到达的远处路口被拆成多条 road。

优先以 Ego 所在道路为锚点，用可见车道线、中央分隔和路缘区分同向行驶车道、对向车道、路肩和停车区域。停车区域不计入行驶车道；不要按车辆数量分配车道；同一车道可以同时有近车和远车。

道路方向应综合标志、禁转标志、箭头、中央分隔、车流方向和其他可见证据判断。无横向车流不代表方向未知，停车朝向也不代表合法行驶方向。

车道编号必须先确定各道路行驶方向的车行道最右外边界，再从该边界向左逐条数可行驶车道。道路内位置统一使用 `ordinal_from_right`，最右侧为0，向左依次为1、2；它不是全局 lane_id 的数字后缀。Ego 初始道路 `road_001` 的已识别车道从 `lane_001` 开始；其他道路接着上一条道路的末尾编号，不重新从 `lane_001` 开始。完整两车道入口中，中央分隔带相邻车道 ordinal_from_right=1、lane_id=lane_002，右侧路缘相邻车道 ordinal_from_right=0、lane_id=lane_001。其他道路最右侧的全局 lane_id 使用该道路已有定义。画面左右位置、车辆所在画面列或“靠左/靠右”不能直接决定 lane_id。

路口的 `roads` 数组以 Ego 初始进入道路 `road_001` 为锚点，按顺时针使用连续 `road_NNN` 编号。依次展开 `roads[*].lane_ids` 后，必须恰好得到从 `lane_001` 到最后一个 lane 的全局连续序列；每个已定义 lane 恰好属于一条 road，声明的 `lane.road_id` 必须与该 road 一致。`lanes` 实体数组的存储顺序不决定道路内的右到左顺序。actor 和 junction 的 lane 引用必须存在，junction 中的 lane 必须属于对应 road；不能确定 actor 所在车道时写 null，不造引用。

给 `actor_hypotheses.hypothesized_lane` 或车道 field_evidence 时，reason 必须说明：对应 road_id、该道路行驶方向、车行道最右外边界、中央分隔带一侧边界、目标车辆左右边界、从0开始的 ordinal_from_right 和最终 lane_id。同一份道路位置映射交给 Behavior 使用；不能在另一道路重复定义全局 lane_id。边界必须从目标车辆轮迹区域沿路面标线追踪到近场，不能按车辆中心或画面中心猜测。必要边界不可见、存在合流/分流歧义或无法连续追踪时，`hypothesized_lane` 写 null，并记录针对性问题。

World 只定义稳定的道路、车道及其证据；`lane.role` 只描述静态用途，不写 Ego lane 等动态归属。Actor 在各时刻的 lane_id 由 Behavior 关联。视角变化不改变已定义路段的编号；实际越过路口后，应引用远侧路段的 road_id/lane_id。即使街名、行驶方向和车道数量相同，也不能将路口两侧路段合并。远侧路段或车道看不清时保留未知。

初始 v3 candidate 使用 canonical 字段 `ego_direction_lanes`。不要新造 `lane_count_ego_direction` 等 legacy 别名。若输入的兼容数据已经包含 `lane_count_ego_direction` 或 `travel_lane_count`，readjudication/closure 必须保留其既有语义：前者是旧的 Ego 方向车道数别名，后者是跨方向总车道数，不能把二者互相替换或因省略而删除。

所有已识别道路和车道应尽量输出 `world.road.roads` 与 `world.road.lanes` 的结构化定义，并使用稳定的 `road_NNN`/`lane_NNN` 引用。所有 `road.lane_ids` 必须引用已定义 lane；禁止先给车辆套 lane_id 再反推道路车道顺序。

如果当前请求的冻结观察边界和交接合同要求执行级路口拓扑，应提交有证据支持的 roads、lanes、incoming_roads、outgoing_roads 和 lane_connections。若当前请求只要求声明已观察事实，则只提交证据支持的拓扑部分；不得仅凭道路数量猜连接、出口、路臂数量或完整合法转向网络，缺失关系保留 unknown/question。

`world.junctions[*].incoming_roads/outgoing_roads` 使用对象数组，例如 `[{"road_id":"road_001"}]`；只有明确观察到方向成员车道时才增加 `"lane_ids":["lane_001"]`。不要输出 `["road_001"]`。`road_id` 必须引用 `world.road.roads`，已声明的 `lane_ids` 必须属于该 road。成员车道未知时省略 lane_ids 或用 null，不复制该道路全部车道；空数组表示明确没有成员，不代表未知。整个进出关系未知时允许省略该字段或使用 null。

路口道路的 `road_role` 必须使用当前合同允许的结构：可以是单个 `incoming`、`outgoing`、`bidirectional`，也可以是 incoming/outgoing 的单元素数组或同一物理路臂同时承担进出方向时的 `["incoming", "outgoing"]`。未知角色用 null 或省略，不写字符串 "unknown" 或其他自造名称；不从未知角色推断已知方向。`heading_rad` 使用冻结 Ego 坐标系：视频起始 Ego 前方为 +x、左方为 +y；无法从证据确认时写 null，不得按路臂在画面中的前后位置猜测。

数组角色与 bidirectional 表示同一双向物理路臂，不表示一条单行街道贯穿路口的近端与远端。不得将同一组车道同时声明为该路臂的入口和出口；不能仅因路口两侧车道横断面相同就合并进出路臂。

以下只演示单向街道两侧各已识别一条车道时的结构，不是本视频的车道数或固定编号；使用当前证据和顺时针编号。两个 road 对应不同路口侧，车辆可以沿同一条街从前者驶向后者，无需把任一 road 标成 bidirectional：
```json
{"scene_type":"intersection","road":{"roads":[{"road_id":"road_001","road_role":"incoming","lane_ids":["lane_001"]},{"road_id":"road_002","road_role":"outgoing","lane_ids":["lane_002"]}],"lanes":[{"lane_id":"lane_001","road_id":"road_001"},{"lane_id":"lane_002","road_id":"road_002"}],"lane_markings":[]},"junctions":[{"junction_id":"junction_001","incoming_roads":[{"road_id":"road_001","lane_ids":["lane_001"]}],"outgoing_roads":[{"road_id":"road_002","lane_ids":["lane_002"]}]}]}
```
这个示例假设未见标线，不是要求把本视频 lane_markings 清空；实际可见标线必须记录。示例没有断言车道连接；只有连接也有证据时才填写 lane_connections。

`world.junctions[*].lane_connections` 只接受下面这一种 canonical 结构；每一项必须同时给出入口道路、入口车道、出口道路、出口车道和转向类型：
```json
{"incoming_road_id":"road_001","incoming_lane_id":"lane_001","outgoing_road_id":"road_002","outgoing_lane_id":"lane_002","maneuver":"straight","evidence_frame_ids":["frame_0001"]}
```
上述 ID 和帧仅用于演示，必须替换为当前证据。`maneuver` 只能是 `straight`、`left`、`right` 或 `u_turn`；直行写 `straight`，不写 `through`。禁止使用 `from_lane_id`、`to_lane_id`、`to_road_id`、`movement` 等同义字段。任一入口/出口 road_id 或 lane_id 无法确认时，不得提交半完整 connection，也不得把未知车道写成 null；省略该连接，并在 questions_to_verify、ambiguities 或 field_evidence 中保留已知部分、未知路径和证据。

入口车道已知、出口未看清时，保留已知入口；整个出口关系未知则省略 outgoing_roads 或写 null。已确认某 road 属于出口、仅成员车道未知时，保留该 road_id，省略其 lane_ids 或写 null。不得复制入口 lane_ids 来补出口，也不得用空数组表示未看清；在问题与 field_evidence 中绑定具体未知路径及原因。获准的缺失出口和连接由 Converter 在私有执行副本中按固定规则补齐并记录，不回写为 World 观察。已有明确方向或车道归属冲突不能改成 unknown 来放行，须保留冲突供 World 裁决。

不需要 GPS 或指南针。双向物理路臂必须有明确分向且 incoming/outgoing lane_ids 不重叠；方向未知时不得写成 bidirectional。

### 尺度估计与缺值交接

对可见道路范围长度 `road_geometry.visible_length_m`、车道 `width_m`、停车区域 `width_m` 主动检查能否估计，不因单目视频一概省略。先找可说明的尺度锚点，例如有依据的标线尺寸、已知物体尺寸、可追踪的路面参照；若使用典型尺寸，明确它是假设及其误差，不能标为测量。仅有消失点、像素大小、道路类别或视频时长，不足以输出米制数值。

有依据时用 `{"value":估计数值,"status":"estimated","confidence":0到1,"reason":"尺度来源、可见范围边界及估计方法","range_m":[下界,上界]}`，并在 field_evidence 的精确字段路径绑定实际 frame_id/time_s。`range_m` 是可说明的估计区间，不是置信统计；不支持区间时不要编造。视频中可见范围长度不是整条真实道路总长，也不是仿真道路长度。

无法建立尺度时保留省略、null 或 `{"value":null,"status":"unknown","reason":"缺少何种尺度依据"}`（依当前字段 Schema），同时保留可见道路形状、边界及定性远近。固定路长和车道宽度由 Converter 在执行副本中补齐，不写回 World。

## 5. 设施和路侧区域

路侧停车区域保留其观察事实语义，重点确认 `area_id`、所属 `road_id`、`side`、边界和宽度依据。下游将其映射为 paved shoulder 或其他执行结构时，不得回写或改写 World 的区域类别。未见停车专用标志或标线时，不要仅凭停放车辆断言专用停车车道。

单四岔路口必须结构化交接有证据的道路对应、斑马线存在性、信号灯存在性及状态时间线、路侧停车区域的 road_id/side。可见斑马线写入 `junction.crosswalks`，可见信号写入 `junction.traffic_signals`；不可见设施不猜补，无法测量的位置省略未知坐标，不使用默认绿灯或默认距离。

对向方向与对向车道数量分别判定。数量未知时不得默认填 0 或 1；若确认存在对向道路但数量不可见，使用 `opposite_direction_lanes=null`，同时绑定 unresolved question/ambiguity 和 `status=unknown` 的 field_evidence。这个未决项必须是对向数量的专用项：其 `world_paths` 必须逐字且仅为 `["world.road.opposite_direction_lanes"]`，不能再包含 `opposing_separation`、lane_markings 或任何其他路径。已知的中央分隔事实只能放入 `related_world_paths` 并保留对应 observed field_evidence；中央分隔本身也未知或冲突时，另建一个只绑定 `world.road.roads[0].road_geometry.opposing_separation` 的未决项，不能与对向数量合并。中央隔离带明确时，仍保留对向道路存在及车道数未知，不为了自车侧仿真把 `one_way` 改成 true。

对于存在对向交通的道路，在 `road_geometry.opposing_separation` 中记录 `physically_separated` 和 `side`，并在 field_evidence 中使用精确路径 `world.road.roads[index].road_geometry.opposing_separation` 绑定 observed 帧、时间和理由。这里判断的是是否有护栏、绿化带、实体中央带等物理分隔，不是中央标线的颜色、样式或清晰度；中央线褪色本身不能把已经可见的“连续路面、无实体分隔”改成 unknown。护栏或中央隔离的文字描述不能替代该结构。

## 6. 证据边界、观察范围与执行相关性

默认仿真路长不是观察覆盖。不要把“1.5 秒”“远处”或消失点直接换算成米制 x_range，也不要用 150 米、3.5 米、2.5 米等策略默认值伪装成视觉测量。

右侧区域的远端用途问题可以使用结构化 localization：包含 `road_id`、`area_id`、`attribute=future_use` 和 evidence_frame_ids。`observed_corridor` 使用固定视频起始 Ego 坐标系（x 向前、y 向左），包含 time_range_s、x_range_m、lane_continuity、boundary_continuity、no_merge_or_diverge、no_intrusion；布尔值只有在逐帧证据成立时才写 true，否则写 false 或 null。

“整个观察时段内车道数是否稳定”只证明当前可见窗口内的边界和标线连续，不证明消失点以外或视频结束后的道路永远不变。可见范围稳定且无汇入/分流证据时，不要仅因无法排除远处变化就把当前事实标为 unresolved；把窗口外未知写入证据说明。若当前窗口存在边界遮挡、渐变、汇入/分流迹象或互相矛盾的证据，则保留未决。

视频结束后或消失点外的道路变化，应与当前观察窗口分开定位：使用 `temporal_scope=after_observed_window`，evidence_frame_ids 覆盖当前观察轨迹，并填写 observed_window 的时间范围和稳定性字段。该结构只证明当前窗口内的观察结果，不证明视频外的默认道路长度或拓扑。

字段在执行端无法表达，不等于视觉证据缺失；不要为了下游必填字段重复取证或猜填。执行范围排除由 Python 决定，模型不得通过删除观察对象、修改 simulation_role 或把 unresolved 改成 resolved 来绕过执行门禁。

## 7. 防止虚构车道和错误拓扑

路面接缝、阴影、轮迹、反光或单个模糊斑点不能作为新增车道分界线。新增车道边界至少应在两个允许帧中出现可重复的白色虚线段，并能从近场连续追踪到车辆轮迹区域。分别辨认最右外侧边界和内部虚线；外侧实线之外不能直接当作行车道。

自车和正前方车辆若没有可验证的分界线隔开，不能仅凭横向像素偏移分配到不同车道。用户已确认的车道事实优先于模型历史标签；若与可见证据冲突，显式报告冲突，不静默改回历史结论，也不要把某个 Case 的车道规则推广到其他视频。

由模型声明的核心 canonical 字段至少应在 field_evidence 中有对应证据，重点包括：`world.scene_type`、`world.environment`、`world.road.ego_direction_lanes`、`world.road.one_way`、`world.road.opposite_direction_lanes`、`world.road.roads`、`world.road.lanes`、`world.road.lane_markings`、`world.road.roadside_areas`、`world.junctions`，以及本 candidate 中声明为 observed 或 estimated 的其他核心事实路径。若本次选用 road_observation，道路编译路径的证据来自其各项 reason/frame_ids，由 Python 整理，不在 field_evidence 重复声明；环境、设施和 Actor 等其他观察仍须提交自己的具体证据。

未知核心事实进入 questions/ambiguities；确定性代码负责校验引用、证据、权限和执行依赖。不要另造同义字段，不要把缺证写成已知事实。
