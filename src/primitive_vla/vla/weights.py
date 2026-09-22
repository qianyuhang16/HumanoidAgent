"""Strict loading shared by the robot adapter and standalone evaluations."""

from pathlib import Path

import torch
from safetensors.torch import load_file
from lerobot.configs.policies import PreTrainedConfig
from lerobot.policies.pi05.modeling_pi05 import PI05Policy


def load_pi05(checkpoint, *, device='cuda', dtype='bfloat16', num_inference_steps=10,
              chunk_size=None, config=None):
    torch.set_num_threads(4)
    checkpoint = Path(checkpoint).expanduser()
    cfg = config if config is not None else PreTrainedConfig.from_pretrained(checkpoint)
    cfg.device, cfg.dtype, cfg.compile_model = 'cpu', dtype, False
    cfg.num_inference_steps = num_inference_steps
    if chunk_size is not None:
        cfg.chunk_size = chunk_size
        cfg.n_action_steps = min(cfg.n_action_steps, chunk_size)
    policy = PI05Policy(cfg)
    # Some LeRobot versions swallow from_pretrained errors. A partial load must fail.
    weights = policy._fix_pytorch_state_dict_keys(load_file(checkpoint / 'model.safetensors'), cfg)
    weights = {k if k.startswith('model.') else 'model.' + k: v for k, v in weights.items()}
    parameters = dict(policy.named_parameters(remove_duplicate=False))
    for name, parameter in parameters.items():
        if name not in weights:
            alias = next((key for key, value in parameters.items()
                          if value is parameter and key in weights), None)
            if alias:
                weights[name] = weights[alias]
    policy.load_state_dict(weights, strict=True)
    del weights
    return policy.eval().to(device)
