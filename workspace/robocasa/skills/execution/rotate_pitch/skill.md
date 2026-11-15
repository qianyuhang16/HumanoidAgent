---
id: execution.robocasa.rotate_pitch
version: '1.0'
description: 保持末端位置，调整夹爪俯仰方向。
tool:
  domain: execution
  input_schema:
    properties:
      target_pitch:
        description: Absolute pitch target, radians (clamped +/-1.5; default 0.6)
        type: number
      gripper:
        description: Gripper command held during rotation (default +1)
        type: number
        minimum: -1
        maximum: 1
      n:
        description: Number of env steps for the rotation (default 12)
        type: integer
        minimum: 1
        maximum: 2000
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
    operation: rotate_pitch
---
# 调整腕部俯仰

## 能力与适用条件
用于调整接近角度，例如操作窄开口；先观察周围障碍和末端位置。

## 所需输入与准备
target_pitch 使用弧度，上游限制到约 ±1.5；gripper 为数值夹爪命令，n 为仿真步数。

## 调用方法
按 Schema 传入目标角度及预算，等待异步操作结束。保持位置不等于保证全程无碰撞。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
重新检查夹爪朝向、接触与物体状态；需要时采集新视角。

## 失败处理
角度不合适或受阻时根据新状态选择调整、退出接触或停止。
