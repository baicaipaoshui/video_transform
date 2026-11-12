---
name: video-pipeline
description: 处理前视行车视频并返回 v3 完整信封或兼容 v2 结果；不用于单独执行内部阶段。
---

# Video Pipeline

新请求省略 `workflow_version` 且未指定 v3 专用模式或参数时，入口先通过 ffprobe 读取元数据：`0 < duration_s <= 2.1` 自动创建/选择兼容 v2（阈值包含 2.002 秒的编码偏差），其他视频默认标准 v3。普通短视频不要预填 v3 或追加 `--core-scene-approximate`。显式版本优先；`--strict-delivery`、`--core-scene-approximate`、执行范围/规划模式及 v3 专用参数保留 v3。时长读取失败保留 v3，并在 stderr 输出诊断；自动选择的版本进入原 Case 参数和 contract，不修改 Converter 或验收。使用 `--case-id` 时不探测或重选版本，参数对象为空时继承该 Case 已存参数和 contract。`quality_profile` 与 `contract_revision` 已停止支持。

## 生产调用

`efficient_v1` 已下线；不再创建或恢复此协议的 Case。历史结果与诊断保留，不改写旧 Case 参数。省略版本使用上述时长路由，也可显式传 `workflow_version=v2|v3`。

普通约 2 秒视频可直接调用（只有明确独立重跑时才加 `--new`）：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4
```

自动选择 v2 复用其原有短视频链路；不保证固定 Batch 数、耗时或必定生成 Target，实际产物和检查状态以最终信封为准。

调用 `execute_shell_command` 运行本 Skill 的正式 `process.py` 命令时，明确设置工具参数 `timeout=3600`（1 小时）；这是 shell 工具的等待上限，不是 `process.py` 的命令行参数。若等待超时，先核对 Case 持久化状态和活动任务，再用原 `case_id` 接管；不要因命令截断而新建重复 Case。

新建标准 v3 Case 默认进入独立的 `core_scene_approximate` 交付模式；只在核心道路、Actor、动作和关系有充分证据时交付，不能确定时停止。明确需要原严格交付时添加 `--strict-delivery`；显式 v2 仍走其原有流程：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 --new \
  --parameters-json '{"workflow_version":"v3"}'
```

此模式沿用原 World、Batch、Census 和 Behavior 生产任务，接收并按原字节归档首个完整的 Behavior JSON 回答；不先尝试严格 Behavior 接纳、完整 Analysis、Converter 或正式发布。冻结事实能构建有证据的核心道路、Actor 与变道时，才在 `video_extractor/core_approximate_deliveries/<case_id>/` 的副本中运行原 ConversionWork、Converter 和独立验收。未决观察与目标车道风险保留在 `manifest.json`、`report.md`；输出 `target.json` 标记为 `core_scene_approximate`，不等于严格 `validate_completed`。不满足准入条件时保持停止，不补写身份或轨迹。`--core-scene-approximate` 仍可显式传入，但新 v3 Case 不再需要它。

默认 Core 路由在新建和原 Case 恢复时相同：独立验收通过即交付 Core；非核心隔离背景观察的 Scope Review 冻结帧覆盖不足或 `identity_evidence_insufficient` 时，自动复用原 Description Fallback B 进行受控诊断构建。未决身份不得变成确认轨迹，未采用观察的交互和碰撞风险仍进入 `unverified` 风险清单；来源绑定的车身部位进展必须在最终 Target 的运动检查中保留。Fallback 的 Target 与 manifest 独立复验，不能宣称 Core Accepted。自车核心运动、来源绑定或其他未分类错误仍停止。`--description-fallback off` 可禁用自动降级。

新建 v2 Case 在内容或有限重试停止后自动尝试一次确定性最小场景兜底，不重新调用视频理解或模型。优先复用已有 Analysis；否则从已保存 Global 的结构化道路、自车事实或原短路口编译器构建草稿。缺少可靠主要动作或存在核心反证时保存 draft.json，不伪造直行。省略外围对象及不确定交互须明确留档。原数值补全/有限规划、Converter、Schema、引用及实际适用的运动/碰撞检查全部通过后，在 `video_extractor/description_fallback_deliveries/<case_id>/v2_minimum_scene/output/` 独立发布 target.json 和 manifest.json，状态 unverified；失败只保存 draft.json。活动任务不接管，旧 Case 不迁移，重复执行复用计划/检查点，不重置尝试次数。`--description-fallback off` 可禁用自动兜底。

v3 内容阻塞时，独立的 description fallback 先尝试原 B 转换；B 的关系或数值任务仍不能完成时，最多再尝试一次 `minimum_scene` 执行副本。它保留 World 道路、自车动作和同一动作走廊中有连续轨迹的已登记车辆，逐项记载省略对象、疑问及碰撞风险，并复验可重用的关系检查点。冻结策略允许时先使用现有有界数值规划；所有结果仍经原 Converter、Target Schema、引用和适用运动检查。只有检查通过才在 `video_extractor/description_fallback_deliveries/<case_id>/minimum_scene/output/` 独立发布 `target.json` 与 `manifest.json`，状态为 `unverified`；否则保留 `draft.json` 和真实错误。此路径不改正式 Census、原 Case 或 L1/L2 验收。

已有 Case 可直接复验或继续，明确的 fallback 操作无需再次提供视频路径：

```bash
../.venv/bin/python skills/video-pipeline/scripts/process.py --case-id case_... --description-fallback check
../.venv/bin/python skills/video-pipeline/scripts/process.py --case-id case_... --description-fallback resume
```

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 --new \
  --strict-delivery
```

任务中断后用同一视频与返回的 `case_id` 执行 `--case-id <case_id>`。入口按该 Case 的冻结近似模式标记自动恢复；旧严格 Case 不会隐式切换。若旧严格 Case 恰好停在 Behavior，且已有终态任务的完整、当前请求绑定的原始回答，可显式执行 `--case-id <case_id> --core-scene-approximate`，只在隔离副本接管该归档回答；不改原 Case 模式、事实、证书或进度，也不重提 Behavior。此接管仍需通过核心身份、动作与转换门禁，不能保证生成 Target。已接纳的关系和数值检查点按原生命周期复验并复用；活动任务先按原任务生命周期核对，不能用 `--new` 重提。严格已完成 Case 不能改报为核心近似。

已冻结核心近似 Case 若停在 Behavior 模型超时、传输失败或无完整回答，普通接管与显式 `--case-id ID --retry-failed` 均先核对原任务终态、冻结请求和已落盘候选。远端运行中或状态未知时停止；原任务明确终态且候选完整时归档并验收同一候选，不新建任务。只有无可复用完整候选、内容恢复不适用且传输恢复预算允许时，显式重试才新增 Behavior 任务。恢复沿用原 Case 预算与任务账本，每次显式命令最多新增一次 Behavior 提交，World、Batch、Census 和 World closure 不重新提交。

严格模式下，straight_preview.enabled 表示允许原有执行近似，不代表强制复核。完整可执行场景直接转换；高速直路、同向车道保持或一辆车的一次相邻变道且缺少尺度时才进行关系复核。显式 false 禁止该严格流程内的近似。已知弯道、转弯、多车变道、同车多次变道或复杂路口按当前能力提前停止，不反复调用模型。v2 保留为已有验证场景的兼容路径，不因一次失败自动切换版本。严格完成以正式信封含 `target.json` 和其余三个 artifact 为准；核心近似完成以独立 `accepted` 信封及 manifest、风险报告为准。

命令的工作目录必须是 `video-coordinator` workspace 根目录。先确认视频绝对路径是普通文件，再运行：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --parameters-json '{"road_source":"preview_json"}'
```

`--parameters-json` 可完全省略；此时新建/查找 v3 Case 默认允许 straight_preview 参数补全；新 Case 实际是否使用由共享场景判定决定。显式 enabled=false 或不含该授权的 allowed_degradations 优先。指定 case-id 的恢复不注入新默认值，旧 Case 不隐式迁移路由或预算。

标准 v3 省略 `allowed_degradations` 时自动允许 Converter 的现有缺值补全类别。速度未知时按场景使用默认速度（高速 80 km/h、路口 30 km/h，可由配置覆盖），其余缺失执行值按冻结策略补齐；不要求用户手填数值或逐项授权。analysis 可用值优先，实际补值逐字段写入回执和报告，观察 unknown 保留。显式授权数组仍作为限制清单；已知事实冲突不被默认值覆盖。

已失败、未交付且仅因 `conversion.fallback_unauthorized` 阻塞的旧 v3 Case，在用户已表达自动补全意图后，通过同一入口恢复，不再询问逐项授权：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed --enable-conversion-defaults
```

该入口保留 Case 参数及模型冻结输入，单独写入 delivery-only 的补全采用记录并绑定已有 analysis，归档原失败和策略，从 conversion 检查点恢复，不重新调用模型或重置预算。已交付 Case 不迁移；普通重试不隐式改变旧授权清单。

需要为“已确认同向/稳定关系但绝对尺度未知”的新 Case 显式启用近似直行预览时，使用最小增量参数；该选择会自动加入对应 degradation 授权并进入 Case 指纹：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 --new \
  --parameters-json '{"fallback_policy":{"straight_preview":{"enabled":true}}}'
```

若调用方显式提供 `allowed_degradations`，则必须自行包含 `straight_preview_policy_default`。已有 Case 不通过普通重试迁移该参数。

用户已明确要求根据场景和车辆运动关系合成仿真参数时，即授权使用上述近似策略，不再要求用户提供实测速度。LLM 负责从帧确认车道、前后关系及相对运动，Builder 生成并记录 `policy_default` 数值；事实层仍保留 unknown。只有选中近似路径才完成一次独立关系复核，不能据此猜测路口速度、设施或连接关系。

同样意图下，恢复已有未完成 Case 使用正式迁移入口（保留参数变更审计），不重新创建 Case：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed --enable-straight-preview
```

此策略当前支持已确认直路上的同向车道保持、steady/receding/approaching 前车关系，以及身份连续、跨线三段证据完整的一辆车一次相邻变道；绝对尺度缺失可由策略覆盖，原 limitation 不改写。不能据此消除身份、第二次变道、非相邻路径或其他事件冲突。无侵入、无事件依赖的对向背景车保留观察，不要求其具备自车道路上的执行坐标；只有存在物理隔离证据时，才能同时豁免对向车辆的身份不确定性。

禁止从 workspace 根目录运行 `python scripts/process.py`；该写法会错误解析为 `video-coordinator/scripts/process.py`。

新 Case 目录名为 `case_<UTC年月日T时分秒毫秒Z>_<8hex>`，例如 `case_20260906T001624123Z_abcdef12`，按名称排序即可看出创建顺序；历史 `case_<16hex>` 继续兼容且不迁移。精确时间仍以 `case.json.created_at` 为准。人工查看时使用 `python skills/video-pipeline/scripts/list_cases.py`；可用 `--status failed`、`--limit 0`、`--oldest-first`、`--problems-only` 或 `--json`。`HEALTH=problem` 表示持久化状态与产物声明矛盾，不等同于模型任务暂时仍在运行。不要手工重命名 `cases/case_*`。

`--parameters-json` 支持 `workflow_version=v2|v3`、`road_source=preview_json|external_xodr`、`allowed_degradations` 和 `fallback_policy`。v3 会在 Case 指纹前展开项目默认策略与授权 ID；默认启用预览的新 Case 另含 straight_preview_policy_default。正常调用复用同 source hash、规范化参数和同 contract 的最佳 Case；`failed` 或已修正确定性门禁后的 `needs_review` 用 `--case-id ... --retry-failed` 从持久检查点恢复，显式独立重跑才用 `--new`。

已完成 Case 是不可变交付：只要四份发布 artifact 与 conversion hash 有效，代码升级或 pipeline fingerprint 变化不会重新消耗模型任务。希望同一视频采用新版规则重新理解时使用 `--new`，不得用普通复用或 `--retry-failed` 隐式改写原交付。

首次 Behavior 前因 `identity.census.unresolved` 停止、最近 World closure 已完成时，可通过 `--case-id ID --retry-failed --resume-world-closure-session SESSION` 显式续接该原 session，复用已有图片统一裁定。只追加一个 task，消耗原 Case 剩余恢复预算，不覆盖冻结请求或原 Batch。活动/受理不明任务先对账；部分未决仍停止。新 Case 在已触发的布局 closure 内同次处理 census，不单独新增自动身份 session。详细条件见协议。

### 对话恢复未决观察

用户可以直接在 Coordinator session 说：“继续这个 Case，让 Behavior 先判断未决观察是否参与执行，按证据过滤，保留检查点和原预算。”这就是采用 Behavior 范围判断的明确授权，不要求用户输入命令或再次确认。使用用户指定的 Case；省略 ID 时仅在当前对话明确对应一个 Case 时沿用它，多 Case 不明确时只询问目标 Case。

先核对该 Case 的持久状态及视频绑定。旧 Case 已有有效 v4 census accepted、停在首次 Behavior 前的 `identity.census.unresolved` 时，Coordinator 内部调用本 Skill 的正式入口，传 `--video` 的原绑定路径、`--case-id ID --retry-failed --enable-behavior-census-scope`；不要同时追加 World session 续接或其他策略迁移。已冻结或合法采用该范围规则的 Case 按普通检查点恢复。采用条件由 Python 验证，完整规则见 [协议的 Behavior 范围判断](references/protocol.md#未决观察的对话恢复)。

仅询问“为什么卡住”、讨论方案或未指定策略的普通重试，不授权旧 Case 采用范围规则。范围未知、受理未明或预算不足时转交正式诊断及检查点；不因对话恢复自动新建 Case、重置预算或修改归档事实。此路由由 Coordinator 执行，World/Batch/Behavior Worker session 不自行推进 Case。

必须等待正式进程终态。`done/done_degraded` 交付四个 artifact；placeholder 只交付 diagnostics；`needs_review` 交付 conflict IDs、world epochs 和恢复点。不得把 `source_contract_validated` 描述成渲染已验证。路径纠正、错误码和完整信封见 [references/protocol.md](references/protocol.md)。

绝不直接转发 World/Batch/Behavior session 的自然语言回复。并发 Case 或旧任务可能晚到；只有正式进程 JSON 信封且其中 `case_id` 与当前请求完全一致时才可面向用户报告。标准编排器通过 `state/agent_leases/<agent>.lock` 跨 Case 串行同一个目标 Agent，避免 QwenPaw 的 from/to session 路由被另一 Case 覆盖。

旧 v2 短路口 Case 的显式事实重编译恢复入口已移除。正常短视频仍通过共享编译器生成 Analysis，并由原聚合流程接纳；当前任务的检查点恢复及转换、发布门禁保留。历史结果仅供查看，不通过该旧入口重新生成。

`confirmed_lane_assignments` 可传入用户确认的整个观察窗口 actor→lane 映射，例如 `{"ego":"lane_002","actor_001":"lane_002"}`。必须核对 actor 身份；只用于对应视频。它随参数进入 Case 指纹，传给 Behavior/关系复核，并由 Builder 硬性核验，模型不能静默覆盖。approaching 使用配置的相对速度差减去自车速度方向，即前车速度=自车速度−receding_delta_kmh；数值必须为正，整个窗口须通过顺序、道路范围与连续碰撞包络检查。

纯传输故障已耗尽普通恢复预算时，用户可对当前失败 Batch 显式授权一轮额外恢复；该入口不清零预算，不重跑已提交的 World/Batch，也不接受内容或校验失败：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed --authorize-transport-retry batch_0002
```

授权仅在所选 Batch 的既有尝试全部为 timeout、stream、gateway 或 task-status 类传输故障且不存在活动任务时成立。授权、原失败码、已保留 Batch 和实际消费状态持久化到 progress diagnostics。每次命令只授权一个新任务；再次授权必须能证明上一轮新增了一个传输失败 session，内容/校验失败或没有新失败记录时拒绝。

转换文字语义阶段因超时失败、普通额度已耗尽时，用户也可显式授权一次受控恢复：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed --authorize-transport-retry conversion_semantic_gap
```

先由编排器查询尚未对账的旧任务；确认终态超时才开放一次转换尝试。继续使用冻结 request/analysis 和已验收上游，保留原预算及失败记录；不通过直接改 progress 或重建 Case 恢复。旧任务运行中、查询失败、已取消或内容验收失败时停止。旧任务已完成时，按普通恢复验收结果，不另授权新任务。授权只放行一次，不延长 1800s 默认任务上限；不得承诺转换必定成功。完整授权/消费规则见 [编排协议](../../../global-video-understanding/skills/video-case-orchestrator/references/protocol.md)。

用户明确要求继续原 json-coverter 会话时，在上述转换恢复命令追加 `--resume-conversion-session SESSION_ID`，值必须来自该 Case 最近失败转换任务。此方式沿用旧会话上下文、创建新 task，仍由编排器持有授权/预算/任务记录；不能直接用 chat 命令脱离 Case 发起候选写入。


## 自车与车辆执行范围

新建 Case 默认冻结 `ego_vehicle_scene.v4` 与 `behavior-census-scope.v2`。v4 延续 v3 的逐帧对向证据检查，并消费 Python 的未决观察执行裁定；没有有效裁定的未知仍阻止交付。旧 v3 Case 保持原投影规则。源 World/Behavior 不变，假设和省略进入质量报告。完整兼容规则见 [Delivery 执行范围协议](../../../global-video-understanding/skills/case-delivery/references/protocol.md)。

普通恢复沿用旧 Case 已冻结的执行范围和有效证书，不自动迁移。用户明确要求未完成旧 v3 Case 采用当前 v3 范围时，使用显式迁移入口：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed --execution-scope ego_vehicle_scene.v4
```

`--execution-scope ego_vehicle_scene.v1` 与 `.v2` 只用于历史兼容或显式选择；不能仅凭版本名推断旧 v1 证书是否排除 background，须遵守上述 Delivery 协议。

缺少结构化关联的旧问题可附加 `--execution-scope-bindings /absolute/bindings.json`。文件含 `input_hashes`（state/world_context.json、state/behavior.json、state/actor_registry.json 的 SHA256）和 `associations`（question/ambiguity/limitation ID 到现有 actor IDs 的数组）。只能补充关联，不能覆盖已有车辆关联；不猜测新 ID。修改事实后必须重新审核关联。执行范围迁移不重置预算，不自动迁移已完成 Case。

排除清单位于 `diagnostics/execution_scope/<hash>/scope.json`，交付的 `quality_report.execution_actor_scope` 和 `observation_source` 保留范围及原始观察。省略对象的成功结果为 done_degraded；render_verification 仍由真实 runner 决定。


## 独立执行计划

### 已失败 Case 的单车运动事实复核

用户指出运动/可见时间识别错误时，可使用 `--case-id CASE --retry-failed --review-actor-motion ACTOR_ID`。仅限未交付 v3，沿用原参数与 Case 恢复预算。独立模型读取当前完整帧索引，只能修正指定车辆的 trajectory、is_static、simulation_role；不得改变身份、World、其他车辆、事件或删除局限。角色/静止状态修正须有多帧证据，事件参与者须另行裁决。原始观察、输入哈希和复核候选存入 diagnostics；提交后重新走完整执行门禁，不能用复核结果直接宣布生成成功。

新标准 v3 冻结 `ego_scene_approximation.v1`。观察验收后形成 `state/execution_plan.json`，结构化记录范围、约束、缺口和默认值。路口只在已有同序直行复核与车道位置锚点下补全出口执行车道；实测精确数量和连接冲突仍阻塞。estimated 数值参与拟合，有范围时不越界，observed 数值保持硬约束。

未完成 v3 可显式恢复：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed --execution-planning ego_scene_approximation.v1 \
  --execution-evidence /absolute/execution_evidence.json
```

execution_evidence 遵循 `global-video-understanding/skills/schemas/execution_evidence.schema.json`，含输入 SHA256、lane_knowledge、limitation_bindings，可附 dependency_bindings。只补结构化关联和知识分类，不改身份结论。旧 accepted 复核候选在输入绑定相同时可重新通过确定性门禁，不重跑已验收 World/Behavior、不重置预算。执行实现、策略或证据变化使旧执行缓存失效；已完成 Case 和 v2 不迁移。最终交付与真实渲染状态仍由正式信封决定。

## 已交付场景的用户复核修订

用户指出车辆车道、参与范围或显示场景错误时，可使用 `--case-id <已完成父Case> --scene-corrections /absolute/corrections.json`。Python 生成独立派生 Case，父 Case 和四份原交付不变，不调用模型。修订文件必须绑定父 analysis 的 SHA256，字段和数值由确定性构建器校验：

- revision=`user_scene_revision.v1`、parent_analysis_sha256、reason。
- actor_lanes：已有车辆 ID 到已有入口 lane_id。保留身份；改正同向车辆参与范围。
- ego_entry_time_s：只可选择原观察进入区间内的时刻，不更改原始 crossing witnesses。
- side_road_lane_counts：只允许 road_002/004 的 count、status=estimated、confidence、reason；估计不宣称实测总数。
- parked_vehicles：用户授权的静止布景列表，每项 side/x_m/color/vehicle_class。只放到已声明的入口 paved shoulder，数量、间距和路口净空受限；独立 scenery ID，不冒充视频身份。
- actor_initial_gaps_m：仅适用于已交付的普通单道路 highway，给仍缺少米制初始纵向坐标的现有运动车辆指定自车前方中心距（米）。这是用户选择的执行近似，写入派生 Case 的 initial_conditions；原观察轨迹保留。不得覆盖已有数值、裁剪车辆或改全局 lead_distance_m；必须通过车身包络、车道变化与目标检查。

间距修订示例：修订 JSON 包含 `revision`、父 `analysis.json` 的 `parent_analysis_sha256`、`reason` 和 `"actor_initial_gaps_m":{"actor_001":12.0}`；从本 Workspace 运行 `python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 --case-id case_... --scene-corrections /absolute/correction.json`。返回新的派生 `case_id`，同样输入重复提交时复用该派生 Case。

完整修订证书保存在 quality_report.scene_revision，转换前重新构建并比较实际执行内容。JSON 数值时间预测不等于真实渲染验证。相同已成功修订可复用；所有结果必须返回派生 case_id 和真实状态。

误合并身份复核使用同一入口的 `--actor-review-mode identity_split`，其派生 Case、受限修改范围和预算规则见 references/protocol.md「显式误合并身份拆分」。这是已有 identity_link 之外的独立合同，不由不同车辆的旧裁决自动升级。

历史 v1/v2 联合 closure 因布局引用越界或重复身份表达缺口产生 invalid_output 时，同一 `--resume-world-closure-session` 入口可申请一次 v3 合同修复。仅限首次 Behavior 前、最近原 session、远端已完成、归档哈希和错误复验通过；新请求不覆盖旧请求。该任务有一次独立账本，保留原已耗尽预算，不退款、不无限续接；新 v3 Case 的首次联合 closure 计正常任务，其内容修复仍共享原两次恢复额度。具体约束以 Global 编排协议为准。

### 旧 v4 Case 的观察级范围升级

首次 Behavior 和 census commit 前，最近 closure 因冻结外观或同帧区分缺项失败时，用户要求按自车相关性判断并保留结果，可用同一正式入口 `--case-id ID --retry-failed --resume-world-closure-session SESSION` 采用 v6。编排器先复验归档错误和远端终态，保存独立 relevance_adoption；旧请求、全部图片、World/Registry 基线、Batch、失败历史及普通额度不改。一次独立合同修复授权仅提交一个新 task；新回答仍须通过相关身份检查和 Behavior 范围证明，不能承诺必定生成 target。历史 v5 仅表示信息更新，普通重试不自动采用新范围语义。

### 已失败 Behavior 的观察范围补充

已接纳 v6 World、最新 Behavior 因 identity_evidence_insufficient 被拒且未提交正式 Behavior/analysis 时，用户明确授权补充观察执行范围，可使用：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed --review-behavior-scope
```

该选项只授权一次独立记账的范围补充任务，优先于旧的 census World 恢复。保留已完成 World、Batch、原 Behavior 候选、原请求和普通恢复预算。仅选择无候选依赖的 unknown 或无额外受影响对象的单候选 needs_identity；多候选/混合车辆争议不借此处理。模型只能补充所选 decisions，不能改车辆、轨迹、事件或其他回答。全帧证据与依赖检查仍执行，unknown 不自动忽略。

重复命令复用同一请求、任务和结果，受理不明先对账，不重复授权。补充通过后仍走完整 Behavior/构建/转换验收；剩余身份问题继续停止，不自动追加 World 或 Behavior 任务。已完成结果不采用该入口。不能与 World session 续接、传输恢复、车辆复核或策略迁移同时使用。

原补充任务明确因服务端 token-plan 额度耗尽而终态失败、尚无输出时，用户恢复额度并明确授权后可使用：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/video.mp4 \
  --case-id case_... --retry-failed \
  --reauthorize-behavior-scope-after-quota task_原额度失败任务
```

此入口只为同一冻结范围额外授权一次任务，不联网探测额度；原任务历史、完成结果和普通预算保留。重复命令复用同一授权，不会再增加任务。输入漂移、已有候选、普通超时或受理不明不适用；额外任务再次失败后也不会获得第三次机会。不可与 `--review-behavior-scope` 或其他恢复/迁移参数合用。

如果上述额度恢复再次因额度失败，只有用户另行明确授权，才可将 task ID 换成这一失败任务，并额外传入 `--scope-quota-retry-generation 2`。此参数显式申请第二轮的一次机会；两次失败记录和授权均保留，普通重试不升级轮次。第二轮再次失败后停止，不支持第 3 轮；其余适用条件和禁止组合的参数不变。
