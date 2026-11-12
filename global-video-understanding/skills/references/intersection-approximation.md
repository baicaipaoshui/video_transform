# 标准 v3 路口执行投影

本文件约束显式 intersection_preview 的关系复核与运动求解，不是所有 target 转换的前提。已验收观察结果可交付时，连接缺失走 Converter 固定补齐并生成 target；仅此缺值不强制进入本流程。补齐只进入私有执行副本并写转换回执，规则见 [Converter fallback](../../../json-coverter/skills/case-conversion/references/fallbacks.md)。

## 有界路径证据与所有权反馈

新 Case 冻结 `intersection_path_contract=intersection_path.v1`。路口复核复用既有证据联合索引并写入 `state/dense_evidence_catalog.json`：按 Actor 必需样本、关键区间锚点和中点细化确定性选择，硬上限12帧。`path_evidence_frame_ids` 可以引用 Behavior 轨迹之外的冻结帧，但不会新增观察轨迹点。

每个执行 Actor 声明入口/出口右起序号、横向运动，以及入口、路口内部和出口边界后三段见证。对 World 的反证写入 `world_challenges`；编排器最多执行一次哈希绑定的 `post_behavior_world_readjudication`，随后各重建一次 Behavior、路由与复核，使用独立结构化预算。

`intersection_lane_change.v1` 仅支持一辆运动汽车做一次相邻左/右变道。执行副本在证据区间内插值一个车道宽的横向位移，并做连续分段线性碰撞包络检查。Converter 输出三段绝对时间动作；这些仍是策略支持的 source-contract prediction，不升级真实渲染状态。

正式策略常量唯一来源为 `_lib/behavior/intersection.py:POLICY`。新 Case 将完整策略冻结到 state/execution_policy.json；旧 Case 不迁移。内部路由为 intersection_preview，不增加用户版本，不恢复 efficient_v1。

冻结 fact_contract_revision=observation-execution.v1 的新 Case 使用共享 world/gaps.py 分类与 runtime/routing.py 路由。事实阶段仍拒绝非法引用、方向矛盾和重复身份，但允许单四岔的未知执行连接延后复核。下文「已存在直行连接」仅约束旧冻结请求；新修订可以基于关系证书补本次参与者所需的最小直行边，保留已有边，绝不生成猜测转向。出口展开仍须满足全部 unknown 与 same_order 的限制。state/observation_plan.json 保存前判及哈希，Behavior 后重新判定；严格拓扑、运动和发布检查对投影执行，不修改原观察。

仅一个四岔路口、自车走廊运动汽车直行且满足上述横向运动合同可进入。变化灯色允许按用户授权在执行副本中替换为全程绿灯，不因变化停止；保留原始时间线及有损投影provenance，不宣称灯色时序保真。复核使用 intersection_review.schema.json，原始 World/Behavior 不改写。已存在的直行 lane_connections 是必需输入；出口仅一个占位车道且该数量证据全部unknown时，可在相关未决项全部被覆盖、exit_lane_continuity=same_order且参与者证据完整的条件下扩展为与入口同序的执行车道。执行ID重新编号及对应关系写provenance，不反写World；observed一车道出口不能扩展。不能凭道路条数生成合法转向网络。首期不支持迟入画参与者、静止参与者或需要执行的行人；不能将其改background绕过。

Python在独立投影中使用150米道路、3.5米行驶车道、2.5米停车区域宽度、30km/h速度及45米位置参考。线性约束保留原有数值、车道、事件区间和同车道顺序；加权L1优先保留速度初值，已观测receding/approaching使用至少1km/h的符号差。数值一致性不证明真实速度。无解、缺依赖或超时停止，不降低约束。

纵向事件的结构化语义由 longitudinal_motion.schema.json 管理；新路口复核请求携带该 Schema，要求逐事件确认对象、区间和证据。已确认的增速、减速或停车使用连续分段速度求解，区间内约束趋势、停车终点约束零速度，观测数值仍为硬约束。最终 target 独立解码检查事件趋势。没有 video_fidelity 的旧 Case 不伪造视频边界证书，也不隐式迁移视频保真策略。仅有刹车灯不能作为减速确认依据；证据不确定继续 needs_review。

斑马线仅来自证据复核，距连接端30/45米作为道路局部布局参考；入口斑马线必须提供初始相对关系或跨越边界区间。该参考值须落入已观测跨越区间且保持前后关系，冲突时调整参考而不是事件。信号位置未知时放在所属道路的路口连接端，不能机械沿用斑马线偏移导致灯位于自车身后。停车使用现有paved shoulder，不计入行驶车道，禁止把缺road_id的停车区扩散到所有道路。道路方向未知、未决主路径和潜在冲突背景不能由常量补齐。静态未知信号仅在控制关系已确定时采用不冲突配置；既有红灯不得覆盖绿色，即使Behavior漏写entry事件也按轨迹是否进入进行红灯冲突检查。

quality_report.intersection_preview 保存策略、证书、输入哈希、原始World/Behavior、求解约束、逐字段provenance和背景省略原因。MELA仍使用原生字段值。平面化保留下坡观测且披露坡度与遮挡损失。生成报告和正式四件套，status=done_degraded、render_verification=not_executed。

首次必要复核属于正常任务；后续复核、传输和内容恢复共享两次额度。证据帧错误只能修正对应actor的证据/上下文/理由，候选与修复基线留档。相同输入失败不重新花费任务；新依赖重新复核但不重置预算。

求解器运行依赖见 `_lib/experimental/requirements-solver.txt`，缺依赖返回明确阻塞，不运行真实模型去修复Python依赖。该底层模块只证明线性模型，发布仍必须经过Builder、MELA Schema、目标投影及运动校验。
