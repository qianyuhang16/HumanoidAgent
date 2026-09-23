# 机器人与环境接入

新架构使用 tools 工具接口。机器人构型、观测、控制和专用模型由 provider 声明，Agent 不依赖固定的 Backend 类型。Python 或 HTTPTools 都可以接入自定义机器人网关，具体 ROS 消息由网关实现。

旧 Mock/SAPIEN Backend 保留在 tools/legacy.py:SceneTools 内部。该 provider 导出 scene.observe 与每个 skill 的独立工具；保留原有 Executive 的前置条件、控制租约、监测与停止确认。

内置 tools.mock:MockRobot 用于工具契约与软件演示，不宣称物理抓取能力。真实 RMbench SDK、ROS 驱动与具体机器人模型适配不包含在当前内置 provider 中。

详见 [工具接口和远程协议](tools.md)。
