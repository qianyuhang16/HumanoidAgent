"""Recorded loader-only adaptation: use bundled executor weights and PyTorch SDPA."""

from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / 'policy/Mem-0/source/models/execution_module/qwen3_vl_model/qwen3_vl_encapsulation.py'
s = p.read_text()
s = s.replace('from transformers import Qwen3VLForConditionalGeneration, AutoProcessor',
              'from transformers import Qwen3VLForConditionalGeneration, AutoProcessor, AutoConfig')
old = '''        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            model_path,
            dtype=torch.bfloat16,
            attn_implementation="flash_attention_2",
            device_map=device_map,
        )'''
new = '''        # The full executor checkpoint includes every Qwen weight; no base weight reload.
        model_config = AutoConfig.from_pretrained(model_path)
        model_config._attn_implementation = "sdpa"
        self.model = Qwen3VLForConditionalGeneration._from_config(
            model_config, dtype=torch.bfloat16, attn_implementation="sdpa"
        ).to(target_device)'''
if old in s:
    p.write_text(s.replace(old, new))
elif new not in s:
    raise SystemExit('Upstream loader changed; review patch before applying')
