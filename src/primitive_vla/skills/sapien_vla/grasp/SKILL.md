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
  near:{object}: 'true'
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
description: VLA 根据相机和关节状态完成闭合、抓取与抬升；先由 MOVE_TO 接近目标。
boundary: atomic
timeout_s: 90
max_attempts: 1
irreversible: false
capability:
  mode: vla_policy
  policy: pi05_base
  adapter: franka_experimental
  robot: Panda simulation; embodiment transfer unvalidated
recovery: Reobserve and replan from measured state.
cancellation: Backend-specific stop with acknowledgement; preserve held objects.
execution:
  type: policy
  prompt: Pick up the {object} from the {source} and lift it. Keep holding it.
---

# GRASP

VLA 根据相机和关节状态完成闭合、抓取与抬升；先由 MOVE_TO 接近目标。

本技能不定义抓取、放置或圆周轨迹脚本。execution.prompt 绑定对象后交给 π0.5；若效果未达标，报告失败，不回退到 IK 操作。base 权重在本场景的成功率尚未验证。
