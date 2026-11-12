# 视觉回答与表示编译：新 Case 使用 visual-answers.v2

仅新建 v3 Case 的 execution_policy 冻结 visual-answers.v2；旧 Case 恢复、复用及正式产物不自动升级。公共 World/Batch/Behavior/analysis/target Schema 不变，仍由唯一 Orchestrator 管生命周期。私有回答格式由现有 Schema 派生，不新增正式业务合同。

## 单次初始 World（v2）

新冻结 response_schema 在 Full World 分支禁止 Actor 的 hypothesized_ordinal_from_right，只在 road_observation 分支开放，与 Prompt 保持一致。已冻结请求的 Schema 不改写；编译器仍保留下述可证明冗余的兼容归一化，用于历史候选恢复。

1. 编排器冻结 state/world_observation.request.json：case.json、global_frames.json、execution_policy.json 和源图字节哈希、允许帧、派生回答格式。只有现有 world model_stage 使用它，不增加 session 或入口；访问范围仍是 prompt_only，未实现操作系统级沙箱。
2. 模型在同一次回答中提交道路、环境、Actor、questions、ambiguities 和证据。完整且稳定的无中央隔离直路可写 road_observation；Python 依据明确的右到左车道/边界顺序生成道路引用。局部可见、车道总数未知、路口、弯道或紧凑格式不能表达的设施/尺度使用同次回答的 world.road，保留原 Schema 的 minimum/unknown；不会为回退再发一个 World 任务。
3. 机械补全仅限：固定 contract_version/初始 epoch、完整道路的确定性 ID/数量/引用、显式 frame_id 对应的真实 PTS、缺失 question_id，以及问题显式 verification_window 到 critical_intervals 的去重分组。Full World 若误带任务私有的 Actor ordinal，只在 `null + null lane` 或 ordinal 与其已有 lane 引用能由 exact 清单/明确 anchor 证明一致时移除该冗余键；不由 ordinal 反推 lane，缺少位置映射或两者冲突仍停止。已有字段冲突或其他显式 null 不覆盖；缺少尺度不填宽度、路长、速度。问题的 resolution/impact_domains 和核实窗口仍由模型回答，不靠文字猜测。已有悬空 question 引用不因新编号碰巧相同而被修复。
4. 紧凑道路的每项事实需要至少两个真实时刻；完整稳定范围必须含真实首末帧。模型也必须实际看边界，脚本不能证明视觉判断正确。首帧 ego hint 只绑定首帧自车，不扩展为整个窗口约束。heading=0 是 Ego 参考轴约定，不是指南针测量。Actor ID 仍由模型明确提交，不由编译器拼接不同车辆。
5. 原回答、编译后候选及请求/候选/结果哈希保留审计；初始编译复用在线与只读候选校验。正式 World 仍经原 admission 后原子提交，之后才冻结 Batch；reconciliation 后才启动 Behavior。局部字段修复沿用原有范围/基线哈希/预算；道路语义冲突、输入漂移或不合规紧凑表示直接停止，不靠整份重生成重试。
6. 编号只对当前 World 版本确定，不承诺某个 lane_001 跨结构裁决始终是同一物理车道。Batch 继续绑定冻结 World/epoch/hash；不把旧 Batch 的标签当新 World 稳定身份。不在本次扩展几何裁决或跨版本车道迁移机制，无法安全接纳的结构冲突保持 needs_review。

基础计划任务是 World + N 个 Batch + Behavior，即 N+2；必要的独立复核和有界修复另计。该改动减少初始重复取证/表示负担，不声称已解决所有视觉识别错误。

## 旧冻结 v1 的两任务兼容路径

1. world_topology 使用现有 model_stage/agent lease，冻结 case.json、global_frames.json 和索引中源帧的字节哈希。请求只允许读取高清帧，不含先前 World、target 或历史车道假设。访问限制目前是 prompt_only，不宣称操作系统沙箱隔离。
2. 模型回答道路类型和完整性、右到左车道/边界顺序、两侧区域及首帧自车序号。每项给短理由和至少两个真实时刻的帧；当前完整直路必须有首末帧依据。完整结果不支持路口、弯道、实体中央隔离、变化横断面、未知路侧用途。未知或其他场景返回原因，进入已有 World 生产者；不是把未知当零，也不是立即强制 needs_review。
3. Python 为当前道路回答生成确定性 road/lane/marking/area ID、数量、引用、帧时间和 field_evidence，不承诺跨 epoch 物理身份稳定。道路 heading=0 是冻结 Ego 参考轴的表示约定，不是指南针测量。宽度、路长、速度不默认填充。车道清单只有明确完整才能编译为 exact；局部出入口绝不变成连续路肩。首帧 ego hint 不扩展为全窗口位置约束。
4. 验收保存原始 candidate 快照和 receipt（请求、候选、编译结果哈希）。后续读取会复算核对。后续模型只补非道路 World，使用冻结的派生 Schema；脚本组装后继续原 admission 和定向字段修复。新视觉反证用 ambiguity/world_paths 交给已有 Batch/reconciliation，而不是绕过裁决覆盖道路。
5. 原始回答与组装后候选均可审计。world_topology 首次是计划任务；最多两次尝试，后续尝试消耗已有全 Case 恢复预算，不重置预算。活动或受理不明任务按原机制对账，不重复提交。

这是一条现有流水线内的初始 World 子任务，不是第二套总入口。批次、Behavior、Builder、Converter 和交付状态机不换轨。缺证回退结果不声称已经改善该场景的识别质量。

## Behavior 的有限机械补全

仅在 visual-answers.v1/v2 启用时，轨迹 frame_id 可唯一生成 time_s，明确 road_id + ordinal_from_right 可通过最终交接的 World 唯一生成 lane_id。显式 null 保留；显式帧时间冲突、车道归属冲突和不唯一位置会拒绝。不从 actor_id、画面位置或前一帧猜车道；不生成速度、事件、身份或轨迹点。

在线验收、candidate CLI 和离线预检复用同一个 materialize 函数，记录补全路径和规则。原候选不原地修改。独立执行近似继续使用既有授权与 provenance，不进入观察编译器。

## 验收边界

自动测试只能证明编译、引用、输入绑定、恢复与原合同衔接。真实视频识别提升需要新 Case 的模型执行和人工对照；target 或源合同通过不等于真实 runner/render 验证。

### 2026-09-24 实施验证

- 新增 `tests/test_semantic_compilation.py`：29 项通过、0 失败、0 跳过，包含三车道/中间自车、不同车道数、路肩与出入口区分、未知/冲突拒绝、源字节绑定、独立进程编译一致性、模拟后台任务、两次尝试上限及活动任务对账。
- 相关回归（semantic_compilation、world_admission、case_fact_contract、frame_evidence_contract、video_fidelity、prompt_schema_alignment）：130 项通过、0 失败、0 跳过。
- 全量 `unittest discover -s tests -t . -q`：780 项，778 通过、2 失败、0 跳过。两项均为修改前已有的断言失败，未在本次扩展修复：`test_observation_analysis.ObservationAnalysisTests.test_world_observation_mode_keeps_identity_reference_and_geometry_gates`；`test_video_case.VideoCaseTests.test_highway_converter_uses_executable_initial_conditions_without_fabricating_actors`。不是环境依赖失败。
- 结构检查、接口指纹检查与 `git diff --check` 通过；公共 schemas 目录无本次修改。
- 新流程的 World 合成响应通过公共源合同和真实候选验收；没有执行真实视频模型任务，没有为旧 Case 重建 target，没有运行 MELA runner，render_verification 仍为 not_executed。合成测试中的“三车道”不是对用户视频的视觉验收结论。

### 2026-09-26 单次 World（v2）验证

- 实现位于 `_lib/world/single_world.py`；初始 World stage、只读候选 CLI 使用同一编译器，Case 创建冻结 v2，v1 编译器及两任务恢复保持兼容。
- 新增 `tests/test_single_world.py` 23 项全部通过；相关回归（single_world、semantic_compilation、case_fact_contract、world_admission、prompt_schema_alignment）112 项通过、0 失败、0 跳过。
- 新测试覆盖：紧凑与完整两种回答均只提交一个初始 World 任务、minimum/unknown 不补成 exact、Actor 序号与首帧线索、冲突/null 不覆盖、问题窗口/悬空引用、源图及输入哈希、活动任务对账、存档候选复验、只读校验、独立进程编译一致性和旧冻结策略不升级。
- 合成新 Case 实际经过 World 验收、N=0 冻结、reconciliation、Behavior、Builder、Converter、报告与发布，四个产物及完成信封通过校验，两次独立 Converter 的 target 字节相同。该测试的米制值是显式合成输入，不是模型测量或编译器默认值；使用模拟/预置响应，不代表真实视频端到端识别通过。
- 修改前全量基线 780 项：778 通过、2 失败、0 跳过；修改后全量 803 项：801 通过、2 失败、0 跳过。失败仍是上节列出的同两项既有断言失败，非环境依赖失败，本次未扩展修改。
- 22 个公共 Schema 文件与修改前 SHA256 完全一致；结构检查、接口指纹检查和 `git diff --check` 通过。未提交真实模型任务、未修改旧 Case、未运行 MELA runner；render_verification 仍为 not_executed。

### 2026-09-27 视频到 World 的职责收拢与验收

- 正式 Coordinator/Orchestrator 入口及六个可发现 Skill 不变，不增加 Agent、总入口或状态机。视频准备按冻结观察模式选择打包预算合同；v2 不再使用旧 Global Schema/业务提示词，仍保留后续使用的 packed 帧与真实源帧。
- World 先选冻结模式：新版单次观察、旧版兼容分别委托；正常候选接纳与存档恢复分开，但复用同一个编译/验收回调和持久预算。v1 两任务恢复及共享道路编译器保留，没有删除完整兼容文件。
- 提示词先选唯一任务合同，再组合共享视觉原则；v2 不再叠加旧完整 World 必填清单和覆盖式说明。观察补充只对已冻结 observation-execution.v1 的请求启用。公开 Schema 与私有回答格式不变。
- 一次 admission 执行一次公共 Schema 检查，之后复用事实和声明检查；不同提交/冻结/交接边界仍保留检查。一般 actor_ids、区间 ambiguity_ids 和 ambiguity.related_question_ids 必须引用已有对象（ego 保留隐式身份）。自动问题编号也避开已有歧义引用，不能碰巧修复悬空引用。
- 原 World 观察请求及源图哈希在 World 复用、Batch 冻结、Behavior 冻结时只读核对；原请求缺失不重建，输入漂移停止。初始数量观察可以交给 Batch，Batch/reconciliation 后仍缺 Ego 道路/车道定义则以 world.handoff.lane_definitions 停在 aggregating/needs_review，不启动 Behavior、不猜填连接或车道。
- 修改前全量基线：832 通过、0 失败、0 跳过。新增 `tests/test_world_flow_simplification.py` 21 项；最终全量回归：853 通过、0 失败、0 跳过（79.128 秒）。最终针对性回归 88 通过、0 失败、0 跳过；覆盖候选等价、unknown/null 保留、原始快照、单次 Schema 检查、漂移/缺请求、悬空引用、交接停止和恢复阶段。
- 新版 v3 的真实 FFmpeg 抽帧测试验证 2.3 秒/10 fps 视频末帧为 2.2 秒、真实首末像素和准备检查点复用；其他既有抽帧/时序测试也在全量中通过。合成/预置 Worker 响应的完整交付与典型停止测试通过，但不是对真实视频识别质量的验收。
- 22 个公共 Schema 文件与本轮修改前字节哈希完全一致；结构检查、源接口指纹、两个正式入口的 `--help` 和 `git diff --check` 通过。生成接口清单只同步当前代码/模板指纹，没有改 Schema。现有 Pydantic 废弃提示不影响测试，无环境依赖失败。
- 未执行真实模型任务，未重跑或改写历史 Case，未运行 MELA runner；render_verification 仍为 not_executed。实际新视频观察效果仍需另行验证，不能由本轮回归推导为每个视频均识别正确。
