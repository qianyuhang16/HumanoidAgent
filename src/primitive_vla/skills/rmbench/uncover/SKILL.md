---
name: UNCOVER
version: '1'
parameters:
  position: [cover_position]
  color: [block_color]
preconditions:
  healthy: 'true'
  covered:right: 'true'
  cover_on:{position}: 'true'
  uncovered:{color}: 'false'
invariants:
  healthy: 'true'
effects:
  uncovered:{color}: 'true'
resources: [left_arm, right_arm, left_gripper, right_gripper]
description: Choose the cover position containing the next requested color, then let Mem-0 uncover it.
backend: rmbench
timeout_s: 600
max_attempts: 1
policy_prompt: Open the {position} cover to uncover the blocks in the order of red, green, and blue.
capability:
  controller: Mem-0 execution checkpoint for cover_blocks
---

# Uncover a remembered color

Use initial_memory to bind the requested color to its original left/middle/right
position. Uncover red, then green, then blue. The color binding declares the
expected effect; it does not tell the simulator which cover to move or overwrite
its result. The position binding alone is sent in the executor instruction.
