Skill: world-understanding
Stage: world_topology

本任务先独立辨认道路，不读取已有 World、Behavior、target、历史 Case 或用户对车道数的猜测。
只读取冻结请求 ${request_path} 及其 frames 明确列出的高清源帧。frame_id 对应真实 PTS，不从图片编号计算时间。不创建其他 session 或 subagent，不启动流水线。

按请求中的 response_schema 回答，并原样回传 request_sha256。只写一次 ${candidate_path}，然后停止。它不是正式 World。

先寻找自车行驶方向的最右外边界，逐条向左辨认行驶车道及每一条边界。数组表示实际路面横断面，不表示画面横坐标。不按车辆数量、画面中心或已有车道标签数车道。接缝、阴影、轮迹不是分界线；遮挡的远车不能作为新增车道的依据。每项给至少两个不同时刻的 frame_ids 和简短可核验的 reason，Python 会关联时间和生成引用，不用你写 lane_id、road_id 或重复的车道数量。

complete_corridor 仅适用于：完整横断面可见、直路、无实体中央隔离、观察窗口没有汇入/分流或路口进入、车道边界稳定；从自车方向右到左是连续同向车道，随后（如有）为连续对向车道。road_context 必须逐项确认，证据覆盖真实首末帧。没有看到对向车辆不能证明 one_way；不能看清总数、分隔方式、右边区域用途或当前窗口拓扑变化时用 insufficient_evidence，不填“完整”答案。弯道、实体隔离、路口或其他不支持的结构用 other_scene。非完整结果只输出 request_sha256、outcome、reason，交回现有 World 取证流程。

N 条行驶车道必须对应 N+1 条从右到左的边界；外边界不算一条车道。roadside 分别描述两侧：continuous_paved_shoulder 是沿道路连续延伸的铺装路肩；driveway_apron 是局部出入口铺装，不是整段路肩；curb_only 表示没有额外路侧铺装区域；不能判定时不要选完整结果。不得把停车带当行驶车道，也不能把局部出入口延长成连续路肩。此版本不能表达的路侧类型返回 other_scene。

ego_initial 是首帧自车轮迹区域两侧边界确定的 ordinal_from_right（从0开始），理由说明边界及数数顺序，frame_ids 必须包含首帧。不能定位时写 null，不能默认最右或中间。此结论仅是初始位置线索，不代表全时段不变道。尺度、速度、距离不在本任务输出范围，绝不填典型值。
