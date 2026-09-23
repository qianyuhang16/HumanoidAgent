# 配置

CLI 和网页共用 `schema_version: 2` 配置。示例：[configs/agent.yaml](../configs/agent.yaml)。

```yaml
schema_version: 2
name: My robot
model:
  adapter: primitive_vla.agent.model:JSONModel
  options:
    provider: http
    base_url: http://127.0.0.1:8000/v1
    model: your-model-id
tool_providers:
  - adapter: primitive_vla.tools.mock:MockRobot
    options:
      arms: [arm_a, arm_b]
tools: {}
instructions: []
system_prompt: ""
limits:
  max_turns: 40
  max_calls: 80
  timeout_s: 180
  stop_timeout_s: 2
  history_limit: 20
  result_chars: 12000
  memory_chars: 12000
  denied_tools: []
finish_check: null
```

`model`、`tool_providers`、`tools`、`finish_check` 都使用 `adapter + options`。Adapter 负责校验自己的 options；框架不静默过滤插件参数。`tool_providers` 导出多个工具；`tools` 的 key 是单个工具暴露给模型的名称，值是构造该工具的 Adapter。

`instructions` 是 UTF-8 文件路径，相对配置文件目录解析。纯文本经验、使用说明和示例都可以放入这些文件。内容在任务启动时读取并冻结。`system_prompt` 直接添加文字说明。

省略 tool_providers 时使用 MockRobot；只需要自定义工具或纯推理时明确设置 `tool_providers: []`。工具重名会导致启动失败，包括与内置 `session.read/search` 的冲突。

`max_turns` 限制模型轮数，`max_calls` 限制工具调用数。达到工具预算后模型仍可利用剩余轮数解释结果；新的调用返回拒绝。任务时间包含插件启动。框架按工具声明的 timeout_s 进一步限制一次调用。

密钥通过环境变量或网页的临时密钥输入提供，不放进配置。JSONModel 默认读取 VLM_API_KEY；HTTPTools 默认读取 TOOL_API_KEY。

CLI 默认加载当前目录的 `.env`，可用 `--env-file` 指定文件；文件值不会覆盖已导出的环境变量。JSONModel 的 `require_api_key: true` 会在缺少指定密钥时拒绝启动任务。本机专用配置可放在 `configs/local/` 或对应[本地扩展](../extensions/README.md)的目录中；它们由 Git 忽略。

## 替换单个工具

```yaml
tools:
  perception.locate:
    adapter: my_robot.light_vlm:create_locator
    options:
      endpoint: http://127.0.0.1:9200
```

factory 接收 `(options, context)`，返回一个 Tool。替换 Adapter 后，向模型暴露的名称仍为 perception.locate。要实现对调用方透明的替换，参数与结果的含义也必须兼容。具体接入示例见 [tools.md](tools.md)。

## 旧配置

没有 schema_version、包含 scene/backends/skill_sources/planner 的文档会加载为 LegacyConfig，再迁移成 JSONModel + SceneTools。场景和技能以解析后的快照保留。旧解析器、候选规划器、max_observations 与固定最终验证模式不再控制新 Agent。

旧配置中的 VLM 连接仍用于 JSONModel，因此原来仅使用 rule planner 的部署需填写模型信息，或显式选择离线演示模型。LegacyConfig 的 SAPIEN/单策略限制只属于旧场景 Adapter；新工具配置没有这些限制。

旧 CLI 的 --scene、--skills、--fault 参数已移除，请在对应工具 provider 的 options 中配置。--task 可以附加结构化上下文，但不再强制解释为符号目标。
