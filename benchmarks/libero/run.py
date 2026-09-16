"""Bounded LIBERO evaluation with real environment success, action logs and videos.

Run with the LIBERO Python environment; the policy has its own Python environment.
Configuration paths are relative to the project root. No training data is needed.
"""

import argparse
from collections import deque
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import selectors
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
os.environ.setdefault('MUJOCO_GL', 'egl')
os.environ.setdefault('PYOPENGL_PLATFORM', 'egl')

import imageio.v2 as imageio
import numpy as np
from PIL import Image
import torch


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


class PolicyProcess:
    def __init__(self, config, config_path, output):
        self.timeout = config['model_timeout_s']
        self.log = (output/'policy.log').open('w')
        self.process = subprocess.Popen([config['model_python'], '-u',
            str(Path(__file__).with_name('policy_worker.py')), str(config_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self.metadata = self.receive()
            if not self.metadata.get('weights_loaded_strict'):
                raise RuntimeError('Policy did not report a strict weight load')
        except BaseException:
            self.close()
            raise

    def receive(self):
        if not self.selector.select(self.timeout):
            raise TimeoutError('Policy timed out; see policy.log')
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('Policy process exited; see policy.log')
        return json.loads(line)

    def infer(self, observation, prompt):
        self.process.stdin.write(json.dumps({'observation': str(observation), 'prompt': prompt})+'\n')
        self.process.stdin.flush()
        return self.receive()

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"stop":true}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=5)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()
        self.log.close()


def image(obs, key, size):
    # Both cameras are rotated 180 degrees, as in the OpenPI training/eval pipeline.
    frame = np.ascontiguousarray(obs[key][::-1, ::-1])
    return np.asarray(Image.fromarray(frame).resize((size, size), Image.Resampling.BILINEAR))


def quat_to_axisangle(quat):
    quat = np.asarray(quat).copy()
    quat[3] = np.clip(quat[3], -1, 1)
    denominator = np.sqrt(1-quat[3]**2)
    return np.zeros(3) if np.isclose(denominator, 0) else quat[:3]*2*np.arccos(quat[3])/denominator


def evaluate_episode(config, policy, env, initial_state, task, task_id, episode_id, output):
    name = f'task-{task_id:02d}-episode-{episode_id:02d}'
    directory = output/name
    directory.mkdir()
    env.reset()
    obs = env.set_init_state(initial_state)
    for _ in range(config['settle_steps']):
        obs, _, _, _ = env.step([0.]*6+[-1.])
    if env.check_success():
        raise RuntimeError('Initial state already satisfies the task; refusing a trivial success')
    started = time.perf_counter()
    queue = deque()
    latencies, success, error, steps, calls = [], False, None, 0, 0
    np.save(directory/'initial-state.npy', initial_state)
    frames = imageio.get_writer(str(directory/'rollout.mp4'), fps=config['video_fps'], codec='libx264')
    trace = (directory/'trace.jsonl').open('w')
    try:
        for step in range(config['max_steps']):
            base = image(obs, 'agentview_image', config['image_resolution'])
            wrist = image(obs, 'robot0_eye_in_hand_image', config['image_resolution'])
            frames.append_data(np.concatenate([base, wrist], axis=1))
            state = np.r_[obs['robot0_eef_pos'], quat_to_axisangle(obs['robot0_eef_quat']),
                          obs['robot0_gripper_qpos']]
            if not queue:
                observation_path = directory/f'observation-{calls:03d}.npz'
                np.savez_compressed(observation_path, image=base, wrist_image=wrist, state=state)
                prediction = policy.infer(observation_path, task.language)
                latencies.append(prediction['latency_ms'])
                trace.write(json.dumps({'event': 'inference', 'step': step, 'call': calls,
                    'observation': observation_path.name, 'state': state.tolist(), **prediction})+'\n')
                queue.extend(prediction['actions'][:config['replan_steps']])
                calls += 1
            action = queue.popleft()
            obs, reward, done, info = env.step(action)
            steps = step+1
            success = bool(env.check_success())
            trace.write(json.dumps({'event': 'action', 'step': step, 'action': action,
                'eef_pos_after': obs['robot0_eef_pos'].tolist(),
                'gripper_qpos_after': obs['robot0_gripper_qpos'].tolist(),
                'reward': float(reward), 'done': bool(done), 'success_predicate': success})+'\n')
            trace.flush()
            if success or done:
                break
        frames.append_data(np.concatenate([
            image(obs, 'agentview_image', config['image_resolution']),
            image(obs, 'robot0_eye_in_hand_image', config['image_resolution'])], axis=1))
    except Exception:
        error = traceback.format_exc()
        trace.write(json.dumps({'event': 'error', 'error': error})+'\n')
    finally:
        trace.close()
        frames.close()
    result = {'task_id': task_id, 'episode_id': episode_id, 'task': task.language,
        'success': success, 'error': error, 'steps': steps, 'inference_calls': calls,
        'latency_ms': latencies, 'elapsed_s': round(time.perf_counter()-started, 2),
        'video': f'{name}/rollout.mp4', 'trace': f'{name}/trace.jsonl',
        'initial_state': f'{name}/initial-state.npy'}
    write_json(directory/'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='benchmarks/libero/config.json')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if not 1 <= config['replan_steps'] <= config['action_horizon']:
        raise ValueError('replan_steps must fit inside the model action horizon')
    output = Path(config['output']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output/'results.json').exists():
        raise FileExistsError('Choose a new output directory to preserve earlier evaluation results')
    write_json(output/'config.json', config)
    (output/'index.html').write_text(Path(__file__).with_name('report.html').read_text())
    sys.path.insert(0, config['libero_root'])
    from libero.libero import benchmark, get_libero_path
    from libero.libero.envs import OffScreenRenderEnv
    np.random.seed(config['seed'])
    torch.set_num_threads(4)
    suite = benchmark.get_benchmark_dict()[config['suite']]()
    expected = len(config['task_ids'])*config['episodes_per_task']
    summary = {'status': 'loading_model', 'config': config, 'episodes': [], 'expected_episodes': expected,
               'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'successes': 0,
               'package_check': {'python': platform.python_version(), **{
                   name: importlib.metadata.version(name) for name in ['mujoco', 'robosuite', 'torch', 'numpy']}}}
    write_json(output/'results.json', summary)
    policy = None
    try:
        policy = PolicyProcess(config, output/'config.json', output)
        summary['policy'] = policy.metadata
        for task_id in config['task_ids']:
            task = suite.get_task(task_id)
            init_file = Path(get_libero_path('init_states'))/task.problem_folder/task.init_states_file
            # Official LIBERO init-state files contain numpy arrays serialized with torch.
            states = torch.load(init_file, weights_only=False)
            bddl_file = Path(get_libero_path('bddl_files'))/task.problem_folder/task.bddl_file
            env = OffScreenRenderEnv(bddl_file_name=str(bddl_file),
                camera_heights=config['render_resolution'], camera_widths=config['render_resolution'],
                control_freq=config['control_hz'])
            env.seed(config['seed'])
            try:
                for episode_id in range(config['initial_state_start'],
                                        config['initial_state_start']+config['episodes_per_task']):
                    summary.update(status='running', current_task=task.language, current_task_id=task_id)
                    write_json(output/'results.json', summary)
                    result = evaluate_episode(config, policy, env, states[episode_id], task,
                                              task_id, episode_id, output)
                    result['bddl_sha256'] = hashlib.sha256(bddl_file.read_bytes()).hexdigest()
                    result['init_file_sha256'] = hashlib.sha256(init_file.read_bytes()).hexdigest()
                    summary['episodes'].append(result)
                    summary['successes'] += int(result['success'])
                    summary['success_rate'] = summary['successes']/len(summary['episodes'])
                    write_json(output/'results.json', summary)
                    print(json.dumps({k: result[k] for k in ['task_id', 'success', 'steps', 'elapsed_s', 'error']}), flush=True)
                    if result['error']:
                        raise RuntimeError('Episode had an execution error; see its trace')
            finally:
                env.close()
        summary['status'] = 'completed'
    except BaseException:
        summary['status'], summary['error'] = 'error', traceback.format_exc()
        raise
    finally:
        if policy is not None:
            policy.close()
        summary['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        write_json(output/'results.json', summary)
    print(f"Success: {summary['successes']}/{len(summary['episodes'])}. Report: {output/'index.html'}", flush=True)


if __name__ == '__main__':
    main()
