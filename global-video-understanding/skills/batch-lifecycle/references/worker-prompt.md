Skill: video-scene-analysis
本模板只用于普通增量 Batch；lane-layout-review.v1 使用 independent-layout-prompt.md，不读取或对齐旧 World/Registry。
处理 Case ${case_id} 的唯一批次 ${batch_id}；当前 session 不得处理其他批次或创建 subagent。
先读取唯一任务包 ${request_path}；其中 input_paths 和 output_path 是权威路径。
若任务包包含 single_frame_review，本批只检查绑定的那一张图片；start_s == end_s 是有效单帧任务。不因其他宽窗口或历史文字改为检查整段视频，不读取相邻帧，不从一帧推断跨帧身份或运动。无法回答时保留 partial/unknown，禁止扩大取证范围后宣称单帧已确认。
新请求的 response_schema 是模型候选格式，已排除编排器拥有的 session_id；input_paths.schema 是注入后正式 Batch 的持久化格式。候选按 response_schema 输出。旧冻结请求没有 response_schema 时，沿用原协议：正式 Schema 除 session_id 外的要求适用于候选，session_id 仍由编排器注入。
禁止猜测路径，尤其禁止读取或写入 ${forbidden_path}。
普通批次默认最多4张，冻结 manifest 的 packing.evidence_selection.capacity 可授权必需证据扩到最多6张；逐张检查任务包实际列出的全部图片，不自行按4张截断。
只读取任务包列出的 manifest、world_context、上一批 registry、图片和 schema；必须逐项回答 manifest 分配的 assigned_question_ids 和 assigned_ambiguity_ids；observations 分别用 question_id 或 ambiguity_id 关联。
若本批 packing.evidence_selection 提供 question_evidence_coverage，先核对每个分配项的 supplied_frame_ids 与 missing_evidence；缺少必要比较帧时保留 partial/unknown 并说明缺口，不自行读取包外帧。引用帧齐全也不代表答案已确认。比较帧可能位于本批 start_s/end_s 之外，证据时刻仍以本批 frames 的源时刻为准。
先逐帧独立清点可见的物理车辆，描述各自位置、外观和相互区别，再与冻结的上一批 registry 对齐。旧身份描述是待验证假设，不是画面事实。不要因旧表只有一辆车就把近处、远处两辆车合成一辆。Batch 追加观察和新对象，旧身份的正式修订交给编排器。对齐规则如下：
- 能够稳定关联已有 actor 时，必须沿用该 actor_id，不得重新编号、复制或创建同一对象的别名；在完整 registry_after 对应条目中追加本批 evidence。只有本批提供的帧直接支持时才新增或更新 last_seen_frame_id 和 last_seen_time_s；历史 Registry 中出现过但本批未提供的帧不能升级为本批新声明的 last_seen。actor_id、actor_type、identity_description、first_seen_frame_id 是不可变身份字段，逐字保留。
- 明确发现冻结 registry 中完全不存在的新对象时，才在 registry_after 追加；必须提供唯一的新 actor_id、首次出现的 first_seen_frame_id、身份描述和可定位证据。不能覆盖、替换或重新编号任何已有 actor。
- 遮挡、出画或视角变化前后无法证明是同一对象时，不得按车型、颜色、位置或相邻帧强行合并；保留身份连续性未决，并将相关证据写入 observation 或 semantic_conflicts（按是否挑战 World 原值分别处理），不要伪造新的确定身份。
唯一输出协议要求完整 registry_after：以 input_paths.previous_actor_registry 为基线，原样保留未在本批出现的 Actor，追加证据和新对象。不得提交增量根字段，不得删除历史对象；新增观察不能直接改写 World，挑战 World 的事实走 semantic_conflicts。
Actor 身份字段为非空字符串；hypothesized_lane 已知时为冻结 World 的全局 lane_id，未知时省略或写 null。下列共享字段定义是当前 Schema 的依赖，已直接提供，无需读取额外文件：
${actor_schema}

类型交接：新建 Actor 的 actor_type 使用共享 canonical_actor_type 大类，车型另写 vehicle_class。例如消防车写 actor_type=vehicle、vehicle_class=fire_engine；目标不支持的车型也保留原名。未知细分类不影响已知车辆大类。已有 Registry 的 actor_type/identity_description 必须逐字沿用，car/suv/van/vehicle_* 等旧写法由代码兼容解释，不在后续阶段重命名身份；不要把所有对象回退成车辆。
声明 `vehicle_class.status=observed` 时，在 `vehicle_class` 对象内写对应视频帧的 `frame_id`/`time_s`，或在它自己的 `evidence[]` 中写带帧/时间的 observed 证据；仅在同级 Actor `evidence[]` 或 `reason` 文字里写帧号不算。看不清车型时写 `{"status":"unknown","value":null,"reason":"具体遮挡或分辨率限制"}`，保留 Actor 及其他已确认事实，不猜默认车型。
先定位每个分配问题所需的时刻和图像，已有图像足以回答时不重复查看同一时刻的其他版本；仅细节不清时查看任务包允许的高清原帧。车道问题先根据所在道路的行驶方向建立最右外边界与另一侧边界，再沿标线追踪到 Actor 轮迹区域；ordinal_from_right 从0开始，最右为0，向左为1、2。随后查冻结 World 的 road_id、lane_ids 和 lane_knowledge.anchors，映射到全局 lane_id，不重新编号。完整清单可以按道路内顺序查找；部分清单仅依赖已知 anchors。例：road_003 最右车道定义为 lane_005 时，ordinal_from_right=0 对应 lane_005。证据 reason 写目标左右边界、road_id、道路内序号和最终 lane_id；World 车道文字只是待核假设。无法完成边界链时保留未知，不另造道路或车道编号。
根字段清单（由当前 Schema 自动生成）：
${output_fields}
model_required 中每个根字段必须输出；model_optional 按当前分配任务填写；orchestrator_injected 由编排器注入，模型不得填写。nested_contracts 是同一 Schema 派生的嵌套字段约束摘要，不代替任务包中的完整回答格式。contract_version 使用当前 Schema 的 const 值。
不得直接改写 world；对 World 原值提出不同判断时写 semantic_conflicts。world_evidence_epoch 只放在 Batch 根对象，复制冻结任务包中的同名值；不得放入单条 conflict。每条 conflict 的字段清单、必填键及额外字段限制遵守上述 nested_contracts.semantic_conflicts.items。缺证回答用 observation 的 partial/unknown 表达，不为缺证凭空创建冲突。
身份关联、疑似重复登记或拆分交给已有 World closure：在带 question_id/ambiguity_id 的 observation 中保留图片证据，census 保留候选与 unresolved。不得构造 world.questions_to_verify、questions_to_verify 或 ambiguities 的 identity_binding 补丁；这些不是通用 World patch 的可写目标，也不得声称已修改 Registry 身份。
每条 conflict 的 world_path 必须精确指向 proposed_value 要替换的那个叶子值，路径当前值与 proposed_value 的 JSON 形状必须一致。不得用集合路径配一个集合成员对象；例如要把 area_001 的 parking_designation 从 unknown 改为 no_parking_signed，应先在冻结 World 的 roadside_areas 中定位其唯一数组下标，再写 `world.road.roadside_areas[下标].parking_designation` 与字符串 `no_parking_signed`，不能写 `world.road.roadside_areas` 与 `{area_id,...}`。无法唯一定位成员时只提交 observation 并保留 partial/unknown，不创建冲突。
只调用一次 write_file 写 ${candidate_path} 后停止。
不要填写 session_id；编排器会注入真实 session ID。
确认原 World 时只写带证据的 observation；只有 proposed_value 与原 World 不同时才写 semantic_conflicts。
每条关联问题的observation须写answer_resolution，允许值按上述 nested_contracts：confirmed表示已有证据回答整个问题且确认当前World值；需修改World写needs_world_patch，给semantic_conflicts并用conflict_ids关联；只能看到相关物体但无法判断所问事实写unknown。
status=observed仅表示观察来源，不等于确认答案。
owner=behavior的歧义只提供本窗口运动和事件边界证据，使用原视频时刻与既有Actor ID；不输出完整behavior、不直接裁决整段事件。边界不全写partial或unknown，不把上一批末态复制成当前观测。
只确认部分事实写partial，即使同时提供了semantic_conflicts也不能宣称整题已解；例如确认方向而车道数未知，必须保留车道数未决。
needs_world_patch只有覆盖该问题world_paths的修正全部应用后才能解除。
不得把停放车辆朝向当交通方向定论，不得把车辆出画当停车性质或事件完成的证据。
方向待核实时查看任务包manifest对应frames.source_path原始帧的单行/禁转标志和箭头，不只看联系表；方向标志不证明车道数，不清晰时仍保留unknown。
Actor 可变证据/车道路径统一写 world.actor_hypotheses.<actor_id>.evidence|hypothesized_lane|is_static|roadside_area_id；actor_id/type/identity_description/first_seen_frame_id 是不可变身份，禁止作为 proposed path。
面向映射查证只回答分配项：确认右侧停车区域不等于确认宽度、所属全部道路或专用停车属性；确认对向方向不等于确认车道数量。分别保留证据与partial/unknown，不填默认数值。细节不清看允许的高清变体，缺少事件前后时刻则报告边界缺口；不得自行解码新帧。paved shoulder等执行表示由下游决定，不能写成World观察类别。

观察范围：“整个观察时段内车道数是否稳定”仅要求逐帧核实自车方向可见车行道的两侧外边界及内部标线，不等于证明消失点以外道路永远不变。可见范围车道数持续明确、无汇入/分流证据时，不得仅因“远处可能有匝道/无法排除增加”将当前车道数标为 partial 或 unresolved；将远处及未采样范围未知保留在证据说明。若可见范围有边界遮挡、渐变/汇入迹象或互相矛盾证据，仍保留未决。默认仿真路长不是观察覆盖，不得据此宣称米制范围已验证。

问题若明确询问视频结束后或消失点外的未来路网，当前 Batch 只回答当前窗口是否保持车道边界连续、车道数稳定、无汇入分流、无进入路口；不得因未来不可见把当前窗口回答成 partial。未来事实仍可 unknown，并由 World 用结构化 temporal_scope 与当前窗口证据决定执行相关性。

视频证据的 frame_id、time_s（或 timestamp_s）必须来自本任务提供的帧索引，二者同时声明时必须对应同一帧；不得自行编造帧、反推图片序号或把媒体结束时长当作末帧时间。事件 time_range_s 和执行触发时刻属于时间区间/执行时钟，不必逐项等于抽帧时刻。代码生成的 execution_extension 不属于模型观察输出，不得自行声明。

当 response_schema.required 包含 vehicle_census 时：对任务实际提供的每一帧输出一项 {frame_id,time_s,vehicles}，包括比较帧；没有可见车辆的帧也用 vehicles=[] 明确记录。车辆逐项为 {object_id,description,binding,actor_id,candidate_actor_ids}。object_id 是帧内唯一的物理车辆标记；description 说明位置、外观及与同帧其他车辆的区别。只统计外部车辆，自车不列入；牵引车和与其连接的挂车作为同一物理车辆，不把车头/车尾/遮挡碎片拆成多辆。
已绑定 Actor 的轮迹若能在该帧独立定位车道，在对应 vehicle_census.description 中同时写明两侧可追踪标线、road_id、`ordinal_from_right=N` 与 `lane_id=lane_NNN`；不能定位时不要填写车道。不同帧若显示同一 Actor 跨到另一车道，逐帧保留两侧观察，不把末帧车道回填到首帧，也不省略变化。
先清点，再关联：binding 表示编号相对本批前序 registry 的来源。前序已有身份用 existing；明确是漏登车辆用 new，并在 registry_after 新增对应 Actor；本批首次合法登记后，在本批后续帧再次确认同一新增编号仍用 new，不改为 existing。新增编号必须在其 first_seen_frame_id 的清点项明确声明 new。未确定或怀疑旧身份混入多辆车时用 unresolved、actor_id=null、candidate_actor_ids=[疑似编号]（无候选则[]）；候选可包括前序编号或本批该时刻之前已合法登记的新编号，不能引用未来才登记的编号。旧编号早于冻结 first_seen_frame_id 的关联必须保留 unresolved，由 World 裁定，不修改 registry 首次出现字段。resolved 对象的 candidate_actor_ids=[]。不得将同帧两个独立对象绑定同一 actor_id；不得通过删去对应表中的第二辆车、虚构新编号或编造 semantic_conflicts 身份路径来躲避矛盾。旧身份若已合并错误，保持 registry 身份锁并报告 unresolved，不能既报告 new 又沿用已知错误身份。逐帧表不代表确认跨帧连续性，单帧任务尤其不能外推运动。旧冻结请求未要求 vehicle_census 时不新增此字段。
