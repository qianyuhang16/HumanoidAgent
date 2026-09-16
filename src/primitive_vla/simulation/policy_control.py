"""Apply policy joint targets directly. No IK or scripted manipulation here."""

import math
import numpy as np


class JointPolicyControl:
    def __init__(self, world):
        self.world = world
        self.phase = 'vla:idle'
        self.applied = []

    def run(self, actions, action_hz, max_joint_step, max_gripper_step):
        values = np.asarray(actions, dtype=float)
        if values.ndim != 2 or values.shape[1] != 8 or not 1 <= len(values) <= 50 or not np.isfinite(values).all():
            raise ValueError('Expected a finite [steps, 8] joint/gripper chunk')
        if action_hz <= 0 or action_hz > self.world.config['control_hz'] or max_joint_step <= 0 or max_gripper_step <= 0:
            raise ValueError('Invalid policy execution rate or limits')
        self.applied = []
        self.phase = 'vla:executing'
        limits = self.world.robot.get_qlimits()[:7]
        elapsed_ticks = 0
        for index, row in enumerate(values):
            current = self.world.robot.get_qpos()
            arm = np.clip(row[:7], current[:7] - max_joint_step, current[:7] + max_joint_step)
            arm = np.clip(arm, limits[:, 0], limits[:, 1])
            # Franka profile: normalized closure 0=open, 1=closed. Panda has
            # two finger joints, each travelling 0..0.04 metres.
            width = .04 * (1 - float(np.clip(row[7], 0, 1)))
            fingers = np.clip(width, current[7:] - max_gripper_step, current[7:] + max_gripper_step)
            self.world.target[:] = np.r_[arm, np.clip(fingers, 0, .04)]
            self.applied.append({'requested': row.tolist(), 'joint_targets': self.world.target.tolist(),
                                 'arm_limited': bool(np.any(np.abs(arm - row[:7]) > 1e-6))})
            ticks = math.ceil((index + 1) * self.world.config['control_hz'] / action_hz)
            for _ in range(ticks - elapsed_ticks):
                yield
            elapsed_ticks = ticks
        self.phase = 'vla:waiting_observation'

    def stop(self):
        self.world.target[:7] = self.world.robot.get_qpos()[:7]
        self.world.stir_active = False
        self.phase = 'vla:stopped'
