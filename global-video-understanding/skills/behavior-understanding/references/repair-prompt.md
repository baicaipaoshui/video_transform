Skill: behavior-understanding
只修订当前Case的Behavior，先读任务包 ${request_path}。
${reading_rule}输出base_sha256和replacements数组，每项path为现有字段JSON Pointer、value为修订值。
最多16个字段替换，允许补齐白名单中的缺失字段，不支持删除或数组增删，不得替换整组或整个实体。仅当任务包 repair_contract=video-fidelity-repair.v1 时，允许替换 allowed_paths 中明确指定的保真观察集合或缺失的 video_fidelity 子树；按包内 value_schemas 验收，其他字段仍不可修改。
只修正validation_error涉及的字段及必要关联字段，其他字段由代码保留。
语义修订使用任务包中的World/registry上下文或明确授权路径，引用本Case已有证据；若包内rules限制只读内嵌上下文及review_frames，以该范围为准，不额外读取文件。不得猜测。
不得删除局限性或更改impact_domains来掩盖核心未知。
仅当 `behavior-source-coverage.v1` 请求明确包含 `source_revision_authorization` 时，允许返回空 replacements 和 `source_dispositions`，每项严格为 `{point_index,action:"withdraw",reason}`。只可选择授权的缺证 Ego 点；事件证据、动作端点、车道转换端点和其他 Actor 均不可撤出。程序独立归档原始候选及修订凭据，统一收窄 Ego 总述并记录未决；不推算车道、坐标或速度。没有该授权的冻结请求沿用原权限。不能在普通 replacements 中伪造 `source_revision` / `source_revision_qualitative` 记录。
速度与坐标报错表示输出数据可能不自洽，不证明视频运动矛盾。不得用典型道路速度或改位置凑公式来修复；先核实固定坐标与相对距离是否混用。仅在任务包白名单允许时纠正有依据的值或保留unknown，不能越权删除旧值；权限不足或缺证时按任务包停止条件交接。
若证据不足已明确决定不生成事件，使用resolution=resolved_no_event且execution_effect=exclude_unconfirmed_event；事实未知与执行裁决完成必须分开表达。
只写一次 ${candidate_path} 后停止；不输出完整Behavior，不创建subagent。
