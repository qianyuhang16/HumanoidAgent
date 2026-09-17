"""Measure optional Blackwell patch projection; does not load task weights or run a robot."""

import argparse
import json
from pathlib import Path
import time

import torch
from transformers import AutoConfig
from transformers.models.qwen3_vl.modeling_qwen3_vl import Qwen3VLVisionPatchEmbed

from primitive_vla.vla.mem0 import linear_patch_embed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backbone', default='workspace/mem0/models/Qwen3-VL-2B-Instruct')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.manual_seed(0)
    config = AutoConfig.from_pretrained(args.backbone).vision_config
    module = Qwen3VLVisionPatchEmbed(config).cuda().to(torch.bfloat16).eval()
    patches = torch.randn(196, module.proj.weight[0].numel(), device='cuda', dtype=torch.bfloat16)
    with torch.inference_mode():
        original = module(patches)
        linear = linear_patch_embed(module, patches)
        torch.cuda.synchronize()
        result = {'device': torch.cuda.get_device_name(), 'random_weights': True,
                  'max_abs_diff': (original-linear).abs().max().item(),
                  'rmse': (original.float()-linear.float()).square().mean().sqrt().item(),
                  'bitwise_equal': torch.equal(original, linear)}
        for name, function in [('conv3d', lambda: module(patches)),
                               ('linear', lambda: linear_patch_embed(module, patches))]:
            start = time.perf_counter()
            for _ in range(3):
                function()
            torch.cuda.synchronize()
            result[name+'_seconds'] = (time.perf_counter()-start)/3
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
