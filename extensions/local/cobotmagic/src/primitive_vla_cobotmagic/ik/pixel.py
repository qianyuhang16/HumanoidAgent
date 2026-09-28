"""Pixel-to-IK workflow shared by CLI and the read-only web adapter."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys
import time
from typing import Literal
from uuid import uuid4

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
import yaml
from primitive_vla.tools import CallContext
from ..bridge import CameraBridge, decode_frames
from ..calibration.core import check_intrinsics
from .execution import decode_positions, load_setup, make_plan, gripper_hold_value, InitialFeedbackError
from .config import load_pixel_settings
from .kinematics import vector

EXTENSION = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = EXTENSION / "configs/ik.yaml"


class PixelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    observation_id: str
    arm: Literal["left", "right"] = "right"
    u: float = Field(ge=0)
    v: float = Field(ge=0)
    orientation: Literal["downward", "position", "current", "rpy"] | None = None
    rpy_deg: list[float] | None = Field(default=None, min_length=3, max_length=3)
    # Accepted for old clients; motion parameters come from the shared config.
    move_duration_s: float | None = Field(default=None, gt=0, le=300)


def rigid_transform(value):
    t = np.asarray(value, float)
    if (t.shape != (4, 4) or not np.isfinite(t).all() or
            not np.allclose(t[3], [0, 0, 0, 1]) or
            not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-5) or
            not np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-5)):
        raise ValueError("Invalid hand-eye rigid transform")
    return t


def ray_plane(pixel, size, intrinsics, plane_transform, plane_z):
    """Undistorted camera ray intersected with z=plane_z in the reference base."""
    check_intrinsics(intrinsics, "front")
    if list(size) != intrinsics["image_size"]:
        raise ValueError("当前图像尺寸与标定内参不一致，不能缩放或裁剪后直接使用")
    u, v = vector(pixel, 2, "pixel")
    if not 0 <= u < size[0] or not 0 <= v < size[1]:
        raise ValueError("标点超出原始图像")
    uv = cv2.undistortPoints(np.array([[[u, v]]], float),
                             np.asarray(intrinsics["K"], float), np.asarray(intrinsics["D"], float))[0, 0]
    t = rigid_transform(plane_transform)
    direction = t[:3, :3] @ np.array([*uv, 1.0])
    origin = t[:3, 3]
    if abs(direction[2]) < 1e-5:
        raise ValueError("视线与高度平面近乎平行，请换标点或高度")
    distance = (plane_z - origin[2]) / direction[2]
    if not np.isfinite(distance) or distance <= 0 or distance > 3:
        raise ValueError("平面交点位于相机后方或距离超过 3 m，请核对平面高度")
    return origin + distance * direction


def compensated_target(pixel, size, intrinsics, base_from_camera, plane_z, distance_m):
    """Move in the image-up direction on the base XY plane, in metres."""
    if type(distance_m) not in (int, float) or not np.isfinite(distance_m) or distance_m < 0:
        raise ValueError("图片向上补偿距离必须是非负有限数，单位米")
    pixel = vector(pixel, 2, "pixel")
    point = ray_plane(pixel, size, intrinsics, base_from_camera, plane_z)
    point[2] = plane_z
    if distance_m == 0:
        return point, point.copy()
    # Use an in-frame neighbour even when the click is on the top edge.
    dv = -1. if pixel[1] >= 1 else 1.
    neighbour = ray_plane(pixel + [0, dv], size, intrinsics, base_from_camera, plane_z)
    direction = (neighbour - point) * (-dv)
    direction[2] = 0.
    length = np.linalg.norm(direction)
    if length < 1e-9:
        raise ValueError("无法从标定计算图片向上对应的水平方向")
    return point, point + direction / length * distance_m


def rpy_from_matrix(r):
    pitch = np.arctan2(-r[2, 0], np.hypot(r[0, 0], r[1, 0]))
    if abs(np.cos(pitch)) < 1e-8:
        return [0.0, float(pitch), float(np.arctan2(-r[0, 1], r[1, 1]))]
    return [float(np.arctan2(r[2, 1], r[2, 2])), float(pitch), float(np.arctan2(r[1, 0], r[0, 0]))]


def project_path(points, camera_from_base, intrinsics):
    points = np.asarray(points, float)
    camera = (camera_from_base[:3, :3] @ points.T).T + camera_from_base[:3, 3]
    result = []
    for p in camera:
        if p[2] <= .001:
            result.append(None)
        else:
            uv, _ = cv2.projectPoints(p.reshape(1, 3), np.zeros(3), np.zeros(3),
                                     np.asarray(intrinsics["K"], float), np.asarray(intrinsics["D"], float))
            result.append(uv.reshape(2).tolist())
    return result


class PixelIK:
    def __init__(self, config_path=DEFAULT_CONFIG, bridge=None):
        self.config_path, self.options = load_pixel_settings(config_path)
        self.settings = self.options.model_dump(mode="json")
        self.calibration = self.resolve(self.settings["calibration_workspace"])
        self.output = self.resolve(self.settings["output_workspace"])
        self.bridge = bridge or CameraBridge(self.settings["host"], self.settings["port"],
                                               connect_timeout_s=3, request_timeout_s=5)
        self.snapshot = None
        self.lock = asyncio.Lock()

    async def plan(self, arm, u, v):
        """Capture once and plan a full-resolution pixel using the configured defaults."""
        target = PixelTarget(observation_id="", arm=arm, u=u, v=v)
        async with self.lock:
            observation = await self.capture()
            target = target.model_copy(update={"observation_id": observation["observation_id"]})
            return await asyncio.to_thread(self.calculate, target)

    def public_settings(self):
        return {"target_base_z_m": self.options.target_base_z_m,
                "forward_compensation_m": self.options.forward_compensation_m,
                "orientation": self.options.orientation, "rpy_deg": self.options.rpy_deg,
                "motion": self.options.motion.model_dump(mode="json")}

    def resolve(self, path):
        return (self.config_path.parent / path).resolve()

    def calibration_result(self, arm, phase):
        active = json.loads((self.calibration / "workspace.json").read_text())["active_runs"]
        key = arm if phase == "handeye" else "tcp_" + arm
        run = active[key]
        parent = (self.calibration / (f"handeye/front-to-{arm}-base" if phase == "handeye" else f"tcp/{arm}-tool") / "runs").resolve()
        folder = (parent / run).resolve()
        if folder.parent != parent:
            raise ValueError("Invalid calibration run")
        result = json.loads((folder / "result.json").read_text())
        manifest = json.loads((folder / "manifest.json").read_text())
        ids = set(result["training_ids"] + result["validation_ids"])
        if (result.get("accepted") is not True or result.get("phase") != phase or
                result.get("arm") != arm or not ids or
                ids != {p.parent.name for p in (folder / "samples").glob("*/sample.json")}):
            raise ValueError(f"{arm} {phase} 标定未通过或采样已变化，请重新求解标定")
        return folder, result, manifest

    async def capture(self):
        self.snapshot = None  # Failed refresh must not leave the old picture selectable.
        started = time.monotonic()
        raw = await self.bridge.observation(CallContext(uuid4().hex, started + 5))
        _, frames = decode_frames(raw, ["front"], .5, 8 * 1024 * 1024)
        frame = frames[0]
        positions = {}
        for arm in ("left", "right"):
            positions[arm] = vector(raw.get("positions", {}).get(arm), 7, arm).tolist()
            stamp = raw.get("joint_timestamps_ns", {}).get(arm)
            if type(stamp) is not int or not -.05 <= (raw["timestamp_ns"] - stamp) / 1e9 <= .5:
                raise ValueError(f"{arm} 关节反馈过期或时间戳无效")
            if abs(stamp - frame.timestamp_ns) / 1e9 > .4:
                raise ValueError(f"{arm} 图像/关节时间差超过 400 ms")
        token = uuid4().hex
        self.snapshot = {"id": token, "raw": raw, "frame": frame, "captured": time.monotonic(),
                         "positions": positions, "transport_s": time.monotonic() - started}
        return {"observation_id": token, "image": "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode(),
                "width": frame.width, "height": frame.height, "positions": positions,
                "image_age_ms": frame.age_ms, "captured_at": datetime.now(timezone.utc).isoformat()}

    def calculate(self, body):
        body = body.model_copy(update={
            "orientation": body.orientation or self.options.orientation,
            "rpy_deg": body.rpy_deg if body.rpy_deg is not None else list(self.options.rpy_deg),
        })
        snap = self.snapshot
        if not snap or snap["id"] != body.observation_id or time.monotonic() - snap["captured"] > 180:
            raise ValueError("画面已更新或超过 3 分钟，请重新取图、标点")
        frame = snap["frame"]
        _, handeye, manifest = self.calibration_result(body.arm, "handeye")
        tcp_folder, tcp, _ = self.calibration_result(body.arm, "tcp")
        if handeye.get("camera") != "front":
            raise ValueError("仅支持已标定的 front 固定相机")
        selected_from_camera = rigid_transform(handeye["T_base_from_camera"])
        compensation_m = self.options.forward_compensation_m[body.arm]
        point, target = compensated_target([body.u, body.v], [frame.width, frame.height],
                          manifest["intrinsics"], selected_from_camera, self.options.target_base_z_m, compensation_m)
        compensation = {"direction": "image_up_on_base_xy_plane", "distance_m": compensation_m,
                        "offset_base_xyz_m": (target-point).tolist()}
        up = np.array([0.0, 0.0, 1.0])
        response = {"gripper_hold_commands_m": {side: gripper_hold_value(values[6]) for side, values in snap["positions"].items()},
                    "bridge_mode": "existing_send_action",
                    "point_xyz_m": point.tolist(), "target_xyz_m": target.tolist(),
                    "compensation": compensation,
                    "settings": self.public_settings(),
                    "target_pixel": project_path([target], np.linalg.inv(selected_from_camera), manifest["intrinsics"])[0],
                    "arm": body.arm, "frame": handeye["base_frame"], "up_in_base": up.tolist(),
                    "solved": False, "execution_ready": False, "command": None,
                    "method": f"RGB 射线与 base 的 Z={self.options.target_base_z_m:g} m 平面求交，再沿图片向上作水平补偿，无深度测量", "blockers": []}
        if self.options.robot is not None:
            config = self.options.robot.model_dump(mode="json")
            config["urdf"] = str(self.resolve(config["urdf"]))
            config.update(arm=body.arm, host=self.options.host, port=self.options.port,
                          tcp_xyz_m=None, tcp_rpy_rad=[0,0,0], require_tcp_bounds=False,
                          tcp_bounds_m=None, via_xyz_m=[], target_rpy_rad=None, up_in_base=[0,0,1])
        else:
            config_path = self.resolve(self.options.hover_configs[body.arm])
            config = yaml.safe_load(config_path.read_text())
            config["urdf"] = str((config_path.parent / config["urdf"]).resolve())
            config.update(host=self.options.host, port=self.options.port)
        config.update(self.options.motion.model_dump(mode="json"))
        config["tcp_calibration_result"] = str(tcp_folder / "result.json")
        model_hash = hashlib.sha256(Path(config["urdf"]).read_bytes()).hexdigest()
        for result in (handeye, tcp):
            if (result["urdf_sha256"] != model_hash or result["base_frame"] != config["base_link"] or
                    result["tip_frame"] != config["tip_link"] or config["arm"] != body.arm):
                raise ValueError("外参、TCP 和当前运动模型/坐标系不一致")
        if manifest["config"]["joint_names"] != config["joint_names"]:
            raise ValueError("外参关节映射与运动配置不一致")
        config.pop("bowl_rim_center_xyz_m", None)
        config.pop("hover_height_m", None)
        config.update(target_tcp_xyz_m=target.tolist(), up_in_base=up.tolist(), move_duration_s=self.options.motion.move_duration_s)
        self.output.mkdir(parents=True, exist_ok=True)
        folder = self.output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:10])
        folder.mkdir()
        # Each click produces a separate immutable review bundle, never overwrites a prior command.
        generated = folder / "hover.yaml"
        generated.write_text(yaml.safe_dump(config, allow_unicode=True))
        loaded, chain = load_setup(generated)
        current = chain.fk(snap["positions"][body.arm][:6])
        config["orientation_mode"] = body.orientation if body.orientation in ("position", "downward") else "fixed"
        config["target_rpy_rad"] = (rpy_from_matrix(current[:3, :3]) if body.orientation != "rpy" else
                                     np.deg2rad(vector(body.rpy_deg, 3, "RPY")).tolist())
        # Freeze the calibrated TCP and model so later calibration runs cannot change a reviewed command.
        config["tcp_xyz_m"], config["tcp_rpy_rad"] = loaded["tcp_xyz_m"], loaded["tcp_rpy_rad"]
        config["tcp_calibration_result"] = None
        shutil.copyfile(config["urdf"], folder / "robot.urdf")
        config["urdf"] = "robot.urdf"
        generated.write_text(yaml.safe_dump(config, allow_unicode=True))
        loaded, chain = load_setup(generated)
        (folder / "front.jpg").write_bytes(frame.jpeg)
        raw = {k: v for k, v in snap["raw"].items() if k != "images"}
        (folder / "snapshot.json").write_text(json.dumps(raw, indent=2))
        provenance = {"selection": {**body.model_dump(), "move_duration_s": self.options.motion.move_duration_s}, "handeye": handeye, "tcp": tcp,
                      "uncompensated_point_xyz_m": point.tolist(), "target_xyz_m": target.tolist(),
                      "compensation": compensation,
                      "target_base_z_m": self.options.target_base_z_m, "intrinsics": manifest["intrinsics"],
                      "robot_control": False, "physical_accuracy_verified": False}
        (folder / "inputs.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
        response.update(bundle=str(folder), start_tcp_xyz_m=current[:3, 3].tolist(),
                        orientation_mode=config["orientation_mode"],
                        start_rpy_deg=np.rad2deg(rpy_from_matrix(current[:3, :3])).tolist(),
                        target_rpy_deg=None if body.orientation == "position" else np.rad2deg(config["target_rpy_rad"]).tolist())
        try:
            plan = make_plan(loaded, chain, snap["positions"])
            if plan["nominal_duration_s"] >= loaded["max_run_s"]:
                response["blockers"].append("轨迹预计用时超过执行上限，请换起点或目标")
        except ValueError as error:
            if isinstance(error, InitialFeedbackError):
                response["blockers"].append(f"{error}。尚未进行目标 IK 求解；请核对反馈、关节映射和模型限位。")
            elif str(error).startswith("移动时间"):
                response["blockers"].append(str(error))
            else:
                response["blockers"].append(f"IK 路径未通过：{error}。未生成执行命令；请换标点或起点姿态，不会自动放弃所选方向约束。")
        else:
            (folder / "plan.json").write_text(json.dumps(plan, indent=2, allow_nan=False))
            projected = project_path(plan["tcp_path_m"], np.linalg.inv(selected_from_camera), manifest["intrinsics"])
            response.update(solved=True, duration_s=plan["nominal_duration_s"], path_pixels=projected,
                            path_mode=plan["path_mode"],
                            jaw_heading_preserved="jaw_heading_rad" in plan,
                            start_jaw_heading_deg=(float(np.rad2deg(plan["jaw_heading_rad"])) if "jaw_heading_rad" in plan else None),
                            target_jaw_heading_deg=plan.get("target_jaw_heading_deg"),
                            target_downward_angle_deg=90-float(np.rad2deg(chain.downward_tilt(chain.fk(plan["joint_path_rad"][-1])))),
                            target_rpy_deg=np.rad2deg(rpy_from_matrix(np.asarray(plan["target_tcp_rotation"]))).tolist(),
                            target_joints_deg=np.rad2deg(plan["joint_path_rad"][-1]).tolist(),
                            path_points=len(plan["joint_path_rad"]))
            if "start_joint_limit_projection_rad" in plan:
                response["start_joint_limit_projection_deg"] = np.rad2deg(plan["start_joint_limit_projection_rad"]).tolist()
            try:
                decode_positions(snap["raw"], loaded["max_feedback_age_s"], snap["transport_s"], arm=body.arm,
                                 feedback_limit_tolerance_deg=loaded["feedback_limit_tolerance_deg"])
            except ValueError as error:
                response["blockers"].append(f"运动桥预检查：{error}")
            command = [sys.executable, "-m", "primitive_vla_cobotmagic.ik", "--plan", str(folder)]
            response["preview_command"] = shlex.join(command)
            if not response["blockers"]:
                response.update(execution_ready=True, command=shlex.join(command + ["--execute"]))
        (folder / "review.json").write_text(json.dumps(response, ensure_ascii=False, indent=2, allow_nan=False))
        return response



def execute_bundle(folder, *, execute=False):
    """Replay a frozen bundle, optionally execute after the terminal Space gate."""
    from .execution import run_plan
    folder = Path(folder).resolve()
    review = json.loads((folder / "review.json").read_text())
    if not review.get("solved") or (execute and not review.get("execution_ready")):
        raise ValueError("该计划未通过：" + "; ".join(review.get("blockers", [])))
    return run_plan(folder / "hover.yaml", reviewed_path=folder / "plan.json",
                    snapshot=None if execute else folder / "snapshot.json",
                    output=folder / ("execution-plan.json" if execute else "preview-plan.json"),
                    execute=execute)


def move_to_pixel(arm, u, v, *, config=DEFAULT_CONFIG, execute=False):
    """One synchronous entry: fresh image pixel -> calibrated plan -> optional motion.

    Coordinates are in the full-resolution front image, never CSS/display pixels.
    execute=True still waits for Space in the calling terminal.
    """
    async def prepare():
        ik = PixelIK(config)
        try:
            return await ik.plan(arm, u, v)
        finally:
            await ik.bridge.close()
    result = asyncio.run(prepare())
    if execute and result["execution_ready"]:
        result["execution_exit_code"] = execute_bundle(result["bundle"], execute=True)
    return result
