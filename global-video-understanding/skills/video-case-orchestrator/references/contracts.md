# JSON 合同索引

本次保留既有 v3 数据格式与单一 Schema 文件，不复制 Schema 到每个 Skill。Python 消费约束仍存在，下面同时列出生产者和完整门禁，后续收敛合同不能只改一端。

| 对象 | candidate 生产者 | 正式提交者 | 权威 Schema / 额外门禁 |
|---|---|---|---|
| World | world-understanding | WorldStage / WorldReconciliation | [world_context](../../schemas/world_context.schema.json)，world_readiness / execution_topology |
| Batch | 外部 video_extractor | BatchLifecycle | [batch_v3](../../schemas/batch_v3.schema.json)，validate_batch / validate_registry / validate_evidence |
| Behavior | behavior-understanding | BehaviorStage | [behavior](../../schemas/behavior.schema.json)，validate_compiled_behavior |
| Analysis | 确定性 Builder | CaseDelivery | [analysis](../../schemas/analysis.schema.json)，validate_analysis / Converter strict_v3_preflight |
| Target | Converter CLI | CaseDelivery | MELA/Pydantic 与 delivery_checks.validate_target |
| Progress | 生命周期进程 | 生命周期进程 | [progress](../../schemas/progress.schema.json) |

阶段 SKILL.md 定义工作单元与读写边界；references 定义字段说明/模型模板；scripts 提供命令；_lib/stages 是进程内适配器。模型的自由文本回复不能代替任意上述对象。
