---
name: STIR
version: '1'
parameters:
  tool:
  - stirrer
  target:
  - bowl
preconditions:
  healthy: 'true'
  holding:right:{tool}: 'true'
  event:stirred:{target}: 'false'
invariants:
  healthy: 'true'
  holding:right:{tool}: 'true'
effects:
  event:stirred:{target}: 'true'
  holding:right:{tool}: 'true'
resources:
- right_arm
- right_gripper
description: 单臂持棒在固定碗中搅拌；所有配料先入碗，按实际工具轨迹与物体接触验收。
backend: sapien
boundary: atomic
timeout_s: 50.0
max_attempts: 1
irreversible: true
capability:
  mode: sapien_physics
  robot: RoboTwin Franka Panda
  control: IK waypoints with joint drives; frictional grasp, no attachment constraint
recovery: Reobserve and replan from measured state.
cancellation: Backend-specific stop with acknowledgement; preserve held objects.
program:
- op: require
  condition: holding
  object: '{tool}'
  reason: Tool is not held
- op: require
  condition: ingredients_in_target
  target: '{target}'
  reason: All configured ingredients must be inside the bowl
- op: point
  kind: tool_offset
  tool: '{tool}'
  into: tool_offset
- op: point
  kind: circle_start
  tool: '{tool}'
  target: '{target}'
  into: p
  offset_ref: tool_offset
- op: phase
  value: lower_tool
- op: move
  point:
    ref: p
    height:
      config: travel_height
- op: move
  point:
    ref: p
- op: phase
  value: stir
- op: circle
  tool: '{tool}'
  target: '{target}'
  radius:
    config: stir_radius
  depth:
    config: stir_depth
  turns:
    config: stir_turns
  period_s:
    config: stir_period_s
  extra_turns: 2
  segments: 48
  min_contact_steps:
    config: min_stir_contact_steps
  offset_ref: tool_offset
- op: move
  point:
    current_tcp: true
    height:
      config: travel_height
---

# STIR

先确认任务要求的配料已进入碗，再使用持有的工具搅拌。用实际工具运动与物体接触验收。结束时仍持工具，若用户要求归位，由规划器随后选择放置。效果未知时不盲目重复。

## 动作流程

上方 `program` 定义执行顺序，参数来自当前技能绑定与仿真配置。修改步骤或新增同类组合不需要修改 Python 技能分支。低层算子由仿真后端执行，末端效果由独立观测验收。
