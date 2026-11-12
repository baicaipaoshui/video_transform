# Workspace Quality Baseline

本文提炼 `/Users/baicaipaoshui/Documents/project/JIR/workspaces` 中历史成功链路的有效做法。该目录只作为只读质量基线，不得参与当前 Case 的运行、恢复、缓存或发布。

## 值得保留的做法

- 对短视频保持完整时间覆盖：以 2 fps probe 覆盖首尾和事件演化，而不是只依赖少量全局关键帧。
- 每次模型调用最多查看 4 张按时间排列的图片，逐批形成结构化观察。
- 用 batch 文件和累计 actor registry 延续上下文；actor ID、类型、首次帧和身份描述保持稳定。
- 明确区分 `observed`、`estimated`、`unknown`：观察绑定帧或时间，估计包含置信度或范围，未知项说明原因。
- 聚合结果应满足工程转换所需的道路、车道、actor、轨迹、事件、环境和证据结构。
- 转换保持确定性，并由当前 `mela-business-logic` 四部分权威模型校验；OpenX 检查只作兼容诊断。

## 在稳定 Case 中的适配

- 每个 batch 和聚合均使用全新 session；连续性只来自 frozen manifest、global context、batch JSON 和 actor registry，不依赖会话历史图片。
- 模型直接提出的 focus window 最多 4 个；World 编译器可为不同问题保留更多精确窗口。Batch 规划将全部问题窗口覆盖到最多 4 个批次（独立布局核验占其中 1 个），不按排序截断末段。≤2.5秒最多1批、2.5–5秒最多2批；每批仍按 start/1⁄3/2⁄3/end 取帧。
- 每批最多 4 图、总详细帧最多 16、单请求低于 12 MiB。
- 工程估计可以保留，但必须标为 `estimated` 并带 `confidence` 或合理范围，不得伪装成直接观察。
- 模型只写 candidate 后立即结束；确定性代码负责 Schema、证据、编号、hash、转换和发布门禁。
- 重试只携带上一次确定性门禁的简短错误，不要求模型重新长篇推理或自我复核整条链路。

## 禁止照搬的旧行为

- 不复用同一个 session 累积所有历史图片。
- 不复制旧结果中的硬编码 actor ID、车型、速度或轨迹数值。
- 不接受模型自行声明的“校验通过”作为正式门禁结果。
- 不把旧 workspace、`runs/` 或临时输出当作 Case 检查点。
