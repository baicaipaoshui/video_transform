# 受限车辆身份拆分 — actor-identity-split.v1

只读 ${request_path} 及其中绑定的 frames 全部图像、world_path、registry_path、behavior_path。只写 ${candidate_path}。不得委托其他 Agent、读取其他帧/视频/历史 Case/target、修改正式文件。旧身份描述和用户提出的疑问都不是拆分结论。

先独立清点每帧的物理车辆，再比较跨帧连续性。只裁决 actor_id 是否混合了两辆车；不能拆分牵引车与其连接的挂车、车身遮挡碎片，不能将其他已登记车辆复制成新车。必须至少一帧同时看到两辆不同车辆、每辆至少两个可见帧，并逐帧说明与 co_visible_actor_ids 中车辆的区别。无法确定、实际超过两辆、涉及旧已验收事件或其他 Actor 的身份纠错时回答 insufficient_evidence。

模型不分配编号：旧 actor_id 保留给原 first_seen_frame_id 对应车辆，另一辆只能使用请求 new_actor_id。不得复用或重命名其他编号。两辆车分别给新的身份描述，不沿用包含混合运动的旧叙述。所有旧轨迹样本必须按 index 精确分配到其中一辆，不能漏掉或同时分给两辆；已有车道/位置/速度不在本次修改权限中，若必须改这些事实才能成立，应保留 insufficient_evidence。

严格输出以下字段（不得附加字段）：

- revision: actor-identity-split.v1
- request_sha256、actor_id: 逐字复制请求
- outcome: split_confirmed 或 insufficient_evidence
- actors: 恰好两个对象，每项仅 actor_id、identity_description、color、vehicle_class（车型名，例如 suv；看不清的颜色或车型用 unknown）
- sightings: 对 frames 每一帧恰好一项，字段仅 frame_id、time_s、vehicles。vehicles 仅列本次两辆车中该帧实际可见者；每项仅 actor_id、features、lane_id、distinct_from_actor_ids。features 描述位置、形态、与同帧另一辆的区别及有证据的连续变化；车道不明用 null，已知只能使用 World 全局编号；distinct_from_actor_ids 列该车与其他已登记同帧车辆的区分，不含本次两辆车自身。
- trajectory_assignments: 按 behavior_path 中旧 actor_id 的 trajectory 数组索引逐项输出 {index,actor_id}；had_behavior=false 时 []
- resolved_issue_ids: split_confirmed 时精确覆盖请求 issues 的 issue_id
- reason: 包含共现帧、区分特征、跨帧关联与反证的简短理由

insufficient_evidence 时 actors、sightings、trajectory_assignments、resolved_issue_ids 都为 []，reason 说明缺口。不能只因希望仿真出现超车就确认拆分；不提供执行米数/速度，运动事实由后续 Behavior 根据已分开的证据描述。编排器负责验收、保留原始证据、提交 World/Registry 和重建下游。
