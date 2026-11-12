---
name: video-case-orchestrator
description: 启动或恢复完整视频 Case，管理唯一生命周期、任务租约、预算和阶段路由；视觉阶段请求使用 World 或 Behavior Skill。
---

# Video Case Orchestrator

新 Case 冻结 fact_contract_revision=observation-execution.v1：World 后保存 observation_plan，Behavior 后用同一缺口规则完成执行路由并绑定哈希。详见 [路口执行策略](../references/intersection-approximation.md)。旧冻结请求、预算和已完成产物不原地迁移；离线模拟回放不得被记录为真实模型验收。

由 `video-coordinator` 或人工终端启动 Python 编排进程；模型 session 不执行本入口。工作目录为本 Agent workspace：

```bash
python skills/video-case-orchestrator/scripts/process.py --video /absolute/video.mp4 --parameters-json '{"workflow_version":"v3"}'
```

1. 绑定视频、参数与 Case，检查既有终态和源哈希；恢复沿用当前 Case 合同。
2. 调度 World candidate 接纳，委托 [batch-lifecycle](../batch-lifecycle/SKILL.md) 冻结并执行 Batch。
3. 委托 [world-reconciliation](../world-reconciliation/SKILL.md) 完成 World handoff，再调度 Behavior candidate 接纳。
4. 路口复核若提交结构化 World challenge，最多执行一次后置 World 定向重裁，并各重建一次 Behavior 与复核；该闭环使用独立预算且不修改旧 Case 合同。
5. 委托 [case-delivery](../case-delivery/SKILL.md) 构建、转换、验证和发布，持久化结果信封交还 Coordinator。

本 Skill 只决定阶段、租约与恢复，不替模型裁决视觉事实。新模型尝试使用新 session；已有未确定终态的 task 必须先对账。只有持久化 `done|done_degraded|placeholder_generated|needs_review|failed` 与完整信封才结束，Worker 的自然语言回复不是完成信号。

恢复与预算读 [protocol.md](references/protocol.md)；维护代码先读 [architecture.md](references/architecture.md)。跨阶段 JSON 门禁入口见 [contracts.md](references/contracts.md)。v2 与旧入口例外见 [legacy.md](references/legacy.md)。输出容量诊断见 [workspace-quality-baseline.md](references/workspace-quality-baseline.md)。
