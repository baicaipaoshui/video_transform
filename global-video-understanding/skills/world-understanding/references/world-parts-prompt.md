Skill: world-understanding
Stage: world

Case: ${case_id}。本次 World 使用 visual-answers.v1。道路已由独立视觉问答编译；你补充环境、设施、Actor census 和未决问题，不重写道路。
只读取 ${case_json}、${frames_path} 及该索引明确列出的图像。不得读取其他 Case、实现代码或创建 session/subagent。只写一次 ${candidate_path}，然后停止。

当前候选的局部输出 Schema（从公共 World Schema 派生）：
${parts_schema}

共享 Actor 字段：
${actor_schema}

道路坐标合同：
${coordinate_convention}

只读编译上下文：
${compiled}

不输出 contract_version、evidence_epoch、world.road，也不输出上述编译器已有的 field_evidence 路径；Python 负责组装它们。scene_type 如输出只能为 highway。所有其他必填键仍按局部 Schema 输出。不要把 enum 自造同义词；未决问题的 impact_domains 使用 Schema 枚举。身份及运动问题必须结构化绑定 actor_ids。

道路仍可被新证据挑战：若发现车道、方向、路侧区域或场景类型与只读上下文矛盾，不改道路、不隐瞒冲突，创建 owner=world 的 unresolved ambiguity，world_paths 指向具体矛盾路径，绑定证据和 critical_intervals。之后由现有 World reconciliation 裁决，不能仅因已有编译结果而确认原结论。

逐帧清点所有道路相关 Actor，再跨帧关联，最后定位车道。不得为下游减少对象；行人、骑行者和静止车辆同样保留。actor_type 用 canonical 大类，vehicle_class 单独描述。同一帧同时可见的两个车辆是互斥身份。关联需外观与连续位置共同支持，不能拼接“车辆 A 的早期位置 + 车辆 B 的后期外观”；短时遮挡后有多个候选就保留 actor_identity 未决。暂时忽略已有 actor_id 和 lane 标签进行反证检查。群体观察不能冒充一个可执行个体。

hypothesized_lane 只引用编译上下文的车道。以轮迹两侧真实边界和道路右到左顺序定位，不按画面中心；不确定写 null。ego_initial_hint 仅支持首帧，不能扩成整个视频车道约束，不能据此预写动态事件。道路/环境为 World 所有；车辆运动歧义归 Behavior，owner=behavior、subject_field=actor_state 或 actor_motion，不误标 actor_lane。

observed 绑定允许帧和真实时间；estimated 说明依据并给 confidence；unknown 保留简短 reason。不要捏造速度、坐标、设施位置、默认颜色或天气。field_evidence 记录你负责的具体事实。道路问答不含米制宽度、距离和速度，这些缺值不能反推成0或典型值。

questions_to_verify 使用 Schema 中 question_id、question、impact_domains、resolution、evidence 等字段，普通证据写 evidence[]，不新造根 evidence_frame_ids。身份关联、位置关联仅在有相应事实时按 Schema 完整填写。未知对象不虚构 ID。核心 unresolved question 与 World ambiguity 用 critical_intervals 安排最小取证时间窗；world_paths 指向真正未知/冲突的字段，related_world_paths 只作已知关联。保留道路外设施和环境事实；当前窗口之外的远端路口不能成为根 junction。
