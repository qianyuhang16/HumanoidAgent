---
id: execution.robocasa.move_delta
version: '1.0'
description: 以当前末端位置为起点执行小幅世界坐标相对位移。
tool:
  domain: execution
  input_schema:
    properties:
      dxyz:
        description: Relative displacement [dx, dy, dz] in meters
        items:
          type: number
        maxItems: 3
        minItems: 3
        type: array
      gripper:
        description: 'Gripper: +1 close, -1 open, or ''hold'' (default ''hold'')'
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
        description: Step budget (default 80)
        type: integer
        minimum: 1
        maximum: 2000
    required:
    - dxyz
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
    operation: move_delta
---
# 相对移动末端

## 能力与适用条件
用于接近、微调和局部对齐，需要理解当前末端与目标的空间关系。

## 所需输入与准备
dxyz 为三维米制位移；gripper 缺省 hold。结合当前状态选择步长和步数预算。

## 调用方法
原语计算目标为当前末端位置加 dxyz，再执行局部伺服。长距离移动应根据观察重新规划。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
等待终态后观察真实位移和目标状态，不把函数返回当作接触或抓取成功。

## 失败处理
误差或目标变化时先获取新状态，必要时改变方向、缩小步幅或请求帮助。
