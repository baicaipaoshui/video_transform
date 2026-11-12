Skill: world-understanding
这是 intersection_review 触发的唯一一次后置 World 定向重裁。
只读 $request_path 以及 focused_context 中列出的 review_path 图片，只写 $candidate_path。
复核证书只提出挑战，World 仍是静态事实唯一所有者；逐项重新判断 world_challenges，不能照抄复核结论。missing挑战仅允许在`/world/road/roadside_areas/-`追加一个证据支持的停车区域，value必须包含area_id、road_id、side、area_type、occupied_by和evidence_frame_ids；仅有路边静止车辆不足以证明停车带。
只输出 world-closure-patch-v1 小补丁，request_sha256/base_sha256 原样复制，operations 只能使用 allowed_paths。
每项操作必须绑定任务包内 evidence_frame_ids。不得改变 Actor 身份、问题/歧义 ID，不能加入未挑战事实。
同一车辆的 observed lane connection 必须有入口车道、路口内部、越过出口边界后三段见证；远端斑马线之前或斑马线上不算出口后见证。
静止路边车辆不得仅凭自车视差推断为横穿车辆。证据不足时使用 stop_contract，不猜测，不生成执行默认值。
写入一次后停止，不解释，不创建 subagent。
