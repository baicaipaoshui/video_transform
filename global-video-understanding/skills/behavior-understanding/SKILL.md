---
name: behavior-understanding
description: 处理已分配的 Behavior 动态理解或定向修复，基于冻结 World 和证据输出 actors、events、limitations 与事件动作 candidate。
---

# Behavior Understanding

道路内 ordinal_from_right 从0开始，全局 lane_id 引用 World 定义；维护规则见 [统一位置合同](../references/lane-location-contract.md)。轨迹可结构化声明 road_id/ordinal_from_right，不能在出口重新从 lane_001 编号。

冻结 visual-answers.v1/v2 的请求渲染 [机械字段补全说明](references/semantic-fields-prompt.md)：有实际 frame_id 的点可省略 time_s，有明确 road_id/ordinal_from_right 的点可省略 lane_id。脚本只查最终交接 World 的唯一映射，不改 null、不覆盖冲突、不推断动态事实。其他字段仍用原 Schema；未冻结此能力的旧 Case 不启用。

intersection_review 是独立只读关系复核，使用 [路口复核合同](references/intersection-review.md) 及其冻结请求。只提交证书或受限证据补丁，不生成米制策略值、不回写 World/Behavior。未知冲突行人不能自动排除。

`actor-identity-link.v1` 使用 [身份复核提示](references/identity-link-prompt.md)，只查看请求绑定的连续早期窗口并提交同车/不同车/证据不足裁决，不修改正式身份。若 Behavior 请求附有已验收身份凭证，该凭证明确批准的早期关联优先于旧 Batch 的该项声明；读取归档的原 Behavior，补足已确认的早期样本，保留其他观察和事件，仅移除凭证列明的旧身份局限。身份确认不提供速度/米制距离，后者仍遵守观察与执行近似边界。

先确认身份、车道、前后关系和相对车距趋势；车道必须先从最右外边界向左建立编号，再用车辆两侧边界定位，不能从画面位置或上游标签直接判断。绝对数值无尺度依据时速度保留 unknown、坐标分量省略，不能按典型道路速度猜填。定性观察可独立提交；固定起始坐标与当前相对距离不可混用。

独立 preview_relations.v1 复核沿用 steady/receding/approaching/unknown，判断依据是帧证据；Builder 支持通过全部门禁的 steady/receding/approaching 前车。Worker 不指定仿真速度，也不覆盖原数值；不能用近似关系消除已有数据矛盾。

新复核请求逐 Actor 给出 required_actor_evidence，evidence_frame_ids 必须逐项一致；intersection_path.v1 另允许从最多12帧的冻结目录选择 path_evidence_frame_ids，作为车道列与路径见证但不新增轨迹点。复核对 World 的反证只能形成 world_challenges，由 World owner 定向重裁。局部证据修复只读指定请求及其图像，输出绑定 base_sha256 的 replacements，仅修改 allowed_paths；不得输出整份复核或改身份、车道、运动。

面向映射所需的稳定身份、首点位置、速度时序和事件边界取证；停车区域存在不证明车辆静止或给出其位置。目标速度单位、参考对象名称和动作编码由代码转换，不能在上游切换单位或补造执行值。证据不足保留 limitation 并交接，执行能力限制不靠重复观察消除。

维护字段取证规则时查阅 [MELA 表达契约](../references/mela-expression-contract.md) 的动态字段及停止条件；运行 Worker 不额外读取全文，repair 仍只消费指定任务包。

本 Skill 是模型 Worker，仅处理当前 Behavior 请求。输入为指定冻结 World、最终 Registry、有效 Batch、帧索引与 Behavior Schema；Census 阅读材料以该冻结请求的交接说明为准，新请求的精简视图保留完整来源按 ID 回查，旧请求沿用原证书交接。修复时以 behavior_repair.request.json 的读取与写入范围为准。

1. 保留 Registry 身份，区分 participant/background 与是否静止。
2. 基于证据给出轨迹、事件、Behavior-owned ambiguity 裁决及不确定性；World 事实冲突交还 World 所有者。
3. 输出请求指定 candidate。initial_state 由编排进程从轨迹首点确定性派生；模型只给轨迹和事件动作。
4. 只写一次后停止；不生成 analysis、target、报告，不推进 phase 或发布。

初始任务使用 [prompt.md](references/prompt.md)，小范围修复使用 [repair-prompt.md](references/repair-prompt.md)，两者均由编排器渲染。合同与执行验收见 [contract.md](references/contract.md)。不得靠删除 limitation 或重命名 impact_domains 隐藏未知。

维护者可运行 `python skills/behavior-understanding/scripts/validate_candidate.py --candidate /absolute/behavior.json --case-dir /absolute/case` 做只读验收，模型运行时不需要 shell。


对象专属 question、ambiguity 和 semantic limitation 使用结构化 `actor_ids` 数组列出全部受影响对象；混合影响必须包含自车/车辆，不得只列行人。保留真实 actor_type 与观察事实，即使当前仿真策略不生成该对象。执行范围排除归 Python，模型不得用删除观察、改 simulation_role 或假设身份 resolved 来代替。未建立 ID 的对象保持未知，不虚构 Actor。

`actor-identity-split.v1` 请求使用 references/identity-split-prompt.md；只写两车身份/逐帧证据及旧样本归属候选，编号由请求分配。确认后正式修订和下游重建由编排器执行。Behavior 重建读身份凭证，旧混合身份描述和对应旧样本归属已被取代；无关车辆/事件/局限及已知数值必须保留。

若当前请求明确附带 census v4 的观察级范围清单，同一次 candidate 按请求输出私有 census_scope_review；这是基础根字段规则的受限例外，正式 Schema 由 Python 去除该字段后验收。逐 issue 对原图明确 confirmed/challenged/unknown，不把事件列表缺席当作无关。Worker 不撤销正式凭证或推进补充任务；这些由编排器处理。

若任务包 repair_contract=behavior-scope-completion.v1，这是完整 Behavior 范围漏答或已知帧覆盖不足后的受限补答：只读 base_path 完整冻结基线和 frames 图像，输出且仅输出 {base_sha256,census_scope_review}，覆盖全部有效 issue。不要输出 replacements 或完整 Behavior；Python 合并补答，已有事实不变。缺证仍答 unknown，不得默认 confirmed。此合同优先于普通修复的 replacements 要求；模型仍只写指定 candidate。

若任务包 revision=behavior-observation-scope-review.v1，仅补充 `issue_ids` 指定观察的执行范围：读取 base_path 和 frames 全部图像，只输出 `{base_sha256,decisions}`，每个指定 issue 恰好一次。不得改其他范围回答、车辆、轨迹、事件、局限、World 或 Registry。候选关联不等于执行依赖；同向且无隔离也可由完整逐帧 `unrelated_observation` 证明排除。单一候选的可选提前目击可在完整证明里提供 `candidate_independence={actor_id,trajectory_frame_ids,observation_used:false,reason}`，逐项说明不使用此观察仍有独立的后续已确认轨迹；不能猜身份或提前补轨迹。无法排除路径、换道、跟驰、避让、碰撞或身份依赖时仍答 unknown/needs_identity。Python 保留原回答和所有未选项，在执行副本消费验收证明。

上述例外同样适用于带 scope_context 的运动复核、独立车道复核和普通 replacements 修复：按请求读取完整基线与全部指定帧，针对应用本轮补丁后的 Behavior 重新复查全部有效 issue，不能继承旧 review。完整基线只扩大读取范围，不扩大字段修改权限；无 scope_context 的请求保持原响应合同。

冻结 execution-gap-recovery.v1 的正常 Behavior 请求会附带身份/车道核查清单。同次检查，避免把纯尺度缺失混入身份/动作限制。可选私有根字段 lane_closures 的形状以冻结请求为准：逐项声明 collection、issue_id、actor_id、outcome、evidence_frame_ids、reason；confirmed 仅表示单 Actor 的车道分量有全部轨迹帧的边界依据，不能表示身份或运动已确认。Python 保留原描述和其他限制，只闭合车道部分；不修改原 World。独立车道修复必须由 packet.lane_closure_revision 明确授权，原 allowed_paths 不扩大。范围两字段补答仍只能输出 base_sha256/census_scope_review，不接受 lane_closures。无新策略的任务不得使用此额外根字段。
