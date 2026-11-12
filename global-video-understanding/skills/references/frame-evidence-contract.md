# 冻结帧证据合同

视频观察必须能对应实际提供的冻结帧；不得为填满字段创造图片或时间。共享验收实现为 `_lib/contracts/frame_evidence.py`，Batch 和 Behavior 的在线提交、候选复验及只读交付预检使用相同规则。此验收始终启用，不要求可选 fidelity policy。

## 来源与范围

- Batch 新证据使用该批 `manifest.batches[i].frames`，不是整个视频的任意帧。完整 `registry_after` 可原样继承冻结前序 Registry 的历史证据与不可变首次出现帧；新增、改写的证据必须重新验收。修改证据内容后不能继续以“继承”绕过范围检查。
- Behavior 使用 `_lib/behavior/compile.py:evidence_frames` 的联合索引：全局帧及已提交 Batch 的冻结帧，保留图像 variants、校验图片哈希与同 ID 时间一致性。尚未提交的 Batch 不增加可用帧。
- 不重抽帧、不改 manifest、不改冻结请求，不向旧 Case 嫁接新图片。

## 声明规则

| 声明 | 验收 |
|---|---|
| 只有 frame_id | 必须存在于当前允许的索引 |
| 只有观察 time_s / timestamp_s | 必须对应索引中的实际时刻 |
| 同时有帧 ID 和时间 | 必须对应同一索引行 |
| 嵌套观察继承容器帧或时间 | 继承的帧与声明时间仍需一致 |
| first_seen/last_seen 的帧与时间 | 新声明或改写的 ID/时间必须对应；原样继承保持不变 |
| evidence_frame_ids | 每个新增引用均须可用 |
| unknown 且没有帧或时间 | 保留未知；其他 Schema/证据规则继续验收 |
| time_range_s、trigger_time_s、执行 primitive 时钟 | 属于事件区间或执行时钟，不要求等于抽帧时刻 |

时间必须为有限非负数。仅允许 `1e-6` 秒浮点误差，不四舍五入到最近一帧，不截断到视频时长。例如提供 `f0=0.0`、`f1=0.5`、`f2=0.9`：`f1/time_s=0.5` 合法；`f1/time_s=0.9` 错配；观察 `time_s=1.0` 不合法。媒体时长 1.0 秒不证明存在 1.0 秒帧。

普通轨迹点的 time_s / timestamp_s 属于视频采样声明，estimated 不豁免帧绑定。事件的观察证据同样绑定，但事件区间内部的估计触发时刻可以位于两帧之间。

## 执行补点

原始模型 candidate 不能声明 `execution_extension=true` 来逃过验收。只有原始观察先通过绑定、代码随后在私有副本调用 `complete_estimated_horizon` 所生成的执行扩展，才由内部调用显式允许；这类补点不声明观察帧，并保留 estimated 来源、原因和 limitation。扩展点若声明 frame_id，该引用仍必须存在并与时间匹配。观察原件不改变。

该门禁不要求观察层补齐路口连接，也不改变 Converter 固定规则补齐连接并生成 target 的路径。

## 失败与恢复

错误为 ContractError 子类 FrameEvidenceError，包含稳定 rule_id、JSON pointer、stage、候选内容摘要、帧索引摘要与恢复建议；编排器沿用现有 candidate 存档、预算和失败诊断机制。主要 rule_id：`evidence.frame_unknown`、`evidence.time_unknown`、`evidence.frame_time_mismatch`、`evidence.time_invalid`、`evidence.time_alias_conflict`、`evidence.index_missing`、`evidence.index_invalid`、`evidence.index_conflict`、`evidence.execution_marker_forbidden`。

不自动替换帧、不自动补造观测、不因相同错误重置预算。离线检查只报告结果，不改变 Case 状态或发布产物。`validate_compiled_behavior(frames=None)` 是无 Case 上下文的结构检查，不能据此宣称帧证据已验证；正式入口必须传入联合索引。
