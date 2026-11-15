---
id: execution.robocasa.scripted_grasp
version: '1.0'
description: 使用上游程序化接近、闭合与抬升原语尝试抓取，不加载 VLA。
tool:
  domain: execution
  input_schema:
    properties:
      xyz:
        description: World-frame grasp target [x, y, z] in meters
        items:
          type: number
        maxItems: 3
        minItems: 3
        type: array
      approach_z:
        description: Z offset above target before descent, m (default 0.10)
        type: number
      grasp_z_offset:
        description: Z offset at grasp point (default 0.0; negative = below target)
        type: number
      step_clip:
        description: Per-step dxyz cap during descent, m (default 0.02)
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
    operation: scripted_grasp
---
# 调用局部抓取原语

## 能力与适用条件
目标坐标和接近空间已知，适合该粗略局部抓取控制。它不是通用任务解题流程。

## 所需输入与准备
xyz 为米制世界坐标；approach_z 和 grasp_z_offset 相对于目标，step_clip 为下降步长。

## 调用方法
阅读实际图像和几何信息后决定是否使用。不要凭空生成抓取位置；这个工具自身包含局部控制序列。

异步返回 call_id 后等待终态；simulation 资源冲突由 Execution 拒绝。暂停 Planning 不会停止动作，取消必须等待停止确认。

## 完成验证
等待终态后重新观察并独立验证效果；原语 completed 不表示已抓起目标。

## 失败处理
依据遮挡、对齐或接触证据决定补充观察、调整姿态、换用基本原语或求助。
