# Scenario Generation

本项目把前视行车视频转换为可审计的 World、Batch、Behavior 事实，再确定性生成 `analysis.json`、MELA `target.json` 和报告。正式用户入口是 `video-coordinator`，Case 生命周期只由 `global-video-understanding` 的 Python 编排器管理。

## 支持环境

- macOS 或 Linux。编排器使用 `fcntl` 文件锁，当前不支持原生 Windows。
- CPython 3.12 或更高版本。
- QwenPaw，且 `qwenpaw` 命令可执行。
- `ffmpeg` 和 `ffprobe` 可从 `PATH` 找到。
- 能处理视频/图像的视觉模型，以及一个文本模型。

运行态目录和凭证不会提交到 Git。新的工作站会重新创建 `cases/`、`sessions/`、`memory/`、`history.db` 等目录；缺少这些历史文件不会阻止新 Case 运行。

## 1. 配置 QwenPaw 模型

先在本机 QwenPaw 中配置 Provider、模型和凭证。安装脚本默认读取工作区 `agent.json` 中的 `active_model`；同组 Agent 配置不一致时会要求显式指定环境变量。当前映射是：

| Agent | Provider / 模型 |
|---|---|
| `global-video-understanding` | `mine/qwen3.8-max` |
| `video_extractor` | `mine/qwen3.8-max` |
| `video-coordinator` | `volcengine-cn-agentplan/glm-5.3` |
| `json-coverter` | `volcengine-cn-agentplan/glm-5.3` |

仓库不包含 `credentials.yaml`。每位使用者必须在自己的 QwenPaw 环境中配置凭证。

如果本机使用不同的 Provider 或模型，通过环境变量传给安装脚本：

```bash
SCENARIO_VISION_PROVIDER_ID=my-vision-provider \
SCENARIO_VISION_MODEL_ID=my-vision-model \
SCENARIO_TEXT_PROVIDER_ID=my-text-provider \
SCENARIO_TEXT_MODEL_ID=my-text-model \
./setup_qwenpaw.sh
```

视觉模型必须能够读取编排器提交的帧图像。Provider 和模型必须在运行脚本前已经存在于 QwenPaw 中，否则 Agent 注册会被 QwenPaw 拒绝。

## 2. 初始化项目

从本目录执行：

```bash
chmod +x setup_qwenpaw.sh
./setup_qwenpaw.sh
```

脚本会：

1. 创建项目 `.venv`；
2. 安装 [requirements.txt](requirements.txt) 中锁定的 Python 依赖；
3. 把四个 `agent.json` 的 `workspace_dir` 更新为当前克隆路径；
4. 使用现有工作区注册四个 QwenPaw Agent；
5. 检查依赖导入、Skill 结构和正式入口。

QwenPaw 创建 Agent 时会尝试生成工作区模板。脚本会临时备份并恢复项目的 `agent.json` 和 `skill.json`，不会用默认模板覆盖项目配置；已经存在的 Markdown 文件也不会被替换。

常用选项：

```bash
# 只检查，不修改文件、不安装依赖
./setup_qwenpaw.sh --check

# 使用已有 .venv
./setup_qwenpaw.sh --skip-install

# 只安装依赖并更新 agent.json，不注册 Agent
./setup_qwenpaw.sh --skip-register
```

如果 QwenPaw 不在 `PATH` 中：

```bash
QWENPAW_BIN=/absolute/path/to/qwenpaw ./setup_qwenpaw.sh
```

如果同名 Agent 已经注册到了另一个目录，脚本会停止，不会自动删除或覆盖该 Agent。请先在 QwenPaw 中确认旧配置，再重新运行安装脚本。

## 3. 验证安装

```bash
./setup_qwenpaw.sh --check

.venv/bin/python \
  global-video-understanding/skills/video-case-orchestrator/scripts/validate_structure.py

.venv/bin/python \
  global-video-understanding/skills/video-case-orchestrator/scripts/check_fidelity_contract.py

.venv/bin/python -m unittest discover -s tests -t . -q
```

修改 Schema、消费代码或纳入发布指纹的提示词后，完成合同核对，再运行 `check_fidelity_contract.py --generate` 更新生成清单，并运行 `tests.test_video_fidelity`。上述不带 `--generate` 的检查用于确认发布清单与当前文件一致。

结构检查只验证目录和接口，不等同于真实视频端到端成功。模型调用还依赖本机 QwenPaw Provider、凭证和服务状态。

原 Behavior 已因身份证据不足失败、且采用 `bounded-recovery.v1` 的未交付 Case，可通过正式入口的
`--case-id ID --retry-failed --review-execution-scope` 授权一次独立下游执行范围复核。
它在旧 World 身份恢复之前检查完整冻结帧，对候选 Actor 和每条未决观察分别证明执行无关；
不修改 Behavior 合同、提示词、Schema、身份或轨迹。事件/动作引用车辆及至少三帧具有已知车道的轨迹必须保留。
复核只消耗现有总预算中的一次额外任务，重复命令不增额；缺证或涉及保留对象依赖仍停止。
通过后仅执行投影裁剪，保留原始争议和证据，正常语义运动补全及严格转换继续运行。

若该严格复核已返回完整的 `retain/unknown` 回答而停止，且用户明确接受核心场景简化，
可用 `--case-id ID --retry-failed --core-scene-actors ego actor_001 actor_002` 指定核心集合。
编号按当前 Case 选择，不是通用固定三车。`core-scene-approximation.v1` 本地生成独立执行凭证，
不派新的范围模型任务；保留失败回答、依赖警告和未决身份，不把它们改成无关证明。
只允许省略原复核候选中最多两帧的车辆观察；事件/动作、持续定位轨迹及核心关系依赖受保护，
涉及保留车辆的混合身份、道路、事件问题仍阻塞。通过后正常生成运动计划与 target，标记 `done_degraded`；
检查只覆盖生成的核心场景，不证明被省略车辆在真实视频中无影响。旧策略和预算不重置，普通重试不自动采用该模式。

新建 v3 Case 在执行策略中冻结 `actor_fact_review=actor-facts.v1`，复用现有 World 字段：身份问题与车道、外观、运动问题分别回答；已知 Actor 的身份疑问必须绑定一致编号和来源帧。Batch 对同一独立问题的完整确认覆盖取证范围且无未决冲突时，可消解其他批次的局部缺证。参与车辆在直路上完全没有已知车道或明确道路/序号定位时，以 `lane_evidence_insufficient` 保留待复核，避免默认落到道路第一车道；只有后续出画而已有已知车道的车辆仍交给 Converter 进行有记录的直行补全。此门禁不自动新增模型任务。旧冻结 Case 不隐式升级。

身份问题按状态准入：`unresolved` 和 `non_core` 涉及已知 Actor 时仍须提供匹配的 `identity_binding`；`resolved` 可省略该绑定，但须保留有效 Actor 引用及至少一条带来源帧和理由的 `observed` 证据。若仍提供绑定，其 Actor 和证据覆盖要求保持不变。census v3 在完整证明后移除未知绑定、写入结案证据；原问题及合并前 ID 由冻结的 World 快照和裁定凭证保留，不能仅改状态来替代身份证明。

新建 Actor 复核 Batch 优先在现有总预算内使用宽 2560 px 的图片，并逐档降低至每图不超过 2 MiB；独立布局请求使用已绑定的 packed JPEG。旧请求的图片路径和哈希保持冻结，不能把旧任务当作使用新图片重新验收。

## 4. 运行新视频

必须从 `video-coordinator` 工作区调用正式入口：

```bash
cd video-coordinator
../.venv/bin/python skills/video-pipeline/scripts/process.py \
  --video /absolute/path/to/video.mp4 \
  --new
```

省略版本时，`0 < duration_s <= 2.1` 的视频自动选择 v2 兼容短视频流程（包含 2.002 秒编码偏差），其他视频默认 v3；入口打印选择结果。显式 `--parameters-json '{"workflow_version":"v3"}'` 或 v3 专用模式优先，已有 `--case-id` 保留冻结版本。`--new` 仅用于明确独立重跑。

正式结果保存在：

```text
video_extractor/cases/<case_id>/final/
```

成功或降级成功的 Case 应声明：

- `analysis.json`
- `target.json`
- `report.md`
- `report.docx`

当前仓库没有可用的真实 MELA runner。生成并校验 `.xodr/.xosc` 或通过 Pydantic 校验不代表已经完成真实渲染；没有真实 runner 证据时，`render_verification` 应保持 `not_executed`。

新 Case 的未决车辆由既有 Behavior 任务回答范围问题后，Python 自动裁定执行方式：有完整物理隔离证据的对向车排除；无同帧硬冲突的单候选早期目击默认关联既有 Actor；远处极小、车道未知且无动作/道路依赖的目标仅保留观察；多候选车辆只有在原回答明确指出自车右邻走廊、右邻同向车道唯一且无动作/道路依赖时，才在执行副本生成一辆代表车。源事实不改，报告标记仿真假设。条件不满足时继续按原恢复/阻塞规则处理，无人工选择或额外模型字段。

## 故障排查

固定高速视频验收以用户确认的 `case_20261011T231731758Z_8adeacad` 为基准。
运行 `.venv/bin/python audit_reports/highway_reference_20261012/verify.py`，只读重放 World、历史 Batch、Behavior、Builder、转换、报告和发布物复验，并检查目标与基准哈希一致。脚本另在临时合同中重放失败样本的联合布局/census 裁决；不修改原 Case、不调用模型。证据和结果见 [固定样本验收说明](audit_reports/highway_reference_20261012/README.md)。离线通过不代表新模型每次都能生成正确答案或真实 MELA 渲染通过。

新 Case 的布局合同为 `lane-layout-decision.v7`：有两帧共同边界证据时，允许否定不存在的内部分隔线并合并连续的旧车道，同步修订边界、车道引用和字段证据。旧冻结合同不隐式升级，已知属性冲突仍停止。校验会同时报告可独立判断的关系帧、数量争议和映射错误。

- `Provider ... not found`：先在 QwenPaw 中创建对应 Provider，或使用环境变量选择现有 Provider/模型。
- `Agent ... already points to ...`：同名 Agent 已注册到另一个工作区；确认后更新或移除旧 QwenPaw 配置。
- `ffmpeg/ffprobe ... not found`：安装 FFmpeg 并加入 `PATH`。
- `.venv must use CPython 3.12`：删除或改名旧 `.venv` 后，用 Python 3.12 重新运行脚本。
- 后端任务超时或状态不明：不要直接重复提交；按 Case 的冻结请求、lease 和恢复协议先对账。

更详细的职责与恢复协议见：

- `video-coordinator/AGENTS.md`
- `global-video-understanding/AGENTS.md`
- `video-coordinator/skills/video-pipeline/SKILL.md`
- `global-video-understanding/skills/video-case-orchestrator/references/architecture.md`
- `global-video-understanding/skills/video-case-orchestrator/references/protocol.md`

新 Case 使用 census v4 的受限观察级范围裁定：仅有充分逐帧证据的静止、车行道外、屏障隔离观察可申请 observation_only，身份仍未知；须排除已登记车辆候选并在正常 Behavior 中逐项明确复查。反证保存完整检查点、追加撤销并共享原恢复预算；已登记车辆仍保留。交付绑定同一范围凭证并报告排除观察，标记 done_degraded。历史 census v1/v2/v3 不隐式迁移。
