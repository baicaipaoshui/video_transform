Skill: video-scene-analysis
Case ${case_id}，Batch ${batch_id}：独立道路布局核验。
只读取冻结任务包 ${request_path} 及 input_paths.images 列出的图片；新包提供可读取的打包 JPEG，旧包按冻结路径读取，不自行寻找原始 PNG。不要寻找 World、Registry、manifest、历史 session 或其他帧。当前模式不做 Actor 身份对齐、不生成 registry_after、不使用全局 lane ID。其余普通 Batch 的增量 Registry 协议不适用于本模式。
先独立追踪可见同向行车道的最右外边界，再向左逐条确认边界；不要把路侧停车带算成行车道，也不要把仍有车辆连续行驶的右侧行车道直接当停车带。
按 response_schema 回答 layout_observation：same_direction_count 的 exact/minimum/unknown、right_outer_boundary 是否确认、同向边界链 boundaries_right_to_left、真实首帧 ego_initial 两侧边界及从右起0基序号、独立的 right_roadside 用途。边界名称是本回答内稳定的物理标识，不是全局 lane ID。exact=N 时完整边界链为 N+1。

新请求的 response_schema 允许 sidewalk 时，右侧白色铺装带若在路缘外沿道路连续，独立报告为 sidewalk；旧冻结请求仍按其枚举回答。不要因位于路缘外就写 curb_only，也不要用局部出入口代替整条带。无法确认连续性或外缘时保留 unknown 和证据疑问。
每个事实分别附 reason 与真实 evidence_frame_ids；已知数量和已确认外边界至少两个不同时刻见证，ego_initial 必须引用真实首帧。reason 说明边界位置及其连续性，不能只写“明显”。无法确定时保留 unknown/null/minimum 并指出缺口，不能为一致性补造边界或确定自车编号。对向数量未知不影响已经可见的同向三条等事实，速度/米制宽度不在本次任务范围。
复制任务包 contract_version、case_id、batch_id、world_evidence_epoch、request_sha256，仅输出 response_schema 的根字段，不输出普通 Batch 字段或 session_id。编排器会将此观察编译为 Batch；差异最终交给 World 裁决，不能把自己的判断直接改写为 World。
只写一次 ${candidate_path} 后停止，不创建 subagent。
