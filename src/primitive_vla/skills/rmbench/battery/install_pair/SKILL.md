---
name: INSTALL_PAIR
version: '1'
parameters:
  position: [battery_pair]
preconditions:
  healthy: 'true'
  initialized: 'false'
invariants:
  healthy: 'true'
effects:
  seated:left: 'true'
  seated:right: 'true'
  polarity:left:positive: 'true'
  polarity:right:positive: 'true'
resources: [left_arm, right_arm, left_gripper, right_gripper]
backend: rmbench
timeout_s: 600
max_attempts: 2
description: Install both batteries in the positive direction using the official dual-arm executor instruction.
policy_prompt: Use dual arm to pick up the batteries and place them into the battery slots in the positive direction.
---

# Initialize the battery trial

The VLA owns grasp, lifting, insertion and release. IK only approaches the two batteries.
Installation does not imply electrical success. After installation inspect the dashboard
and the measured battery poses. Never infer the hidden correct polarity combination.
