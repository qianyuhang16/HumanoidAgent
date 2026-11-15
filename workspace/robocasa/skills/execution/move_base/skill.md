---
id: execution.robocasa.move_base
version: '1.0'
description: 在机器人局部坐标系施加底盘前进、横移和转向命令。
tool:
  domain: execution
  input_schema:
    properties:
      forward:
        description: Forward velocity, [-1, 1] (default 0)
        type: number
        minimum: -1
        maximum: 1
      lateral:
        description: Lateral / strafe velocity, [-1, 1] (default 0)
        type: number
        minimum: -1
        maximum: 1
      turn:
        description: Yaw rotation velocity, [-1, 1] (default 0)
        type: number
        minimum: -1
        maximum: 1
      steps:
        description: Number of env steps (default 10)
        type: integer
        minimum: 1
        maximum: 2000
      gripper:
        description: 'Gripper while driving: +1 close, -1 open, or ''hold'' (default ''hold'')'
        anyOf:
        - type: number
          minimum: -1
          maximum: 1
        - const: hold
    type: object
    additionalProperties: false
  output_schema:
    type: object
  provider: rpent.robocasa
  supported_robots:
  - robocasa.PandaOmron
  required_resources:
  - simulation
  execution_mode: async
  timeout: 120.0
  entrypoint: runtime.environment:NativeTool
  options:
    operation: move_base
---
# 局部移动底盘

## 能力与适用条件
适合已观察通路的短距离调整和视角修正；不提供全局避障或 SLAM。

## 所需输入与准备
forward、lateral、turn 在 [-1,1]；正值分别前进、向右横移和逆时针转向。steps 为仿真步数；gripper 缺省 hold。

## 调用方法
依据观察使用小幅命令与有限步数，等待终态或按需观察状态；移动和其他仿真动作共享 simulation 资源。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
结合新位姿和图像判断是否到达目标或获得需要的视角。

## 失败处理
发生资源冲突时等待或取消并确认停止；路径不明时补充观察，不盲目持续移动。
