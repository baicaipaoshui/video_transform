Skill: world-understanding
Case ${case_id}，World 同向车道与右侧连续路侧区域裁决。只读冻结任务包 ${request_path} 和 focused_context.evidence_frames 指定的 review_path 原始图片。不要读其他 Case、历史 session 或完整视频；不要创建 subagent。

先逐帧确认物理边界，再核对独立 Batch。独立 Batch 已给出 `same_direction_count.kind=exact` 时，该数量及非 null 的初始自车序号是本次 decision 的接受约束：原始帧支持时必须采用；认为帧不足以支持时返回 needs_evidence，不得提交与之冲突的 decision。`focused_context.prior_world_structure` 仅提供旧 ID 连续性和走廊映射，其中可能包含本次要修正的旧布局错误，不能作为视觉证据，也不能复述旧 World 理由来否定 Batch。严格遵循 lane_layout_contract、focused_context.scope_rule 和 value_schemas.decision。只写一次 ${candidate_path}，写入 contract_version=lane-layout-decision.v3、精确复制 request_sha256/base_sha256、status=decision 和一份 decision。不得提交 operations、World 对象或派生车道/路侧数组；这些由 Python 从裁决统一生成。

decision.boundaries_right_to_left 只列 N 条同向行驶车道的 N+1 个可见物理边界，从最右行驶车道外缘向左。每项有唯一 boundary_id、kind、marking_type、reason 和直接 evidence_frame_ids。路缘/道路边缘也作为物理边界登记。总边界与最右行驶边界至少由两张真实帧见证。已有物理边界保持相同 ID，不为同一线另造名称。

空置不是非车道证据。若两条相邻物理边界之间形成与相邻车道相当的连续铺装走廊，即使全程无车，也按边界链判断其是否为行驶车道；不得用“没有车辆行驶”把它降为沟带或路肩。褪色虚线、凸起反光道钉或点状标线若位于两个完整车道宽度走廊之间，是内部车道分隔，不是道路外缘。旧 World 对同一物理边界的 `marking_type` 可能正是待修正错误；复用 boundary_id，并按原始帧提交正确 kind/marking_type 和直接证据。

right_roadside 单独判定最右行驶边界之外是否存在连续的 shoulder 或 curbside_parking，并给出有证据的 surface=paved|unpaved|unknown。若有，这条行驶边界是路侧区域的内缘；另在 right_roadside.outer_boundary 提交其外缘（通常为路缘），附独立帧证据。outer_boundary 不得与行驶边界链任何元素相同；本裁决只能编译已确认铺装的连续区域，未确认铺装时返回 needs_evidence。若没有连续路侧带或用途未定，outer_boundary 为 null。不得仅因没有停放车辆、停车标线或自行车符号就将连续铺装带当作行驶车道；也不得仅因有静止车辆就断言专用停车带。已有 roadside_areas 与新裁决冲突时，必须逐帧核对；无法唯一判定就返回 needs_evidence。

ego_initial 只给初始自车的右起零基行驶走廊序号、理由和第一帧在内的直接证据。old_lane_corridors 对每个旧 lane_id 给出同一物理走廊的序号、理由和证据；编号变化不是车辆变道。actor_corridors 仅用于有直接证据支持的 Actor 走廊修正。不能把已知对向或非行驶区域并入同向行驶链。

若任一关键行驶边界、路侧外缘、旧车道对应关系、初始自车位置或与独立 Batch 的冲突不能由指定帧唯一裁决，按 stop_contract 返回 needs_evidence、具体 blocking_issues 和 reason。不要猜测、不要用部分字段凑过校验。
