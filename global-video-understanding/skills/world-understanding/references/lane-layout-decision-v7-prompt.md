Skill: world-understanding
Case ${case_id}：只读冻结任务包 ${request_path} 和 focused_context.evidence_frames 指定图片，一次完成同向车道、路侧和初始车道关系裁决，写入 ${candidate_path}。不要创建 subagent。

先独立观察原图，从右到左列出行驶边界和每对边界间的完整走廊，然后对照 layout_claims。World、Batch 都是待验证主张，旧 ID 只用于对应，不是视觉事实。观察每条内部虚线是否有独立连续线迹，不将同一条线的透视延续重复计数，不因车辆遮挡、空车道或出口指示牌自行增减车道。

严格使用冻结 value_schemas.decision；contract_version=lane-layout-decision.v7，复制 request_sha256/base_sha256，status=decision，只提交 decision，不输出 World 数组、operations 或 ordinal_from_right。Python 编号并编译重复字段。

boundaries_right_to_left 是 N 条行驶车道的 N+1 条物理边界，每项写 boundary_id、kind、reason、evidence_frame_ids；lane_marking 还写实际 marking_type。driving_corridors 逐对相邻边界给直接帧证据。最右边界至少两帧，完整链至少两帧；每条走廊必须有一帧同时支持两侧边界。

ego_initial、old_lane_corridors、actor_corridors 每项写 right_boundary_id、left_boundary_id、reason、evidence_frame_ids。关系及两侧边界的证据帧交集必须非空，自车交集必须含真实首帧。不能用后来的空道路帧证明早先车辆的位置；看不清就保持未决，不添加未观察到的引用。

claim_disputes 只回答 count 的数值冲突，minimum 是下界。常规增减车道：两帧和相邻 disputed_interval；漏计的区间为 driving，误计的路侧区间为 non_driving。
若旧判断虚构了内部边界，把一条真实行驶车道拆成多条：disputed_interval 写保留的两侧旧 boundary ID、observed_role=driving、absent_boundary_ids（按旧链顺序列出中间所有被否定的分隔线）。至少两张共同帧同时显示保留的两侧边界与中间没有独立分隔线的完整走廊。该区间必须解释全部数量差，其他旧边界保持顺序。old_lane_corridors 中只有这些连续旧车道可合并映射到这一走廊；不同已知宽度等属性冲突不能被覆盖。不能把仍存在的虚线或另一个真实车道当作不存在。此类证据不足或多个不连续区间不能表达时返回 needs_evidence。

roadside_intervals 从最右行驶边界向外列出所有连续物理带，每段含 inner_boundary_id、type、surface、reason、evidence_frame_ids 和独立 outer_boundary（boundary_id、kind、reason、evidence_frame_ids）。第一段内边界是行驶链首项，以后连接前段外边界；外边界至少两帧，类型为 curb/road_edge。只将直接证实的铺装带、路肩、人行道或路边停车带放在这里，不能合并不同物理带；没有时写 []。无法确认或否定旧路侧带时返回 needs_evidence。

输出前一次核对边界、所有共同帧、争议区间、每条旧车道映射与所有相关 Actor，不只修反馈中的第一项。保留身份、已知尺度和无关事实；不能唯一裁决时按 stop_contract 返回 needs_evidence。
