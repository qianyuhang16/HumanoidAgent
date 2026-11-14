# Primitive VLA — 通用具身 Agent 核心

以可替换的 Planning、Memory 和 Tool System 构成的事件驱动框架。Planning 决定下一步；
Skill 描述使用知识；Provider 实现能力；Execution 管理控制资源；Verification 记录效果判断。

本版本提供核心框架、独立桌面工作台，以及可选的 **RoboCasa 环境与动作原语 Provider**。
核心通过接口注入 Planner，独立 `vlm/` 接口提供模型通信，可选 **DeepSeek Flash** 实现支持真实模型决策与图像输入。
没有内置 VLA、SLAM 或预设任务工作流；模型适配器独立于通用运行循环。
RoboCasa 的依赖与初始化独立于核心，见 [接入说明](docs/robocasa.md)。
测试替身只位于 `tests/`，用于验证运行机制，不代表真实模型自主性或真机效果已验收。

## 已实现

| 能力 | 实现 |
| --- | --- |
| Planning | 共享提示词与 `ModelPlanner`、结构化下一步决策、动态工具/Skill 发现 |
| VLM | 独立生成接口；可选 DeepSeek 通信实现；统一文本、图像与函数调用 |
| Tool + Hook | JSON Schema 2020-12、三域注册/路由、调用前后、异常和进度 Hook |
| Execution | 同步/异步调用、进度、状态、超时、取消确认、机器人实例级资源互斥与资源别名 |
| Verification | `success / failure / uncertain`、依据、资源证据、关联动作 ID |
| Memory | 长短期 Markdown、可读索引、短期删除、定时长期整理、原始证据日志及资源引用 |
| Session | 持久化、用户提问/回复、暂停/恢复/取消、决策/调用/连续错误/活动时间预算 |
| 事件与上下文 | 合并唤醒信号、观测订阅、按需检索、带分页提示的有界上下文 |
| 重启恢复 | 恢复记录和预算；隔离未结束的控制调用，等待 Provider 确认停止；不重放动作 |
| 用户接口 | Python `TaskService`、本地 API/SSE、Electron 独立桌面窗口，支持任务控制与资源展示 |
| 机器人视角 | Provider 可选只读订阅、默认相机、切换/放大/收起；预览独立于 Planning 与 Memory |
| RoboCasa | 可选 RPent 仿真与原语接入、RGB/深度资源、独立任务验证和真实环境检查入口 |

## 安装与测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -t . -v
```

Python 3.10+；唯一运行依赖为 `jsonschema`。测试使用标准库 `unittest`，不需要模型服务或机器人。

## 独立桌面工作台

桌面端包含任务会话、执行时间线、Memory 检索、工具与 Skill 浏览、资源预览、状态和预算面板。
未接入 Planner 时可以保存草稿与查看历史；接入后使用同一套 Runtime 执行真实任务。

```bash
.venv/bin/python -m pip install -e '.[gui,test]'
npm --prefix desktop ci
npm --prefix desktop run setup
.venv/bin/primitive-vla desktop
```

桌面构建需要 Node 22.12+（推荐 24）和图形会话。原生启动方式、Agent 工厂接入和 Linux 打包见
[桌面使用文档](docs/desktop.md)。不安装 GUI 可选依赖时，核心仍独立运行，GUI 测试自动跳过。

## 接入应用

由应用负责准备 `planner`、`registrations` 和机器人资源拓扑，框架不替应用选择模型或能力。
下面的装配函数可以直接放进宿主应用：

```python
from primitive_vla.memory import Memory
from primitive_vla.runtime.agent import Agent
from primitive_vla.runtime.session import Limits
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry


def build_agent(planner, registrations, robot_id, resource_names, aliases, skill_directory):
    registry = ToolRegistry()
    for spec, provider in registrations:
        registry.register(spec, provider)

    resources = ResourceManager()
    resources.define_robot(robot_id, set(resource_names), aliases)
    skills = SkillStore()
    skills.load(skill_directory)

    return Agent(planner, registry, Memory("workspace/agent"), skills, resources,
                 Limits(max_decisions=100, max_tool_calls=60))
```

```python
from primitive_vla.interfaces import TaskService


async def run_task(agent, goal, robot_id, robot_type):
    service = TaskService(agent)
    session = service.start(goal, robot_id, robot_type)
    await service.tasks[session.id]
    # waiting_user: 展示 session.question 和 session.last_result 中的资源。
    # service.respond(session.id, "用户回答", {"任意业务字段": "用户选择"}) 可继续任务。
    return service, session
```

`pause` 暂停高层决策，正在执行的动作仍由 Provider 管理。需要停止动作时使用 `cancel`。
`cancel` 只有收到停止确认后才进入最终状态；否则保留 `stopping` 和资源占用。
关闭应用前，先结束或暂停 runner，再 `await service.close()`；未确认停止时关闭会报错，
宿主应保持运行并通过 `agent.reconcile(session_id)` 再次查询停止确认。

## 文档与范围

- [架构与设计决策](docs/architecture.md)：运行边界、持久化、并发与恢复语义。
- [Planner / Provider 接入契约](docs/extensions.md)：接口、动作列表、异步协议和错误处理。
- [Skill Markdown 规范](skills/README.md)：文档结构与版本索引。
- [验收映射](docs/acceptance.md)：需求、测试证据及尚未接入的实际能力。
- [抓取与导航用户故事](docs/user-stories.md)：任务选型、环境与权重评估、架构缺口及鲁棒性验收设计。
- [自主 Agent 评测选型](docs/agent-evaluation.md)：多步桌面操作、连续寻物导航，以及其他具身 Agent 的评测方法。
- [RPent 接入调研](docs/rpent-integration.md)：现成能力、RoboCasa 快速接入路线、环境容量及契约差异。
- [RoboCasa 接入与运行](docs/robocasa.md)：安装、工具清单、Agent 装配、生命周期及真实环境检查。
- [桌面工作台](docs/desktop.md)：独立窗口、启动、任务控制与 Runtime 接入。
- [机器人视角接口](docs/robot-views.md)：Provider 契约、桌面装配、采集线程与订阅生命周期。
- [VLM 与 DeepSeek 接入](docs/deepseek.md)：统一模型接口、共享 Planning 提示词、配置与装配示例。
- [文件 Memory 与整理](docs/memory.md)：Markdown、短期遗忘、长期整理与证据来源。

P0、P1、P2 的通用机制已实现。RoboCasa 开始接入具体仿真能力；P3 的建图、SLAM、完整导航、
VLA、视觉验证和实际地图 UI 仍需相应扩展。DeepSeek 接入不代表自主任务成功率已经验收。
