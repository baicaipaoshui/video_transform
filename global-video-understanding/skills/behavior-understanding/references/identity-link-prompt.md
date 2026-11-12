# 受限车辆身份复核 — actor-identity-link.v1

只读 ${request_path}，以及其中 frames 的全部预算后图像。请求中的旧描述是待复核资料，不是视觉结论。不得委托其他 Agent，不读其他帧、视频、历史 Case、target 或实现代码；只写 ${candidate_path}。

沿时间顺序判断早期车辆与 actor_id 原首次出现及后续车辆是否为同一物理对象。逐帧比较车身形态、颜色、细节、遮挡、车道邻接及相对运动连续性。颜色相同、用户希望出现超车、执行器需要某速度，都不能证明身份。特别检查同帧并存车辆，不能把两个对象拼成一条轨迹。

只回答三种结果之一：same_vehicle、different_vehicle、insufficient_evidence。禁止新增、合并、拆分或重编号 Actor。确认同一车辆时，需要更早、原首次和更晚的至少三帧，且覆盖所供窗口中首次与末次证据之间的每一帧；说明与 co_visible_actor_ids 中各对象的区别。遮挡或连续性不足则回答 insufficient_evidence，不强行确认。

严格 JSON 字段（不附加其他字段）：

- revision: actor-identity-link.v1
- request_sha256: 逐字复制请求
- actor_id: 逐字复制请求
- outcome: 上述枚举
- first_seen_frame_id: same_vehicle 时为证据中最早帧；否则 null
- evidence: 数组，每项仅 frame_id、time_s、features（具体可见特征与跨帧连续性/区别）、distinct_from_actor_ids（同帧明确不同的已登记对象）
- resolved_issue_ids: same_vehicle 时精确覆盖请求 issues 的 issue_id；否则 []
- reason: 证据理由，解释运动连续性及反证，不能引用仿真期望

你不拥有 World、Registry、Behavior 或 Case 的写入权限。正式身份提交和后续 Behavior 重建由编排器负责。
