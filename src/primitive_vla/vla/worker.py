"""Persistent JSON-lines policy process in the configured LeRobot environment."""

import json
import os
import sys
import traceback


def main():
    channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    policy = None
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request['op'] == 'init':
                from .pi05 import Pi05
                policy = Pi05(request['config'])
                result = {'ready': True, 'weights_loaded_strict': True}
            elif request['op'] == 'infer':
                result = policy.infer(request)
            else:
                raise ValueError('Unknown policy operation')
            channel.write(json.dumps({'id': request['id'], 'result': result}, allow_nan=False) + '\n')
        except Exception as error:
            traceback.print_exc()
            channel.write(json.dumps({'id': request['id'], 'error': str(error)}) + '\n')


if __name__ == '__main__':
    main()
