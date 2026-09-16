---
name: COVER
version: '1'
parameters:
  position: [cover_position]
preconditions:
  healthy: 'true'
  covered:{position}: 'false'
  cover_on:{position}: 'false'
invariants:
  healthy: 'true'
effects:
  covered:{position}: 'true'
resources: [left_arm, right_arm, left_gripper, right_gripper]
description: Use Mem-0 to cover the block at the selected table position.
backend: rmbench
timeout_s: 600
max_attempts: 1
policy_prompt: Cover the {position} block with the {position} cover.
capability:
  controller: Mem-0 execution checkpoint for cover_blocks
---

# Cover a block

Cover the left, middle, and right blocks in that order. Before any covering,
remember each block's color from the initial_memory image. Keep that image as
visual memory; no simulator color-to-position mapping is supplied to the planner.
A classifier boundary ends execution; physical evidence independently verifies the effect.
