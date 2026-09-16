"""SAPIEN tabletop, RoboTwin Panda asset and observation-derived task evidence."""

import math
from pathlib import Path
from time import time

import numpy as np
import sapien
from PIL import Image
from scipy.spatial.transform import Rotation


class KitchenScene:
    def __init__(self, config: dict, media: Path, session: str):
        self.config, self.media, self.session = config, media, session
        self.scene = sapien.Scene()
        self.scene.set_timestep(1 / config['physics_hz'])
        self.scene.set_ambient_light([.45, .45, .45])
        self.scene.add_directional_light([1, -.5, -1], [1.4, 1.3, 1.2], shadow=True)
        self.scene.add_point_light([.4, -.5, 1.5], [3, 3, 3])
        self.contact_material = self.scene.create_physical_material(config['static_friction'], config['dynamic_friction'], 0)
        self._box('table', [.36, 0, -.035], [.65, .5, .035], [.25, .32, .34, 1], static=True)
        self.bowl_center = np.array(config['bowl_position'], dtype=float)
        self.radius, self.bowl_height = config['bowl_radius'], config['bowl_height']
        self._bowl()
        self.objects, self.initial = {}, {}
        for item in config['objects']:
            pos = [*item['position'], config['object_size'] / 2 + .002]
            actor = self._box(item['id'], pos, [config['object_size'] / 2] * 3, item['color'])
            self.objects[item['id']] = actor
            self.initial[item['id']] = np.array(pos)
        self.tool_home = np.array(config['tool_position'], dtype=float)
        self._box('tool_tray', [*self.tool_home[:2], -.0005],
                  [config['tool_home_radius'], config['tool_home_radius'], .001], [.42, .49, .47, 1], static=True)
        self.tool = self._tool()
        self.objects['stirrer'] = self.tool
        self.initial['stirrer'] = self.tool_home.copy()
        urdf = Path(config['robotwin_root']).expanduser() / 'assets/embodiments/franka-panda/panda.urdf'
        loader = self.scene.create_urdf_loader()
        loader.fix_root_link = True
        self.robot = loader.load(str(urdf))
        self.links = {link.name: link for link in self.robot.get_links()}
        self.hand = self.links['panda_hand']
        self.joints = self.robot.get_active_joints()
        self.robot.set_qpos([0, -.4, 0, -2, 0, 1.6, math.pi / 4, .04, .04])
        self.target = self.robot.get_qpos().copy()
        for i, joint in enumerate(self.joints):
            prefix = 'arm' if i < 7 else 'gripper'
            joint.set_drive_properties(config[prefix + '_stiffness'], config[prefix + '_damping'],
                                       force_limit=config[prefix + '_force_limit'])
        for name in ('panda_leftfinger', 'panda_rightfinger'):
            for shape in self.links[name].get_collision_shapes():
                shape.set_physical_material(self.contact_material)
        self.model = self.robot.create_pinocchio_model()
        self.hand_index = list(self.robot.get_links()).index(self.hand)
        self.camera = self.scene.add_camera('overview', config['width'], config['height'], math.radians(55), .01, 10)
        eye, look = np.array(config['camera_position']), np.array(config['camera_target'])
        forward = (look - eye) / np.linalg.norm(look - eye)
        left = np.cross([0, 0, 1], forward); left /= np.linalg.norm(left)
        up = np.cross(forward, left)
        q = Rotation.from_matrix(np.column_stack([forward, left, up])).as_quat()
        self.camera.entity.set_pose(sapien.Pose(eye, q[[3, 0, 1, 2]]))
        self.ticks = 0
        self.stir_angle = 0.0
        self.stir_contact_steps = 0
        self.stir_last_angle = None
        self.stir_active = False
        self.held_contacts = {}
        self.last_frame = None
        self.video = f'{session}.mp4'
        self.writer = None
        self.frame_id = 0
        for _ in range(config['physics_hz'] // 2):
            self.step()

    def _material(self, color):
        material = sapien.render.RenderMaterial()
        material.base_color = color
        material.roughness = .65
        return material

    def _box(self, name, position, half, color, static=False):
        builder = self.scene.create_actor_builder()
        builder.add_box_collision(half_size=half, material=self.contact_material, density=300)
        builder.add_box_visual(half_size=half, material=self._material(color))
        builder.initial_pose = sapien.Pose(position)
        return builder.build_static(name=name) if static else builder.build(name=name)

    def _bowl(self):
        builder = self.scene.create_actor_builder()
        material = self._material([.78, .88, .85, 1])
        upright = sapien.Pose([0, 0, .005], [math.sqrt(.5), 0, math.sqrt(.5), 0])
        builder.add_cylinder_collision(pose=upright, radius=self.radius, half_length=.005, material=self.contact_material)
        builder.add_cylinder_visual(pose=upright, radius=self.radius, half_length=.005, material=material)
        for i in range(32):
            a = 2 * math.pi * i / 32
            pose = sapien.Pose([self.radius * math.cos(a), self.radius * math.sin(a), self.bowl_height / 2],
                               [math.cos(a / 2), 0, 0, math.sin(a / 2)])
            half = [.004, self.radius * math.tan(math.pi / 32), self.bowl_height / 2]
            builder.add_box_collision(pose=pose, half_size=half, material=self.contact_material)
            builder.add_box_visual(pose=pose, half_size=half, material=material)
        builder.initial_pose = sapien.Pose(self.bowl_center)
        self.bowl = builder.build_static(name='bowl')

    def _tool(self):
        builder = self.scene.create_actor_builder()
        material = self._material([.64, .38, .16, 1])
        # Upright paddle with a broad foot, allowing a vertical grasp and return.
        for center, half in (([0, 0, 0], [.009, .009, .07]), ([0, 0, -.06], [.022, .012, .012])):
            pose = sapien.Pose(center)
            builder.add_box_collision(pose=pose, half_size=half, material=self.contact_material, density=350)
            builder.add_box_visual(pose=pose, half_size=half, material=material)
        builder.initial_pose = sapien.Pose(self.tool_home)
        return builder.build(name='stirrer')

    def tcp(self):
        return (self.hand.pose * sapien.Pose([0, 0, self.config['tcp_offset']])).p

    def step(self):
        self.robot.set_qf(self.robot.compute_passive_force(gravity=True, coriolis_and_centrifugal=True))
        for joint, target in zip(self.joints, self.target):
            joint.set_drive_target(float(target))
        self.scene.step()
        self.ticks += 1
        contacts = {name: set() for name in self.objects}
        tool_hits = False
        for contact in self.scene.get_contacts():
            names = {body.entity.name for body in contact.bodies}
            if not any(np.linalg.norm(point.impulse) > 1e-7 for point in contact.points):
                continue
            for name in self.objects:
                if name in names:
                    contacts[name].update(names & {'panda_leftfinger', 'panda_rightfinger'})
            if 'stirrer' in names and names & (self.objects.keys() - {'stirrer'}):
                tool_hits = True
        self.held_contacts = contacts
        tip = (self.tool.pose * sapien.Pose([0, 0, -.06])).p
        if self.stir_active and self.holding('stirrer') and np.linalg.norm(tip[:2] - self.bowl_center[:2]) < self.radius - .02:
            angle = math.atan2(*(tip[:2] - self.bowl_center[:2])[::-1])
            if self.stir_last_angle is not None:
                delta = math.atan2(math.sin(angle - self.stir_last_angle), math.cos(angle - self.stir_last_angle))
                if abs(delta) < .2 and self.bowl_center[2] + .01 < tip[2] < self.bowl_center[2] + self.bowl_height:
                    self.stir_angle += abs(delta)
            self.stir_last_angle = angle
            self.stir_contact_steps += int(tool_hits)
        else:
            self.stir_last_angle = None

    def holding(self, name):
        # Opposing finger contacts are measured after each physics step.
        return len(self.held_contacts.get(name, ())) == 2

    def in_bowl(self, name):
        actor = self.objects[name]
        p = actor.pose.p - self.bowl_center
        return (np.linalg.norm(p[:2]) < self.radius - self.config['object_size'] / 2
                and .005 < p[2] < self.bowl_height + self.config['object_size'] / 2
                and not self.holding(name))

    def render(self, record=True):
        self.scene.update_render(); self.camera.take_picture()
        rgb = (np.clip(self.camera.get_picture('Color')[..., :3], 0, 1) * 255).astype(np.uint8)
        self.frame_id += 1
        self.last_frame = f'{self.session}-{self.frame_id:06d}.jpg'
        Image.fromarray(rgb).save(self.media / self.last_frame, quality=85)
        if record and self.config['record_video']:
            if self.writer is None:
                import imageio.v2 as imageio
                self.writer = imageio.get_writer(str(self.media / self.video), fps=self.config['render_fps'], codec='libx264', quality=7)
            self.writer.append_data(rgb)
        return rgb

    def observe(self):
        now = time()
        held = [name for name in self.objects if self.holding(name)]
        healthy = bool(np.isfinite(self.robot.get_qpos()).all())
        values = {'healthy': healthy, 'free:right': not held,
                  'reachable:bowl': True, 'reachable:table': True, 'reachable:tool_home': True,
                  'accepts:bowl': True, 'accepts:table': True, 'accepts:tool_home': True}
        for name, actor in self.objects.items():
            holding = name in held
            values.update({f'holding:right:{name}': holding, f'ready:{name}': holding,
                           f'reachable:{name}': float(np.linalg.norm(actor.pose.p[:2])) < .8,
                           f'at:{name}:table': not holding and actor.pose.p[2] < self.config['object_size']
                           and not self.in_bowl(name),
                           f'at:{name}:bowl': name != 'stirrer' and self.in_bowl(name),
                           f'at:{name}:tool_home': name == 'stirrer' and not holding
                           and np.linalg.norm(actor.pose.p[:2] - self.tool_home[:2]) < self.config['tool_home_radius']
                           and actor.pose.p[2] < self.tool_home[2] + .02
                           and np.linalg.norm(actor.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent).linear_velocity) < .05})
        names = [o['id'] for o in self.config['objects']]
        values['ingredients:bowl'] = all(self.in_bowl(name) for name in names)
        values['event:stirred:bowl'] = self.stir_angle >= 2 * math.pi * self.config['stir_turns'] and self.stir_contact_steps >= self.config['min_stir_contact_steps']
        for target in [*self.objects, 'bowl', 'tool_home']:
            values[f'near:{target}'] = np.linalg.norm(self.tcp() - self.approach_point(target)) < self.config.get('near_tolerance', .04)
        return {'timestamp': now, 'facts': {key: {'value': 'true' if value else 'false', 'source': 'sapien:contacts_and_poses',
                  'timestamp': now, 'confidence': 1, 'ttl_s': None if key.startswith('event:') else 5} for key, value in values.items()},
                'images': {'simulation': self.last_frame} if self.last_frame else {},
                'proprioception': {'qpos': self.robot.get_qpos().tolist(), 'tcp': self.tcp().tolist(),
                   'objects': {name: actor.pose.p.tolist() for name, actor in self.objects.items()},
                   'contacts': {name: sorted(contacts) for name, contacts in self.held_contacts.items()},
                   'sim_time_s': self.ticks / self.config['physics_hz'], 'stir_angle_rad': self.stir_angle,
                   'stir_contact_steps': self.stir_contact_steps, 'video': self.video if self.config['record_video'] else None},
                'frames': {'simulation': 'world'}, 'sensors': ['sapien:joint_state', 'sapien:contacts', 'sapien:actor_pose', 'sapien:rgb']}

    def approach_point(self, target):
        if target in self.objects:
            point = self.objects[target].pose.p.copy()
        elif target == 'bowl':
            point = self.bowl_center.copy() + [0, 0, self.bowl_height]
        elif target == 'tool_home':
            point = self.tool_home.copy()
        else:
            raise ValueError(f'Unknown approach target: {target}')
        point[2] += self.config.get('approach_clearance', .10)
        return point

    def close(self):
        if self.writer:
            self.writer.close()
