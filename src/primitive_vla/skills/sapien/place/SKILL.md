---
name: PLACE
version: '1'
parameters:
  object:
  - ingredient
  - stirrer
  target:
  - bowl
  - rack
  hand:
  - right
preconditions:
  healthy: 'true'
  holding:{hand}:{object}: 'true'
  accepts:{target}: 'true'
  reachable:{target}: 'true'
invariants:
  healthy: 'true'
effects:
  holding:{hand}:{object}: 'false'
  free:{hand}: 'true'
  at:{object}:{target}: 'true'
  ready:{object}: 'false'
resources:
- right_arm
- right_gripper
description: 将持有物体放入碗或返回工具区；根据物体位姿确认放置。
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
  - op: require
    condition: holding
    object: '{object}'
    reason: Not holding {object}
  - op: point
    kind: placement_tcp
    object: '{object}'
    target: '{target}'
    into: p
  - op: phase
    value: transfer:{object}
  - op: move
    point:
      current_tcp: true
      height:
        config: travel_height
  - op: move
    point:
      ref: p
      height:
        config: travel_height
  - op: move
    point:
      ref: p
  - op: phase
    value: release:{object}
  - op: gripper
    opened: true
  - op: move
    point:
      ref: p
      height:
        config: travel_height
  - op: wait
    seconds: 0.4
---

# PLACE

将持有物体放到指定支撑面或容器中。执行包含转运、释放和退让。目标是碗时用于投放配料；目标是工具区时用于归还工具。完成后读取实际位姿和持物事实确认，不补发第二次放置。

## 动作流程

上方 `program` 定义执行顺序，参数来自当前技能绑定与仿真配置。修改步骤或新增同类组合不需要修改 Python 技能分支。低层算子由仿真后端执行，末端效果由独立观测验收。
