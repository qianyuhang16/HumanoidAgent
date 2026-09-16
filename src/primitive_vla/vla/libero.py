"""LeRobot π0.5 adapter aligned with the official LIBERO checkpoint."""

import json
from pathlib import Path
from time import perf_counter

import numpy as np
import sentencepiece
import torch

from primitive_vla.vla.weights import load_pi05
from lerobot.utils.constants import OBS_LANGUAGE_ATTENTION_MASK, OBS_LANGUAGE_TOKENS


class LiberoPolicy:
    def __init__(self, config):
        self.config = config
        torch.manual_seed(config['seed'])
        self.policy = load_pi05(config['checkpoint'], device=config['device'], dtype=config['dtype'],
                                num_inference_steps=config['num_inference_steps'],
                                chunk_size=config['action_horizon'])
        self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=config['tokenizer'])
        self.stats = json.loads(Path(config['norm_stats']).read_text())['norm_stats']
        self.q01, self.q99 = [np.asarray(self.stats['actions'][key]) for key in ('q01', 'q99')]
        if self.q01.shape != (7,) or self.q99.shape != (7,):
            raise ValueError('LIBERO OSC_POSE requires seven action statistics')

    def infer(self, request):
        started = perf_counter()
        with np.load(request['observation']) as observation:
            state = observation['state']
            images = [observation['image'].copy(), observation['wrist_image'].copy()]
        prompt = request['prompt'].strip().replace('_', ' ').replace('\n', ' ')
        if self.config['discrete_state_input']:
            low, high = [np.asarray(self.stats['state'][key]) for key in ('q01', 'q99')]
            normalized = np.pad((state-low)/(high-low+1e-6)*2-1, (0, 32-len(state)))
            bins = np.digitize(normalized, np.linspace(-1, 1, 257)[:-1])-1
            prompt = f"Task: {prompt}, State: {' '.join(map(str, bins))};\nAction: "
            tokens = self.tokenizer.encode(prompt, add_bos=True)
        else:
            # Exact OpenPI non-discrete prompt format, including separately encoded newline.
            tokens = self.tokenizer.encode(prompt, add_bos=True) + self.tokenizer.encode('\n')
        length = self.policy.config.tokenizer_max_length
        tokens = tokens[:length]
        mask = [True]*len(tokens) + [False]*(length-len(tokens))
        tokens += [0]*(length-len(tokens))
        device = self.config['device']
        batch = {OBS_LANGUAGE_TOKENS: torch.tensor([tokens], device=device),
                 OBS_LANGUAGE_ATTENTION_MASK: torch.tensor([mask], device=device)}
        for key, image in zip(self.config['image_keys'], images):
            batch[key] = torch.from_numpy(image).permute(2, 0, 1)[None].to(device).float()/255
        with torch.inference_mode():
            raw = self.policy.predict_action_chunk(batch)[0].float().cpu().numpy()[:, :7]
        actions = (raw+1)/2*(self.q99-self.q01+1e-6)+self.q01
        if actions.shape != (self.config['action_horizon'], 7) or not np.isfinite(actions).all():
            raise ValueError('Invalid model action chunk')
        return {'actions': actions.tolist(), 'raw_normalized_actions': raw.tolist(),
                'latency_ms': round((perf_counter()-started)*1000, 2),
                'prompt': prompt, 'state_used_by_model': self.config['discrete_state_input']}
