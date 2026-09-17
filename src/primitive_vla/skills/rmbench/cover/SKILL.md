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
A classifier boundary is advisory; physical evidence independently verifies the effect.

## Handoff and recovery

In IK mode, initialize visual memory before moving to the selected cover, then
update it from the post-approach image. IK only executes a pregrasp pose motion.
The VLA owns grasping, contact, transport, release, and retreat. If the classifier
ends before the effect is verified, the Agent may continue the same policy memory,
reapproach an empty gripper, or stop. Never reapproach while carrying a cover.
Recovery choices are bounded by the configured review and action budgets.
