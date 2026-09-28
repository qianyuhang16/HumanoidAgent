# 机器人与环境接入

新架构使用 tools 工具接口。机器人构型、观测、控制和专用模型由 provider 声明，Agent 不依赖固定的 Backend 类型。Python 或 HTTPTools 都可以接入自定义机器人网关，具体 ROS 消息由网关实现。

核心不再保留旧 `backends/`、`execution/` 或 `SceneTools`。通用参数校验、资源互斥、取消与停止确认在 `tools/runtime.py`；机器人运动、传感器及控制器协议由扩展负责。

软件 MockRobot 已移到 [demo 扩展](../extensions/demo/README.md)，需显式安装和配置，不宣称物理抓取能力。实际 Cobotmagic、桌面仿真和模型组合各自位于对应扩展中。

详见 [工具接口和远程协议](tools.md)。
