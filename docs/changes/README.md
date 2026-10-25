# 具身 Agent 重构记录

2026-10-23。按单一主题逐步提交，每次提交包含本目录中的对应说明。
讨论材料保留在原 research 目录，没有将尚未跟踪的研究资料混入代码提交。

| 步骤 | 提交 | 内容与说明 |
| --- | --- | --- |
| 001 | e4f737c | [状态贡献契约](001-state-contracts.md) |
| 002 | 1810d1b | [状态记录与会话分离](002-state-journal.md) |
| 003 | 572f4f5 | [执行控制与生命周期](003-execution-control.md) |
| 004 | dfdb5d1 | [验证目标及采纳条件](004-verification-adoption.md) |
| 005 | 3e66e83 | [能力后端装配与 Tool](005-capability-backends.md) |
| 006 | ad39dca | [Cobotmagic 契约迁移](006-cobotmagic-contracts.md) |
| 007 | 9748572 | [离线测试夹具](007-offline-test-fixtures.md) |
| 008 | abf892f | [跨模块证据检查](008-evidence-boundaries.md) |
| 009 | 473d830 | [精简上下文与查看视图](009-responsibility-views.md) |
| 010 | b190aef | [严格验收时间关系](010-verification-timing.md) |
| 011 | 93396e0 | [架构与接入文档同步](011-architecture-documentation.md) |
| 012 | b085155 | [多模态资源契约](012-multimodal-resources.md) |
| 013 | eaf0675 | [空值与不完整状态](013-nullable-state.md) |
| 014 | e7746dd | [多模态能力后端](014-multimodal-backends.md) |
| 015 | 4c8320a | [多模态验证链路](015-multimodal-verification.md) |
| 016 | b21fcf1 | [多模态接入文档](016-multimodal-documentation.md) |
| 017 | 本文所在提交 | [main 生产路径与安装包检查](017-main-production-audit.md) |

2026-10-25 多模态二次重构：核心 114 项全部通过；扩展离线回归 160 项中 153 项通过、
7 项按可选环境条件跳过。新增示例及验证范围见 016；本轮依旧每个提交一个主题并附说明。

核心验证从原始 70 项增长到 103 项，全部通过。扩展全量运行 160 项，153 项通过、
7 项按原有可选环境条件跳过；未运行真实机器人或加载真实感知／VLA 模型。
最后的严格时间检查修改后，再运行受影响的相机、动作、网页和恢复测试。

当前范围与限制：

- 统一状态贡献、执行回执和验证判断，提供可配置的状态后端与显式回退接口。
- 主 Agent、独立验证器、扩展模型的结果通过明确入口记录；默认验证仍是建议模式。
- 对象跟踪、模型准确性与任务效果没有由契约替代，需要实际任务验证。
- 控制仲裁仍以当前进程和机器人后端配合为基础；不声称已经实现跨所有调用方的设备仲裁。
- Cobotmagic VLA 旧图像缺少采集时间时保持 unknown；严格验收可以补充带时间戳的相机观测。
- 保留旧 ToolRuntime 导入、call/stop、data/images、配置版本及旧快照读取。

使用入口：[架构](../architecture.md)、[配置](../configuration.md)、[工具接入](../tools.md)、
[结果验证](../verification.md)。
