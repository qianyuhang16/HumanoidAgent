"""One persistent LIBERO world per Agent task; never reset between skills."""

import json
import os
from pathlib import Path
import sys
import time
import traceback


class World:
    def __init__(self, config, media, run_dir, session):
        import imageio.v2 as imageio
        import numpy as np
        import torch
        sys.path.insert(0, config['libero_root'])
        from libero.libero import benchmark, get_libero_path
        from libero.libero.envs import OffScreenRenderEnv
        self.np, self.config, self.media, self.run_dir, self.session = np, config, Path(media), Path(run_dir), session
        torch.set_num_threads(4)
        np.random.seed(config['seed'])
        suite = benchmark.get_benchmark_dict()[config['suite']]()
        task = suite.get_task(config['task_id'])
        bddl = Path(get_libero_path('bddl_files'))/task.problem_folder/task.bddl_file
        initial_file = Path(get_libero_path('init_states'))/task.problem_folder/task.init_states_file
        initial = torch.load(initial_file, weights_only=False)[config['initial_state']]
        self.env = OffScreenRenderEnv(bddl_file_name=str(bddl),
            camera_heights=config['render_resolution'], camera_widths=config['render_resolution'],
            control_freq=config['control_hz'])
        self.env.seed(config['seed'])
        self.env.reset()
        self.obs = self.env.set_init_state(initial)
        for _ in range(config['settle_steps']):
            self.obs, _, _, _ = self.env.step([0.]*6+[-1.])
        self.steps, self.generation, self.active, self.snapshot_id = 0, 0, None, 0
        self.video_name = session+'.mp4'
        self.video = imageio.get_writer(str(self.media/self.video_name), fps=config['control_hz'], codec='libx264')
        self.trace = (self.run_dir/'physics.jsonl').open('w')
        (self.run_dir/'environment.json').write_text(json.dumps({'suite': config['suite'], 'task_id': config['task_id'],
            'task': task.language, 'bddl': str(bddl), 'initial_file': str(initial_file),
            'initial_state_index': config['initial_state'], 'reset_count': 1}, indent=2))
        self.capture()

    def cameras(self):
        from PIL import Image
        size = self.config['image_resolution']
        return [self.np.asarray(Image.fromarray(self.np.ascontiguousarray(self.obs[key][::-1, ::-1]))
            .resize((size, size), Image.Resampling.BILINEAR))
            for key in ('agentview_image', 'robot0_eye_in_hand_image')]

    def capture(self):
        from PIL import Image
        frame = self.np.concatenate(self.cameras(), axis=1)
        temporary = self.media/(self.session+'.tmp.png')
        Image.fromarray(frame).save(temporary)
        self.frame_name = f'{self.session}-{self.steps:05d}.png'
        temporary.replace(self.media/self.frame_name)
        self.video.append_data(frame)

    def values(self):
        env = self.env.env
        values = {key: bool(env._eval_predicate(predicate)) for key, predicate in self.config['predicates'].items()}
        held = [obj for obj in env.objects_dict.values()
                if env._check_grasp(env.robots[0].gripper, obj.contact_geoms)]
        values.update({'healthy': bool(self.np.isfinite(self.obs['robot0_joint_pos']).all()),
                       'free:right': not held, 'benchmark:success': bool(self.env.check_success())})
        return {key: 'true' if value else 'false' for key, value in values.items()}

    def observe(self):
        now = time.time()
        return {'timestamp': now, 'facts': {key: {'value': value, 'source': 'libero:physical_predicate',
            'timestamp': now, 'confidence': 1., 'ttl_s': 5.} for key, value in self.values().items()},
            'images': {'simulation': self.frame_name},
            'frames': {}, 'sensors': ['agentview', 'wrist', 'mujoco_contact', 'libero_predicates'],
            'proprioception': {'sim_time_s': self.steps/self.config['control_hz'], 'qpos': self.obs['robot0_joint_pos'].tolist(),
                'video': self.video_name, 'suite': self.config['suite'], 'task_id': self.config['task_id'],
                'control_steps': self.steps, 'reset_count': 1, 'controller': 'OSC_POSE'}}

    def model_observation(self):
        image, wrist = self.cameras()
        quat = self.obs['robot0_eef_quat'].copy()
        quat[3] = self.np.clip(quat[3], -1, 1)
        den = self.np.sqrt(1-quat[3]**2)
        angle = self.np.zeros(3) if self.np.isclose(den, 0) else quat[:3]*2*self.np.arccos(quat[3])/den
        state = self.np.r_[self.obs['robot0_eef_pos'], angle, self.obs['robot0_gripper_qpos']]
        self.snapshot_id += 1
        path = self.run_dir/f'observation-{self.snapshot_id:04d}.npz'
        self.np.savez_compressed(path, image=image, wrist_image=wrist, state=state)
        return {'path': str(path), 'state': state.tolist()}

    def begin(self, message):
        if self.active is not None or message['generation'] <= self.generation:
            raise ValueError('Busy controller or stale generation')
        if message['deadline'] <= time.time():
            raise ValueError('Expired skill call')
        self.generation = message['generation']
        self.active = {**message, 'chunk': 0, 'steps': 0, 'stable': 0}
        return {'ready': True}

    def chunk(self, message):
        active = self.active
        if active is None or message['call_id'] != active['call_id'] or message['generation'] != self.generation:
            raise ValueError('No matching active skill lease')
        if message['chunk'] != active['chunk']+1 or time.time() > active['deadline']:
            raise ValueError('Stale, repeated or expired action chunk')
        actions = self.np.asarray(message['actions'])
        if actions.ndim != 2 or actions.shape[1] != 7 or not self.np.isfinite(actions).all():
            raise ValueError('Expected finite LIBERO seven-dimensional actions')
        active['chunk'] = message['chunk']
        applied, completed = [], False
        for action in actions:
            if active['steps'] >= self.config['max_skill_steps']:
                break
            self.obs, _, _, _ = self.env.step(action.tolist())
            self.steps += 1
            active['steps'] += 1
            self.capture()
            values = self.values()
            established = all(values.get(key) == value for key, value in active['effects'].items())
            active['stable'] = active['stable']+1 if established else 0
            applied.append({'action': action.tolist(), 'step': self.steps})
            self.trace.write(json.dumps({'call_id': active['call_id'], 'generation': self.generation,
                'step': self.steps, 'action': action.tolist(), 'facts': values,
                'eef_pos': self.obs['robot0_eef_pos'].tolist()})+'\n')
            if active['stable'] >= self.config['success_hold_steps']:
                completed = True
                break
        self.trace.flush()
        return {'status': 'completed', 'applied': applied, 'skill_complete': completed,
                'skill_steps': active['steps'], 'exhausted': active['steps'] >= self.config['max_skill_steps']}

    def stop(self, call_id):
        if self.active is not None and self.active['call_id'] != call_id:
            raise ValueError('Stop does not match active call')
        self.active = None
        # Physics advances only on accepted chunks; clearing the lease prevents further motion.
        return {'stopped': True, 'call_id': call_id}

    def close(self):
        self.video.close()
        self.trace.close()
        self.env.close()


def main():
    channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    config_path, media, run_dir, session = sys.argv[1:]
    world = World(json.loads(Path(config_path).read_text()), media, run_dir, session)
    channel.write(json.dumps({'id': 0, 'result': {'ready': True}})+'\n')
    try:
        for line in sys.stdin:
            message = json.loads(line)
            op = message['op']
            if op == 'close':
                break
            try:
                if op == 'observe': result = world.observe()
                elif op == 'model_observation': result = world.model_observation()
                elif op == 'begin': result = world.begin(message)
                elif op == 'chunk': result = world.chunk(message)
                elif op == 'stop': result = world.stop(message['call_id'])
                else: raise ValueError('Unknown operation: '+op)
                response = {'id': message['id'], 'result': result}
            except Exception:
                response = {'id': message['id'], 'error': traceback.format_exc()}
            channel.write(json.dumps(response)+'\n')
    finally:
        world.close()


if __name__ == '__main__':
    main()
