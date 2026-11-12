# AGENTS.md — Deterministic JSON Converter

请求 policy 的 `source_witness_revision=motion-witnesses.v1` 是独立冻结的程序约束版本。Converter 继续复用 speed_knots，不重解释已确认身份、主要变道或未确认的超越完成状态。接收与最终检查复验相同来源处理记录、稳定车距和车身部位约束；Builder 缺值说明只能由实际执行补值回执闭合。原始观察不改，分段速度、车长/距离近似和摘要取舍须留档。无该版本标记的旧请求及成功 Case 保持原语义。

来源绑定的 `single_frame_motion` 保留已确认车辆，不把未采用的早期斑点变成同一身份。规划请求中的车道选择仅用于执行，原 analysis 的未知车道不改；Converter 使用相同选择及前段/尾段数值，逐项记录假设。旁侧/远距包络不是测量值，必须与其他车辆联合通过既有运动检查。`program_owned_motion_assumptions` 的纯未知运动来源由 Python归一到 unobserved，不能因模型换分类再次要求身份证明；新的身份或动作反证仍拒绝。未覆盖文字仍需正常转换关系任务，不将程序生成的部分方案当作完整验收。

`bounded-execution.v1` 使用已绑定来源补明确的时刻关系；请求的 `program_owned_source_relations` 不需要模型重复抄写。剩余关系完整后，Python 在正常数值阶段提交可重放的程序检查点，优先有限联合匀速规划；不启动数值模型任务、不覆盖已接受计划/已知值、不删车或改变主要动作。仅边界接触的变道时刻可按来源许可作执行近似，观察包络与执行区间分别留档。有限搜索失败明确归为规划能力/策略问题，不声称场景无解。旧无标记请求及已完成 Case 保持原语义。

若文字转换请求带 `program_owned_unobserved_motion`，该清单中的来源处置和未观察尾段数值由 Python 按 `retained-unobserved-motion.v1` 完成；保留这些已确认车辆，不重新求身份或无交互证明。模型仍负责其他来源、车辆与主要动作，未知不能伪装为观察事实。程序生成的方案使用同一转换合同和最终校验；原始 save、来源凭证、数值假设与重启重放保留。无此字段的旧请求保持原规则。

生产 Converter 仍是由 `global-video-understanding` 同步调用的确定性 CLI。新 Case 的 `conversion-continuation.v1` 使用两个正常文字会话：先整理时序关系，再联合规划执行数值。模型只读取冻结 analysis/request 及请求携带的检查点，不看图片或视频，只在指定 generation 目录按序写累积 save 文件，及时保存后继续当前会话；不自行创建会话。最终方案仍为 `conversion-semantic-motion.v1`，数值注明执行近似，不能改已知事实。Case 生命周期、任务租约、检查点验收和受控接续由编排器拥有。未冻结 continuation 的历史运动任务保持单 candidate；历史 semantic-gap v1/v2/v3 保留冻结合同。

- 拥有 analysis 到 MELA Preview JSON 的固定映射、硬编码执行兜底与权威 Pydantic/下游静态合同校验，但不拥有 Case 生命周期。
- 转换顺序固定为 analysis 可用值优先。标准新 Case 默认允许现有执行缺值补全，按冻结 conversion-policy.v1 逐字段应用；显式限制清单和旧冻结清单仍受门禁约束。无策略的历史 Case 保留原转换规则。所有补值与执行对象排除必须写入 conversion_result.json，不得反向修改 analysis。
- 路口连接缺失按固定规则补齐并生成 target，不要求关系复核证书；规则和回执 reason 见 case-conversion/references/fallbacks.md。已有连接优先保留，补齐不代表视频观察确认。
- 保留 analysis 的天气和 `curbside_parking` 事实；target 执行兼容值及 paved shoulder 映射不得反向污染事实层。
- 固定 analysis 与代码生成确定性 ID 和规范 JSON；跨 section 检查名字及父对象引用。新合同禁止把已知弯道改为直道或把已知变化灯色替换为常量，即使授权补值也不能覆盖已知事实。已观察设施缺少状态或米制位置时，补值须符合冻结策略与授权。历史全绿灯行为仅限未绑定新策略的旧 Case，详见 fallback 新旧边界。
- Converter CLI 不读取视频、Batch 或历史，也不自行调用模型。语义任务只读冻结 analysis/request，新运动合同输出时间关系、原文引用与执行近似数值，历史车距合同仍只输出档位；不写 target；观察冲突保留在 analysis，目标执行副本按固定映射生成。不生成报告、不发布，也不向用户返回整条流水线的最终答复。

正式入口见 `skills/case-conversion/SKILL.md`；协议和映射规则位于该 Skill 的 `references/`。
