---
name: GRASP
version: '1'
parameters:
  object:
  - ingredient
  - stirrer
  source:
  - support
  - rack
  hand:
  - right
preconditions:
  healthy: 'true'
  free:{hand}: 'true'
  at:{object}:{source}: 'true'
  reachable:{object}: 'true'
invariants:
  healthy: 'true'
effects:
  holding:{hand}:{object}: 'true'
  free:{hand}: 'false'
  at:{object}:{source}: 'false'
  ready:{object}: 'true'
resources:
- right_arm
- right_gripper
description: 单臂接近、闭合夹爪并抬起；用双侧接触确认持物。
boundary: atomic
timeout_s: 50.0
max_attempts: 1
irreversible: false
capability:
  mode: sapien_physics
  robot: RoboTwin Franka Panda
  control: IK waypoints with joint drives; frictional grasp, no attachment constraint
recovery: Reobserve and replan from measured state.
cancellation: Backend-specific stop with acknowledgement; preserve held objects.
execution:
  type: program
  steps:
  - op: point
    kind: object_grasp
    object: '{object}'
    into: p
  - op: phase
    value: approach:{object}
  - op: gripper
    opened: true
  - op: move
    point:
      ref: p
      height:
        config: travel_height
  - op: move
    point:
      ref: p
  - op: phase
    value: grasp:{object}
  - op: gripper
    opened: false
  - op: require
    condition: holding
    object: '{object}'
    reason: No opposing finger contacts on {object}
  - op: phase
    value: lift:{object}
  - op: move
    point:
      ref: p
      height:
        config: travel_height
  - op: require
    condition: holding
    object: '{object}'
    reason: 'Grasp slipped during lift: {object}'
---

# GRASP

在手空闲、目标可达且来源位置已确认时使用。完成后继续持物。先接近和闭合，再通过双侧接触确认；抬升后再次确认。失败时读取新观测再决定恢复，不能把动作返回完成当作抓取成功。

## 动作流程

上方 `program` 定义执行顺序，参数来自当前技能绑定与仿真配置。修改步骤或新增同类组合不需要修改 Python 技能分支。低层算子由仿真后端执行，末端效果由独立观测验收。
