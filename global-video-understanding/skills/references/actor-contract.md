# Actor 跨阶段字段合同

字段类型唯一来源是 [actor_fields.schema.json](../schemas/actor_fields.schema.json)。World、Batch v3、累计 Registry 与 Behavior 的 Schema 引用该定义；必填集合仍由各阶段 Schema 决定。各阶段 Prompt 由编排器直接内联该定义，不要求 Worker 扩大文件读取范围。

World 的 actor_hypotheses 提供初步身份与位置假设。Batch 的 registry_after 是以前序登记表为基线的完整累计表，保留未在本批出现的对象、追加新对象及证据。Behavior 沿用登记身份，增加自己的 simulation_role、is_static 与多时刻 trajectory，不重新输出道路或车道定义。

actor_id、actor_type、identity_description 是各阶段共同身份字段；first_seen_frame_id 是 World/Registry 的必填身份字段，Behavior 无需重新声明。声明的身份字段均为非空字符串。actor_type 保留既有类型词汇，不为仿真统一改为 vehicle 或 sedan。

Batch 不删除历史 Actor，不改写 actor_id、actor_type、first_seen_frame_id、identity_description。Behavior 不改写 Registry 的 actor_type 与 identity_description，并必须保留登记对象。身份未决通过现有问题与证据交接，不用更换 ID 或改写描述消除未决。

hypothesized_lane 与 trajectory[*].lane_id 共用可空车道引用类型。已知值为冻结 World 的全局 lane_id，未知省略或写 null；数字、空字符串不是合法未知。World/Batch 的已知假设在交接前检查引用存在性；Behavior 沿用现有道路归属与序号检查。

初步车道与后续轨迹属于不同时刻，不要求它们始终相同。ordinal_from_right 沿所在道路的行驶方向从最右外边界开始，0 表示最右；查 World 的完整 lane_ids 或已知 lane_knowledge.anchors 才能得到全局 ID，不能使用全局 ID 的数字后缀反算序号。

正式 v3 Batch 只提交完整 registry_after，不提交 actor_updates/new_actors 根字段，也不启用 batch_delta_v1。当前模板与 batch_v3.schema.json 共同遵守这一输出协议；历史或其他内部增量协议不因此变成 Batch Worker 的权限。

继续保留合法历史字段与阶段扩展字段，不批量迁移、改写历史 Case 或冻结请求。该合同检查观察格式与身份连续性，不要求上游填满未知，也不改变 Converter 在私有执行副本固定补齐连接并生成 target 的规则。
