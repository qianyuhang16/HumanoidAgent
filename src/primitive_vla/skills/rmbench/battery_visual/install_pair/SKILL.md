---
name: INSTALL_PAIR_VISUAL
version: '1'
parameters:
  position: [battery_pair]
preconditions:
  control:available: 'true'
  inspection:needed: 'false'
  target:left:available: 'true'
  target:right:available: 'true'
invariants: {}
effects:
  inspection:needed: 'true'
resources: [left_arm, right_arm, left_gripper, right_gripper]
backend: rmbench
timeout_s: 600
max_attempts: 2
description: Approach the visually localized batteries, then ask Mem-0 to install both. Always inspect afterwards.
policy_prompt: Use dual arm to pick up the batteries and place them into the battery slots in the positive direction.
---

# Install visually localized batteries

IK only moves to a clearance above the RGB-D surface. Mem-0 controls grasp,
insertion and release. A returned classifier end vote or action budget only
returns control; it does not confirm placement or polarity. Invoke INSPECT_SCENE
next. The requested positive direction is command history, not measured state.
