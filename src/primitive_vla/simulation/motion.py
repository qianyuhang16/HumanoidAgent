"""Generic motion operators executing programs loaded from SKILL.md."""

import math

import numpy as np
import sapien


class MotionProgram:
    def __init__(self, world):
        self.world = world
        self.phase = 'idle'

    def wait(self, seconds):
        for _ in range(max(1, round(seconds * self.world.config['control_hz']))):
            yield

    def move(self, tcp, seconds=None):
        world = self.world
        start = world.tcp().copy()
        end = np.asarray(tcp, dtype=float)
        seconds = seconds or max(.4, np.linalg.norm(end - start) / world.config['speed_m_s'])
        count = max(1, round(seconds * world.config['control_hz']))
        for i in range(1, count + 1):
            t = i / count
            p = start + (end - start) * (t * t * (3 - 2 * t))
            pose = sapien.Pose(p + [0, 0, world.config['tcp_offset']], [0, 1, 0, 0])
            q, ok, error = world.model.compute_inverse_kinematics(world.hand_index, pose,
                initial_qpos=world.target, active_qmask=np.array([1] * 7 + [0] * 2), max_iterations=100, eps=1e-4)
            if not ok:
                raise RuntimeError(f'IK failed at {p.tolist()}: {np.linalg.norm(error):.4f}')
            world.target[:7] = q[:7]
            yield
        yield from self.wait(.12)
        if np.linalg.norm(world.tcp() - end) > .025:
            raise RuntimeError(f'End effector failed to reach waypoint: {world.tcp().tolist()} vs {end.tolist()}')

    def grip(self, opened):
        self.world.target[7:] = .04 if opened else 0
        yield from self.wait(.6)

    def point(self, instruction, bindings, variables):
        if 'ref' in instruction:
            point = variables[instruction['ref']].copy()
        elif instruction.get('current_tcp'):
            point = self.world.tcp().copy()
        else:
            point = np.asarray(instruction['xyz'], dtype=float)
        if 'height' in instruction:
            point[2] = self.value(instruction['height'])
        return point

    def value(self, value):
        return self.world.config[value['config']] if isinstance(value, dict) and 'config' in value else value

    def geometry(self, step, variables):
        world = self.world
        kind = step['kind']
        if kind == 'approach':
            return world.approach_point(step['target'])
        if kind == 'object_grasp':
            actor = world.objects[step['object']]
            point = actor.pose.p.copy()
            if actor is world.tool:
                point[2] += .045
            return point
        if kind == 'placement_tcp':
            name, target = step['object'], step['target']
            offset = world.objects[name].pose.p - world.tcp()
            if target == world.bowl.name:
                index = [o['id'] for o in world.config['objects']].index(name)
                angle = 2 * math.pi * index / len(world.config['objects'])
                point = world.bowl_center + [world.radius * .45 * math.cos(angle), world.radius * .45 * math.sin(angle), world.bowl_height + .035]
            elif target == 'tool_home':
                point = world.tool_home.copy() + [0, 0, .006]
            else:
                point = world.initial[name].copy() + [0, 0, .008]
            return point - offset
        if kind == 'tool_offset':
            return self.tool_offset(step['tool'])
        if kind == 'circle_start':
            offset = variables[step['offset_ref']]
            return world.bowl_center + [world.config['stir_radius'], 0, world.config['stir_depth']] - offset
        raise ValueError(f'Unknown geometric point: {kind}')

    def tool_offset(self, tool):
        return (self.world.objects[tool].pose * sapien.Pose([0, 0, -.06])).p - self.world.tcp()

    def circle(self, step, variables):
        world = self.world
        radius, depth, turns, period, minimum = [self.value(step[k]) for k in ('radius', 'depth', 'turns', 'period_s', 'min_contact_steps')]
        segments = step.get('segments', 48)
        extra = step.get('extra_turns', 1)
        if radius <= 0 or turns <= 0 or period <= 0 or segments <= 0 or extra < 0:
            raise ValueError('Circle trajectory parameters must be positive')
        offset = variables[step['offset_ref']]
        center = world.bowl_center + [0, 0, depth]
        world.stir_active = True
        try:
            for angle in np.linspace(0, 2 * math.pi * (turns + extra), int((turns + extra) * segments) + 1)[1:]:
                yield from self.move(center + [radius * math.cos(angle), radius * math.sin(angle), 0] - offset, seconds=period / segments)
                if world.stir_angle >= 2 * math.pi * turns and world.stir_contact_steps >= minimum:
                    break
            else:
                raise RuntimeError('Observed circular motion or ingredient contacts did not meet the criteria')
        finally:
            world.stir_active = False

    def run(self, program, bindings):
        # This dispatch vocabulary is independent of skill names. No eval or
        # generated Python: the selected contract supplies a snapshotted program.
        supported = {'phase', 'point', 'move', 'gripper', 'wait', 'require', 'circle'}
        if not program or any(step.get('op') not in supported for step in program):
            raise ValueError('Skill needs a nonempty program using supported motion operators')
        variables = {}
        for original in program:
            step = {k: v.format(**bindings) if isinstance(v, str) else v for k, v in original.items()}
            op = step['op']
            if op == 'phase':
                self.phase = step['value']
            elif op == 'point':
                variables[step['into']] = self.geometry(step, variables)
            elif op == 'move':
                yield from self.move(self.point(step['point'], bindings, variables), self.value(step.get('seconds')))
            elif op == 'gripper':
                yield from self.grip(step['opened'])
            elif op == 'wait':
                yield from self.wait(self.value(step['seconds']))
            elif op == 'require':
                if step['condition'] == 'holding':
                    confirmed = self.world.holding(step['object'])
                elif step['condition'] == 'ingredients_in_target':
                    confirmed = step['target'] == self.world.bowl.name and all(self.world.in_bowl(o['id']) for o in self.world.config['objects'])
                else:
                    raise ValueError(f"Unknown observation condition: {step['condition']}")
                if not confirmed:
                    raise RuntimeError(step['reason'])
            elif op == 'circle':
                yield from self.circle(step, variables)
        self.phase = 'completed'

    def stop(self):
        self.world.target[:7] = self.world.robot.get_qpos()[:7]
        self.world.stir_active = False
        self.phase = 'stopped'
