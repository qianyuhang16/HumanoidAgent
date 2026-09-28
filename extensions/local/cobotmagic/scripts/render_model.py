"""Offline URDF visual rendering only. No ROS, network, robot or control imports.

Run with a Python environment containing SAPIEN, scipy and Pillow (e.g. robotwin).
Official meshes must already exist under assets/reference/mobile_aloha_sim/.
"""

import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import sapien
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parents[2] / "workspace/cobotmagic-model"
OUTPUT.mkdir(parents=True, exist_ok=True)
SOURCE = ROOT / "assets/reference/aloha_tracer2_dabai_dark.urdf"
tree = ET.parse(SOURCE).getroot()


def origin(node):
    result = np.eye(4)
    if node is not None:
        result[:3, 3] = np.fromstring(node.get("xyz", "0 0 0"), sep=" ")
        result[:3, :3] = Rotation.from_euler("xyz", np.fromstring(node.get("rpy", "0 0 0"), sep=" ")).as_matrix()
    return result


def pose(matrix):
    quaternion = Rotation.from_matrix(matrix[:3, :3]).as_quat()
    return sapien.Pose(matrix[:3, 3], quaternion[[3, 0, 1, 2]])


def material(color):
    value = sapien.render.RenderMaterial()
    value.base_color = color
    value.roughness = .65
    return value


scene = sapien.Scene()
scene.set_ambient_light([.7, .7, .7])
scene.add_directional_light([-.6, .1, -1], [2.0, 1.9, 1.8], shadow=True)
scene.add_directional_light([.2, -.7, -.5], [.9, .95, 1.1], shadow=False)

# All joints at model zero: a display pose, never an observation of the real robot.
frames = {"footprint": np.eye(4)}
pending = list(tree.findall("joint"))
while pending:
    progress = []
    for joint in pending:
        parent = joint.find("parent").get("link")
        if parent in frames:
            frames[joint.find("child").get("link")] = frames[parent] @ origin(joint.find("origin"))
            progress.append(joint)
    if not progress:
        raise ValueError("Disconnected URDF")
    pending = [joint for joint in pending if joint not in progress]

orange = material([.95, .44, .15, 1])
for link in tree.findall("link"):
    builder = scene.create_actor_builder()
    builder.initial_pose = pose(frames[link.get("name")])
    for visual in link.findall("visual"):
        mesh = visual.find("geometry/mesh")
        if mesh is None:
            raise ValueError("Expected mesh visual")
        filename = ROOT / "assets/reference/mobile_aloha_sim" / mesh.get("filename").removeprefix("package://")
        if not filename.is_file():
            raise FileNotFoundError(filename)
        kwargs = {}
        if link.get("name").startswith("fr_"):
            kwargs["material"] = orange
        builder.add_visual_from_file(str(filename), pose=pose(origin(visual.find("origin"))),
            scale=np.fromstring(mesh.get("scale", "1 1 1"), sep=" "), **kwargs)
    builder.build_static(name=link.get("name"))


def box(name, xyz, half, rgba):
    builder = scene.create_actor_builder()
    builder.initial_pose = sapien.Pose(xyz)
    builder.add_box_visual(half_size=half, material=material(rgba))
    return builder.build_static(name=name)


box("floor", [0, 0, -.025], [20, 20, .025], [.84, .88, .91, 1])
for coordinate in np.arange(-2, 2.1, .2):
    box("grid_x", [coordinate, 0, .0005], [.001, 2, .0005], [.65, .71, .77, 1])
    box("grid_y", [0, coordinate, .0005], [2, .001, .0005], [.65, .71, .77, 1])
base = frames["fr_base_link"][:3, 3]
for index, rgba in enumerate(([.94, .16, .13, 1], [.16, .67, .3, 1], [.14, .4, .93, 1])):
    direction = np.eye(3)[index]
    half = np.full(3, .003)
    half[index] = .11
    box("base_axis", base + .11 * direction, half, rgba)

views = {"overview": ([2.4, -2.4, 1.95], [0, 0, .7]),
         "front": ([2.7, -.15, 1.5], [0, 0, .8])}
metadata = {"source_urdf": str(SOURCE), "joint_pose": "all model joints zero; NOT live robot feedback",
            "robot_connected": False, "right_base_world_m": base.tolist(),
            "views": {}}
for name, (eye, target) in views.items():
    camera = scene.add_camera(name, 1500, 1100, math.radians(43), .05, 100)
    forward = np.asarray(target) - eye
    forward /= np.linalg.norm(forward)
    left = np.cross([0, 0, 1], forward)
    left /= np.linalg.norm(left)
    up = np.cross(forward, left)
    matrix = np.eye(4)
    matrix[:3, :3] = np.column_stack([forward, left, up])
    matrix[:3, 3] = eye
    camera.entity.set_pose(pose(matrix))
    scene.update_render()
    camera.take_picture()
    rgb = (np.clip(camera.get_picture("Color")[..., :3], 0, 1) * 255).astype(np.uint8)
    Image.fromarray(rgb).save(OUTPUT / f"{name}.png")
    metadata["views"][name] = {"eye": eye, "target": target}
    print("Rendered", OUTPUT / f"{name}.png", flush=True)
(OUTPUT / "render.json").write_text(json.dumps(metadata, indent=2))
