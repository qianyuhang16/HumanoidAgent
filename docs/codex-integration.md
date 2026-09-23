# 决策模型接入

主循环调用 Model Adapter；内置 JSONModel 复用 HTTP 多模态 JSON 与 Codex CLI 两种 transport。

```yaml
model:
  adapter: primitive_vla.agent.model:JSONModel
  options:
    provider: codex
    model: your-accessible-model-id
    timeout_s: 120
    max_tokens: 4096
    codex:
      executable: codex
      reasoning_effort: medium
```

Codex transport 使用本机已有登录，模型调用产生结构化工具决策；实际机器人工具由本仓库运行时执行。CLI 子进程在独立临时目录中运行，代码中关闭 shell 工具并采用只读配置。

为支持任意工具参数 schema，Codex 的严格输出 wire 用 arguments_json 字符串承载参数；客户端解析回 arguments 对象，随后统一校验。HTTP JSONModel 直接返回 arguments 对象。

如果使用兼容 HTTP 服务，设置 provider: http、base_url、model，以及需要时的 api_key_env。详见 [配置](configuration.md)。自定义模型协议可实现 step/close 后替换 model.adapter。
