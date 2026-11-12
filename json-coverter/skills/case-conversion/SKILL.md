---
name: case-conversion
description: 确定性地将 analysis 转换为 MELA JSON；优先使用可用分析值，标准新 Case 按冻结策略自动补齐执行缺口并记录来源。
---

# Case Conversion

当编排器发送 `analysis-reconciliation.v1` 时，先按 [文字取舍提示](references/analysis-reconciliation-prompt.md) 只处理冻结问题包，写受限候选；不要启动 Converter 或改写 Analysis/Target。无待决项不产生这个任务，正式身份仍由上游 Census 拥有。

新 Case 冻结 `conversion-continuation.v1`，按 [分会话文字转换任务](references/conversion-continuation-prompt.md) 分两次正常会话完成：第一会话整理全时段文字关系并覆盖每条证据；第二会话依据已验收关系联合规划所有车辆的执行近似数值。每个会话内及时、多次写指定目录的累积 `save_NNNN.json`；保存不创建新会话。编排器验收检查点、核对剩余证据/车辆，终态失败时最多共用一个额外会话，仍消耗既有恢复额度。最终完整验收后形成 `conversion-semantic-motion.v1`，CLI 确定性生成 target。覆盖自车、同向背景与晚出现车辆；身份、车道和已知数值不可改。不看图像或视频，不委托视觉 Agent，身份未决交回上游。失败不能等速兜底。未冻结 continuation 的历史运动 Case 继续 [单候选任务](references/semantic-motion-prompt.md)，历史 semantic-gap v1/v2/v3 保持原合同。

新 Case 的正式请求必须携带 `--policy` 和 `--policy-sha256`；数值、授权、输入哈希和缓存绑定见 [协议](references/protocol.md#frozen-policy-for-new-cases-2026-09-26)。标准新请求默认允许现有补全类别，速度等未知执行数值无需用户手填或逐项授权；显式限制清单仍有效，已知事实冲突仍停止。以下无 policy 的命令示例仅用于旧合同兼容。旧 Case 只通过编排器正式恢复参数采用补全，不由 Converter 隐式迁移。

维护上游到目标字段的映射前，先读 [MELA 表达契约](../../../global-video-understanding/skills/references/mela-expression-contract.md)：区分事实缺失、允许的有损表示和未接通能力。路侧停车当前映射为 left/right_shoulder.type=paved，不写 lanes.type=paved；格式枚举存在不能代替能力检查。该契约补充下述历史映射指南，v3 行为以当前代码和正式协议为准。

正式入口：

```bash
python scripts/process.py --input <analysis> --output <target> \
  --scene-type highway --result <conversion_result> \
  --workflow-contract video-case-world-behavior
```

Converter 不要求 analysis 已经包含全部执行字段。可用且受 MELA 支持的值直接映射；缺失、`null`、`unknown`、不支持枚举和无法进入目标格式的观察值由确定性硬编码补齐。补全只进入私有执行副本，并在 `conversion_result.json.fallbacks` 逐字段记录。普通转换逐个保留输入中的全部单体车辆，background、静止或未参与事件均不构成排除理由；非车辆/群组及显式复核范围的排除仍记录在 `excluded_actor_ids`。完成 MELA/Pydantic、引用和 OpenX 输入静态合同校验后把控制权交还编排器；Converter 不拥有发布和最终答复。

本项目只允许修改上游框架、Skill 及其转换逻辑；MELA/OpenX 源码与内嵌权威模型包不在修改范围。车辆颜色异常先按 [外观执行边界](../../../global-video-understanding/skills/references/mela-expression-contract.md#车辆外观执行边界) 核对：真实颜色继续写入 `scenario.vehicles[].color`，不能交换车型、身份或车道来迎合固定模型。目标字段正确不代表下游采用了该颜色；不生成或应用下游源码补丁作为交付修复。

输入输出与幂等协议见 [references/protocol.md](references/protocol.md)；硬编码规则见 [references/fallbacks.md](references/fallbacks.md)；修改映射前读 [references/mapping-rules.md](references/mapping-rules.md)，再按场景读取 highway/intersection mapping。`scripts/mela-business-logic/` 是格式权威实现，不是独立 Skill。
