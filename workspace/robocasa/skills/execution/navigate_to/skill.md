---
id: execution.robocasa.navigate_to
version: '1.0'
description: 驱动底盘接近指定世界平面坐标；不提供全局路径规划或语义寻物。
tool:
  domain: execution
  input_schema:
    properties:
      xy:
        description: World-frame target [x, y] in meters (z ignored if provided)
        items:
          type: number
        maxItems: 2
        minItems: 2
        type: array
      tol:
        description: Distance threshold to stop, m (default 0.20)
        type: number
        exclusiveMinimum: 0
      max_steps:
        description: Step budget (default 300)
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
    required:
    - xy
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
    operation: navigate_to
---
# 向世界坐标导航

## 能力与适用条件
当前位置和目标可靠，已观察移动空间。不能假设直达路径畅通。

## 所需输入与准备
xy 为米制二维世界坐标，tol 为正的到达距离，max_steps 为预算；gripper 缺省 hold。

## 调用方法
原语校准朝向、转向并闭环接近目标。根据场景决定分段目标和容差，不指定固定业务路线。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
调用完成后根据位姿、图像和需要的 Verification 判断目标是否真正达到。

## 失败处理
预算不足、运动受阻或未到达时读取当前状态，决定修正目标、补充观察或终止；不用重置替代恢复。
