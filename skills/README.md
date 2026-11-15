# 每个工具一个 skill.md

工具声明与使用说明放在同一个文件：`skills/<domain>/<tool>/skill.md`。
文件的 YAML 元信息定义可调用契约和执行入口；Markdown 正文说明适用条件、准备、验证和恢复。
运行配置选择文件或目录，框架扫描并注册工具。平台 Python 代码只实现能力，不维护工具名称清单。

以 RoboCasa observe 为例：

```markdown
---
id: state.robocasa.observe
version: "1.0"
description: 读取最近完成仿真步的机器人状态、任务语言、时间和步号。
tool:
  domain: state
  provider: rpent.robocasa
  input_schema:
    type: object
    properties: {}
    additionalProperties: false
  output_schema:
    type: object
  supported_robots: [robocasa.PandaOmron]
  required_resources: []
  execution_mode: sync
  timeout: 120
  entrypoint: runtime.environment:NativeTool
  options:
    operation: observe
---
# 读取当前状态

## 能力与适用条件
需要确认当前状态时使用。动作期间读到最近完成步的缓存。

## 所需输入与准备
无需参数，结合 task_language 确认目标。

## 调用方法
调用 state.robocasa.observe，阅读时间、步号和机器人状态。

## 完成验证
状态是观察事实，不代表任务效果已经达成。

## 失败处理
信息不足时补充相机观察，根据证据决定下一步。
```

实际文件见 [observe/skill.md](../workspace/robocasa/skills/state/observe/skill.md)。

## 字段与装配

- `id` 同时是 Skill ID 和 Tool 名称，是名称的唯一来源；在同一次装配中必须唯一。
- `version` 为字符串，`description` 同时用于 Skill 目录和工具发现。
- `tool` 声明工具域、输入/输出 JSON Schema、实现来源、本体支持、控制资源、执行方式和超时。
- `entrypoint` 是平台环境可导入的 `module:factory`。框架调用 `factory(context, spec, options)`，取得实现 execute/cancel 的 ToolHandler。
- `options` 是交给该实现的配置，例如映射到现有 SDK 的某个操作。模块导入和能力执行发生在平台子进程。
- `required_tools` 可选，用于声明额外工具依赖，不表示调用顺序；不能代替本工具的 `tool` 声明。

`SkillStore.load(path)` 接收一个 Markdown 文件，或递归扫描目录中的 `skill.md`。
同一份已解析元信息提供工具注册和 Skill 目录，正文由 VLM 按需分页读取。
工具名称与 Skill ID 相同，因此调用 `read_skill` 时直接使用工具名称即可。
不含 tool 的文档仅作为知识读取，不会注册可执行能力。

修改名称、描述、Schema、资源、超时或入口，只需修改对应 skill.md 并重新启动。
新增工具：添加一个目录和 skill.md，指向已有实现或为新算法提供实现；被选中的目录会自动发现它。
移除文件或取消运行配置选择，该工具就不会注册。目录本身不含任务执行顺序。

## 规则与接口

正文中的技能适用条件和恢复策略由 VLM 判断，不把它们编译为固定工作流。
参数类型与静态范围在 Tool 调用入口校验；当前 episode、实际图像尺寸等动态状态由能力实现检查。
控制资源互斥与停止确认由框架执行。Execution 的 completed 不表示任务目标达成。
平台共享 command 维持设备和仿真生命周期，每个工具的入口由自己的 skill.md 指定，不逐次重启设备。
