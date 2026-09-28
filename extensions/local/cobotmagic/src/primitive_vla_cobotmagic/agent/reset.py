"""Single-arm reset through the existing bridge, with feedback-based completion."""
import json
from pathlib import Path
import threading
import time
import numpy as np
from ..ik.execution import MotionBridge, current_state_command, feedback_tolerance
from ..vla.config import ResetSettings

class ResetRun:
    def __init__(self, bridge_config, settings, arm, folder):
        self.settings=ResetSettings.model_validate(settings).model_dump()
        self.config={**bridge_config,'arm':arm,
                     'feedback_limit_tolerance_deg':self.settings['feedback_limit_tolerance_deg']}
        self.arm=arm
        self.folder=Path(folder)
        self.cancelled=threading.Event()
        self.sent=self.completed=False
        self.task=None

    @property
    def control_released(self):
        return self.completed or not self.sent

    def run(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        bridge=MotionBridge(self.config,allow_motion=True)
        try:
            if self.cancelled.is_set():raise RuntimeError('Reset cancelled')
            positions,_=bridge.observation()
            goal=np.asarray(self.settings['joints_rad'][self.arm],float)
            gripper_goal=self.settings['gripper_position_m'][self.arm]
            tolerance=feedback_tolerance(self.config)
            # Use the bridge's bounded hold as the interpolation seed, leaving
            # raw arrival feedback intact. Explicit home targets stay strict.
            initial=current_state_command(positions, feedback_limit_tolerance_deg=tolerance)[self.arm]
            start=np.asarray(initial[:6])
            gripper_start=initial[6]
            current_state_command(positions,self.arm,goal,feedback_limit_tolerance_deg=tolerance)
            duration=self.settings['duration_s'];began=time.monotonic()
            final_sent=False
            with (self.folder/'reset.trace.jsonl').open('w') as log:
                while True:
                    if self.cancelled.is_set():raise RuntimeError('Reset cancelled')
                    elapsed=time.monotonic()-began
                    error=float(np.max(np.abs(np.asarray(positions[self.arm][:6])-goal)))
                    gripper_error=None if gripper_goal is None else abs(positions[self.arm][6]-gripper_goal)
                    gripper_arrived=gripper_error is None or gripper_error<=self.settings['gripper_tolerance_m']
                    if final_sent and error<=self.settings['joint_tolerance_rad'] and gripper_arrived:
                        self.completed=True
                        return {'arm':self.arm,'arrival_verified':True,'reset_completed':True,
                                'joint_error_rad':error,'target_joints_rad':goal.tolist(),
                                'target_gripper_m':gripper_goal,'gripper_error_m':gripper_error,
                                'nominal_duration_s':duration,'positions':positions}
                    if elapsed>duration+self.settings['settle_timeout_s']:
                        raise TimeoutError(f'Reset did not reach target: joint error {error:.4f} rad; gripper error {gripper_error} m')
                    ratio=min(1.,elapsed/duration)
                    joints=start+(goal-start)*ratio
                    command=current_state_command(positions,self.arm,joints,feedback_limit_tolerance_deg=tolerance)
                    if gripper_goal is not None:
                        command[self.arm][6]=gripper_start+(gripper_goal-gripper_start)*ratio
                    if self.cancelled.is_set():raise RuntimeError('Reset cancelled')
                    self.sent=True
                    log.write(json.dumps({'elapsed_s':elapsed,'positions':command})+'\n');log.flush()
                    bridge.action(command)
                    final_sent=ratio>=1.
                    self.cancelled.wait(1/self.settings['fps'])
                    positions,_=bridge.observation()
        finally:
            if self.sent and not self.completed:
                try:
                    positions,_=bridge.observation()
                    bridge.action(current_state_command(positions,feedback_limit_tolerance_deg=feedback_tolerance(self.config)))
                except Exception:pass
            bridge.close()
