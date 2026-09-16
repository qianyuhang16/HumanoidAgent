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
  near:{target}: 'true'
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
description: VLA 完成局部放置、释放和退让；先由 MOVE_TO 接近目标位置。
backend: sapien
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
program: []
policy_prompt: Place the held {object} in the {target}, release it, and move the gripper
  away.
---

# PLACE

VLA 完成局部放置、释放和退让；先由 MOVE_TO 接近目标位置。

本技能不定义抓取、放置或圆周轨迹脚本。policy_prompt 绑定对象后交给 π0.5；若效果未达标，报告失败，不回退到 IK 操作。base 权重在本场景的成功率尚未验证。
