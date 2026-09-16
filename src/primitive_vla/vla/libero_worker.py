"""JSON worker for the LIBERO policy; model dependencies stay in its own environment."""

import json
import os
from pathlib import Path
import sys
import traceback


def main():
    channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    from .libero import LiberoPolicy
    policy = LiberoPolicy(json.loads(Path(sys.argv[1]).read_text()))
    channel.write(json.dumps({'id': 0, 'result': {'ready': True, 'weights_loaded_strict': True}})+'\n')
    for line in sys.stdin:
        request = json.loads(line)
        if request['op'] == 'close':
            break
        try:
            result = policy.infer(request)
            response = {'id': request['id'], 'result': result}
        except Exception:
            response = {'id': request['id'], 'error': traceback.format_exc()}
        channel.write(json.dumps(response)+'\n')


if __name__ == '__main__':
    main()
