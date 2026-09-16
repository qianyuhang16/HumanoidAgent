# 参考 PhyAgentOS-core 的取舍

参考版本：2026-09-15 查看公开主分支的 [README](https://github.com/PhyAgentOS/PhyAgentOS-core)、[配置 schema](https://github.com/PhyAgentOS/PhyAgentOS-core/blob/main/PhyAgentOS/config/schema.py) 与 [provider 接口](https://github.com/PhyAgentOS/PhyAgentOS-core/blob/main/PhyAgentOS/providers/base.py)。本项目采用独立实现，没有引入或复制整个框架。

| 参考设计 | 当前实现 |
|---|---|
| 按职责组织包 | `agent/`、`providers/`、`config/`、`verification/` 分立；执行与后端分别放入 `execution/`、`backends/`，应用入口放入 `web/` 与 `cli.py` |
| 模型供应商与 Agent 分离 | `VLMClient` 负责 HTTP 多模态 JSON 请求，解析器、规划器、验证器独立调用 |
| 统一声明式配置 | `AppConfig` 同时供 CLI 与网页使用，包含模型、策略、技能、观察器和预算 |
| 执行、证据、结论分开 | `SkillResult.execution`、带来源的 Observation、独立任务级 verification |
| 持久化任务聚合 | SQLite 保存任务、完整配置快照、事件和最终结果 |
| 有界恢复 | 每步重规划、观察预算、技能尝试上限，未知不可逆效果不盲目重试 |
| 可审计的执行边界 | 高层只提交 SkillCall；所有后端经过同一个 Executive |

当前保持一个 Agent 进程和串行控制权限。没有加入自动技能进化、多渠道消息系统、Cron、Dora 运行时或资源注册市场。这些功能需要真实技能数据和部署需求后再设计。

网页是本项目新增的控制台，使用轮询读取任务快照与增量事件，不是对 PhyAgentOS UI 的复用。HTTP 动作网关也不是 Forge 协议的兼容实现。

模型 HTTP 格式参照 [vLLM 在线服务文档](https://docs.vllm.ai/en/latest/serving/online_serving/) 中的兼容服务机制；实际可用模型、多模态能力和 JSON 模式以目标服务为准。
