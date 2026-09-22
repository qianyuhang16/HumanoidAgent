---
name: MOVE_TO
version: '1'
parameters:
  target:
  - ingredient
  - stirrer
  - bowl
  - rack
preconditions:
  healthy: 'true'
  reachable:{target}: 'true'
invariants:
  healthy: 'true'
effects:
  near:{target}: 'true'
resources:
- right_arm
- right_gripper
description: 仅用 IK 将夹爪移动到目标上方的接近位置，保持当前夹爪开度；局部操作交给后续 VLA 技能。
boundary: atomic
timeout_s: 50
max_attempts: 2
capability:
  controller: IK approach only
execution:
  type: program
  steps:
  - op: phase
    value: ik:move_to:{target}
  - op: point
    kind: approach
    target: '{target}'
    into: p
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
---

# MOVE_TO

先移动到将要抓取的物体附近，再选择 GRASP。持物后先移动到目标容器附近，再选择 PLACE 或 STIR。若 near 已满足，不重复接近。MOVE_TO 不改变夹爪开合、不抓取、不释放、不搅拌。接近距离由 approach_clearance 配置。
