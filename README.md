# Primitive VLA

由模型自主调用工具的具身 Agent。输入自然语言目标，模型读取工具说明、观测和历史，生成工具名与参数；运行时执行并返回结果，模型继续选择下一步。

```text
目标 + 工具说明 + 会话历史
  → Model.step()
  → ToolCall(name, arguments)
  → 工具执行与反馈
  → 下一轮模型决策 / 最终报告
```

核心不要求预定义场景对象、布尔目标、VLA 或机器人构型。定位、运动、夹爪、VLA 和评估模型都可以是工具。

`main` 已合入 Cobotmagic 扩展，包含通用 Agent 核心和机器人集成。`feat/cobotmagic-extension` 分支继续保留，可用于后续扩展开发；完成后合并回 `main`。核心与扩展仍分别维护依赖、配置和测试。

## 快速开始

```bash
python -m pip install -e .
primitive-vla init-config --workspace workspace/tools
```

在生成的配置中填写 `model.options.model` 和 `model.options.base_url`，然后：

```bash
primitive-vla run --workspace workspace/tools --instruction "观察当前环境，报告可用能力和当前状态。"
primitive-vla serve --workspace workspace/tools
```

默认仅注册会话检索工具；机器人和模型能力通过扩展配置。HTTP 模型样例见 [configs/agent.yaml](configs/agent.yaml)。

具体机器人、模型组合和场景适配可以放在 `extensions/local/<name>/`，作为独立 Python 包安装，再通过配置加载。当前仅保留 [Cobotmagic 扩展](extensions/local/cobotmagic/README.md) 并纳入版本管理；新扩展、权重、环境和运行产物默认由 Git 忽略。通用框架不依赖具体扩展及其模型或本机配置。目录约定与接入方式见 [本地扩展](extensions/README.md)。

## 扩展

- 新能力实现 `ToolDefinition + async call(arguments, context)`；动作工具还需声明资源并实现确认停止。
- 能力包通过 `factory(options, PluginContext)` 构造，导出 `tools` 列表。
- 决策模型实现 `step(ModelContext, tool_definitions)`，返回工具调用或最终状态。
- Python 工具直接配置 import path；远程模型/机器人可使用内置 HTTPTools。
- 使用说明可以作为工具 description 或配置中的 instructions 文件提供，不必定义执行配方。
- 工具调用成功与任务成功分别记录。模型决定是否补充观察、调用评估工具或报告阻塞；可选 finish_check 可以约束最终验收。
- 默认由主 Agent 结合前后图片判断结果，在正常回复中记录判断并继续行动；可配置 `verification.mode: independent` 使用其他 VLM、世界模型或实测规则。判断记录不限制后续工具顺序，见[结果判断](docs/verification.md)。

详见 [工具接入](docs/tools.md)、[配置](docs/configuration.md)、[当前架构](docs/architecture.md)和[模型接入](docs/codex-integration.md)。

## 核心边界

`src/primitive_vla` 仅保留 Agent、工具协议与执行生命周期、模型通信、会话、存储、通用配置、结果判断和网页。VLA 协议、IK、ROS、仿真场景、技能说明和权重加载归各自扩展。

旧符号场景、Executive、内置 SAPIEN/Franka VLA、技能目录及 `SceneTools` 迁移层已移除。旧 scene/backends 配置不再自动迁移，需改为扩展工具配置。已有 Cobotmagic 的 IK、标定和 VLA 实现留在其扩展中。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -t . -v
```

测试覆盖工具契约、反馈恢复、记忆检索、停止确认、资源冲突、HTTP 多模态模型、远程工具、任务持久化、网页接口及核心与扩展的依赖边界。日志与任务记录位于所选 workspace；不会在重启后自动重放物理动作。

通用运行层支持连续会话：调用 `TaskService.start(instruction, parent_id=上一轮ID)` 接续最近一轮，省略 `parent_id` 创建新会话。会话历史和工作记忆保存在任务数据库，重启后可继续；每轮独立创建工具实例，不恢复旧执行句柄、不重放动作。
