"""Isolated LeRobot inference; stdin/stdout carry synchronous JSON messages."""

import contextlib
import importlib.metadata
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from primitive_vla.vla.libero import LiberoPolicy


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    with contextlib.redirect_stdout(sys.stderr):
        policy = LiberoPolicy(config)
    print(json.dumps({'ready': True, 'weights_loaded_strict': True,
                      'action_horizon': policy.policy.config.chunk_size,
                      'state_used_by_model': config['discrete_state_input'],
                      'packages': {name: importlib.metadata.version(name)
                                   for name in ['torch', 'transformers', 'lerobot', 'sentencepiece']}}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        if request.get('stop'):
            break
        with contextlib.redirect_stdout(sys.stderr):
            result = policy.infer(request)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
