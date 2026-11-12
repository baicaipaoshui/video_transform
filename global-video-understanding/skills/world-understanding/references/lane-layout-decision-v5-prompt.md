Skill: world-understanding
Case ${case_id}，同向车道和右侧路侧区域视觉裁决。只读冻结任务包 ${request_path} 与 focused_context.evidence_frames 指定的 review_path 原始图片。不要读其他 Case、历史 session 或完整视频；不要创建 subagent。

先逐帧列出从右到左的物理边界，以及每对相邻边界之间实际是什么区域；再对照 focused_context.layout_claims 中 World 和 Batch 的主张。两者都是待核实的观察。focused_context.prior_world_structure 只用于保持物理线和旧 ID 的对应，不能替代画面证据。空区域不自动等于路肩；有车也不自动等于行驶车道。用边界连续性、宽度关系、标线位置和区域用途作判断。看不清关键区间时返回 needs_evidence。

严格遵循 lane_layout_contract、focused_context.scope_rule 和 value_schemas.decision。只写一次 ${candidate_path}：contract_version=lane-layout-decision.v5，精确复制 request_sha256/base_sha256，status=decision，并提交一份 decision。不要提交 operations 或 World 的派生数组；Python 会从这一份裁决生成车道、边界登记及引用。

decision.boundaries_right_to_left 列 N 条同向行驶车道的 N+1 个物理边界。每项给 boundary_id、kind、具体 reason 和直接 evidence_frame_ids。kind=lane_marking 时还要给可见的 marking_type；kind=curb 或 road_edge 时只需 kind，标线类型由 Python 推导，勿把“没有油漆”另填成一种路缘类型。decision.driving_corridors 按边界顺序逐段说明每个行驶走廊的两侧 boundary_id、reason 和直接帧证据。完整边界链和最右行驶边界至少有两帧见证，每段走廊至少有一帧同时显示其两侧边界。褪色虚线或反光道钉位于两个完整车道走廊之间时是内部车道分隔，不是道路外缘。

模型不填写 supported/refuted/not_asserted 标签。Python 会按最终车道数和自车序号计算数值主张是否成立：minimum=2 表示“至少两条”，最终三条并不推翻它；旧 World 对第三条车道的边界遗漏由新的物理边界链纠正。decision.claim_disputes 只列真正的数值冲突：exact 数量不同、最终数量低于 minimum、或已知自车初始序号不同。没有数值冲突时给空数组 []。

每个 count 争议须写 claim_id、field=count、具体 reason、至少两张不同的原始帧与 disputed_interval={right_boundary_id,left_boundary_id,observed_role}。主张漏数车道时，该区间是最终边界链中的相邻一段，observed_role=driving；主张把非行驶带算作车道时，该区间是被否定主张的边界链中的相邻一段，observed_role=non_driving。每个自车位置争议须写 claim_id、field=ego_initial、reason 和包含真实首帧的证据，不写 disputed_interval。对 World、Batch 使用同样要求；不能唯一判断就返回 needs_evidence，不要添加多余争议来解释纯粹的编号或边界差异。

right_roadside 独立判定最右行驶边界之外是否有连续 shoulder、sidewalk 或 curbside_parking，并提供 surface=paved|unpaved|unknown。仅当冻结请求的 value_schemas.decision 枚举包含 sidewalk 时，白色铺装带若是路缘外沿道路连续的人行道，选 sidewalk；旧冻结请求仍按其枚举回答。不能因为白色连续区域在路缘外就选 curb_only，也不能用局部 driveway_apron 代替整条带。若有连续区域，该行驶边界是路侧区内缘，还须在 outer_boundary 提交独立外缘及帧证据；outer_boundary 不得与行驶边界链重合。未确认铺装、外缘或无法区分走廊与路侧带时返回 needs_evidence，不能让同一铺装区域同时成为车道和路侧带。Python 保留 sidewalk 观察事实；获准执行时才近似为 paved_shoulder。

ego_initial 提交真实首帧的自车右起零基走廊序号、理由和直接证据。old_lane_corridors 对每个旧 lane_id 给出同一物理走廊的序号与证据；重编号不是车辆变道。actor_corridors 只写有直接证据支持的 Actor 走廊修正。若关键边界、旧车道对应、初始自车位置或必要的数值争议无法裁决，按 stop_contract 返回 needs_evidence、blocking_issues 和具体 reason。
