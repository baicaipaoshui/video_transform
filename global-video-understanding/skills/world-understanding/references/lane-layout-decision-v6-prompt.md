Skill: world-understanding
Case ${case_id}：一次完成同向车道及右侧物理区域裁决。只读冻结任务包 ${request_path} 和 focused_context.evidence_frames 指定的 review_path 图片。不要读其他 Case、历史 session 或完整视频；不要创建 subagent。

先独立观察原始帧，从右到左列出每条行驶走廊的两侧边界，再从最右行驶边界向外列出**每一段连续路侧区域**的内外边界。之后才对照 layout_claims；World 和 Batch 都是待核实主张。prior_world_structure 仅用于旧 ID 对应，不是视觉证据。看不清关键边界、区域用途或旧车道对应时，按 stop_contract 返回 needs_evidence。

严格按 lane_layout_contract、focused_context.scope_rule、value_schemas.decision，一次写入 ${candidate_path}。contract_version=lane-layout-decision.v6；精确复制 request_sha256/base_sha256；status=decision 时只提交 decision，不提交 World 派生数组或 operations。Python 会生成 World 字段和证据。

boundaries_right_to_left 是 N 条同向行驶车道的 N+1 个边界；driving_corridors 对每对相邻边界给出直接帧证据。每个边界有 boundary_id、kind、reason、evidence_frame_ids；lane_marking 还需实际 marking_type，curb/road_edge 不填 marking_type。最右边界与完整车道链至少两帧见证，每段走廊至少一帧同时显示两侧边界。空置不等于路侧，路缘外铺装也不自动是行驶车道。

roadside_intervals 从最右行驶边界向外按顺序列**全部**连续物理带。每段写 inner_boundary_id、type、surface、reason、evidence_frame_ids，以及独立 outer_boundary（boundary_id、kind、reason、evidence_frame_ids）。首段 inner_boundary_id 等于行驶边界链首项；后段等于前段 outer_boundary.boundary_id。不同物理带不能因都映射为 paved_shoulder 而合并。例如铺装带之后还有人行道，就提交两段。type 可为 paved_strip、shoulder、sidewalk、curbside_parking；只有确认铺装、独立外缘且有直接帧证据才能提交。没有连续带时给 []。若旧 World 有一段但画面无法确认或否定，返回 needs_evidence，不能遗漏它换取通过。

claim_disputes 只提交确实与最终车道数冲突的 count 主张；minimum 是下界。count 争议给两帧和具体相邻 disputed_interval；自车位置争议由 Python 按 ego_initial 边界组合和真实首帧证据自动生成，不提交 field=ego_initial。old_lane_corridors 为每条旧车道给同一物理走廊的 right_boundary_id/left_boundary_id，actor_corridors 只写直接证据支持的修正。ego_initial 必须由真实首帧支持。不能唯一裁决时返回 needs_evidence，不猜填。

道路左右按道路行驶方向判断，不按画面左右。ego_initial、old_lane_corridors、actor_corridors 每项只提交 right_boundary_id、left_boundary_id、reason、evidence_frame_ids；两侧必须是行驶边界链中相邻的一对。不要提交 ordinal_from_right、lane_id 或重复位置数值，Python 会确定性生成。模型负责直接视觉事实，代码负责编号。行驶链只包含确认的同向行驶走廊，路肩/停车带/人行道只能放入 roadside_intervals；缺证或不能唯一绑定旧车道时返回 needs_evidence。
