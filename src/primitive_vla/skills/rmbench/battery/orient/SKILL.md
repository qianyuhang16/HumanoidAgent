---
name: ORIENT_BATTERY
version: '1'
parameters:
  position: [battery_side]
  direction: [battery_direction]
preconditions:
  healthy: 'true'
  initialized: 'true'
  polarity:{position}:{direction}: 'false'
invariants:
  healthy: 'true'
effects:
  seated:{position}: 'true'
  polarity:{position}:{direction}: 'true'
resources: [left_arm, right_arm, left_gripper, right_gripper]
backend: rmbench
timeout_s: 600
max_attempts: 3
description: Reorient and reinsert one battery, then inspect measured electrical feedback.
policy_prompt: Pick up the {position} battery and place it into the battery slot in the {direction} direction.
---

# Try one polarity change

Select a battery and direction from current measured state and past trials. Do not repeat
an already disproven pair of polarities. Change one battery at a time so the feedback is
interpretable. The native evaluator determines whole-task success; a classifier end or
one successful insertion is not whole-task success. IK only approaches; VLA handles
contact, rotation and insertion. During incomplete insertion preserve policy memory
instead of starting a new skill from an invalid entry state.
