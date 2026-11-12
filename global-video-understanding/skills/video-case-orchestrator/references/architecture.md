# Skill 与运行时职责地图

| Skill | 执行主体 | 负责 | 不负责 | 代码入口 |
|---|---|---|---|---|
| video-case-orchestrator | Python 生命周期进程 | Case、租约、预算、恢复、流程 | 视觉裁决 | scripts/process.py → _lib.runtime.orchestrator.main → _lib.runtime.v3.V3Orchestrator |
| world-understanding | 模型新 session | 静态世界与指定 World 裁决 candidate | 正式提交、Behavior、发布 | _lib/prompts.py → 本 Skill references |
| behavior-understanding | 模型新 session | 动态轨迹、事件与修复 candidate | World、发布 | _lib/prompts.py → 本 Skill references |
| batch-lifecycle | 同一生命周期进程 | 冻结、调度、验收、Registry | 看图裁决、World 合并 | _lib/stages/batch.py |
| world-reconciliation | 同一生命周期进程 | 冲突合并、handoff、请求 World 裁决 | 自行猜测事实 | _lib/stages/reconciliation.py |
| case-delivery | 同一生命周期进程 | Builder、转换、门禁、报告、发布 | 模型调用、用户回复 | _lib/stages/delivery.py |

WorldStage/BehaviorStage 位于 _lib/stages/world.py、behavior.py，是编排进程的 candidate 验收适配器，不是 Worker 本身。所有适配器组合为同一个 V3Orchestrator，共享持久状态与后端，不产生第二个生命周期所有者。

## 视频到 World 的正常路径与兼容边界

`case_core.prepare` 保留真实 PTS、首末帧和后续可用的 packed 证据；`global_pack_contract` 根据冻结模式选择预算合同。visual-answers.v2 使用当前 World 提示词及派生回答 Schema，不再消费旧 Global 的业务规则；旧模式保留 global_context 打包规则。已冻结的帧索引不重新生成。

`WorldStage.world_understanding` 先选择冻结模式：v2 准备观察请求后进入 `admitted_world`；旧模式委托 `legacy_world_understanding`，其中冻结 v1 才调用 topology 子任务。`admitted_world` 管候选编译、验收与原子提交；存档恢复集中在 `_recover_admitted_world`，仍由同一进程拥有任务、租约和预算。

`prompts.world_request_text` 先选择唯一任务合同，再渲染共享视觉规则。v2 使用 [single-world-prompt.md](../../world-understanding/references/single-world-prompt.md)；旧完整回答使用 [legacy-world-task-prompt.md](../../world-understanding/references/legacy-world-task-prompt.md)。v2 不再追加旧完整 World 必填清单或覆盖式指令。道路编译器仍共享，不删除 `semantic.py` 中被新版使用的逻辑。

验收依次执行公共 Schema、`validate_world_facts`、`check_world_declarations` 和观察拓扑/证据检查；一次 admission 不重复执行 Schema。其他入口仍使用完整的 `validate_world`/`check_world_shape`。初始 World 可只有合法观察和待核实问题，Batch 后的 `observation_handoff_issues` 要求可引用的 Ego 道路/车道，但不要求未知路口连接或全部路臂执行完整。

复用 World、冻结 Batch 和冻结 Behavior 均只读核对原 World 观察请求及原图哈希；请求缺失不能事后重建，图片漂移不绑定为新事实。已完成 Case 的复用入口和旧冻结模式不升级。详见 [协调协议](../../world-reconciliation/references/protocol.md)。

新 Case 的执行路线由 `_lib/runtime/routing.py` 统一判定，编排器与 Builder 共用。该模块只消费已接纳事实，不充当视觉 Worker；World/Behavior 问题通过已有定向 patch 入口归还所有者。Preview 证据修复位于 `_lib/behavior/preview_repair.py`，只修改哈希绑定的关系证据字段，接纳后仍执行完整门禁。

目录结构：

```text
skills/
  video-case-orchestrator/  # 公开启动命令、恢复协议、结构检查
  world-understanding/     # World 三种模式的提示词、只读验收
  behavior-understanding/  # Behavior 提示词、只读验收
  batch-lifecycle/         # Batch 管理与下游 Worker 提示词
  world-reconciliation/    # 确定性合并与闭合路由协议
  case-delivery/           # 交付及离线预检
  _lib/
    prompts.py             # 模板渲染，不改状态
    fingerprint.py         # 包含阶段实现与模板
    stages/                # 五类进程内适配器
  schemas/                 # 既有唯一 Schema
  references/              # 既有能力与默认值策略
  *.py                     # 保留导入/CLI 兼容的成熟内部模块
```

职责重构已把阶段方法从总编排器移出，提示词从 Python 字符串移到所属 Skill，模型请求标记所用 Skill。Agent 服务 ID 和工具权限保持现有配置；同一服务仍需跨 Case 串行，不声称已经实现独立 Agent 服务隔离。

维护者验证：运行 scripts/validate_structure.py，验证六个 Skill 的 frontmatter、manifest、引用路径；运行 tests.test_skill_boundaries 和既有测试。结构检查与离线门禁不能代替真实模型端到端运行。

2026-09-08 交接实现：`stages/batch.py` 维护 World 内容快照与逐批 Registry；`behavior/compile.py` 共用证据索引和候选准备；`contracts/v3.py` 只汇总接纳事实与获准默认，不再按聚合车道数造路口；`delivery/checks.py` 检查跨区引用；Converter `lib/builders.py` 负责确定性 ID。没有新增业务阶段或独立服务。

执行依赖门禁：`world/readiness.py::execution_dependencies` 是 Builder/Preview 共用入口。World 的 unresolved/non_core 保留原值；对每项输出 required/excluded/undetermined 和依据。判定首先依据 Builder/Converter 的结构化字段消费：非停车路侧区域没有目标投影，且无 Actor、事件或动作引用时，其未知保留但不阻塞；明确路边停车、行为引用、事件或非初始动作仍需依赖检查。缺少结构化路侧实体时才要求 localization 和逐帧 corridor 证据，不能用问题文字或模型标签放行。定向补证使用独立冻结请求和现有提交日志，沿用全局额外任务预算，旧请求/证书不覆盖。
