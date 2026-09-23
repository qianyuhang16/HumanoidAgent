# 仿真工具

原有 Mock 与 SAPIEN 场景由 SceneTools provider 暴露为工具。旧 configs/sapien.yaml 会自动迁移成 JSONModel + SceneTools；请保留对应仿真环境并检查本机路径和模型设置。

SAPIEN 内部继续支持 program 与 policy 执行声明，以及可选 π0.5 worker。新 Agent 自主调用 scene.observe、skill.MOVE_TO、skill.GRASP 等工具，不再从预先枚举的候选编号中选择。

configs/battery-demo.yaml 是另一个纯软件场景，只验证通用工具循环。它不依赖 SAPIEN，也不是真实 RMbench 实现。其他模拟器实现自己的工具 provider 即可复用同一 Agent。
