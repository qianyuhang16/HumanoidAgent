"""Isolate optional GPU dependencies behind the shared JsonProcess transport."""
import json
import os
from pathlib import Path
import sys
import traceback


def main():
    channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    from .model import GroundedSAM
    model = GroundedSAM(json.loads(Path(sys.argv[1]).read_text()))
    channel.write(json.dumps({'id': 0, 'result': {'ready': True}})+'\n')
    for line in sys.stdin:
        request = json.loads(line)
        request_id, op = request.pop('id'), request.pop('op')
        if op == 'close':
            break
        try:
            if op != 'inspect':
                raise ValueError('Unsupported perception operation')
            response = {'id': request_id, 'result': model.inspect(**request)}
        except Exception:
            response = {'id': request_id, 'error': traceback.format_exc()}
        channel.write(json.dumps(response, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
