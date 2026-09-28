# 机器人与环境接入

新架构使用 tools 工具接口。机器人构型、观测、控制和专用模型由 provider 声明，Agent 不依赖固定的 Backend 类型。Python 或 HTTPTools 都可以接入自定义机器人网关，具体 ROS 消息由网关实现。

核心不再保留旧 `backends/`、`execution/` 或 `SceneTools`。通用参数校验、资源互斥、取消与停止确认在 `tools/runtime.py`；机器人运动、传感器及控制器协议由扩展负责。

本分支的 Cobotmagic 集成位于自己的扩展中；其他机器人按同一工具接口接入，核心不附带具体机器人实现。

详见 [工具接口和远程协议](tools.md)。
