---
name: TRANSFER
version: '1'
parameters:
  object: [item]
  destination: [basket]
preconditions:
  healthy: 'true'
  free:right: 'true'
  at:{object}:{destination}: 'false'
invariants:
  healthy: 'true'
effects:
  at:{object}:{destination}: 'true'
  free:right: 'true'
resources: [right_arm, right_gripper]
description: 根据物体与目标容器绑定，由 π0.5 完整执行抓取、搬运和释放；对象入篮且夹爪不再持物后交还 Agent。
backend: libero
boundary: atomic
timeout_s: 120
max_attempts: 2
irreversible: false
capability:
  mode: vla_policy
  policy: pi05_libero_base
  robot: LIBERO Panda
  action_space: OSC_POSE
policy_prompt: put the {object} in the {destination}
program: []
recovery: Reobserve the same world. Retry only if the measured entry conditions permit it; do not reset the environment.
cancellation: Stop accepting action chunks, pause simulation, preserve current object and gripper state.
---

# TRANSFER

选择用户目标里尚未入篮的一件物体，调用本技能。`object` 和 `destination` 由 Agent 从合法候选绑定中选择；不在 Python 中预设搬运顺序。

π0.5 接收这个对象对应的单次搬运指令与实时双相机图像。搬运轨迹、夹爪开闭都由模型预测。每执行一个动作前缀后重新观测，达到技能效果并保持配置要求的步数后停止。

此场景可能还有番茄酱等干扰物，除非用户要求，否则不要搬运。完成第一件物体后必须查看新的事实和执行结果，再选择下一件；禁止假设另一目标已经完成。

`at` 使用 LIBERO 原始空间谓词，`free:right` 使用夹爪与物体的物理接触判据。效果来自仿真真值，当前不是纯视觉感知实验。技能交接不重置环境、不自动生成下一次调用。
