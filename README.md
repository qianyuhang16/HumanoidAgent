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

默认工具是软件 MockRobot，不会连接真实机器人。HTTP 模型样例见 [configs/agent.yaml](configs/agent.yaml)。

无需模型服务的离线闭环演示：

```bash
primitive-vla run --config configs/battery-demo.yaml --workspace workspace/battery-demo \
  --instruction "装入电池，调整方向直到电源表转动"
```

此演示使用确定性的 DemoModel 和软件电池场景，验证多轮调用、观测与记录；不代表 VLM 推理能力、RMbench 或机器人性能。将 model 换成 JSONModel 即可让实际 LLM/VLM 操作同一组演示工具。

具体机器人、模型组合和场景适配可以放在 `extensions/local/<name>/`，作为独立 Python 包安装，再通过配置加载。该目录默认由 Git 忽略；tabletop 示例的源码、配置和测试已单独纳入版本管理。通用框架不依赖这些扩展及其模型或本机配置。目录约定与接入方式见 [本地扩展](extensions/README.md)。

## 扩展

- 新能力实现 `ToolDefinition + async call(arguments, context)`；动作工具还需声明资源并实现确认停止。
- 能力包通过 `factory(options, PluginContext)` 构造，导出 `tools` 列表。
- 决策模型实现 `step(ModelContext, tool_definitions)`，返回工具调用或最终状态。
- Python 工具直接配置 import path；远程模型/机器人可使用内置 HTTPTools。
- 使用说明可以作为工具 description 或配置中的 instructions 文件提供，不必定义执行配方。
- 工具调用成功与任务成功分别记录。模型决定是否补充观察、调用评估工具或报告阻塞；可选 finish_check 可以约束最终验收。

详见 [工具接入](docs/tools.md)、[配置](docs/configuration.md)、[当前架构](docs/architecture.md)和[模型接入](docs/codex-integration.md)。

## 现有仿真与迁移

保留 Mock/SAPIEN、π0.5 worker、技能文件及控制租约实现。旧 scene 配置会转换为 `SceneTools`：模型可以调用 `scene.observe` 与 `skill.*`，自主生成参数与调用顺序。旧 rule planner 和 task parser 不再驱动主循环；旧配置也需要可用的决策模型。

`configs/sapien.yaml` 仍是旧配置迁移样例，使用前修改本机路径和模型设置。ROS 设备驱动、具体 VLA 输入转换及真实 RMbench SDK 仍需按实际接口提供工具实现。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -t . -v
```

测试覆盖工具契约、反馈恢复、记忆检索、停止确认、资源冲突、HTTP 多模态模型、远程工具、任务持久化、网页接口与原有执行器。日志与任务记录位于所选 workspace；不会在重启后自动重放物理动作。
