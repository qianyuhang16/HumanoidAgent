"""A SAPIEN Panda, table and configurable target, driven only by perceived grasps."""

import math
import shutil
from pathlib import Path
from uuid import uuid4

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw
import sapien
from scipy.spatial.transform import Rotation, Slerp
from .evaluation import task_goal, evaluate_samples


class TabletopScene:
    def __init__(self, options, media):
        self.options, self.media = options, Path(media)
        self.media.mkdir(parents=True, exist_ok=True)
        self.session = uuid4().hex[:12]
        self.scene = sapien.Scene()
        self.scene.set_timestep(1 / 240)
        self.scene.set_ambient_light([.5, .5, .5])
        self.scene.add_directional_light([.5, -.3, -1], [2.2, 2.1, 2.0], shadow=True)
        self.scene.add_point_light([.5, .4, 1.2], [2., 2., 2.])
        self.material = self.scene.create_physical_material(1.5, 1.2, 0.)
        self._box("table", [.4, 0, -.035], [.65, .5, .035], [.58, .43, .28, 1], static=True)
        self.objects = {}
        self.goal = task_goal(options)
        self.zones = {"left": {"center": [.38, -.23], "half_size": [.075, .075]},
                      "right": {"center": [.62, -.23], "half_size": [.075, .075]}}
        scenario = options.get("scenario", "clear")
        if scenario != "clear":
            for name, zone in self.zones.items():
                # Visual markings, not dynamic objects or raised collision surfaces.
                builder = self.scene.create_actor_builder()
                material = sapien.render.RenderMaterial()
                material.base_color = [.22, .65, .3, 1] if name == "left" else [.85, .72, .18, 1]
                builder.add_box_visual(half_size=[*zone["half_size"], .0005], material=material)
                builder.initial_pose = sapien.Pose([*zone["center"], .0005])
                builder.build_static(name=f"{name}_pad")
        rng = np.random.default_rng(options.get("seed", 7))
        if options.get("object_present", True) and scenario != "empty":
            pos = [.46 + float(rng.uniform(-.035, .035)), .06 + float(rng.uniform(-.04, .04)), .027]
            yaw = float(rng.uniform(-.55, .55))
            self.objects["target"] = self._box("target", pos, [.026, .018, .025], [.85, .08, .045, 1], yaw=yaw,
                                                static=scenario == "anchored")
            if scenario == "sorting":
                self.objects["blue"] = self._box("blue", [.60, .16, .027], [.026, .018, .025], [.05, .20, .85, 1], yaw=-.25)
            if scenario == "obstructed":
                self._box("barrier", [pos[0] + .062, pos[1], .095], [.012, .065, .095], [.38, .40, .44, 1], static=True)
        urdf = Path(options["robot_urdf"]).expanduser()
        if not urdf.is_file():
            raise FileNotFoundError(f"Panda URDF not found: {urdf}")
        loader = self.scene.create_urdf_loader()
        loader.fix_root_link = True
        self.robot = loader.load(str(urdf))
        self.links = {link.name: link for link in self.robot.get_links()}
        self.hand = self.links["panda_hand"]
        self.joints = self.robot.get_active_joints()
        self.robot.set_qpos([0, -.4, 0, -2, 0, 1.6, math.pi / 4, .04, .04])
        self.target = self.robot.get_qpos().copy()
        for i, joint in enumerate(self.joints):
            joint.set_drive_properties(1200 if i < 7 else 400, 120 if i < 7 else 30,
                                       force_limit=100 if i < 7 else 35)
        for name in ("panda_leftfinger", "panda_rightfinger"):
            for shape in self.links[name].get_collision_shapes():
                shape.set_physical_material(self.material)
        self.model = self.robot.create_pinocchio_model()
        self.hand_index = list(self.robot.get_links()).index(self.hand)
        self.tcp_offset = .1034
        self.top = self._camera("top", 640, 640, [.45, .02, .78], [.45, .02, 0], [0, 1, 0])
        self.overview = self._camera("overview", 800, 600, [1.05, -.95, .85], [.4, 0, .12], [0, 0, 1])
        self.ticks, self.revision, self.frame = 0, 0, 0
        self.contacts = {}
        self.support_contacts = {}
        self.phase = "ready"
        self.writer = None
        self.video = f"{self.session}-grasp.mp4"
        self.plans = {}
        self.finger_width = .08
        home = self._ik(np.array([.25, -.28, .32]), np.diag([1., -1., -1.]), self.target)
        self.target[:7] = home[:7]
        for _ in range(720):
            self.step(record=False)
        self.initial_heights = {name: float(obj.pose.p[2]) for name, obj in self.objects.items()}
        self.record()

    def _box(self, name, xyz, half, color, static=False, yaw=0):
        mat = sapien.render.RenderMaterial()
        mat.base_color, mat.roughness = color, .6
        builder = self.scene.create_actor_builder()
        builder.add_box_collision(half_size=half, material=self.material, density=400)
        builder.add_box_visual(half_size=half, material=mat)
        builder.initial_pose = sapien.Pose(xyz, [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)])
        return builder.build_static(name=name) if static else builder.build(name=name)

    def _camera(self, name, width, height, eye, target, up):
        camera = self.scene.add_camera(name, width, height, math.radians(55), .01, 5)
        forward = np.array(target) - eye
        forward = forward / np.linalg.norm(forward)
        left = np.cross(up, forward)
        left /= np.linalg.norm(left)
        real_up = np.cross(forward, left)
        q = Rotation.from_matrix(np.column_stack([forward, left, real_up])).as_quat()
        camera.entity.set_pose(sapien.Pose(eye, q[[3, 0, 1, 2]]))
        return camera

    def tcp(self):
        return (self.hand.pose * sapien.Pose([0, 0, self.tcp_offset])).p

    def step(self, record=True):
        self.robot.set_qf(self.robot.compute_passive_force(gravity=True, coriolis_and_centrifugal=True))
        for joint, target in zip(self.joints, self.target):
            joint.set_drive_target(float(target))
        self.scene.step()
        self.ticks += 1
        self.contacts = {name: set() for name in self.objects}
        self.support_contacts = {name: False for name in self.objects}
        for contact in self.scene.get_contacts():
            if not any(np.linalg.norm(point.impulse) > 1e-7 for point in contact.points):
                continue
            names = {body.entity.name for body in contact.bodies}
            for name in self.objects:
                if name in names:
                    self.contacts[name].update(names & {"panda_leftfinger", "panda_rightfinger"})
                    self.support_contacts[name] |= "table" in names
        if record and self.ticks % 12 == 0:
            self.record()

    def _rgb(self, camera):
        camera.take_picture()
        return (np.clip(camera.get_picture("Color")[..., :3], 0, 1) * 255).astype(np.uint8)

    def record(self):
        self.scene.update_render()
        rgb = self._rgb(self.overview)
        image = Image.fromarray(rgb)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 800, 30), fill=(20, 25, 30))
        draw.text((12, 8), f"SAPIEN | {self.phase} | physical contacts, no object attachment", fill="white")
        if self.writer is None:
            self.writer = imageio.get_writer(str(self.media / self.video), fps=20, codec="libx264", quality=8, macro_block_size=2)
        self.writer.append_data(np.asarray(image))
        self.frame += 1
        self.last_overview = f"{self.session}-overview.jpg"
        image.save(self.media / self.last_overview, quality=92)

    def snapshot_overview(self):
        name = f"{uuid4().hex}-overview.jpg"
        shutil.copyfile(self.media / self.last_overview, self.media / name)
        return name

    def observe(self):
        self.record()
        self.scene.update_render()
        rgb = self._rgb(self.top)
        positions = self.top.get_picture("Position")
        depth = -positions[..., 2]
        depth[(positions[..., 3] >= 1) | (depth <= 0)] = np.nan
        observation = uuid4().hex
        image = f"{observation}-rgb.png"
        data = f"{observation}-frame.npz"
        Image.fromarray(rgb).save(self.media / image)
        world_from_camera = self.top.get_model_matrix() @ np.diag([1, -1, -1, 1])
        np.savez_compressed(self.media / data, rgb=rgb, sensor_depth=depth,
                            intrinsics=self.top.get_intrinsic_matrix(), world_from_camera=world_from_camera,
                            revision=self.revision)
        return {"observation_id": observation, "frame_ref": data, "rgb_ref": image,
                "task_contract": self.goal, "object_labels": {"target": "red block", "blue": "blue block"},
                "calibrated_zones": self.zones,
                "revision": self.revision, "tcp_world_m": self.tcp().tolist(),
                "gripper_width_m": float(sum(self.robot.get_qpos()[7:])),
                "camera": {"coordinates": "OpenCV: x right, y down, z forward", "depth_unit": "m"},
                "video": self.video, "images": {"top": image, "overview": self.snapshot_overview()}}

    def _ik(self, tcp, rotation, initial):
        if not np.isfinite(tcp).all() or not (.12 <= tcp[0] <= .75 and -.4 <= tcp[1] <= .4 and .012 <= tcp[2] <= .5):
            raise ValueError("Requested pose is outside the tabletop workspace")
        hand_xyz = tcp - rotation @ np.array([0, 0, self.tcp_offset])
        q = Rotation.from_matrix(rotation).as_quat()
        pose = sapien.Pose(hand_xyz, q[[3, 0, 1, 2]])
        values, ok, error = self.model.compute_inverse_kinematics(self.hand_index, pose,
            initial_qpos=initial, active_qmask=np.array([1] * 7 + [0] * 2), max_iterations=200, eps=1e-4)
        if not ok or not np.isfinite(values).all():
            raise ValueError(f"IK did not converge: {float(np.linalg.norm(error)):.5f}")
        limits = self.robot.get_qlimits()
        if np.any(values[:7] < limits[:7, 0]) or np.any(values[:7] > limits[:7, 1]):
            raise ValueError("IK violates joint limits")
        return values

    def plan(self, grasp):
        if grasp["revision"] != self.revision:
            raise ValueError("Perception is stale; observe again before planning")
        center, rotation = np.array(grasp["position_world_m"]), np.array(grasp["rotation_world"])
        if grasp["width_m"] > .078:
            raise ValueError("The grasp is wider than the Panda gripper")
        points = {"approach": center + [0, 0, .15], "descend": center, "lift": center + [0, 0, .18]}
        # A parallel-jaw grasp is equivalent after a half turn about approach.
        # Choose the reachable equivalent requiring less joint travel, rather
        # than inheriting GG-CNN's arbitrary sign and rotating through a singularity.
        choices = []
        for candidate_rotation in (rotation, rotation @ np.diag([-1., -1., 1.])):
            q, cost = self.target.copy(), 0.
            try:
                for point in points.values():
                    previous = q.copy()
                    q = self._ik(point, candidate_rotation, q)
                    cost += float(np.linalg.norm(q[:7] - previous[:7]))
                choices.append((cost, candidate_rotation))
            except ValueError:
                continue
        if not choices:
            raise ValueError("Neither equivalent parallel-jaw orientation has reachable IK endpoints")
        _, rotation = min(choices, key=lambda item: item[0])
        plan_id = uuid4().hex
        self.plans[plan_id] = {"points": points, "rotation": rotation, "phase": "planned", "revision": self.revision, "kind": "grasp"}
        return {"verdict": "feasible", "plan_id": plan_id, "waypoints_world_m": {k: v.tolist() for k, v in points.items()},
                "grasp_width_m": grasp["width_m"], "required_order": ["open gripper", "approach", "descend", "close gripper", "lift"],
                "planning_scope": "Top-down single-object tabletop; joint limits and workspace checked"}

    def plan_place(self, grasp_plan_id, zone):
        source = self.plans[grasp_plan_id]
        if source["kind"] != "grasp" or source["phase"] != "lift" or self.finger_width > .005:
            raise ValueError("Placement requires a lifted grasp and closed gripper")
        if np.linalg.norm(self.tcp() - source["points"]["lift"]) > .015:
            raise ValueError("Robot is no longer at the source grasp's lift pose")
        xy = self.zones[zone]["center"]
        # Reuse the perceived grasp height; no actor poses enter placement planning.
        height = max(.025, float(source["points"]["descend"][2]))
        points = {"transfer": np.array([*xy, .28]), "lower": np.array([*xy, height]),
                  "retreat": np.array([*xy, .28])}
        q = self.target.copy()
        for point in points.values():
            q = self._ik(point, source["rotation"], q)
        plan_id = uuid4().hex
        self.plans[plan_id] = {"points": points, "rotation": source["rotation"], "phase": "planned",
                               "revision": self.revision, "kind": "place"}
        return {"verdict": "feasible", "plan_id": plan_id, "zone": zone,
                "waypoints_world_m": {k: v.tolist() for k, v in points.items()},
                "required_order": ["transfer", "lower", "open gripper", "retreat"],
                "planning_scope": "Calibrated pad, workspace and endpoint IK; no full-arm collision guarantee"}

    def move(self, plan_id, waypoint):
        plan = self.plans[plan_id]
        if plan["revision"] != self.revision:
            raise ValueError("Plan is stale after an unrelated action; observe and plan again")
        required = ({"approach": "planned", "descend": "approach", "lift": "descend"} if plan["kind"] == "grasp"
                    else {"transfer": "planned", "lower": "transfer", "retreat": "lower"})
        if waypoint not in required:
            raise ValueError("Waypoint does not belong to this plan")
        if plan["phase"] != required[waypoint]:
            raise ValueError(f"Cannot execute {waypoint} from {plan['phase']}")
        if waypoint in ("approach", "descend", "retreat") and self.finger_width < .07:
            raise ValueError("Open the gripper before approaching the object")
        if waypoint in ("lift", "transfer", "lower") and self.finger_width > .005:
            raise ValueError("Close the gripper before lifting")
        end = plan["points"][waypoint]
        start, rotation = self.tcp().copy(), plan["rotation"]
        if waypoint in ("approach", "transfer"):
            waypoints = [np.array([start[0], start[1], max(start[2], .28)]),
                         np.array([end[0], end[1], max(start[2], .28)]), end]
        else:
            waypoints = [end]
        # Solve every sample before moving. An invalid path produces zero dispatch.
        q, path = self.robot.get_qpos().copy(), []
        initial_rotation = Rotation.from_quat(self.hand.pose.q[[1, 2, 3, 0]])
        orientation = Slerp([0, 1], Rotation.from_matrix([initial_rotation.as_matrix(), rotation]))
        for target in waypoints:
            count = max(30, int(np.linalg.norm(target - start) / .10 * 60))
            for i in range(1, count + 1):
                t = i / count
                position = start + (target - start) * (t * t * (3 - 2 * t))
                previous = q.copy()
                q = self._ik(position, orientation(t * t * (3 - 2 * t)).as_matrix(), q)
                if np.max(np.abs(q[:7] - previous[:7])) > .12:
                    raise ValueError("IK path has a discontinuous joint jump")
                path.append(q[:7].copy())
            start = target
            orientation = Slerp([0, 1], Rotation.from_matrix([rotation, rotation]))
        self.phase = waypoint
        self.revision += 1
        for joints in path:
            self.target[:7] = joints
            yield
        for _ in range(20):
            yield
        error = float(np.linalg.norm(self.tcp() - end))
        if error > .008:
            raise RuntimeError(f"TCP tracking error is too large: {error:.4f} m")
        plan["phase"] = waypoint
        plan["revision"] = self.revision
        return {"waypoint": waypoint, "tcp_error_m": error, "tcp_world_m": self.tcp().tolist()}

    def grip(self, width):
        self.phase = "open gripper" if width > .04 else "close gripper"
        self.target[7:] = width / 2
        self.finger_width = width
        compatible = [plan for plan in self.plans.values() if plan["revision"] == self.revision
                      and ((width >= .07 and plan["kind"] == "grasp" and plan["phase"] == "planned")
                           or (width <= .005 and plan["kind"] == "grasp" and plan["phase"] == "descend")
                           or (width >= .07 and plan["kind"] == "place" and plan["phase"] == "lower"))]
        self.revision += 1
        for plan in compatible:
            plan["revision"] = self.revision
        for _ in range(60):
            yield
        return {"actual_width_m": float(sum(self.robot.get_qpos()[7:]))}

    def verify(self):
        # Actor state is privileged evaluation evidence, never a planning input.
        samples = []
        # Verification advances physics; invalidate perception and plans afterward.
        for _ in range(120):
            self.step()
            sample = {}
            for name, actor in self.objects.items():
                corners = np.array([[x, y, z] for x in (-.026, .026) for y in (-.018, .018) for z in (-.025, .025)])
                world = corners @ Rotation.from_quat(actor.pose.q[[1, 2, 3, 0]]).as_matrix().T + actor.pose.p
                inside = [zone for zone, bounds in self.zones.items()
                          if np.all(np.abs(world[:, :2] - bounds["center"]) <= np.array(bounds["half_size"]) - .005)]
                dynamic = actor.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent)
                if dynamic is None and actor.find_component_by_type(sapien.physx.PhysxRigidStaticComponent) is None:
                    raise RuntimeError("Evaluator cannot measure an actor without a rigid body")
                sample[name] = {"bilateral_contacts": len(self.contacts[name]) == 2,
                                "finger_contact": bool(self.contacts[name]), "table_contact": self.support_contacts[name],
                                "lift_m": float(actor.pose.p[2] - self.initial_heights[name]), "inside_zones": inside,
                                "linear_speed_m_s": float(np.linalg.norm(dynamic.linear_velocity)) if dynamic is not None else 0.,
                                "angular_speed_rad_s": float(np.linalg.norm(dynamic.angular_velocity)) if dynamic is not None else 0.}
            samples.append(sample)
        self.revision += 1
        result = evaluate_samples(self.goal, samples)
        self.phase = "goal verified" if result["success"] else "goal not verified"
        self.record()
        return {**result, "task_contract": self.goal, "revision": self.revision, "simulation_tick": self.ticks,
                "evidence_source": "sapien actor state and physical contacts (evaluation only)",
                "objects": samples[-1],
                "video": self.video, "images": {"verification": self.snapshot_overview()}}

    def stop(self):
        self.target[:] = self.robot.get_qpos()
        self.revision += 1
        self.phase = "stopped"

    def close(self):
        if self.writer:
            self.writer.close()
