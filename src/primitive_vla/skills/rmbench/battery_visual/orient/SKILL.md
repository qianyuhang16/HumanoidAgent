---
name: ORIENT_VISUAL
version: '1'
parameters:
  position: [battery_side]
  direction: [battery_direction]
preconditions:
  control:available: 'true'
  inspection:needed: 'false'
  target:{position}:available: 'true'
invariants: {}
effects:
  inspection:needed: 'true'
resources: [left_arm, right_arm, left_gripper, right_gripper]
backend: rmbench
timeout_s: 600
max_attempts: 2
description: Ask Mem-0 to reinsert the visually localized battery in the requested orientation. Inspect the actual result.
policy_prompt: Pick up the {position} battery and place it into the battery slot in the {direction} direction.
---

# Reorient one battery

The left/right aliases come from RGB-D world-x ordering, not simulator actor IDs.
Both arms may move under the official learned policy. A requested polarity is not
an observation. If a terminal is occluded, retain unknown rather than inferring
that this command succeeded. Do not read the hidden target combination.
