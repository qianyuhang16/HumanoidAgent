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
  near:{target}: 'true'
invariants:
  healthy: 'true'
  holding:right:{tool}: 'true'
effects:
  event:stirred:{target}: 'true'
  holding:right:{tool}: 'true'
resources:
- right_arm
- right_gripper
description: VLA 控制工具搅拌；先由 MOVE_TO 接近碗，由观测验收运动与接触。
boundary: atomic
timeout_s: 90
max_attempts: 1
irreversible: true
capability:
  mode: vla_policy
  policy: pi05_base
  adapter: franka_experimental
  robot: Panda simulation; embodiment transfer unvalidated
recovery: Reobserve and replan from measured state.
cancellation: Backend-specific stop with acknowledgement; preserve held objects.
execution:
  type: policy
  prompt: Stir all ingredients in the {target} with the held {tool}. Keep holding
    the tool.
---

# STIR

VLA 控制工具搅拌；先由 MOVE_TO 接近碗，由观测验收运动与接触。

本技能不定义抓取、放置或圆周轨迹脚本。execution.prompt 绑定对象后交给 π0.5；若效果未达标，报告失败，不回退到 IK 操作。base 权重在本场景的成功率尚未验证。
