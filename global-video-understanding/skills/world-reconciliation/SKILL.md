---
name: world-reconciliation
description: 在编排进程内合并 Batch 冲突、检查 World handoff 并路由必要的 World 裁决；不自行推断视觉事实。
---

# World Reconciliation

新冻结 observation-execution.v1 合同由共享缺口分类延后获准的路口执行完整性检查，不豁免事实合法性。closure 按字段路径、问题 ID、道路/actor 选择证据并限制补丁范围；没有相关可用帧时提交前停止，不花模型任务。身份未知保留给后续依赖判定，明确身份冲突继续阻塞。

输入是已接纳 World、有效 Batch 和 Case 诊断记录。由总编排器调用 `_lib/stages/reconciliation.py:WorldReconciliation`。

1. 确定性合并有明确证据的 conflict 和回答；机械 evidence 形状错误及唯一 ID 可定位、单叶变化的集合成员寻址错误本地规范化并审计。
2. 无法唯一裁决的几何冲突交给 [world-understanding](../world-understanding/SKILL.md) 的 geometry_readjudication；结构/拓扑 handoff issue 仍走哈希绑定 closure。在线观察模式通常不因未决事实单独创建 closure；新 lane-layout-review.v1 的独立布局差异/关键结构缺口是例外，自动定向裁决并验收后续跑，详见 [协议](references/protocol.md#同向布局自动闭环新冻结请求)。
3. World Worker 返回 candidate 后，核对输入哈希、授权路径、epoch、Actor 身份和全部门禁，再原子提交。
4. World 观察 handoff 通过允许 Behavior；身份冲突不能降级。严格检查及已提交 closure 的缺证结果仍返回 needs_review，补丁权限或协议错误返回明确错误，不在同一证据上无限重试。已有活动任务必须先按冻结合同对账。

本 Skill 的终点是可交接的 World 或明确停止原因。模型只写 candidate；本组件在编排进程授权下提交 World。不得将 observed 标签直接解释为问题已解决。

规则见 [protocol.md](references/protocol.md)。closure 的模型提示词属于 World Skill，不在本 Skill 复制。
