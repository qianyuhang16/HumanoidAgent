"""RMBench subtask Pi0.5 adapter using the released LeRobot preprocessing."""
import dataclasses
import json
from pathlib import Path

import numpy as np


def encode_state(qpos):
    """Aloha [L6, Lgrip, R6, Rgrip] -> training [L6, pad, R6, pad, grips]."""
    qpos = np.asarray(qpos, dtype=np.float32)
    if qpos.shape != (14,):
        raise ValueError('RMBench Pi0.5 requires 14 Aloha joints/grippers')
    return np.r_[qpos[:6], 0., qpos[7:13], 0., qpos[6], qpos[13]].astype(np.float32)


def decode_actions(actions):
    actions = np.asarray(actions, dtype=np.float32)
    if actions.ndim != 2 or actions.shape[1] != 16 or not np.isfinite(actions).all():
        raise ValueError('Expected finite 16-dimensional Pi0.5 actions')
    return actions[:, [0, 1, 2, 3, 4, 5, 14, 7, 8, 9, 10, 11, 12, 15]]


class RMBenchPi05:
    def __init__(self, config):
        import draccus
        import torch
        from safetensors.torch import load_file
        from lerobot.policies.pi05.configuration_pi05 import PI05Config
        from lerobot.policies.pi05.processor_pi05 import make_pi05_pre_post_processors
        from .weights import load_pi05

        self.torch = torch
        self.config = config
        root = Path(config['checkpoint'])
        raw = json.loads((root/'config.json').read_text())
        if raw.get('kf_xattn_enabled') or raw.get('use_relative_actions'):
            raise ValueError('This adapter supports the released vanilla absolute-action checkpoint')
        fields = {f.name for f in dataclasses.fields(PI05Config)}
        # The released fork adds disabled cross-attention and memory provenance fields.
        cfg = draccus.decode(PI05Config, {k:v for k,v in raw.items() if k in fields})
        self.policy = load_pi05(root, device='cuda', config=cfg)
        self.policy.config.device = 'cuda'
        stats = {}
        for key, value in load_file(root/'policy_preprocessor_step_3_normalizer_processor.safetensors').items():
            feature, statistic = key.rsplit('.', 1)
            stats.setdefault(feature, {})[statistic] = value
        self.pre, self.post = make_pi05_pre_post_processors(self.policy.config, stats)
        self.metadata = {'weights_loaded_strict':True, 'model':'pi05-rmbench-preset-60000',
            'checkpoint':str(root), 'action_horizon':self.policy.config.n_action_steps,
            'memory_mode':'current_head_when_no_retrieval', 'has_end_classifier':False}
        self.reset(config['seed'])

    def reset(self, seed=None):
        self.policy.reset()
        if seed is not None:
            self.torch.manual_seed(seed)
        return {'reset':True}

    def infer(self, request):
        torch = self.torch
        with np.load(request['snapshot']) as obs:
            batch = {'observation.state':torch.from_numpy(encode_state(obs['qpos'])),
                     'task':request['prompt']}
            for camera in ('head_camera', 'left_wrist', 'right_wrist', 'keyframe'):
                image = obs['head_camera'] if camera == 'keyframe' else obs[camera]
                batch['observation.image.'+camera] = torch.from_numpy(image.copy()).permute(2,0,1).float()/255
        with torch.inference_mode():
            raw = self.policy.predict_action_chunk(self.pre(batch))
            actions = self.post(raw)[0].float().cpu().numpy()
        actions = decode_actions(actions[:self.policy.config.n_action_steps])
        return {'actions':actions.tolist()}
