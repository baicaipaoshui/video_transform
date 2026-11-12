---
name: batch-lifecycle
description: 在编排进程内冻结、串行调度、验收或恢复详细 Batch，维护 manifest 和累计 Registry；不执行逐帧视觉判断。
---

# Batch Lifecycle

取证规划的维护依据是 [MELA 表达契约](../references/mela-expression-contract.md)。将视觉可解决的问题与可观察窗口对应；执行端没有表达能力不构成新增视觉问题。当前 freeze_v3 从已解码帧中选取、打包，高清变体不等于新增时间采样；本指引不启用自动重解码或动态提高帧率。

本 Skill 由唯一 Case 编排进程执行，不能在 World/Behavior 模型 session 中独立调用。

1. 根据 World 未决问题和视频采样预算冻结 manifest；完整短视频允许零 Batch。
2. 每批生成唯一 state/requests/batch_NNNN.json，指定读取路径、Schema 和隐藏输出路径。
   新 Full World 的布局核验独立占一个计划批次，仅给中性问题和原帧，使用 [独立模板](references/independent-layout-prompt.md)；不泄露旧 World/Registry 结论。候选编译为原正式 Batch，Registry 原样保留。
3. 串行调用外部 `video_extractor` 的 [video-scene-analysis](../../../video_extractor/skills/video-scene-analysis/SKILL.md)；每次新尝试使用新 session。
4. 校验 Case/Batch、绑定 World 内容、分配问题、证据和身份连续性；每批原子提交后更新累计 Registry，下一批读取不可变的前序副本。恢复先复验本 Case 候选，租约与预算委托总编排器。

终点为所有冻结 Batch 已验收且 Registry 已落盘，或向总编排器返回明确阻塞。不能自行合并 World、生成 Behavior 或发布。

任务包和完整 Registry 兼容合同见 [protocol.md](references/protocol.md)；[worker-prompt.md](references/worker-prompt.md) 是发送给 Extractor 的提示词模板，不赋予本 Skill 视觉 Worker 身份。实现入口为 `_lib/stages/batch.py:freeze_v3/BatchLifecycle.v3_batches`。
