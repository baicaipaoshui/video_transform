# 兼容边界

- video-case-orchestrator/scripts/process.py 和父级 orchestrate_case.py 继续是稳定入口；v3_orchestrator.V3Orchestrator、freeze_v3 和四个 prompt 函数保留导入兼容。
- 原 offline_preflight.py 委托 case-delivery 同名脚本，不保留第二份实现。
- 显式 v2 的 global_context、六分片聚合、short_scene_compiler 继续沿原路由；旧短路口事实重编译恢复入口已移除，正常编译、聚合和当前任务检查点恢复保留，历史结果不迁移。
- low-token 实验实现已移除，quality_profile/contract_revision 拒绝；历史审计数据不代表可运行 Skill。
- skills 根目录保留 video_case、orchestrate_case、v3_contract、world_readiness、world_closure、behavior_compile/repair、转换/投影/报告模块与 schemas/references，作为兼容内部库。这些文件不注册为 Skill；它们存在不赋予模型 shell 或生命周期权限。
- 本次只拆阶段适配器与提示词，没有重新设计 JSON、增量 Registry 或新建 Agent 服务；World 与 Behavior 仍复用同一服务路由的不同新 session。
- runs/、历史 chats/sessions/checkpoints 与 media 输出不属于可发现 Skill。json-coverter 的历史拼写保持。
