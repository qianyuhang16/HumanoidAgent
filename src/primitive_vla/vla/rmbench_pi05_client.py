"""Synchronous simulator adapter over the shared persistent JSON worker transport."""
import asyncio
from pathlib import Path

import numpy as np

from primitive_vla.runtime.process import JsonProcess


class RMBenchPi05Client:
    def __init__(self, config, directory):
        self.loop = asyncio.new_event_loop()
        self.snapshot = Path(directory)/'policy-observation.npz'
        self.worker = JsonProcess(config['policy_python'], 'primitive_vla.vla.worker', [],
            Path(directory)/'pi05.log', timeout=180, startup_timeout=300,
            init_payload={'config':{'adapter':'rmbench_pi05', 'checkpoint':config['checkpoint'],
                                   'seed':config['seed']}}, env={'HF_HUB_OFFLINE':'1'})
        self.loop.run_until_complete(self.worker.start())
        self.metadata = self.worker.metadata
        self.executor = self.worker
        self.end_count = 0

    def seed(self, seed):
        self.loop.run_until_complete(self.worker.request('reset', seed=seed))

    def reset(self):
        self.end_count = 0
        self.loop.run_until_complete(self.worker.request('reset'))

    def update(self, observation, prompt):
        self.observation, self.prompt = observation, prompt
        return 0

    def infer(self):
        obs = self.observation
        np.savez(self.snapshot, qpos=obs['joint_action']['vector'],
                 head_camera=obs['observation']['head_camera']['rgb'],
                 left_wrist=obs['observation']['left_camera']['rgb'],
                 right_wrist=obs['observation']['right_camera']['rgb'])
        result = self.loop.run_until_complete(self.worker.request('infer',
            snapshot=str(self.snapshot), prompt=self.prompt))
        return np.asarray(result['actions'], dtype=np.float32)

    def close(self):
        self.loop.run_until_complete(self.worker.close())
        self.loop.close()
