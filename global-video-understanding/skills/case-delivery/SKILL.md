---
name: case-delivery
description: 确定性构建 analysis、调用 Converter、验证目标并生成报告和发布；支持只读离线预检，不承担视觉理解。
---

# Case Delivery

Builder 保留来源中的身份未知、冲突与 unresolved 观察。执行范围投影按冻结的 ego_vehicle_scene 版本决定哪些 Actor 进入执行副本，规则与历史兼容见 protocol.md。Converter 保留其输入中的全部单体车辆，包括仍在输入中的 background 和静止车辆；不再次按角色或事件参与名单筛选。显式复核预览遵守已绑定执行范围；非车辆及群组的表达限制保持独立，并在 analysis/报告与转换回执中留痕。

新 Case 可由共享路由选择 intersection_preview；确定性策略、证书与来源边界见 [路口执行策略](../references/intersection-approximation.md)。使用独立执行投影，保留原始 World/Behavior，在校验及 Converter 入口重建比对；停车区域必须按道路绑定映射 paved shoulder。交付为 done_degraded，不代表真实渲染通过。

intersection_path.v1 允许 `intersection_lane_change.v1`：仅一辆运动汽车、一次相邻车道变化、直行穿越且变化区间两端有冻结帧。投影保存逐车路径和连续分段碰撞包络；Converter 只能输出 `drive_straight → lane_change → drive_straight`，目标车道为变化段 transition reference，只有首段携带 start position。

显式启用 straight_preview 且关系复核通过时，由 Builder 在 simulation_scenarios 生成近似初始条件：steady 前车与自车等速，receding/approaching 前车分别增加/减少配置速度差；允许一辆车按冻结 before/straddle/after 证据执行一次相邻变道，并对横向插值和纵向运动做连续碰撞包络检查。策略参数标 policy_default 并保留版本、复核绑定及字段来源；Behavior 观察轨迹保持原样。80 km/h 等现有配置不是视频测量值。

analysis 中可用且受目标支持的值直接转换。标准新 Case 默认启用现有缺值补全类别，经冻结 conversion-policy.v1 按字段应用，不要求用户手填速度或逐项授权；显式 allowed_degradations 仍限制补全，已知事实冲突不能靠补值消除。旧 Case 保持原合同；有自动补全意图的未交付策略阻塞 Case 由正式恢复参数生成独立采用记录，不修改模型输入。具体新旧边界、报告回执和离线双模式见 [protocol.md](references/protocol.md)。

对于对向交通已定位在自车道路执行范围之外的 highway，Builder 可建立 `ego_carriageway_only` 执行范围：自车侧道路和全部 participant 的车道必须明确，且不得有事件或动作依赖对向区域；唯一获准的当前道路相邻变道除外。此时仅 `world.road.opposite_direction_lanes` 的未知保留在 world_uncertainties，但不阻塞；World 中 one_way=false 和对向道路存在均保持原值。物理隔离可为 true 或 false，但必须有结构化方向与帧证据；若要排除对向车辆的身份不确定性，仍必须证明存在物理隔离。

完整交付由总编排器调用 `_lib/stages/delivery.py:CaseDelivery`，输入为已验收 World、Behavior、Registry 和规范化参数。

1. Builder 生成 analysis，并记录策略值、降级与 provenance。
2. 执行 capability gate，调用独立 `json-coverter` 的 case-conversion CLI。
3. 执行目标投影、拓扑及适用的运动门禁，生成报告。
4. 发布四个产物及哈希，交还总编排器持久化完成信封；placeholder 仅留 diagnostics。

本组件不调用模型、不修订 World/Behavior 事实、不向用户发送最终答复。

已完成普通 highway 的车辆初始间距可通过 `user_scene_revision.v1` 的 `actor_initial_gaps_m` 在独立派生 Case 中修订。数值只进入执行初始条件，原始轨迹保持不变；修订绑定父 analysis 哈希，拒绝覆盖已有米制观测，并由 Converter 重放及变道连续碰撞检查验收。

新建 v3 Case 另冻结 `conversion-continuation.v1`：`conversion_relations` 整理全时段文字关系，`conversion_semantic_gap` 联合规划执行数值，正常共两个会话。会话内累积保存检查点，终态失败最多共用一个额外会话并消耗已有恢复额度。覆盖全部证据、自车、同向背景与晚出现车辆；身份未决交回上游，已知事实优先。完整方案仍用 `conversion-semantic-motion.v1`，私有投影与 target 均检查积分和相对关系，失败不退回等速默认值。无 continuation marker 的旧运动 Case 保持单会话；历史 gap 合同不变。受理不明或可能仍活动的任务先对账，禁止重复提交。

车辆外观按 [外观执行边界](../references/mela-expression-contract.md#车辆外观执行边界) 区分输入正确与渲染一致。已确认下游固定模型忽略颜色时，保留真实颜色、车型、身份和车道，并在现有报告的限制说明中记录受影响参与者、预期颜色及源码选择的模型；不得声称与视频外观一致，不触发无效视觉重试，也不修改 MELA/OpenX。此说明不新增完成信封字段、不改变既有门禁；若没有真实回放证据，注明结论来自源码核查。

独立只读预检入口（从本 Agent workspace 运行）：

```bash
python skills/case-delivery/scripts/offline_preflight.py --case-dir /absolute/case
```

它在临时目录转换、检查和生成报告，不改 Case、不发布、不调用模型；通过不代表恢复无需模型，也不代表真实渲染已验证。交付门禁与信封见 [protocol.md](references/protocol.md)。
