# 架构参考与实现范围

本项目当前采用模型—工具—反馈循环。核心是 agent/core.py、tools/runtime.py、agent/memory.py 和 runtime/session.py；模型与具体设备通过插件接入。

此前版本参考过 PhyAgentOS-core 的模块划分，但本次工具型重构采用独立实现，不声称兼容其传输协议。旧符号执行代码已移除，具体场景由扩展自己的工具实现。

实现范围与扩展接口以 [当前架构](architecture.md)、[配置](configuration.md)和[工具接入](tools.md)为准。设计演进记录见 [设计提案](extensibility-proposal.md)。
