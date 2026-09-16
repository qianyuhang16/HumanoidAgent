"""JSON-lines subprocess protocol; all SAPIEN access stays on the main thread."""

import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback


def main():
    # Native renderer messages use stdout too; reserve a duplicated pipe for RPC.
    channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    messages = queue.Queue()

    def read():
        for line in sys.stdin:
            try:
                messages.put(json.loads(line))
            except ValueError:
                continue
        messages.put({'id': None, 'op': 'close'})

    def send(message):
        channel.write(json.dumps(message, allow_nan=False) + '\n')

    threading.Thread(target=read, daemon=True).start()
    world = motion = active = policy = joint_policy = None
    generation = 0
    completed = {}
    next_render = 0
    last_phase = None
    try:
        while True:
            started = time.monotonic()
            try:
                message = messages.get(timeout=.02 if world is None else .001)
            except queue.Empty:
                message = None
            if message is not None:
                op, request_id = message['op'], message['id']
                try:
                    if op == 'init':
                        from .scene import KitchenScene
                        from .motion import MotionProgram
                        from .policy_control import JointPolicyControl
                        world = KitchenScene(message['config'], Path(message['media']), message['session'])
                        motion = MotionProgram(world)
                        joint_policy = JointPolicyControl(world)
                        world.render()
                        send({'id': request_id, 'result': {'ready': True}})
                    elif op == 'observe':
                        send({'id': request_id, 'result': world.observe()})
                    elif op == 'run':
                        call = message['call']
                        if call['call_id'] in completed:
                            previous = completed[call['call_id']]
                            if previous['call'] != call:
                                raise ValueError('Call ID payload collision')
                            send({'id': request_id, 'result': previous['result']})
                        else:
                            if active is not None or policy is not None or message['generation'] <= generation:
                                raise ValueError('Busy worker or stale control generation')
                            if call['deadline'] <= time.time():
                                raise ValueError('Expired call')
                            generation = message['generation']
                            active = {'id': request_id, 'call': call, 'controller': motion,
                                      'iterator': motion.run(message['program'], call['bindings'])}
                    elif op == 'policy_begin':
                        call = message['call']
                        if active is not None or policy is not None or message['generation'] <= generation:
                            raise ValueError('Busy worker or stale policy generation')
                        if call['deadline'] <= time.time():
                            raise ValueError('Expired policy call')
                        generation = message['generation']
                        policy = {'call': call, 'generation': generation, 'chunk': 0}
                        world.stir_active = bool(message.get('track_stir'))
                        send({'id': request_id, 'result': {'ready': True}})
                    elif op == 'policy_chunk':
                        if policy is None or active is not None or message['call_id'] != policy['call']['call_id'] or message['generation'] != generation:
                            raise ValueError('No matching active policy lease')
                        if message['chunk'] != policy['chunk'] + 1:
                            raise ValueError('Repeated or out-of-order policy chunk')
                        if policy['call']['deadline'] <= time.time():
                            raise ValueError('Expired policy chunk')
                        policy['chunk'] = message['chunk']
                        active = {'id': request_id, 'call': policy['call'], 'controller': joint_policy, 'policy': True,
                                  'iterator': joint_policy.run(message['actions'], message['action_hz'],
                                      message['max_joint_step'], message['max_gripper_step'])}
                    elif op == 'policy_end':
                        if policy is None or active is not None or message['call_id'] != policy['call']['call_id'] or message['generation'] != generation:
                            raise ValueError('No idle matching policy lease')
                        joint_policy.stop()
                        policy = None
                        send({'id': request_id, 'result': {'stopped': True}})
                    elif op == 'stop':
                        if active and active['call']['call_id'] == message['call_id']:
                            active['iterator'].close(); active['controller'].stop()
                            result = {'status': 'cancelled', 'reason': message['reason']}
                            completed[message['call_id']] = {'call': active['call'], 'result': result}
                            send({'id': active['id'], 'result': result}); active = None
                        if policy and policy['call']['call_id'] == message['call_id']:
                            joint_policy.stop(); policy = None
                        # One simulation process owns one controller; idle is stopped.
                        send({'id': request_id, 'result': {'stopped': active is None and policy is None, 'call_id': message['call_id']}})
                    elif op == 'close':
                        if active:
                            active['iterator'].close(); active['controller'].stop()
                        if policy:
                            joint_policy.stop()
                        break
                    else:
                        raise ValueError(f'Unknown operation: {op}')
                except Exception as error:
                    traceback.print_exc()
                    send({'id': request_id, 'error': str(error)})
            if world is None:
                continue
            if active:
                try:
                    if active['call']['deadline'] <= time.time():
                        raise TimeoutError('Simulation call deadline exceeded')
                    next(active['iterator'])
                    phase = active['controller'].phase
                    if last_phase != phase:
                        last_phase = phase
                        send({'event': 'feedback', 'call_id': active['call']['call_id'], 'generation': generation, 'phase': phase})
                except Exception as error:
                    status = 'completed' if isinstance(error, StopIteration) else 'failed'
                    if status == 'failed':
                        active['controller'].stop(); traceback.print_exc()
                    result = {'status': status, 'reason': '' if status == 'completed' else str(error)}
                    if active.get('policy'):
                        result['applied'] = joint_policy.applied
                    else:
                        completed[active['call']['call_id']] = {'call': active['call'], 'result': result}
                    send({'id': active['id'], 'result': result})
                    active = None
            for _ in range(world.config['physics_hz'] // world.config['control_hz']):
                world.step()
            if world.ticks >= next_render:
                world.render()
                next_render = world.ticks + max(1, world.config['physics_hz'] // world.config['render_fps'])
            delay = 1 / world.config['control_hz'] / world.config['realtime_factor'] - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
    finally:
        if world:
            world.render()
            world.close()
            evidence = Path(sys.argv[1]) if len(sys.argv) > 1 else None
            if evidence:
                evidence.write_text(json.dumps(world.observe(), indent=2))
        channel.close()


if __name__ == '__main__':
    main()
