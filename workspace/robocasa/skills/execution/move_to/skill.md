---
id: execution.robocasa.move_to
version: '1.0'
description: 通过 OSC 控制将末端移动到指定世界坐标，保持当前俯仰和偏航。
tool:
  domain: execution
  input_schema:
    properties:
      xyz:
        description: World-frame target [x, y, z] in meters
        items:
          type: number
        maxItems: 3
        minItems: 3
        type: array
      gripper:
        description: 'Gripper: +1 close, -1 open, or ''hold'' to maintain current finger width (default
          ''hold'')'
        anyOf:
        - type: number
          minimum: -1
          maximum: 1
        - const: hold
      step_clip:
        description: Per-step dxyz cap, m (default 0.02)
        type: number
        exclusiveMinimum: 0
      max_steps:
        description: Step budget (default 200)
        type: integer
        minimum: 1
        maximum: 2000
      tol:
        description: Position tolerance, m (default 0.012)
        type: number
        exclusiveMinimum: 0
    required:
    - xyz
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
    operation: move_to
---
# 移动末端到世界坐标

## 能力与适用条件
目标位置可靠，已观察接近路径和末端当前姿态。需要改变俯仰时考虑独立旋转工具。

## 所需输入与准备
xyz 为米制三维世界坐标；gripper 缺省 hold 保持当前指宽；step_clip、max_steps、tol 控制单步位移、预算与容差。

## 调用方法
单次位移超过 0.30 m 可能导致上游 OSC/IK 姿态翻转，应依据观察分段移动。搬运时避免持续挤压小物体。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
等待异步终态，再结合新位姿和图像判断效果；completed 仅表示本次原语结束。

## 失败处理
根据误差或运动异常决定缩短位移、调整目标、重新观察或停止，不自动重复同一动作。
