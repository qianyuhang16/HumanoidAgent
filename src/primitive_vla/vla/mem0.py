"""Official Mem-0 executor with external subtask scheduling (no official planner)."""

import importlib
from pathlib import Path
import sys


def linear_patch_embed(module, hidden_states):
    """Same single-patch projection as Conv3d, using GEMM on Blackwell.

    Each input already is one complete non-overlapping C×T×H×W patch.
    This preserves weights but may change floating-point rounding.
    """
    import torch.nn.functional as functional
    weight = module.proj.weight
    patches = hidden_states.reshape(-1, weight[0].numel()).to(dtype=weight.dtype)
    return functional.linear(patches, weight.flatten(1), module.proj.bias)


class Mem0Policy:
    def __init__(self, config):
        import numpy as np
        import torch
        from omegaconf import OmegaConf
        self.np, self.torch, self.config = np, torch, config
        root = Path(config['repo']).resolve()
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / 'policy/Mem-0'))
        from source.models.execution_module.memorymatters_executor import MemoryMattersExecutor
        self.codec = importlib.import_module('policy.Mem-0.deploy_policy')
        cfg = OmegaConf.load(root / 'policy/Mem-0/deploy_policy.yml')
        cfg.execution_module.qwen_vl.model_path = config['backbone']
        torch.set_num_threads(4)
        torch.manual_seed(config['seed'])
        self.executor = MemoryMattersExecutor(cfg, device=torch.device('cuda')).to('cuda').eval()
        checkpoint = torch.load(config['checkpoint'], map_location='cpu', mmap=True, weights_only=False)
        self.executor.load_state_dict(checkpoint['model_state_dict'], strict=True)
        if config.get('linear_patch_embed', False):
            from types import MethodType
            from transformers.models.qwen3_vl.modeling_qwen3_vl import Qwen3VLVisionPatchEmbed
            for module in self.executor.modules():
                if isinstance(module, Qwen3VLVisionPatchEmbed):
                    module.forward = MethodType(linear_patch_embed, module)
        self.metadata = {'weights_loaded_strict': True, 'global_step': checkpoint['global_step'],
                         'parameters': sum(p.numel() for p in self.executor.parameters()),
                         'attention': 'sdpa', 'official_planner_loaded': False,
                         'linear_patch_embed': config.get('linear_patch_embed', False)}
        self.codec._STATS.update(self.codec.load_stats(config['norm_stats']))
        if any(self.codec._STATS[k] is None for k in ('state_min','state_max','action_min','action_max')):
            raise ValueError('Mem-0 requires task-specific min-max statistics')
        self.codec._RUNTIME_SETTINGS.update(camera_key='head_camera', image_size=(224,224))
        self.reset()

    def reset(self):
        self.executor.memory_bank.reset()
        self.fused = self.state = None
        self.end_count = self.observation_count = 0

    def update(self, observation, prompt):
        torch = self.torch
        observation = dict(observation, instruction=prompt)
        encoded = self.codec.encode_obs(observation)
        with torch.inference_mode():
            qwen = self.executor.qwen_model
            inputs = qwen.build_qwenvl_inputs([[encoded['image']]], [prompt], system_prompt=None,
                add_summary_token=False, add_generation_prompt=False, max_length=128)
            output = qwen(**inputs, output_attentions=False, output_hidden_states=True, return_dict=True)
            image_feature, text_feature = qwen.extract_features(inputs.input_ids, output.hidden_states[-1])
            memory, anchor, flag = self.executor.memory_bank.update_on_eval(
                image_feature, text_feature, self.executor.classifier, episode_id=0)
            self.fused = torch.cat([memory, anchor, text_feature], dim=1)
            self.state = torch.from_numpy(encoded['state']).to(device=self.fused.device,
                                                               dtype=self.fused.dtype).unsqueeze(1)
        self.end_count += int(flag)
        self.observation_count += 1
        return int(flag)

    def infer(self):
        with self.torch.inference_mode():
            head = self.executor.action_model
            dtype = next(head.parameters()).dtype
            normalized = head.predict_action(self.fused.to(dtype), self.state.to(dtype)).float().cpu().numpy()
        actions = self.codec._postprocess_action_chunk(normalized)
        if actions.ndim != 2 or actions.shape[1] != 14 or not self.np.isfinite(actions).all():
            raise ValueError('Invalid Mem-0 Aloha action chunk')
        return actions[:self.config['action_horizon']]
