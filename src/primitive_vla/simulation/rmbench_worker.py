"""Persistent RMBench environment and Mem-0 executor, controlled by external Agent."""

import json
import importlib
import faulthandler
import os
from pathlib import Path
import sys
import signal
import time
import traceback


def cuda_camera_rgba(cameras):
    """Read the same rendered Color buffer through SAPIEN's CUDA interop."""
    selected = []
    if cameras.collect_wrist_camera:
        selected.extend([(cameras.left_camera, 'left_camera'), (cameras.right_camera, 'right_camera')])
    selected.extend((camera, name) for camera, name in
                    zip(cameras.static_camera_list, cameras.static_camera_name)
                    if name != 'head_camera' or cameras.collect_head_camera)
    result = {}
    for camera, name in selected:
        rgba = camera.get_picture_cuda('Color').torch().cpu().numpy()
        result[name] = {'rgba': (rgba * 255).clip(0, 255).astype('uint8')}
    return result


class World:
    positions = ('left', 'middle', 'right')
    colors = ('red', 'green', 'blue')

    def __init__(self, config, directory):
        import numpy as np
        import yaml
        import imageio.v2 as imageio
        self.np, self.config, self.directory = np, config, Path(directory)
        self.media = self.directory / 'media'
        self.media.mkdir(exist_ok=True, parents=True)
        self.trace = (self.directory / 'physics.jsonl').open('w')
        root = Path(config['repo']).resolve()
        os.chdir(root)
        sys.path.insert(0, str(root))
        from primitive_vla.simulation.rmbench_tasks import task_adapter
        self.task = task_adapter(config.get('task_name', 'cover_blocks'))
        task_class = getattr(importlib.import_module('envs.' + self.task.name), self.task.name)
        args = yaml.safe_load((root / 'task_config/demo_clean.yml').read_text())
        if config.get('head_camera_only', False):
            args['camera']['collect_wrist_camera'] = False
            args['data_type']['third_view'] = False
        sensors_only = config.get('observation_mode') == 'sensors'
        if sensors_only:
            if self.task.name not in ('battery_try', 'cover_blocks'):
                raise ValueError('Unsupported sensor-only environment dynamics')
            args['data_type'].update(mesh_segmentation=False, actor_segmentation=False,
                                     depth=False, endpose=False, pointcloud=False)
        robot = root / 'assets/embodiments/aloha-agilex'
        embodiment = yaml.safe_load((robot / 'config.yml').read_text())
        camera = yaml.safe_load((root / 'task_config/_camera_config.yml').read_text())[args['camera']['head_camera_type']]
        args.update(task_name=self.task.name, task_config='demo_clean', now_ep_num=0, seed=config['seed'],
            is_test=True, eval_mode=True, collect_data=False, save_data=False, render_freq=0,
            eval_video_log=False, left_robot_file=str(robot), right_robot_file=str(robot),
            left_embodiment_config=embodiment, right_embodiment_config=embodiment,
            dual_arm_embodied=True, head_camera_h=camera['h'], head_camera_w=camera['w'])
        self.native_evaluator = task_class.check_success
        class ConfiguredTask(task_class):
            def check_success(task):
                if sensors_only:
                    # Preserve the visible gauge dynamics, but never latch a verdict
                    # or stop the robot from an evaluator answer during rollout.
                    from primitive_vla.simulation.rmbench_sensors import advance_sensor_state
                    advance_sensor_state(task, config.get("task_name", "cover_blocks"))
                    return False
                return super().check_success()

            def setup_scene(task, **kwargs):
                super().setup_scene(**kwargs)
                import sapien
                # Configure before cameras are created, not after their first render.
                sapien.render.set_camera_shader_dir(config.get('camera_shader', 'rt'))
                sapien.render.set_ray_tracing_denoiser(config.get('denoiser', 'oidn'))

        self.env = ConfiguredTask()
        self.env.setup_demo(**args)
        self.env.set_instruction(config['instruction'])
        if config.get('cuda_camera_readback', False):
            from types import MethodType
            self.env.cameras.get_rgba = MethodType(cuda_camera_rgba, self.env.cameras)
        self.policy = None
        self.active = None
        self.generation = 0
        self.chunks = self.ik_moves = 0
        self.video = imageio.get_writer(str(self.media / 'rollout.mp4'), fps=10)
        self.current = self.env.get_obs()
        self.capture(initial=True)
        (self.directory / 'environment.json').write_text(json.dumps({
            'task': self.task.name, 'seed': config['seed'], 'step_limit': self.env.step_lim,
            'stage_max': self.task.stage_max, 'reset_count': 1,
            'expert_seed_filter': False, 'mode': config['mode']}, indent=2))

    def capture(self, initial=False):
        from PIL import Image
        frame = self.current['observation']['head_camera']['rgb']
        self.frame = f'frame-{self.env.take_action_cnt:05d}-{self.ik_moves}.png'
        Image.fromarray(frame).save(self.media / self.frame)
        if self.config.get('live_frame'):
            temporary = self.media / 'latest.tmp.png'
            Image.fromarray(frame).save(temporary)
            temporary.replace(self.media / 'latest.png')
        if initial:
            Image.fromarray(frame).save(self.media / 'initial.png')
        self.video.append_data(frame)

    def facts(self):
        values = {'healthy': not self.task.failed(self.env) and self.env.plan_success,
                  'benchmark:success': self.env.eval_success, **self.task.facts(self.env)}
        return {key: 'true' if value else 'false' for key, value in values.items()}

    def observe(self):
        now = time.time()
        return {'timestamp': now, 'facts': {key: {'value': value, 'source': 'rmbench:physical_state_machine',
            'timestamp': now, 'confidence': 1., 'ttl_s': 600.} for key, value in self.facts().items()},
            'images': {'simulation': self.frame, 'initial_memory': 'initial.png'},
            'proprioception': {'qpos': self.current['joint_action']['vector'].tolist(),
                'control_steps': self.env.take_action_cnt, 'stage': self.task.stage(self.env),
                'fail_flag': self.task.failed(self.env), 'reward': float(self.env.max_reward),
                'chunks': self.chunks, 'ik_moves': self.ik_moves, 'reset_count': 1,
                'task': self.task.name, 'stage_max': self.task.stage_max},
            'frames': {'simulation': 'head_camera'}, 'sensors': ['rgb','joint_state','simulator_evaluator']}

    def warmup(self):
        if self.policy is None:
            from primitive_vla.vla.mem0 import Mem0Policy
            self.policy = Mem0Policy(self.config)
        return self.policy.metadata

    def begin(self, call_id, generation, prompt, position, deadline):
        if self.active or generation <= self.generation:
            raise RuntimeError('Invalid or occupied skill lease')
        self.active, self.generation, self.prompt, self.deadline = call_id, generation, prompt, deadline
        self.skill_start = self.env.take_action_cnt
        self.policy.reset()
        # Keep the old post-approach anchor available for controlled ablation.
        before_ik = self.config.get('handoff_memory', 'pre_approach') == 'pre_approach'
        if self.config['mode'] == 'ik_approach':
            if before_ik:
                self.policy.update(self.current, prompt)
            self.approach(position)
        self.policy.update(self.current, prompt)
        self.policy.end_count = 0
        return {'started': True}

    def approach(self, position):
        from envs.utils import ArmTag, Action
        from primitive_vla.simulation.approach import select_approach
        programs, targets = [], []
        for actor, arm, options in self.task.targets(self.env, position):
            tag = ArmTag(arm)
            pose, attempts = select_approach(self.env, actor, tag,
                self.config['approach_distance'], options.get('grasp_dis', 0.),
                self.config.get('approach_fallback'))
            self.trace.write(json.dumps({'event': 'approach_selection', 'arm': arm,
                'position': position, 'attempts': attempts})+'\n')
            self.trace.flush()
            if pose is None:
                raise RuntimeError(f'No reachable approach for {arm}: {len(attempts)} candidates')
            approach = Action(tag, 'move', target_pose=pose)
            programs.append((tag, [approach]))
            targets.append({'arm': arm, 'target_pose': approach.target_pose})
        before = self.current['joint_action']['vector'].copy()
        started = time.monotonic()
        self.env.move(*programs, save_freq=None)
        if not self.env.plan_success:
            raise RuntimeError('IK approach planning failed')
        self.ik_moves += 1
        self.current = self.env.get_obs()
        self.capture()
        self.trace.write(json.dumps({'event': 'ik_approach', 'position': position, 'targets': targets, 'elapsed_s': time.monotonic()-started,
            'qpos_before': before.tolist(), 'qpos_after': self.current['joint_action']['vector'].tolist(),
            'privileged_pose': True})+'\n')
        self.trace.flush()

    def resume(self, call_id, generation, action, position):
        if self.active != call_id or generation != self.generation or time.time() >= self.deadline:
            raise RuntimeError('No matching live skill lease')
        if action == 'reapproach':
            self.approach(position)
            self.policy.update(self.current, self.prompt)
        elif action != 'continue':
            raise ValueError('Unsupported recovery action')
        # Keep the scene anchor and temporal memory; reject only the stale end vote.
        self.policy.end_count = 0
        return {'resumed': action}

    def infer(self):
        started = time.monotonic()
        return {'actions': self.policy.infer().tolist(), 'elapsed_s': time.monotonic()-started}

    def chunk(self, call_id, generation, actions, stop_on_end=True):
        if self.active != call_id or self.generation != generation or time.time() >= self.deadline:
            raise RuntimeError('No matching live skill lease')
        started = time.monotonic()
        actions = self.np.asarray(actions)
        self.chunks += 1
        executed = 0
        for action in actions:
            if time.time() >= self.deadline:
                break
            self.env.take_action(action, action_type='qpos')
            self.current = self.env.get_obs()
            self.capture()
            self.policy.update(self.current, self.prompt)
            executed += 1
            if ((stop_on_end and self.policy.end_count >= self.config['end_threshold']) or self.task_terminated() or
                    self.env.take_action_cnt >= self.env.step_lim):
                break
        result = {'executed': executed, 'end_signals': self.policy.end_count,
            'subtask_end': self.policy.end_count >= self.config['end_threshold'],
            'skill_steps': self.env.take_action_cnt-self.skill_start,
            'exhausted': self.env.take_action_cnt >= self.env.step_lim or
                         self.env.take_action_cnt-self.skill_start >= self.config['max_skill_steps'],
            **self.task_feedback(), 'frame': self.frame,
            'ik_moves': self.ik_moves, 'elapsed_s': time.monotonic()-started}
        self.trace.write(json.dumps({'event': 'vla_chunk', 'call_id': call_id, 'prompt': self.prompt,
                                    'actions': actions[:executed].tolist(), **result})+'\n')
        self.trace.flush()
        return result

    def task_terminated(self):
        return self.env.eval_success or self.task.failed(self.env)

    def task_feedback(self):
        return {'success': bool(self.env.eval_success), 'fail_flag': self.task.failed(self.env),
                'stage': self.task.stage(self.env), 'stage_max': self.task.stage_max}

    def stop(self, call_id):
        if self.active == call_id:
            self.active = None
        return {'call_id': call_id, 'stopped': self.active is None}

    def close(self):
        self.video.close()
        self.trace.close()
        self.env.close_env()


def main():
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    config = json.loads(Path(sys.argv[1]).read_text())
    world_type = World
    if config.get('observation_mode') == 'sensors':
        from primitive_vla.simulation.rmbench_sensors import SensorWorld
        world_type = SensorWorld
    if config.get('persistent_vla_lab'):
        from primitive_vla.simulation.vla_lab_world import VLALabWorld
        world_type = VLALabWorld
    world = world_type(config, sys.argv[2])
    channel.write(json.dumps({'id': 0, 'result': {'ready': True}})+'\n')
    try:
        for line in sys.stdin:
            message = json.loads(line)
            request_id, operation = message.pop('id'), message.pop('op')
            if operation == 'close':
                break
            try:
                if getattr(world, 'finalized', False):
                    raise RuntimeError('Episode is finalized; no further sensing or control')
                result = getattr(world, operation)(**message)
                response = {'id': request_id, 'result': result}
            except Exception:
                response = {'id': request_id, 'error': traceback.format_exc()}
            channel.write(json.dumps(response, allow_nan=False)+'\n')
    finally:
        world.close()


if __name__ == '__main__':
    main()
