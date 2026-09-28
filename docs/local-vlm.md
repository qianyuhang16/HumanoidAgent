# 本地 VLM

配置 model.adapter 为 primitive_vla.agent.model:JSONModel，在 model.options 中设置 provider: http、base_url 和 model。服务应提供 /chat/completions，接受多模态消息并在 message.content 中返回工具调用 JSON。

仓库不附带模型权重服务，核心仅保留模型通信接口。可以连接自行部署的兼容服务；模型是否能可靠生成工具参数需要实际评估，JSON 模式只约束输出形式。

也可以直接实现 Model Adapter，接收 ModelContext 和工具定义，输出 ModelReply。新模型不需要新增场景谓词或修改 Agent。详见 [工具接入](tools.md)。
