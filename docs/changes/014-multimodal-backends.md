# 014：打通多模态工具与能力后端

StateRequest.artifacts 从已有证据生成带 evidence ID 前缀的类型化资源视图，
后端可同时读取图片、点云、深度及不可用的 null 槽；原始 evidence 保留完整数据。
StateResult 可返回 claims、任意 JSON data 或 artifacts，不强制感知模型产生判断。
成功但完全无输出仍拒绝；unsupported/error 的区分及显式 fallback 规则保留。

state.estimate 将资源输出转为通用 ToolResult.artifacts，保存输入证据 ID，
下游通过调用结果 ID 能再次读取这些资源；原 data.artifacts 兼容入口保留。
输出资源不凭模型返回时刻冒充新采集观测，不会因此产生新鲜状态或通过严格验收。

ToolDefinition.parameters 可以使用含 Artifact 的输入模型 JSON Schema。
修复嵌入主 Agent response_schema 后本地 $ref 的指向，使嵌套资源与 JSON 数据
在模型输出校验和实际工具派发校验中保持一致。

验证：混合模态输入、资源输出后再输入、无 claim 的成功输出、空成功拒绝、
输入 JSON Schema 引用解析、nullable 参数、数组输出，以及错误资源类型零派发。
