"""RGB-D boundary: no task adapter facts, actor poses, contact annotations or stage."""
import json
import time

from .rmbench_worker import World


def advance_sensor_state(task, task_name):
    """Keep physical display dynamics / private sequence grading inside the world."""
    if task_name == 'battery_try':
        task.check_correct_combination()
    elif task_name == 'cover_blocks':
        # Order-sensitive grading needs trajectory history. Never export this state
        # or use it to terminate control; the native verdict is requested only at end.
        if not task.fail_flag:
            task.update_state_transition()
    else:
        raise ValueError('Unsupported sensor-only environment dynamics')


class SensorWorld(World):
    finalized = False

    def facts(self):
        return {}

    def task_terminated(self):
        return False

    def task_feedback(self):
        return {}

    def observe(self):
        return {'timestamp': time.time(), 'facts': {},
                'images': {'simulation': self.frame, 'initial_memory': 'initial.png'},
                'proprioception': {'qpos': self.current['joint_action']['vector'].tolist(),
                    'control_steps': self.env.take_action_cnt, 'remaining_action_steps': max(0, self.env.step_lim-self.env.take_action_cnt),
                    'chunks': self.chunks, 'ik_moves': self.ik_moves},
                'frames': {'simulation': 'head_camera'}, 'sensors': ['rgb', 'depth', 'calibration', 'joint_state']}

    def sense(self):
        if self.active:
            raise RuntimeError('RGB-D inspection requires a stopped controller')
        camera = self.env.cameras.static_camera_list[self.env.cameras.static_camera_name.index('head_camera')]
        # Position texture is optical geometry, not instance segmentation or actor IDs.
        position = camera.get_picture_cuda('Position').torch().cpu().numpy()
        depth = -position[..., 2]
        depth = self.np.where(position[..., 3] < 1, depth, 0).astype('float32')
        calibration = self.current['observation']['head_camera']
        sensor_path = self.media / (self.frame.removesuffix('.png') + '-rgbd.npz')
        extrinsic = self.np.eye(4)
        extrinsic[:3] = calibration['extrinsic_cv'][:3]
        self.np.savez_compressed(sensor_path, depth=depth, intrinsic=calibration['intrinsic_cv'], extrinsic=extrinsic)
        return {'rgb': str(self.media / self.frame), 'sensor': str(sensor_path), 'frame': self.frame}

    def begin(self, call_id, generation, prompt, position, deadline, targets=None):
        self.approach_targets = targets or {}
        return super().begin(call_id, generation, prompt, position, deadline)

    def approach(self, position):
        arms = ('left', 'right') if position == 'pair' else (position,)
        result = self._move_targets([{'arm': arm, 'point': self.approach_targets[arm],
            'clearance': self.config['sensor_approach']['clearance']} for arm in arms], position)
        if not result.get('moved'):
            raise RuntimeError(result.get('error', 'IK controller planning failed'))
        return result

    def move_to(self, targets):
        if self.active:
            raise RuntimeError('Explicit IK requires a stopped VLA controller')
        if self.env.take_action_cnt >= self.env.step_lim:
            raise RuntimeError('Episode action budget exhausted')
        return self._move_targets(targets, 'agent_selected')

    def _move_targets(self, requested, position):
        from envs.utils import ArmTag, Action
        programs, targets = [], []
        config = self.config['sensor_approach']
        for target in requested:
            arm = target['arm']
            point = self.np.asarray(target['point'], dtype=float)
            if point.shape != (3,) or not self.np.isfinite(point).all():
                raise ValueError('A finite RGB-D surface point is required')
            pose = [*point[:2].tolist(), float(point[2] + config['tool_offset'] + target['clearance']),
                    *config['orientations'][arm]]
            result = getattr(self.env.robot, f'{arm}_plan_path')(pose)
            if result['status'] != 'Success':
                return {'error': f'RGB-D approach unreachable for {arm}; no motion dispatched'}
            tag = ArmTag(arm)
            programs.append((tag, [Action(tag, 'move', target_pose=pose)]))
            targets.append({'arm': arm, 'surface_world': point.tolist(), 'target_pose': pose})
        before = self.current['joint_action']['vector'].copy()
        self.env.move(*programs, save_freq=None)
        self.ik_moves += 1
        self.current = self.env.get_obs()
        self.capture()
        self.trace.write(json.dumps({'event': 'ik_approach', 'position': position, 'targets': targets,
            'qpos_before': before.tolist(), 'qpos_after': self.current['joint_action']['vector'].tolist(),
            'privileged_pose': False, 'target_source': 'grounding_dino+sam+rgbd'})+'\n')
        self.trace.flush()
        return {'moved': bool(self.env.plan_success), 'targets': targets, 'frame': self.frame}

    def evaluate(self):
        if self.active:
            raise RuntimeError('Stop control and commit the Agent report before evaluation')
        self.finalized = True
        # Only the runner receives this terminal verdict. No subsequent control is allowed.
        return {'success': bool(self.native_evaluator(self.env)), 'source': 'native_terminal_evaluator',
                'final_state_only': True}
