"""LeRobot PI05 weights with an explicit experimental Franka adapter."""

import json
from pathlib import Path
from time import monotonic

import numpy as np
import torch
from PIL import Image
from transformers import AutoTokenizer

from lerobot.utils.constants import OBS_LANGUAGE_ATTENTION_MASK, OBS_LANGUAGE_TOKENS
from primitive_vla.vla.weights import load_pi05


class Pi05:
    def __init__(self, config):
        self.config = config
        self.policy = load_pi05(config['checkpoint'], device=config['device'], dtype=config['dtype'],
                                num_inference_steps=config['num_inference_steps'])
        cfg = self.policy.config
        self.tokenizer = AutoTokenizer.from_pretrained('google/paligemma-3b-pt-224', local_files_only=True)
        self.stats = json.loads(Path(config['norm_stats']).expanduser().read_text())['norm_stats']
        for field in ('state', 'actions'):
            low, high = [np.asarray(self.stats[field][key], dtype=np.float32) for key in ('q01', 'q99')]
            if low.shape != (8,) or high.shape != (8,) or not np.isfinite([low, high]).all() or np.any(high <= low):
                raise ValueError('Franka adapter requires eight valid state/action quantile dimensions')
        if config['image_key'] not in cfg.image_features:
            raise ValueError('Configured image key is absent from model input features')

    def infer(self, request):
        started = monotonic()
        qpos = np.asarray(request['qpos'], dtype=np.float32)
        if qpos.shape != (9,) or not np.isfinite(qpos).all():
            raise ValueError('Panda input requires seven arm joints and two finger positions')
        state = np.r_[qpos[:7], np.clip(1 - qpos[7:].sum() / .08, 0, 1)]
        low, high = [np.asarray(self.stats['state'][key]) for key in ('q01', 'q99')]
        normalized = np.zeros(32, dtype=np.float32)
        normalized[:8] = (state - low) / (high - low + 1e-6) * 2 - 1
        bins = np.digitize(normalized, np.linspace(-1, 1, 257)[:-1]) - 1
        task = request['prompt'].strip().replace('_', ' ').replace('\n', ' ')
        prompt = f"Task: {task}, State: {' '.join(map(str, bins))};\nAction: "
        tokens = self.tokenizer(prompt, return_tensors='pt', max_length=self.policy.config.tokenizer_max_length,
                                padding='max_length', truncation=True)
        image = np.asarray(Image.open(request['image']).convert('RGB').resize((224, 224))).copy()
        device = self.config['device']
        batch = {self.config['image_key']: torch.from_numpy(image).permute(2, 0, 1).float().unsqueeze(0).to(device) / 255,
                 OBS_LANGUAGE_TOKENS: tokens.input_ids.to(device),
                 OBS_LANGUAGE_ATTENTION_MASK: tokens.attention_mask.bool().to(device)}
        with torch.inference_mode():
            raw = self.policy.predict_action_chunk(batch)[0].float().cpu().numpy()
        if raw.shape != (50, 32) or not np.isfinite(raw).all():
            raise ValueError('PI05 returned invalid action chunk')
        low, high = [np.asarray(self.stats['actions'][key]) for key in ('q01', 'q99')]
        actions = (raw[:, :8] + 1) / 2 * (high - low) + low
        if self.config['action_mode'] == 'joint_delta':
            # Each horizon entry is relative to the observation at chunk start,
            # not a delta accumulated on the previous predicted entry.
            actions[:, :7] += state[:7]
        return {'actions': actions.tolist(), 'raw_normalized_actions': raw.tolist(),
                'state': state.tolist(), 'normalized_state': normalized.tolist(),
                'latency_ms': round((monotonic() - started) * 1000),
                'masked_cameras': [key for key in self.policy.config.image_features if key != self.config['image_key']],
                'action_mode': self.config['action_mode'], 'adapter': self.config['adapter'],
                'weights_loaded_strict': True}
