Skill: behavior-understanding
你已处于 Case ${case_id} 唯一 Behavior 新 session。
默认读取冻结的 ${world_path}、${registry_path}、${batch_instruction} 和 ${schema_path}。Census 裁定材料按本请求后续的冻结交接说明读取；需要核查时按来源 ID 定向回查。
Batch 列表为空表示没有 Batch；不得猜测其他 Batch 路径或寻找目录。
若最终 World.metadata.lane_layout_review.adjudication 存在，lane_id_map 是旧物理车道到当前车道的已验收映射：旧 Batch 的车道标签属于 source_epoch，不能直接当作当前 lane_id。身份保持不变，先看原帧，再按当前 World 的边界和 Registry 关联；地图修正不等于动态变道证据。
Actor 沿用 Registry 的 actor_id、actor_type、identity_description；首次出现帧不需要重新声明。轨迹 lane_id 与 World 的初步 hypothesized_lane 是不同时刻的观察，可以变化。下列共享字段定义是当前 Schema 的依赖，已直接提供，无需读取额外文件：
${actor_schema}

类型交接：新建 Actor 的 actor_type 使用共享 canonical_actor_type 大类，车型另写 vehicle_class。例如消防车写 actor_type=vehicle、vehicle_class=fire_engine；目标不支持的车型也保留原名。未知细分类不影响已知车辆大类。已有 Registry 的 actor_type/identity_description 必须逐字沿用，car/suv/van/vehicle_* 等旧写法由代码兼容解释，不在后续阶段重命名身份；不要把所有对象回退成车辆。
根字段清单（由当前 Schema 自动生成）：
${output_fields}
model_required 中每个根字段必须输出；model_optional 仅按 Schema 和当前任务的启用条件输出，不新增根字段。nested_contracts 是同一 Schema 派生的嵌套字段约束摘要，不代替完整 Schema。contract_version 使用当前 Schema 的 const 值；world_evidence_epoch 必须复制冻结 World 的 evidence_epoch，不自行递增。启用视频保真合同时，video_fidelity 虽为基础 Schema 的可选字段，本任务仍必须输出。
actors 必须显式包含 actor_id=ego 及其车型、颜色和 trajectory；每个 actor 的必填字段、simulation_role 允许值及 is_static 类型遵守上述 nested_contracts；initial_state由代码从轨迹首点派生，不重复填写。
首点保留有证据的lane_id、position_ego_m.x/y和speed_mps；无法确定lane_id可为null，其他缺失按Schema省略或标unknown，在semantic_limitations说明。观察位置与执行估计不一致时如实保留冲突，不为通过检查改写坐标。executor_primitives只填写有证据的事件动作。
取证顺序：先确定ego及关键参与者的起始车道，再关联各时刻的运动方向、车道变化与事件边界。lane_id只能引用World已有车道；用标线和相对位置关联，不把图像左/右侧直接当车道编号，也不为填满字段猜车道。
车道判断必须执行“边界→道路归属→道路内序号→World全局lane_id”，不得从World或Batch已有Actor车道标签开始反推证据。先按所在道路的行驶方向确定车行道最右外边界和中央分隔带一侧边界，再从右向左计数；ordinal_from_right统一从0开始，最右侧为0，向左为1、2。随后查冻结World对应road_id的lane_ids及lane_knowledge.anchors映射到全局lane_id，不重新编号。只有完整road_001入口的最右车道才是lane_001；例如road_003最右车道定义为lane_005时，ordinal_from_right=0对应lane_005。只有完整车道清单才能直接按数组位置查询；部分清单依赖已知anchors，未识别的车道lane_id为null。画面位置不能代替边界证据。
ego及每个关键参与者的trajectory.detail必须在对应frame_id下明确写出“road_id、左边界、右边界、从0开始的ordinal_from_right、lane_id”；禁止只写“左车道/右车道”或“车辆在画面左/右”。若车辆左侧为中央分隔带旁实线、右侧为通往另一可行驶车道的虚线，则不能判为ordinal_from_right=0。ego与直接前车若位于同一道路的同一对边界之间，必须使用相同lane_id；若结论不同，先视为冲突并将不确定项置null。任一边界无法从车辆轮迹区域连续关联时lane_id写null，不使用车辆类别、颜色或画面中心补猜。
自车车道须依据两侧边界、中央分隔及前车相对位置独立关联；World的hypothesized_lane、lane.position和identity_description中的车道文字都只是待核假设，不能替代当前帧证据。重点保留碰撞相关对象、横穿行人及路口冲突车辆，不能以background标签消除其影响。
冻结World中的未决问题是观察上下文，不要求你修订World或确认不可见事实；如影响当前路径或事件，在semantic_limitations保留缺口。车况仅描述可见的停走、减速、失控、受损等现象，不推断不可见的机械故障。
同一道路上的变道需要跨越车道边界的时序证据；通过路口后引用另一道路的车道不自动等于lane_change，应核对World已有连接与实际路径。完全遮挡期间不是新的车道观测：不得仅因Actor被其他车辆遮挡就为该时刻补一个lane_id=null的trajectory点；保留Actor及遮挡前最后一个真实可见点，后续真实状态在semantic_limitations中说明未知。若遮挡后重新出现，只在真实可见帧新增trajectory点；没有跨越边界证据时不补造变道或转向。
lane_change 的时间边界以该Actor的trajectory为唯一来源，不得根据inside/trigger帧另猜一份时间：start是进入连续null过渡段前最后一个明确source lane点的time_s，end是该过渡段后第一个明确target lane点的time_s。例如lane_003@1.5→null@3.0/4.5/6.0→lane_002@7.5必须写time_range_s=[1.5,7.5]；明显骑跨帧4.5只可作为trigger_time_s。event.evidence必须复用这两个轨迹边界帧核对actor_id/frame_id/time_s/lane_id，detail仅解释图像依据，不能覆盖结构化轨迹。

`lane_change` 是身份连续性成立后的二阶结论。Registry 的 actor_id、identity_description 和 hypothesized_lane 是冻结输入中的待核假设，不证明 before、inside、after 三段属于同一物理车辆。每个拟生成的 lane_change 必须先通过以下身份门禁：

1. before、inside、after 证据中的车辆主色、车型和车身轮廓兼容；
2. 相邻证据帧中的位置变化、纵向顺序和遮挡关系物理连续；
3. before 与 after 对象没有在任一允许帧中作为两个独立车辆同时出现；同帧共存即证明不是同一 actor；
4. inside 帧确实显示通过前两项确认的同一车辆跨越同一条可追踪车道边界，不能把另一辆车靠近边界当成该 Actor 的 inside 见证；
5. 不存在另一辆外观不同且时空关系更合理的车辆，可以分别解释所谓 before 或 after 轨迹。

任一身份门禁不能确认时，不得生成 lane_change event 或对应 executor primitive，不得把两辆车的轨迹段拼接；相关 lane_id 写 null 或保留各自已有 Actor，并以 actor_identity semantic limitation 结构化绑定受影响 actor_ids。提交前对每个 lane_change 做反证检查：暂时忽略 actor_id 和 lane 标签，仅凭车辆外观、连续位置与同帧共存关系，before→inside→after 仍必须唯一指向同一车辆，否则撤销该事件。
对确认进入路口的每个participant（包括ego，如适用）按同一Actor的时序证据分成approach、junction、departure三段，阶段与证据说明写入detail。已确认道路/车道位置时，trajectory点可同时给road_id、lane_id及ordinal_from_right；road_id必须拥有对应lane_id。进入路口内部时lane_id为null，road_id及ordinal_from_right也可为null；不能继续复制入口归属。不新增region、junction或道路拓扑结构。缺证保留未知，不为了填满字段补猜。
  - approach：在detail中按World的road_id原样说明从哪条入口道路进入、入口车道及其判定依据；保留最后一个仍明确位于入口车道内的证据帧，使用该帧的lane_id（无法确认则为null并记录局限）。
  - junction：保留第一帧明确进入路口内部及必要的内部连续证据；这些点的lane_id必须为null，不得继续复制入口lane_id冒充路口内部车道。若进入边界或内部路径不可见，保留unknown/limitation，不按典型路径补点。
  - departure：保留第一帧明确进入出口道路的证据；在detail中按World的road_id原样说明驶向哪条出口道路及出口车道，只有出口车道有边界证据时才填写对应lane_id，否则为null并说明原因。
  三段至少要能由前后边界帧区分；可见证据不足时宁可缺段并保留semantic_limitation，也不能把入口车道延伸穿过路口。若生成intersection_entry/vehicle_intersection_entry等事件，event.evidence必须复用同一Actor的before→inside/after边界帧，不能只在detail中声称已进入。入口最右车道road_001/lane_001/ordinal_from_right=0→路口内部lane_id=null→出口最右车道road_003/lane_005/ordinal_from_right=0可表示直行同序延续，具体ID必须以冻结World为准，不自动生成lane_change；只有同一道路内能以连续证据证明跨越可追踪车道边界，才记录lane_change及其前后证据。
trajectory 的数组、非空和点结构约束遵守上述 nested_contracts；叙述文本放 identity_description 或 point.detail，严禁把 trajectory 写成字符串；不得重新输出roads、lanes、junctions或环境定义，轨迹点的road_id/lane_id只是冻结World引用。
ego 的 trajectory 必须包含所给证据索引中时间最早和最晚的真实帧，time_s 原样使用索引时间；末帧状态不确定时保留 unknown 和原因，不能省略末帧或用媒体结束时长伪造一帧。其他车辆保留其首末可见证据；完全遮挡或出画后不再追加观察trajectory点，也不得删除Registry中的Actor。可在最后可见点detail和现有semantic_limitations.description中记录遮挡对象、最后可见时间和最后可见lane_id，但不得把执行延续写成视频观察。
executor primitive按Schema表达已有证据支持的执行值，不为凑齐lane/position/speed补值；registry identity_description/actor_type逐字保留。
自车外观不可见属于 appearance，不属于基本actor_identity未知；仍保留unknown来源。
群体 actor 保留原身份；有证据证明仅为路侧背景时可设 background，并明确群体代表位置的局限，禁止虚构个体轨迹。
background 不等于静止；不可因群体或背景身份把已观察到的行人运动改成 observed speed=0。
影响参与者、车道、事件或执行的局限必须保留核心 impact_domains，不得改名为 background_detail 等来绕过门禁。
每项semantic_limitation必须如实声明execution_effect；核心事件方向、参与者、路径证据缺失时保持未决。
可行集合与发布授权由确定性代码验证，模型不能以自己的可行声明或改标签消除核心不确定性。
World 中 owner=behavior 的 ambiguity 必须在 challenge_resolutions 用同一 ambiguity_id 作为 challenge_id 明确裁决，并让 actors/trajectory/is_static 与裁决一致。
已确认事件使用resolution=resolved；证据不足且决定不生成该事件时，使用resolution=resolved_no_event、execution_effect=exclude_unconfirmed_event，并保留decision/rationale/consistency/evidence_frame_ids；不得把‘事件未确认’写成‘执行裁决未完成’。
未再次识别、遮挡、出画不证明事件完成；信号灯状态不证明某Actor违反信号。
pedestrian_crossing/cross_roadway必须有同一actor_id和boundary_id的两个不同时刻正向观测；event.evidence逐项写status=observed、actor_id、frame_id、time_s、boundary_id、boundary_state=before|inside|after，必须实际证明before→inside/after或inside→after。
单帧疑似行人不得升级为横穿事件、补写典型速度或外推终点。
position_ego_m使用视频起始时刻冻结的自车坐标系，所有时刻共用同一原点；不是每帧随自车移动的坐标系。
向前为x、向左为y；行驶中的ego不能每帧都写(0,0)。
时间使用原视频秒数，速度估计保留estimated证据。
先描述身份、车道、前后关系及车距稳定/扩大/缩小；没有测速、标定或可说明的尺度依据时，speed_mps写status=unknown、value=null，未知position_ego_m分量省略，不根据典型道路车速填数。相对当前自车的距离变化放detail，不能写成固定起始坐标系中的位移；自车前进时前车保持相对距离，不意味着前车在固定坐标系中几乎不动。
定性观察足够而绝对数值未知，可以提交观察candidate并保留局限。普通 v3 由 Converter 在私有执行副本补齐剩余缺值；只有显式复核预览沿用其独立策略。本阶段不填仿真假设。

身份已经确认而车型、车道或后续运动未知时，semantic_limitations 分别使用 appearance、actor_lane、actor_motion 等对应域，不附加 actor_identity。车辆离开视野后保留最后真实 trajectory 点；没有身份关联争议时不因此阻断身份。已知车道的直路车辆可由 Converter 记录执行延长，本阶段不补造 frame_id 或观察轨迹；所有车道点均未知则如实保留，不把道路第一条车道当观察值。

运动估计应主动尝试，不因单目视频一律写 unknown。按真实帧时间追踪同一对象，先记录车距稳定/扩大/缩小、停走及加减速趋势，再检查尺度锚点与连续采样是否足够。可用 World 已有的有依据尺度估计、可追踪静态路面参照或已提供的测速信息；如引用估计尺度，说明其来源和误差传播。不声称已计算光流、标定或测速，除非输入确实提供了这些结果。

有依据的 speed_mps 写 {"value":数值,"status":"estimated","confidence":0到1,"reason":"尺度来源、使用的帧与时间差、推导方法","range_mps":[下界,上界]}，每点绑定真实 frame_id/time_s。未知速度写 status=unknown/value=null；若多个点共享同一原因，可不逐点重复 reason，但必须有且只有一条 semantic_limitation 以 actor_ids 精确绑定受影响 Actor、以 impact_domains 包含 speed，并在 description 说明原因；局部 reason 优先于共享 limitation。不要给 30 或 80 km/h 作为视频估计。position_ego_m 仍是起始 Ego 固定坐标系，未知分量省略；坐标的 estimated 依据写 point.evidence/detail，不把当前车距当固定坐标位移。

自车与前车的距离 d(t) 变化只支持相对速度 Δd/Δt；至少三个可靠的米制距离采样及对应时间才可尝试相对加速度。它们分别是 v_front-v_ego、a_front-a_ego，不能直接当自车或前车的绝对值。只有还知道其中一方的运动，才可推导另一方。前车减速但自车同步减速时，车距可以不变。无尺度时仍可保留相对趋势，禁止据此猜绝对加速度。

若能估计绝对速度时序，优先输出可信的 speed_mps 采样；相邻采样的平均加速度由代码按 (v1-v0)/(t1-t0) 确定性推导，不能仅凭距离缩短写 ego 加速。加速度趋势、区间、对象关系及依据可放 point.detail，缺证时说明未知。速度与位移必须使用同一坐标系并大致满足时间积分，差异超过估计范围时保留冲突，不反改观察来匹配仿真。
simulation_role表示是否需要进入本次可执行的自车运动场景：直接前车、冲突或避让对象为participant；正常沿对向侧通过、全窗未侵入自车同向车道且不参与任何事件的对向车辆，以及始终静止在车行道外且已排除汇入事件的路侧车辆，应为background，并继续保留逐帧观察，background不等于静止。只因距离近不等于与自车交互；但越线、横穿、借道、掉头、碰撞风险或是否侵入自车路径无法判断时，不能仅为放行改成background，应保留participant及相应缺口。
新 Case 若要裁剪运动中的背景车辆，必须给该 Actor 的 direction_evidence={status:"observed",value:"opposite_to_ego",frame_ids:[...],reason:"..."}；frame_ids 覆盖该 Actor 每个 trajectory 样本，reason 说明车辆朝向及其所在的对向走廊。不得从 identity_description 的文字、background 标签或“未参与事件”推断对向。无法逐帧确认对向，或车辆在自车同向车道行驶时，保留该车辆及相应不确定性；静止路侧车辆可保留 background，但不会因背景标签被裁剪。
若物理隔离的对向交通仅作为整体观察保留、没有创建可稳定识别的 actor，相关 semantic_limitation 必须设置 scope_binding={"kind":"physically_separated_opposing_observations","world_paths":["world.road.opposite_direction_lanes"],"representation":"observation_only","affected_event_ids":[]}。只有 World 有逐帧物理隔离证据、当前无跨向事件且自车侧 participant 全部留在自车道路时才可使用；同向遗漏对象、未隔离对向交通、路口、借道、掉头或碰撞对象禁止使用该绑定。已有 background actor 继续逐个输出，不使用 observation_only 代替稳定 Actor。
若自车进入路口，提供type=intersection_entry的事件primitive及trigger_time_s，依据当前帧时间证据，不能把整个通过区间起点当进入时刻。
该primitive必须用event_id关联事件；intersection_entry/ego_intersection_entry/vehicle_intersection_entry事件同样必须有actor绑定的边界前后两帧证据，time_range_s=[最后before时刻,最早inside或after时刻]。
trigger_time_s必须落在此区间；路牌可见、绿灯和遮挡均不能证明进入或完成通过。
读取 ${frames_path} 并查看这些边界帧，时间和frame_id必须匹配本Case索引。
该索引合并全局与已接纳Batch的帧，variants保留同源图像的路径和分辨率变体；优先读取相关边界或细节图，不重新浏览所有图片。批次只提供局部观察，事件时间和身份由本阶段跨批统一。
直路中对自车、前车及碰撞相关车辆，定向核对中央护栏与车道分界线两侧的归属；World/Batch的车辆车道描述仅是假设。必须优先查看靠近自车的近场标线，再沿同一边界向远处追踪至车辆轮迹，不能从消失点或车辆屏幕横坐标开始判断。用trajectory.detail和frame_id记录完整边界链；车道与y同时自洽不证明识别正确。同车道不要求y相等，自车原点不等于车道中心。
不得补猜 world。
actor_id必须全视频稳定；若已确认同一actor_id关联两个不同对象，必须报告actor_identity冲突并标blocks_execution，不能改background或删帧掩盖。只有已独立确定的稳定物理对象标识才可写trajectory.object_id，不得把逐帧检测编号当object_id。
不得创建 subagent。
面向MELA映射取证：停车区域存在不证明某车静止、所在车道或米制位置，仍按同一Actor的时序证据判断。上游保留speed_mps和冻结坐标系，不生成MELA reference_name或改用km/h；目标编码及近似归代码。执行缺字段时保留实际证据和缺口，不为完成映射编造速度、起点或事件边界。
路侧静态车辆必须结构化交接已有证据支持的停车位置：能关联冻结 World 停车区域时，在 actor 写 roadside_area_id；只确定道路及侧别时，在首个轨迹点写 road_id，在 actor 写 parking_side=left|right。左右以该道路方向为准，证据放真实帧对应的 trajectory.detail。不能仅在 identity_description/detail 中描述右侧停车而省略已知结构化侧别；不确定时省略或写 null，不创建 World 区域、不猜侧别。普通行驶车道内临停车辆保留其 lane_id，不因 is_static 就改为路侧停车。
只调用一次 write_file 写 ${candidate_path} 后停止。

防止虚构车道：路面接缝、阴影、轮迹、反光或单个模糊斑点不能作为新增车道分界线。必须在至少两个允许帧中定位可重复的白色虚线段，并沿近场到车辆轮迹连续追踪。分别辨认最右外侧实线与内部虚线，外侧实线之外不能直接当行车道。自车和正前方车辆若没有可验证的分界线隔开，不能仅凭横向像素偏移分配到不同车道；边界不清就报告未决。
用户已确认的车道事实优先于模型历史标签；World 从 case.json.parameters.confirmed_lane_assignments 读取，Behavior 使用请求附带约束。若与可见证据冲突，显式报告冲突，不静默改回历史结论；不要把某个案例的第二车道规则推广到其他视频。

外观必须结构化交接：identity_description 已明确的车辆主车身颜色和车型，同时填写 actor.color 和 actor.vehicle_class（例如白色厢式车 white/van，红色小车 red/compact_car）。不要只在描述文字里写颜色，否则转换器无法区分车辆。车身主色与车门标识颜色分开；不可见的自车颜色保留 unknown，不能因生成需要推断。
actor.color 若为 estimated，必须给出 0..1 的数字 confidence 和非空 reason；无法给出可信置信度时写 {"status":"unknown","value":null,"reason":"无法可靠区分主车身颜色的原因"}。颜色缺证不应促使改写身份、轨迹或事件，执行所需默认色只由 Converter 在私有副本填写。

局限分类：仅因缺少标定而无法量化绝对速度/米制位置时，写 limitation_kind=absolute_scale，impact_domains 仅列 speed、position（可附 execution）；execution_effect 可保留 blocks_execution。不要因无法形成可执行初态而附加 executor_primitive，该域用于动作能力/事件原语缺口。尺度未知与身份、车道、路径、动作、事件冲突必须拆成独立 limitation，后者写 limitation_kind=other，不得借尺度默认值消除。

入路口命名合同：新输出的纯入路口 executor primitive 统一使用 intersection_entry，通过 actor_id 区分自车和其他车。事件可使用 intersection_entry、ego_intersection_entry、vehicle_intersection_entry；三者均须满足同样的参与者绑定和边界前后证据要求。兼容读取旧 primitive 别名时，程序仅在执行副本映射为 intersection_entry，保留原始观察；包含 crossing/turn 的复合事件不属于这个别名映射。

视频证据的 frame_id、time_s（或 timestamp_s）必须来自本任务提供的帧索引，二者同时声明时必须对应同一帧；不得自行编造帧、反推图片序号或把媒体结束时长当作末帧时间。事件 time_range_s 和执行触发时刻属于时间区间/执行时钟，不必逐项等于抽帧时刻。代码生成的 execution_extension 不属于模型观察输出，不得自行声明。
