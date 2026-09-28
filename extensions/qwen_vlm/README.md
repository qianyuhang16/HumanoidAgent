# Qwen3-VL 服务扩展

原 `primitive_vla.providers.local_server` 中的 Qwen3-VL 加载逻辑移到本包。它是具体模型服务，不是 Agent 通信接口；核心仅通过 HTTP 使用它。权重和 GPU 依赖由该扩展的模型环境维护。

在已有模型环境安装：

```bash
python -m pip install -e ./extensions/qwen_vlm
python -m primitive_vla_qwen.server --config /path/to/local-model.json
```

JSON 配置包含 `model_path`、`model`，可选 `device`、`dtype`、`host`、`port`；`model_path` 指向已有本地权重。模型仅在服务启动时加载。

Agent 配置仍使用 `primitive_vla.agent.model:JSONModel`，将 `model.options.base_url` 指向本服务的 `/v1` 地址。本次仅移动代码，不下载权重或启动模型服务。
