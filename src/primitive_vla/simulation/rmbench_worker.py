"""Persistent RMBench environment and Mem-0 executor, controlled by external Agent."""

import json
import faulthandler
import os
from pathlib import Path
import sys
import signal
import time
import traceback


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
        from envs.cover_blocks import cover_blocks
        args = yaml.safe_load((root / 'task_config/demo_clean.yml').read_text())
        if config.get('head_camera_only', False):
            args['camera']['collect_wrist_camera'] = False
            args['data_type']['third_view'] = False
        robot = root / 'assets/embodiments/aloha-agilex'
        embodiment = yaml.safe_load((robot / 'config.yml').read_text())
        camera = yaml.safe_load((root / 'task_config/_camera_config.yml').read_text())[args['camera']['head_camera_type']]
        args.update(task_name='cover_blocks', task_config='demo_clean', now_ep_num=0, seed=config['seed'],
            is_test=True, eval_mode=True, collect_data=False, save_data=False, render_freq=0,
            eval_video_log=False, left_robot_file=str(robot), right_robot_file=str(robot),
            left_embodiment_config=embodiment, right_embodiment_config=embodiment,
            dual_arm_embodied=True, head_camera_h=camera['h'], head_camera_w=camera['w'])
        class ConfiguredTask(cover_blocks):
            def setup_scene(task, **kwargs):
                super().setup_scene(**kwargs)
                import sapien
                # Configure before cameras are created, not after their first render.
                sapien.render.set_camera_shader_dir(config.get('camera_shader', 'rt'))
                sapien.render.set_ray_tracing_denoiser(config.get('denoiser', 'oidn'))

        self.env = ConfiguredTask()
        self.env.setup_demo(**args)
        self.env.set_instruction(config['instruction'])
        self.policy = None
        self.active = None
        self.generation = 0
        self.chunks = self.ik_moves = 0
        self.video = imageio.get_writer(str(self.media / 'rollout.mp4'), fps=10)
        self.current = self.env.get_obs()
        self.capture(initial=True)
        (self.directory / 'environment.json').write_text(json.dumps({
            'task': 'cover_blocks', 'seed': config['seed'], 'step_limit': self.env.step_lim,
            'target_state_transition': self.env.target_state_transition, 'reset_count': 1,
            'expert_seed_filter': False, 'mode': config['mode']}, indent=2))

    def capture(self, initial=False):
        from PIL import Image
        frame = self.current['observation']['head_camera']['rgb']
        self.frame = f'frame-{self.env.take_action_cnt:05d}-{self.ik_moves}.png'
        Image.fromarray(frame).save(self.media / self.frame)
        if initial:
            Image.fromarray(frame).save(self.media / 'initial.png')
        self.video.append_data(frame)

    def facts(self):
        stage = self.env.current_state_pointer
        values = {'healthy': not self.env.fail_flag and self.env.plan_success,
                  'benchmark:success': self.env.eval_success}
        for index, position in enumerate(self.positions):
            values[f'covered:{position}'] = stage >= index + 1
            values[f'cover_on:{position}'] = bool(self.env.check_if_cover(index))
        for index, color in enumerate(self.colors):
            values[f'uncovered:{color}'] = stage >= index + 4
        return {key: 'true' if value else 'false' for key, value in values.items()}

    def observe(self):
        now = time.time()
        return {'timestamp': now, 'facts': {key: {'value': value, 'source': 'rmbench:physical_state_machine',
            'timestamp': now, 'confidence': 1., 'ttl_s': 600.} for key, value in self.facts().items()},
            'images': {'simulation': self.frame, 'initial_memory': 'initial.png'},
            'proprioception': {'qpos': self.current['joint_action']['vector'].tolist(),
                'control_steps': self.env.take_action_cnt, 'stage': self.env.current_state_pointer,
                'fail_flag': bool(self.env.fail_flag), 'reward': float(self.env.max_reward),
                'chunks': self.chunks, 'ik_moves': self.ik_moves, 'reset_count': 1},
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
        if self.config['mode'] == 'ik_approach':
            self.approach(position)
        self.policy.update(self.current, prompt)
        return {'started': True}

    def approach(self, position):
        from envs.utils import ArmTag
        index = self.positions.index(position)
        cover = self.env.covers[index]
        arm = ArmTag('left' if cover.get_pose().p[0] < 0 else 'right')
        tag, actions = self.env.grasp_actor(cover, arm_tag=arm, pre_grasp_dis=self.config['approach_distance'])
        # Only the first pose move; no close/open, grasp or place commands.
        approach = actions[0]
        if approach.action != 'move':
            raise RuntimeError('Expected a pre-grasp pose motion')
        before = self.current['joint_action']['vector'].copy()
        started = time.monotonic()
        self.env.move((tag, [approach]), save_freq=None)
        if not self.env.plan_success:
            raise RuntimeError('IK approach planning failed')
        self.ik_moves += 1
        self.current = self.env.get_obs()
        self.capture()
        self.trace.write(json.dumps({'event': 'ik_approach', 'position': position, 'arm': str(arm),
            'target_pose': approach.target_pose, 'elapsed_s': time.monotonic()-started,
            'qpos_before': before.tolist(), 'qpos_after': self.current['joint_action']['vector'].tolist(),
            'privileged_pose': True})+'\n')
        self.trace.flush()

    def infer(self):
        started = time.monotonic()
        return {'actions': self.policy.infer().tolist(), 'elapsed_s': time.monotonic()-started}

    def chunk(self, call_id, generation, actions):
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
            if (self.policy.end_count >= self.config['end_threshold'] or self.env.eval_success or
                    self.env.fail_flag or self.env.take_action_cnt >= self.env.step_lim):
                break
        result = {'executed': executed, 'end_signals': self.policy.end_count,
            'subtask_end': self.policy.end_count >= self.config['end_threshold'],
            'skill_steps': self.env.take_action_cnt-self.skill_start,
            'exhausted': self.env.take_action_cnt >= self.env.step_lim or
                         self.env.take_action_cnt-self.skill_start >= self.config['max_skill_steps'],
            'success': bool(self.env.eval_success), 'fail_flag': bool(self.env.fail_flag),
            'stage': self.env.current_state_pointer, 'elapsed_s': time.monotonic()-started}
        self.trace.write(json.dumps({'event': 'vla_chunk', 'call_id': call_id, 'prompt': self.prompt,
                                    'actions': actions[:executed].tolist(), **result})+'\n')
        self.trace.flush()
        return result

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
    world = World(config, sys.argv[2])
    channel.write(json.dumps({'id': 0, 'result': {'ready': True}})+'\n')
    try:
        for line in sys.stdin:
            message = json.loads(line)
            request_id, operation = message.pop('id'), message.pop('op')
            if operation == 'close':
                break
            try:
                result = getattr(world, operation)(**message)
                response = {'id': request_id, 'result': result}
            except Exception:
                response = {'id': request_id, 'error': traceback.format_exc()}
            channel.write(json.dumps(response, allow_nan=False)+'\n')
    finally:
        world.close()


if __name__ == '__main__':
    main()
