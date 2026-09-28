"""Inference-only JSON worker. No simulator, ROS or motion client."""
import json
import os
import sys
import time
import traceback


def main():
    channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    engine = None
    for line in sys.stdin:
        message = json.loads(line)
        try:
            op = message['op']
            if op == 'close':
                break
            if op == 'init':
                from .models import CameraPerception
                engine = CameraPerception(message['options'], message['media'])
                result = {'ready':True, 'robot_control':False, 'models_dir':str(engine.root)}
            else:
                if engine is None or message['deadline'] <= time.time():
                    raise ValueError('Inference not initialized or request expired')
                args = message['arguments']
                if op == 'segment':
                    result = engine.segment(args['frame_ref'], args['query'])
                elif op == 'depth':
                    result = engine.depth(args['frame_ref'])
                else:
                    raise ValueError(f'Unknown inference operation: {op}')
            response = {'id':message['id'],'result':result}
        except Exception as error:
            traceback.print_exc()
            response = {'id':message['id'],'error':f'{type(error).__name__}: {error}'}
        channel.write(json.dumps(response,allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
