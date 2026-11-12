---
name: world-understanding
description: 处理已分配的 World 初始视觉理解、geometry_readjudication 或 world_closure 请求，只提交静态世界 candidate 或指定字段补丁。
---

# World Understanding

维护道路内序号与全局车道引用时使用 [统一位置合同](../references/lane-location-contract.md)，与 Behavior、Builder 和 Converter 共享含义。

新冻结事实修订的说明见 [观察补充](references/observation-prompt.md)，仅由编排器对相应新请求渲染。身份未知与冲突分开，隔离背景须绑定 actor 和帧，不能凭文字描述排除；旧请求不升级。

单四岔路口必须结构化交接道路对应、斑马线/信号灯存在性与状态、路侧停车区域的 road_id/side。不可见车道数和未知尺度仍按原契约保留，不因下游 intersection_approximation.v1 而填入150米、3.5米、2.5米或默认绿灯。执行近似不等于World事实闭合。

取证先明确下游需要的道路归属、方向、车道边界和路侧区域事实，再定位证据；同一道路的车道先从最右外边界向左编号，再定位车辆，不能用画面位置反推编号。必填目标字段不能作为猜值理由。路侧停车保留事实语义，paved shoulder 由下游映射；对向数量未知不能默认一条。已有图像无法解决的问题按当前合同交接后停止。

维护观察规则时查阅 [MELA 表达契约](../references/mela-expression-contract.md) 的字段表、停车映射与对向车道未知处理；运行 Worker 使用已渲染提示词，无需额外读取该全文，closure 的任务包范围不变。

本 Skill 是模型 Worker。根据请求中的 stage 选择一种工作，不启动流水线或其他 session。

`world-census-adjudication.v1` 复用现有 world_closure。按[联合裁定提示](references/world-census-adjudication-prompt.md)只读其冻结请求和指定已有 JPEG，写一个私有外层 candidate。布局回答仍遵守内层冻结合同；census 逐项裁定身份或执行范围，无法证明时保留 unresolved。模型不分配正式新 Actor ID、不直接改 Registry，不补造米制运动。census_after_layout 模式 layout=null，不能重新解释已验收布局。凭证、提交与预算归 Python。

`world-census-adjudication.v2` 使用[按问题组织证据的裁定提示](references/world-census-adjudication-v2-prompt.md)，仍复用同一 closure。遵守冻结 evidence_plan；分别证明候选否定与新关联。仅作比较的已有车辆使用 role=comparison，不能据此修订身份；不能把检查帧存在当作车辆连续可见的证明。不能完成 links 或共现证明时保留 unresolved。

| 请求 | 读取范围 | 唯一输出 |
|---|---|---|
| world（visual-answers.v2） | world_observation.request.json、其派生回答格式和允许高清帧 | world_context.candidate.json，同次回答静态道路、环境、Actor、问题；脚本机械补全 |
| world_topology（仅冻结 v1） | 旧 visual-answers.v1 请求及明确列出的高清帧，不读历史 World | topology_answers.candidate.json，小型视觉问答 |
| world（parts，仅冻结 v1） | 已编译道路上下文、派生局部 Schema 和允许帧 | world_context.candidate.json，仅非道路部分，由脚本组装 |
| world | 指定 case、global_frames、World Schema、索引允许的图像 | world_context.candidate.json |
| geometry_readjudication | 冻结 World、有效 Batch、reconciliation 中指定冲突 | world_context.readjudicated.candidate.json |
| world_closure | 哈希绑定任务包及包内允许的证据帧 | world_closure.candidate.json；新 Case 为 decision-v7，旧冻结布局沿用 patch-v1、decision-v2/v3/v4/v5/v6，或停止信封 |

静态道路、环境、初步 Actor 身份和待核实事实归本 Skill；动态运动歧义归 Behavior。只写请求指定 candidate 一次后停止。未知事实保留未知；闭合缺证返回 needs_evidence，权限不足返回 blocked_by_patch_contract。正式 World 和 epoch 由编排进程验收后提交。

新请求使用 [单次 World 补充](references/single-world-prompt.md)，视觉回答与编译边界见 [语义编译合同](references/semantic-compilation.md)。完整、稳定直路可回答紧凑道路观察；局部可见、未知或复杂道路在同次回答中使用原 World 结构，保留 minimum/unknown 和问题，不启动第二个 World session。旧冻结 v1 仍使用 [topology-answers-prompt.md](references/topology-answers-prompt.md) 与 [world-parts-prompt.md](references/world-parts-prompt.md)，不迁移原请求或产物。

编排器按模式渲染 [初始提示词](references/prompt.md)、[几何裁决提示词](references/readjudication-prompt.md) 或 [闭合提示词](references/closure-prompt.md)，无需模型额外读取模板。任务包限制优先；closure 不扩展读取完整 World/Schema。字段与验收边界见 [contract.md](references/contract.md)。维护者可用 `python skills/world-understanding/scripts/validate_candidate.py --candidate /absolute/world.json` 做离线检查，不提交状态。

lane-layout-review.v1 的 Full World 分别记录同向边界链、右外边界、自车初始位置及路侧用途。新 Case 的 Batch 差异通过已有 world_closure 的 [decision-v7 物理边界裁决模板](references/lane-layout-decision-v7-prompt.md) 回到本 Skill：同一次模型调用提交行驶边界、每条走廊和右侧全部连续物理区域的边界链；Python 按物理边界匹配旧区域，不按执行投影类型合并。模型仅对实际数值冲突提交争议区间；Python 自动计算 `minimum`/`exact` 数量和初始自车主张是否相容，并从 `kind=curb|road_edge` 推导登记类型。否定数量仍须指出具体区间与至少两帧直接证据；无法裁决则停止。Python 同步生成 World 的车道及路侧结构，并拒绝漏掉旧连续区域或让路侧与行驶车道占用同一空间。已有冻结 decision-v5 请求仍用[原标量模板](references/lane-layout-decision-v5-prompt.md)及原 Schema；冻结 decision-v4 使用[原 v4 模板](references/lane-layout-decision-v4-prompt.md)，decision-v3 使用[原 v3 模板](references/lane-layout-decision-v3-prompt.md)，decision-v2 使用[原单一裁决模板](references/lane-layout-decision-prompt.md)，patch-v1 使用[补丁模板](references/lane-layout-closure-prompt.md)，不隐式迁移。模型不直接改正式 World；缺证返回停止信封。


对象专属 question、ambiguity 和 semantic limitation 使用结构化 `actor_ids` 数组列出全部受影响对象；混合影响必须包含自车/车辆，不得只列行人。保留真实 actor_type 与观察事实，即使当前仿真策略不生成该对象。执行范围排除归 Python，模型不得用删除观察、改 simulation_role 或假设身份 resolved 来代替。未建立 ID 的对象保持未知，不虚构 Actor。

decision-v6/v7 的 ego_initial、old_lane_corridors、actor_corridors 仅用相邻 right_boundary_id/left_boundary_id、reason 和帧证据表达位置，不输出 ordinal_from_right。Python 根据行驶方向下的右到左边界链生成序号、车道 ID 和引用，自动生成初始自车数值主张的差异记录。模型仅提交 count 的 claim_disputes。自车边界关系及两侧边界需共同绑定真实首帧；缺证、方向不明或旧车道不能唯一映射则停止。冻结 v5 多区域请求继续使用原 v5 bands 模板和 Schema。

新 Case 使用 [decision-v7 模板](references/lane-layout-decision-v7-prompt.md)。减少车道数时允许两种有证据的解释：旧路侧区间误计，或不存在的内部边界把一条行驶车道重复拆分。后者在 disputed_interval 中写 observed_role=driving 与 absent_boundary_ids，至少两帧同时见到保留的两侧边界及完整走廊；所列旧内部边界必须解释全部数量差。仅相应连续旧车道可合并，已知属性冲突继续停止；编译器同步重建引用、移除被否定的旧分隔线和过时数组证据。独立的关系、数量和映射错误一并反馈，不能补造帧引用。冻结 v6 请求不隐式采用此能力。

`world-census-adjudication.v3` 使用[分域证据与重复身份裁定提示](references/world-census-adjudication-v3-prompt.md)。layout 只能引用 evidence_scopes.layout；census 使用自己的范围。已有 ID 疑似重复时通过 identity_groups 逐个证明原锚点与完整过渡，不能伪造不同车的否定证据；无法证明仍 unresolved。正式身份合并、问题解除与提交均由编译器执行。
