---
name: STIR
version: '1'
description: 一个双臂协作技能负责扶碗与搅拌，结束后右手持勺、左手空闲。
parameters:
  tool:
  - spoon
  target:
  - bowl
preconditions:
  healthy: 'true'
  holding:right:{tool}: 'true'
  free:left: 'true'
  ingredients:{target}: 'true'
  reachable:{target}: 'true'
  event:stirred:{target}: 'false'
invariants:
  healthy: 'true'
  holding:right:{tool}: 'true'
effects:
  event:stirred:{target}: 'true'
  holding:right:{tool}: 'true'
  free:left: 'true'
resources:
- right_arm
- right_hand
- left_arm
- left_hand
- object:{tool}
- zone:{target}
max_attempts: 1
irreversible: true
capability:
  mode: symbolic_mock_only
  stir: one coordinated demo cycle; no mixing-quality claim
---

# STIR

先确认任务要求的配料已进入碗，再使用持有的工具搅拌。用实际工具运动与物体接触验收。结束时仍持工具，若用户要求归位，由规划器随后选择放置。效果未知时不盲目重复。
