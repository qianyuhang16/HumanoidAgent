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
tool_providers: []
tools: {}
instructions: []
system_prompt: ""
limits:
  max_turns: 40
  max_model_retries: 2
  max_calls: 80
  timeout_s: 180
  stop_timeout_s: 2
  history_limit: 20
  result_chars: 12000
  memory_chars: 12000
  denied_tools: []
finish_check: null
verification:
  mode: main_agent
  reviewer: null
```

`model`、`tool_providers`、`tools`、`finish_check`、`verification.reviewer` 都使用 `adapter + options`。Adapter 负责校验自己的 options；框架不静默过滤插件参数。`tool_providers` 导出多个工具；`tools` 的 key 是单个工具暴露给模型的名称，值是构造该工具的 Adapter。

`verification.mode` 默认 `main_agent`：主 Agent 在正常下一轮回复中判断动作结果，不另发模型请求。选择 `independent` 必须同时配置 `verification.reviewer`，接口为 `assess(OutcomeContext) -> OutcomeAssessment`；仅为声明 `review_outcome=True` 的动作自动验证。两种模式都只记录判断，不据 verdict 拦截工具或最终回答。验证时间计入任务总时间，自动验证不消耗决策轮数；显式 `session.assess` 计入工具调用次数。详见[结果判断](verification.md)。

`instructions` 是 UTF-8 文件路径，相对配置文件目录解析。纯文本经验、使用说明和示例都可以放入这些文件。内容在任务启动时读取并冻结。`system_prompt` 直接添加文字说明。

省略 `tool_providers` 时不注册任何机器人或场景工具；内置 `session.read/search` 仍可用。工具重名会导致启动失败，包括与这些内置工具的冲突。具体工具包需显式安装和配置，见 [扩展接口](../extensions/README.md)。

`max_turns` 限制结构有效的模型决策轮数，`max_calls` 限制工具调用数。JSON 解析或结构校验失败不消耗决策轮数，每次决策最多额外修复 `max_model_retries` 次（默认 2）；连续失败会明确停止，不会无限重试。有效决策恢复后修复额度重置。报告的 `model_requests` 记录总请求次数，`model_errors` 记录格式错误次数，`turns` 记录有效决策次数。达到工具预算后模型仍可利用剩余轮数解释结果；新的调用返回拒绝。任务时间包含插件启动和格式重试。框架按工具声明的 timeout_s 进一步限制一次调用。

`model.options.max_tokens` 是模型单次生成额度，和 Agent 的轮数限制不同。部分推理模型的思考也计入该额度，额度过小会在生成 JSON 前被截断。HTTP 模型日志记录 `finish_reason`、token 用量和解析错误类型；`finish_reason=length` 的不完整回复不会用于工具调用。模型重试只生成新的决策，不会重放已执行的工具。

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

旧 `scene/backends/skill_sources/planner` 配置和自动迁移层已移除。这类字段会报配置校验错误，不会被静默忽略。场景、机器人参数和专用模型配置应放入所属扩展的 `options`，由扩展负责解析。

通用配置只描述模型、工具、指令、预算、可选结果判断和网页。核心不要求安装任何技能目录、仿真器或具体 VLA。

旧 CLI 的 --scene、--skills、--fault 参数已移除，请在对应工具 provider 的 options 中配置。--task 可以附加结构化上下文，但不再强制解释为符号目标。
