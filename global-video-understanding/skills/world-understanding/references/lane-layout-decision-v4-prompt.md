Skill: world-understanding
Case ${case_id}，World 同向车道与右侧路侧区域证据裁决。只读冻结任务包 ${request_path} 和 focused_context.evidence_frames 指定的 review_path 原始图片。不要读其他 Case、历史 session 或完整视频；不要创建 subagent。

先逐帧建立自己的物理横截面：从最右行驶边界向左记录每条真实边界、相邻边界间的区域、自车首帧位置，以及右侧是否另有路肩或停车带。完成这一步后，再逐条核对 focused_context.layout_claims。World 与 Batch 都是待核实主张；source 标签、旧 World 车道 ID、Batch 的 exact 字样都不是视觉证据。focused_context.prior_world_structure 只供保持旧 ID 与物理走廊对应，不用其旧边界引用决定哪条是车道。

严格遵循 lane_layout_contract、focused_context.scope_rule 和 value_schemas.decision。只写一次 ${candidate_path}：contract_version=lane-layout-decision.v4，精确复制 request_sha256/base_sha256，status=decision，并给一份 decision。不得提交 operations、World 对象或派生车道数组；Python 从裁决统一生成它们。

decision.boundaries_right_to_left 是 N 条同向行驶车道的 N+1 个物理边界。每个边界填唯一 boundary_id、kind、marking_type、具体 reason 和直接 evidence_frame_ids。decision.driving_corridors 按同一顺序逐段写出 right_boundary_id、left_boundary_id、该段为何是行驶车道的 reason 和直接 evidence_frame_ids。右外边界和整条链至少由两张不同的真实帧见证；每条行驶走廊须有一帧同时看到其两侧边界。空置不是排除行驶车道的理由；两个完整车道宽度走廊之间的褪色虚线或反光道钉不能当作道路外缘。旧 World 的边界类型若错，可用原 boundary_id 和直接帧证据纠正。不能把对向或非行驶区域并入同向行驶链。

对 focused_context.layout_claims 的每个 claim_id，在 decision.claim_assessments 中恰好提交一项，分别评估 count 与 ego_initial。每项填写 verdict=supported|refuted|not_asserted、具体 reason 和指定帧中的 evidence_frame_ids。数值与最终裁决一致才是 supported；主张 unknown/null 才是 not_asserted。对已声明但无法判定的主张，停止并返回 needs_evidence，不得用 not_asserted 绕过冲突。

当 count=refuted，必须指出哪一个由相邻边界围成的争议区间：disputed_interval={right_boundary_id,left_boundary_id,observed_role}。如果主张漏了一条车道，区间须是最终边界链中的相邻一段，observed_role=driving；如果主张把非行驶带算作车道，区间须是被否定主张的边界链中的相邻一段，observed_role=non_driving。至少引用两张不同的原始帧，在 reason 中说明该区间的连续性、标线位置和用途；仅凭有车或无车不够。对 ego_initial=refuted，说明实际自车走廊，证据必须包含真实首帧。其余评估不要添加 disputed_interval。无论被推翻的是 World 还是 Batch，要求相同。

right_roadside 单独判定最右行驶边界之外是否有连续 shoulder 或 curbside_parking，并给出 surface=paved|unpaved|unknown。若有，最右行驶边界是路侧区内缘，另在 outer_boundary 提供外侧路缘或道路边缘及独立帧证据。外缘不得与行驶边界链重合；未确认铺装时返回 needs_evidence。不得把同一铺装空间同时判为行驶车道和路肩。已有 roadside_areas 与新裁决不符时逐帧核对，不能唯一判定就停止。

ego_initial 填初始自车右起零基走廊序号、理由和包含首帧的直接证据。old_lane_corridors 对每个旧 lane_id 填同一物理走廊的序号与证据；重编号不是变道。actor_corridors 仅用于有直接证据支持的 Actor 修正。若关键边界、争议区间、旧车道对应或自车位置不能由指定帧唯一判定，按 stop_contract 返回 needs_evidence、blocking_issues 和具体 reason，不要猜测。
