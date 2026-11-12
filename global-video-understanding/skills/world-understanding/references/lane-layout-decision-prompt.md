Skill: world-understanding
Case ${case_id}，World 同向车道布局裁决。只读冻结任务包 ${request_path} 和 focused_context.evidence_frames 指定的 review_path 原始图片。不要读其他 Case、历史 session 或完整视频；不要创建 subagent。

先独立逐帧确认物理边界，再比较 World 与 Batch；Batch 是独立挑战，不是默认真值。严格遵循 lane_layout_contract、focused_context.scope_rule 和 value_schemas.decision。只写一次 ${candidate_path}，写入 contract_version=lane-layout-decision.v2、精确复制 request_sha256/base_sha256、status=decision 和一份 decision。不得提交 operations、lane_id_map、World 对象、车道数组、lane_ids、lane_knowledge 或 field_evidence；这些由 Python 从裁决统一生成。

decision.boundaries_right_to_left 必须从最右行驶车道外缘向左列出 N+1 个可见物理边界，每项有唯一 boundary_id、kind、marking_type、reason 和直接 evidence_frame_ids。kind=curb 时 marking_type=curb；kind=road_edge 时 marking_type=road_edge；漆线用 kind=lane_marking 和实际 marking_type。路缘虽然不是漆线，也作为物理边界裁决，Python 会登记在 lane_markings 中。总边界至少由两张真实帧见证，最右边界也至少两帧。

ego_initial 只给自车初始右起零基行驶走廊序号、理由和直接帧证据，必须含第一帧。right_roadside 单独判断最右行驶边界之外区域的用途，不能因为那里有无车辆就直接增减行驶车道。old_lane_corridors 对每个旧 lane_id 给出同一物理走廊的右起序号、理由和帧证据；编号变化不是车辆变道。actor_corridors 仅用于有直接证据支持的 Actor 物理走廊修正，保持 Actor 身份不变。不能把已知对向或非行驶车道并入同向行驶链。

若任一关键边界、旧车道对应关系、初始自车位置或与独立 Batch 的冲突不能由指定帧唯一裁决，按 stop_contract 返回 needs_evidence、具体 blocking_issues 和 reason。不要猜测、不要用部分字段补丁凑过校验。
