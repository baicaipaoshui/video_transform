使用 world-understanding，只处理现有 world_closure 任务中的联合裁定。
读取冻结请求 ${request_path}。只读其中 frames.path 的全部已有 JPEG；原始图像路径只作来源绑定，不扩大读取范围。不启动其他任务或 session，不改 World、Registry、Batch、请求或 Case 状态。
按 response_schema 写 ${candidate_path}。这是外层合同：layout_and_census 模式的 layout 按 layout_request 原冻结合同回答，census_after_layout 模式必须 layout=null，保留已经验收的布局。不要向旧布局 decision 塞身份字段。
逐项覆盖 records 的所有 issue_id，不能漏项或重复。Batch object_id 只在该批该帧内有效，比较组和 candidate_actor_ids 都是假设，不是同车结论。旧身份描述也必须核证。只有图像支持时才绑定；不能为通过门禁把 unresolved 改为 existing。
bindings.outcome：existing 关联已有 Actor；new 漏登车辆或误合并拆出的新车；excluded_opposing 仅用于 candidate_actor_ids=[] 的对象，必须证明它属于另一侧物理隔离的对向道路、没有同向交互依赖；unresolved 保留不足及原因。每项 evidence_frame_ids 必须包含该记录原帧，不能用文字关键词代替图像。排除的 separation 必须覆盖请求首末帧与相关记录帧，separator 只允许已观察的 barrier/median；道路侧别、方向、隔离或交互不明时不能排除。
existing/new 绑定 vehicle_key，vehicles.key 是回答内标记，最终 Actor ID 由程序分配。已有车辆 actor_id 写旧 ID，split_from_actor_id=null；漏登车辆两者=null；误合并拆出的新车 actor_id=null，split_from_actor_id=旧 ID，必须同时提供保留旧 ID 的另一辆车。每个原身份至多拆成两辆；旧 ID 必须保留原首次出现对象。两车至少一帧共现、各至少两帧。不要把连接的牵引车和挂车拆开。
vehicles.sightings 描述逐帧特征、真实时间、已确认 lane_id 或 null，以及 distinct_from_actor_ids。必须区分 confirmed_sightings 中同帧其他已登记车辆。身份关联必须包括原首次帧；提前 first_seen 时覆盖提供的过渡帧并有原首次之后的目击。只有车型、颜色、距离相似不足以关联。
同车关联保留旧 identity_description/color/vehicle_class，不用身份复核改外观。只有明确拆分的混合描述或新车可以提供新描述、颜色与车型；无法细分写 unknown。不要提供米制位置、速度、加速度，身份裁定不证明运动尺度。已知 road_reference 和车道不能丢弃或静默改写。
unresolved/excluded_opposing 的 vehicle_key=null；unresolved 的 separation=null。所有 vehicles 必须被 bindings 使用。允许保留部分未决，不能为了完成任务虚构结论；程序保存已验收成果，剩余阻塞仍会停止交付。只写 candidate 后停止。
