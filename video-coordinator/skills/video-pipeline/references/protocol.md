# Video Pipeline Protocol

## 显式早期身份复核

沿用车辆复核入口，增加模式参数：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/source.mp4 --case-id case_ID --retry-failed --review-actor-motion actor_002 --actor-review-mode identity_link
```

未传模式时仍为原车辆运动复核。`identity_link` 只判断更早的车辆是否为已登记的同一对象；不会直接提高速度，也不会要求 json-coverter 看图。已完成 Case 自动创建或复用派生 Case，返回信封 case_id 为子 Case；必须向用户说明其 parent，不把子 Case ID 当成原 Case。未完成 Case 仅在已冻结身份复核策略时允许原地恢复，旧未启用 Case 不隐式升级。

同车确认后重建 Behavior，再由文字语义任务生成执行近似及 target；不同车辆或证据不足保持 needs_review。原 Case、原 Batch 和普通身份锁不变；所有任务沿用现有预算。身份、重建和后续语义任务仍可能受预算阻塞，不能把“代码支持复核”描述为“视频已修正”或“渲染已验证”。

## v3 request and response

首次 Behavior 前若错误为 identity.census.unresolved，且最新完成模型任务是 World closure，可显式用 `--case-id ID --retry-failed --resume-world-closure-session SESSION` 继续该原 session。它创建一次新 task，沿用原恢复预算，不重新抽帧、不覆盖旧冻结请求。新私有请求统一核查 census 与已有图片，保留已验收布局。远端须确认 completed，活动/受理不明任务先对账；预算不足或证据未决仍如实停止。该参数不能组合转换恢复、其他策略迁移、Actor 独立复核或 --new。代码支持恢复不等于当前视频已经裁定或交付。

公开入口的新请求未传 `workflow_version`、未带 v3 专用模式或参数时，先通过 ffprobe 读取视频元数据（10 秒超时，不解码帧、不调用模型）：`0 < duration_s <= 2.1` 自动选择 v2，其余选择 v3。2.1 秒阈值覆盖名义 2 秒视频的编码偏差（如 2.002 秒）。选择结果输出到 stderr，标准 stdout 信封不变；有效版本通过原参数进入 Case contract 和匹配指纹。探测失败保留 v3 并输出诊断。显式 v2/v3 始终优先；strict/core approximate、execution scope/planning 和 v3 专用参数保留原语义。已有 `--case-id` 请求不探测、不改变 contract 或参数。`quality_profile` 与 `contract_revision` 已停止支持。

`efficient_v1` 已下线。生产入口、新建 Case 和模型调度均拒绝该策略，返回 execution_policy_retired；历史 Case、产物和诊断保留，不能原地切换参数。新运行省略 execution_policy_version，按上述时长路由或显式 workflow_version 选择版本。

v3 参数在 Case 匹配前规范化；省略策略时展开版本化项目默认策略，省略授权时展开基础 degradation ID 及 Converter 的全部已登记缺值补全类别。用户无需手填速度等未知执行数值或逐项确认默认值。显式 allowed_degradations 数组仍限制补值；已存 Case 的显式规范参数不因新默认值变化而改写。展开值不同的请求不得共享 Case。

`fallback_policy.straight_preview` 支持只传需要覆盖的子字段。显式传 `{"enabled":true}` 且省略 `allowed_degradations` 时，规范化器自动加入 `straight_preview_policy_default`；若显式传授权数组，则缺少该ID立即失败。Coordinator 对新 v3 Case 默认允许近似；共享路由在接纳事实后判断是否需要预览，路口或完整可执行场景不进入预览。显式 false 禁止近似，已有 Case 恢复不迁移路由和预算。

终态信封包含 `status`、`generation_status`、`verification_level`、`source_contract_validation`、`render_verification`、`artifacts` 和 `diagnostics`。`done/done_degraded` 含四个正式 artifact；placeholder 不含正式 artifact；`needs_review` 含 conflict IDs、world epochs 和恢复点。无真实 runner 时 render verification 必须为 `not_executed`。

## 输入

- `--video`：必填，源视频绝对路径；源文件不复制进 Case。
- `--parameters-json`：可选 JSON 对象，保存相机、自车和目标平台参数。
- `--case-id`：可选，只恢复绑定同一路径和 SHA-256 的指定 Case。
- `--new`：可选且默认禁用；只有用户明确要求独立重跑时才强制创建新 Case。
- 新建 v3 Case 使用 `--new` 时默认进入独立 `core_scene_approximate`；`--strict-delivery` 显式选择原严格交付。显式 v2 保持原流程。`--case-id` 按已冻结的近似模式标记自动续接；无标记的旧 Case 保持严格路由。仅显式 `--case-id ID --core-scene-approximate` 可在旧 Case 停于 Behavior 且有当前请求绑定的终态完整回答时，从归档回答进入隔离近似分支；原 Case 不改，核心证据缺口照常拒绝。
- Core 正常优先；独立验收成功返回 `accepted`。非核心隔离背景观察的 Scope Review 冻结帧覆盖不足或 `identity_evidence_insufficient` 可自动进入已有 Description Fallback B，返回独立复验的 `unverified` Target、manifest 和原 Core 失败信封；B 自身证据及运动门禁仍须通过。恢复冻结 Core Case 使用同一路由；核心运动、来源错误及未分类异常不获降级授权。

相同视频 SHA-256、等价参数对象和当前 workflow contract 默认复用同一 Case；done 直接返回，未完成/failed 从检查点恢复。同 hash 新路径允许重绑。参数变化或 contract 变化创建新 Case，源内容变化以 `source_changed` 失败旧绑定并创建新 Case。

## 内部路由与顺序

```text
新建 v3 默认：World → N 个串行 Batch → reconciliation → 原始 Behavior 候选
               → 隔离核心 Analysis → 原 ConversionWork → 原 Converter → 独立近似验收

v3 显式严格：World → N 个串行 Batch → reconciliation → Behavior
               → deterministic Builder → Converter → Projector → report/publish

```

Coordinator 不直接执行以上任一内部阶段；orchestrator 直接执行转换 CLI，其余理解阶段才使用 Agent。

Agent 阶段由后台 task 驱动，task_id/session_id 在提交后立即持久化；同一阶段只允许一个 in-flight task。恢复时先接管有效 candidate、target/result 和报告检查点，再决定是否需要新任务。旧 v2 才保留短视频 Global 编译兼容路径；标准 v3 短视频仍执行 World 和 Behavior，只有 N=0 时跳过 Batch。

## 成功输出

```json
{
  "case_id": "case_xxxxxxxxxxxxxxxx",
  "status": "done",
  "artifacts": {
    "analysis.json": {"path": "/absolute/path", "sha256": "..."},
    "report.md": {"path": "/absolute/path", "sha256": "..."},
    "report.docx": {"path": "/absolute/path", "sha256": "..."},
    "target.json": {"path": "/absolute/path", "sha256": "..."}
  }
}
```

以上四件套信封只用于严格交付，四个 artifact 缺一不可。核心近似成功使用独立 `accepted` 信封，附带 `delivery_level=core_scene_approximate`、Target、manifest 与风险报告路径，不声称 `validate_completed`。Coordinator 原样返回实际信封，不重新解释或改写结构化结果。

## 错误码与恢复边界

用户显式授权转换超时重试时，在原 `--video ... --case-id ... --retry-failed` 命令上增加 `--authorize-transport-retry conversion_semantic_gap`。该入口先对账旧任务，再从已有检查点继续；不新建 Case、不清零普通恢复预算。授权门禁、绑定及一次性消费以 [编排恢复协议](../../../../global-video-understanding/skills/video-case-orchestrator/references/protocol.md) 为准；Coordinator 不手工修改 progress，也不自动连续授权。

错误码是可扩展字符串，以下按处理方式列出常见值，并非只允许原先九个值。调用方必须保留未识别的 code 和完整 error，不按字符串猜测恢复动作；以 error.stage、resume_from、automatic_retry、rule_id 和绑定摘要及冻结预算为准。

```text
unsupported_v1_limit
unsupported_scene_type
source_changed
packing_budget_exceeded
gateway_413
model_attempts_exhausted
schema_validation_failed
conversion_failed
publish_failed
world_patch_contract_blocked
world_evidence_insufficient
world_closure_no_progress
behavior_semantic_blocked
identity_conflict
identity_evidence_insufficient
conversion_policy_blocked
intersection_execution_blocked
preview_evidence_insufficient
preview_contract_blocked
preview_prerequisite_blocked
model_recovery_budget_exhausted
missing_output
backend_unavailable
submission_unresolved
task_status_unavailable
task_deadline_exceeded
service_access_denied
task_cancelled
gateway_429
gateway_5xx
local_dependency_missing
local_io_error
internal_pipeline_error
contract_incompatible
release_snapshot_mismatch
execution_policy_retired
```

- World/Behavior 合同阻塞：保留 candidate，按具体 rule_id 修复或提供证据。同一 candidate、输入和 validator 未改变时，普通重试不会产生新信息；`--new` 是独立重跑，不是通用修复。
- conversion_policy_blocked：显式限制或旧冻结清单缺授权，或策略违反已知事实；检查 diagnostics。标准新请求默认允许已登记补全。旧未交付 Case 仅因 conversion.fallback_unauthorized 停止且用户已表达自动补全意图时，使用 --case-id --retry-failed --enable-conversion-defaults；绑定已有 analysis，保存原失败/原策略和独立采用记录，只恢复转换，不改模型输入和预算。普通重试不增加授权，已知事实冲突不能通过该入口消除。
- submission_unresolved、task_status_unavailable、task_deadline_exceeded：远端可能仍运行，先对账，不能重复提交。service_access_denied 直接停止。
- model_recovery_budget_exhausted：保持持久预算，不因进程重启清零。local_dependency_missing/local_io_error/internal_pipeline_error：检查本地环境或代码，不转成模型补值。

失败时以 Case `state/progress.json.error` 为权威；不得把已有 candidate 或部分 final 文件作为成功交付。

## 工作区边界

唯一运行根目录为：

```text
/Users/baicaipaoshui/Documents/project/JIR/jlr_scenario_catalog/scenario_generation
```

Case 只位于 `video_extractor/cases/`。禁止读取或修改 `video_extractor/runs/`，禁止复用旧 workspace 状态。

## 显式误合并身份拆分

用户确认需要复核“两辆车误合并为一个编号”时，复用正式入口：

```bash
python skills/video-pipeline/scripts/process.py --video /absolute/source.mp4 --case-id case_ID --retry-failed --review-actor-motion actor_002 --actor-review-mode identity_split
```

编号仅作命令示例，必须使用目标 Case 中待复核的实际编号。拆分结论由绑定图片决定，不能把用户描述直接写成事实。已完成 Case 派生修订，原 Case 不变；未完成 Case 需已冻结 actor-identity-split.v1。复核确认后拆开身份及观察，重建下游；证据不足、事件依赖或预算不足如实停止。最多12张已有来源图，不要求 json-coverter 看图，不重置恢复预算，不承诺真实渲染已通过。普通新 Batch vehicle_census 未决时先解决上游身份，不能继续转换掩盖缺口。

历史 v1/v2 联合 closure 因布局引用越界或重复身份表达缺口产生 invalid_output 时，同一 `--resume-world-closure-session` 入口可申请一次 v3 合同修复。仅限首次 Behavior 前、最近原 session、远端已完成、归档哈希和错误复验通过；新请求不覆盖旧请求。该任务有一次独立账本，保留原已耗尽预算，不退款、不无限续接；新 v3 Case 的首次联合 closure 计正常任务，其内容修复仍共享原两次恢复额度。具体约束以 Global 编排协议为准。

## 未决观察的对话恢复

已有 v4 census accepted、当前停在首次 Behavior 前的 identity.census.unresolved 时，用户明确同意让 Behavior 判断未决观察范围，可使用 --case-id CASE --retry-failed --enable-behavior-census-scope。采用记录独立绑定原冻结策略，不改原 policy 或恢复额度，不与其他迁移/续接参数组合。首次正常 Behavior 仍可在额外额度耗尽后执行，但漏答补答、身份裁定与必要重建仍计原额度。仅有完整隔离对向证据的观察可排除；远处目标或身份相关对象不保证交付。未知时保留完整候选和现场，不独立 --new 重跑掩盖未决。新 Case 默认冻结该规则；旧 Case 普通重试不自动采用。

上述授权可以来自 Coordinator session 中的自然语言，无需用户输入命令。Coordinator 按 SKILL 的对话恢复规则选择明确的 Case、读取其原视频绑定，在内部调用同一个正式入口。已有范围规则按普通恢复执行；仅问原因或普通重试不会增加旧 Case 的采用授权。该对话路由不创建独立的 Worker chat 任务，仍由 Python 记录采用凭证、任务、预算和提交状态。响应以本次正式信封为准。
## 早期关联独立性恢复

新建 Case 自动冻结 `early-association-independence.v2` 并在正常 Behavior 验收内检查独立性，无需先失败或传递恢复参数。只有证据满足有界早期关联约束才继续，原始身份不确定性和全部候选车辆保留。使用 `--new` 从视频头验证新链路；不对已完成 Case 补写策略或重跑结果。

用户明确授权后，可对已经完成范围补充、仍因早期身份不足停在 Behavior 的原 Case 使用 `--case-id CASE --retry-failed --accept-early-association-independence`。此入口本地检查既有证据并追加绑定凭证，不重新判断已成功项、不改原始 Actor/轨迹、不重置预算或任务历史。不能组合 `--review-behavior-scope`、额度重新授权或其他修订入口。条件不满足仍停止；重复执行复用同一凭证。恢复命令随后进入正常下游，文字运动规划可能调用模型；仅准备命令不代表已实际恢复。


新 Case 冻结 `batch_evidence_capacity=batch-evidence-capacity.v1`：普通 Batch 默认最多4张；先按原规则跨批分配必需证据，仍有未覆盖引用时才允许相关普通批次扩到最多6张。新增名额只用于结构化必需证据和窗口边界，不增加普通抽样；总批数仍最多4，独立布局仍最多4张、单帧复核仍严格1张。扩展批次的所有证据在每档 JPEG 打包中保留，超出原请求12 MiB或单图2 MiB限制则停止，不删必需帧。选帧、打包、冻结 manifest、Batch 重放和只读交付预检共用容量合同；旧 manifest Schema 文件不改，在 Python 中按冻结策略组合6帧语法和严格用途校验。

不再提供旧 Case 的证据容量升级入口。新 Case 按冻结策略自动使用上述容量规则；已有容量采用回执保留只读校验，历史结果、冻结批次和原始失败记录不改写。
# 有限恢复入口

新标准 v3 默认使用 bounded-recovery.v1：同一冻结请求传输重试最多2次、
每阶段问题组内容修复最多2次、全 Case 额外模型任务最多8次；无进展提前停止。
范围帧索引可从已通过校验的完整证明本地重建，不消耗模型调用。
原始观察与历史保留，未知不自动忽略；具体计数、专用限制和提交规则以 Global 编排协议为准。

旧 Case 的有限恢复升级入口已移除。新 v3 Case 创建时自动冻结该规则，当前任务按原有预算和检查点恢复；历史采用回执仍可只读校验，不再创建新采用记录或退役旧补答请求。
