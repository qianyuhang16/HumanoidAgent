# 仿真工具

模拟器应作为独立工具扩展接入。核心不依赖 SAPIEN、机器人 URDF、场景对象、特定动作空间或策略模型协议。

旧内置 SAPIEN/Franka 场景、π0.5 worker、符号技能和 `configs/sapien.yaml` 已移除。现有 `extensions/local/tabletop` 拥有自己的场景、模型和 worker；它不依赖已删除的核心仿真代码。

电池试错演示见 [demo 扩展](../extensions/demo/README.md)。它是纯软件工具循环测试，不依赖 SAPIEN，也不是真实 RMbench 实现。其他模拟器实现自己的工具 provider 即可复用同一 Agent。
