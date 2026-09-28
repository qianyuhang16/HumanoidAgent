"""Manual bowl-hover experiment: plan by default; robot writes require --execute.

No enable/reset/grasp, no VLM-estimated metric coordinates, no collision planner.
The camera provider deliberately does not import or register this module.
"""

import argparse
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys
import termios
import time
import tty
from xml.etree.ElementTree import ParseError

import numpy as np
import yaml

from .kinematics import SerialIK, check_joint_limits, rotation, rotation_error, transform, vector
from .config import MotionParameters, DEFAULT_FEEDBACK_LIMIT_TOLERANCE_DEG


SIDES = ("left", "right")
# Intersection with the existing magicbot_ros_bridge's default limits.
BRIDGE_LIMITS = np.array([[-2.6179, 2.6179], [0, 3.14], [-2.967, 0],
                         [-1.745, 1.745], [-1.22, 1.22], [-2.09439, 2.09439]])


class InitialFeedbackError(ValueError):
    """An invalid observed start, independent of the requested target or angle."""


def feedback_tolerance(config):
    return config.get("feedback_limit_tolerance_deg", DEFAULT_FEEDBACK_LIMIT_TOLERANCE_DEG)


class MotionBridge:
    """Separate from the camera-only client; never enables robot motors."""

    def __init__(self, config, allow_motion=False):
        self.config, self.allow_motion = config, allow_motion
        self.sock = None
        self.stamps = {}

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def request(self, payload):
        if payload.get("op") not in ("get_observation", "send_action"):
            raise ValueError("Unsupported operation")
        if payload["op"] == "send_action" and not self.allow_motion:
            raise RuntimeError("Motion disabled: run explicitly with --execute")
        timeout = self.config["request_timeout_s"]
        deadline = time.monotonic() + timeout
        try:
            if self.sock is None:
                self.sock = socket.create_connection((self.config["host"], self.config["port"]), timeout)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            def remaining():
                value = deadline - time.monotonic()
                if value <= 0:
                    raise TimeoutError("Bridge exceeded request deadline")
                self.sock.settimeout(value)
            def receive(size):
                result = bytearray()
                while len(result) < size:
                    remaining()
                    chunk = self.sock.recv(size - len(result))
                    if not chunk:
                        raise ConnectionError("Bridge disconnected")
                    result.extend(chunk)
                return bytes(result)
            body = json.dumps(payload, allow_nan=False).encode()
            remaining()
            self.sock.sendall(struct.pack("!I", len(body)) + body)
            size, = struct.unpack("!I", receive(4))
            if not 0 < size <= 32 * 1024 * 1024:
                raise ValueError("Invalid bridge packet length")
            result = json.loads(receive(size))
            if time.monotonic() > deadline:
                raise TimeoutError("Bridge response arrived too late")
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("Bridge rejected request")
            return result
        except BaseException:
            self.close()
            raise

    def observation(self):
        started = time.monotonic()
        result = self.request({"op": "get_observation"})
        positions = decode_positions(result, self.config["max_feedback_age_s"],
                                     time.monotonic() - started, arm=self.config["arm"],
                                     feedback_limit_tolerance_deg=feedback_tolerance(self.config))
        for side in SIDES:
            stamp = result["joint_timestamps_ns"][side]
            if side in self.stamps and stamp < self.stamps[side]:
                raise ValueError("Joint timestamp moved backwards")
            self.stamps[side] = stamp
        return positions, result

    def action(self, positions):
        positions = current_state_command(positions, feedback_limit_tolerance_deg=feedback_tolerance(self.config))
        result = self.request({"op": "send_action", "positions": positions,
                               "speed_percent": self.config["speed_percent"]})
        for side in SIDES:
            applied = vector(result.get("positions", {}).get(side), 7, "action response")
            if np.max(np.abs(applied - positions[side])) > 1e-5:
                raise RuntimeError("Bridge changed the command; model/limits/feedback may disagree")
        # Command echo is not arrival feedback. Arrival is measured separately.


def gripper_hold_value(value):
    """Use the existing bridge's command range, without altering raw feedback."""
    if not np.isfinite(value):
        raise ValueError("Non-finite gripper feedback")
    return float(np.clip(value, 0.0, .08))


def current_state_command(positions, arm=None, joints=None, *,
                          feedback_limit_tolerance_deg=DEFAULT_FEEDBACK_LIMIT_TOLERANCE_DEG):
    """Copy current state; replace only the requested arm's six joint targets."""
    if (arm is None) != (joints is None) or (arm is not None and arm not in SIDES):
        raise ValueError("Specify a valid arm and six joint targets together")
    command = {side: vector(positions[side], 7, f"{side} feedback").tolist() for side in SIDES}
    for side in SIDES:
        q = check_bridge_feedback(command[side][:6], side, feedback_limit_tolerance_deg)
        # Match the existing bridge's hold conversion at an encoder zero stop.
        # Raw observations and explicit trajectory targets are never clipped.
        command[side][:6] = np.clip(q, BRIDGE_LIMITS[:, 0], BRIDGE_LIMITS[:, 1]).tolist()
        command[side][6] = gripper_hold_value(command[side][6])
    if arm is not None:
        q = check_joint_limits(joints, BRIDGE_LIMITS[:, 0], BRIDGE_LIMITS[:, 1],
                               [f"joint{i}" for i in range(1, 7)], tolerance_rad=0,
                               label=f"{arm} command")
        command[arm][:6] = q.tolist()
    return command


def check_bridge_feedback(q, side, tolerance_deg):
    return check_joint_limits(q, BRIDGE_LIMITS[:, 0], BRIDGE_LIMITS[:, 1],
                              [f"joint{i}" for i in range(1, 7)],
                              tolerance_rad=np.deg2rad(tolerance_deg), label=f"{side} feedback")


def decode_positions(reply, max_age_s, elapsed_s=0, *, arm=None,
                     feedback_limit_tolerance_deg=DEFAULT_FEEDBACK_LIMIT_TOLERANCE_DEG):
    stamp = reply.get("timestamp_ns")
    if reply.get("ok") is not True or type(stamp) is not int or stamp <= 0:
        raise ValueError("Invalid observation timestamp or status")
    result = {}
    for side in SIDES:
        joint_stamp = reply.get("joint_timestamps_ns", {}).get(side)
        if type(joint_stamp) is not int or joint_stamp <= 0:
            raise ValueError(f"Missing {side} joint timestamp")
        age = (stamp - joint_stamp) / 1e9
        if age < -.05 or age + elapsed_s > max_age_s:
            raise ValueError(f"Stale/future {side} joint feedback; motion aborted")
        values = vector(reply.get("positions", {}).get(side), 7, f"{side} feedback")
        check_bridge_feedback(values[:6], side, feedback_limit_tolerance_deg)
        # Preserve raw telemetry; command conversion is handled by current_state_command.
        result[side] = values.tolist()
    return result


def load_setup(path, *, preview=False):
    config = yaml.safe_load(path.read_text())
    config.update(MotionParameters.from_execution_config(config).model_dump(mode="json"))
    urdf = Path(config["urdf"]).expanduser()
    if not urdf.is_absolute():
        urdf = path.parent / urdf
    if config.get("tcp_calibration_result"):
        result_path = Path(config["tcp_calibration_result"]).expanduser()
        if not result_path.is_absolute():
            result_path = path.parent / result_path
        result = json.loads(result_path.read_text())
        manifest = json.loads((result_path.parent / "manifest.json").read_text())
        expected = {"phase": "tcp", "arm": config["arm"],
                    "base_frame": config["base_link"], "tip_frame": config["tip_link"],
                    "urdf_sha256": hashlib.sha256(urdf.read_bytes()).hexdigest()}
        if result.get("accepted") is not True or any(result.get(k) != v for k, v in expected.items()):
            raise ValueError("TCP calibration is unaccepted or arm/frame/URDF does not match")
        if manifest["config"]["joint_names"] != config["joint_names"]:
            raise ValueError("TCP calibration joint mapping does not match")
        solved_ids = set(result["training_ids"] + result["validation_ids"])
        sample_ids = {p.parent.name for p in (result_path.parent / "samples").glob("*/sample.json")}
        if not solved_ids or solved_ids != sample_ids:
            raise ValueError("TCP calibration samples changed; solve calibration again")
        config["tcp_xyz_m"] = vector(result["tcp_xyz_m"], 3, "calibrated TCP").tolist()
        config["tcp_rpy_rad"] = vector(result["tcp_rpy_rad"], 3, "calibrated TCP orientation").tolist()
        config["tcp_calibration_result"] = str(result_path.resolve())
    if type(config.get("require_tcp_bounds", True)) is not bool:
        raise ValueError("require_tcp_bounds must be a boolean")
    execution_fields = ["tcp_xyz_m"]
    if config.get("require_tcp_bounds", True):
        execution_fields.append("tcp_bounds_m")
    direct_target = config.get("target_tcp_xyz_m") is not None
    required = ("arm", "urdf", "target_tcp_xyz_m" if direct_target else "bowl_rim_center_xyz_m")
    if not preview:
        required += tuple(execution_fields)
    missing = [key for key in required if config.get(key) is None]
    if missing:
        raise ValueError("Fill measured configuration before connecting: " + ", ".join(missing))
    config["preview_missing_fields"] = [key for key in execution_fields
                                         if config.get(key) is None]
    config["reference_point"] = "configured TCP"
    if preview and config.get("tcp_xyz_m") is None:
        # A named URDF link origin is defined geometry, not a guess at the gripper TCP.
        # This substitute exists only in memory and marks the resulting plan non-executable.
        config["tcp_xyz_m"], config["tcp_rpy_rad"] = [0, 0, 0], [0, 0, 0]
        config["reference_point"] = f"{config['tip_link']} origin (TCP unknown)"
    if config["arm"] not in SIDES:
        raise ValueError("arm must be left or right")
    if config.get("orientation_mode", "fixed") not in ("fixed", "position", "downward"):
        raise ValueError("orientation_mode must be fixed, position or downward")
    if not isinstance(config["host"], str) or not config["host"]:
        raise ValueError("host is required")
    if type(config["port"]) is not int or not 0 < config["port"] < 65536:
        raise ValueError("Invalid port")
    ranges = {"hover_height_m": (.05, .4),
              "request_timeout_s": (.01, .22), "max_feedback_age_s": (.01, .25),
              "max_run_s": (1, 300), "speed_percent": (1, 100)}
    if direct_target:
        del ranges["hover_height_m"]
    for key, (lower, upper) in ranges.items():
        value = config[key]
        if type(value) not in (int, float) or not lower <= value <= upper:
            raise ValueError(f"{key} must be finite and between {lower} and {upper}")
    if type(config["speed_percent"]) is not int:
        raise ValueError("speed_percent must be an integer")
    if config.get("tcp_bounds_m") is not None:
        bounds = np.asarray(config["tcp_bounds_m"], dtype=float)
        if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[0] >= bounds[1]):
            raise ValueError("tcp_bounds_m needs ordered finite [min_xyz, max_xyz]")
    up = vector(config["up_in_base"], 3, "up_in_base")
    if abs(np.linalg.norm(up) - 1) > 1e-4:
        raise ValueError("up_in_base must be a measured unit vector")
    vector(config["target_tcp_xyz_m"] if direct_target else config["bowl_rim_center_xyz_m"],
           3, "target TCP" if direct_target else "bowl center")
    for waypoint in config["via_xyz_m"]:
        vector(waypoint, 3, "waypoint")
    if config["target_rpy_rad"] is not None:
        vector(config["target_rpy_rad"], 3, "target_rpy_rad")
    chain = SerialIK(urdf, config["base_link"], config["tip_link"], config["joint_names"],
                     transform(config["tcp_xyz_m"], config["tcp_rpy_rad"]))
    chain.lower = np.maximum(chain.lower, BRIDGE_LIMITS[:, 0])
    chain.upper = np.minimum(chain.upper, BRIDGE_LIMITS[:, 1])
    if np.any(chain.lower >= chain.upper):
        raise ValueError("URDF and bridge joint limits do not overlap")
    return config, chain


def check_tcp(config, xyz):
    if config.get("tcp_bounds_m") is None:
        if config.get("require_tcp_bounds", True) and "tcp_bounds_m" not in config.get("preview_missing_fields", []):
            raise ValueError("Missing TCP bounds outside preview mode")
        return
    low, high = np.asarray(config["tcp_bounds_m"])
    if np.any(xyz < low) or np.any(xyz > high):
        raise ValueError("Planned/measured TCP leaves the configured workspace envelope")


def set_move_duration(plan, config, chain, seconds):
    """Retimes the path against model limits, without the old local slow cap."""
    if type(seconds) not in (float, int) or not np.isfinite(seconds) or seconds <= 0:
        raise ValueError("移动时间必须为正数，单位秒")
    durations = np.asarray(plan["durations_s"], float)
    path = np.asarray(plan["joint_path_rad"], float)
    speeds = chain.velocity
    required = np.max(np.abs(np.diff(path, axis=0)) / speeds, axis=1)
    minimum = float(np.max(required / durations) * durations.sum())
    if seconds < minimum - 1e-8:
        raise ValueError(f"移动时间 {seconds:g} 秒会超过 URDF 关节速度上限；模型要求至少 {minimum:.2f} 秒")
    result = dict(plan)
    result["durations_s"] = (durations * (seconds / durations.sum())).tolist()
    result["nominal_duration_s"] = float(seconds)
    result["requested_duration_s"] = float(seconds)
    return result


def wait_for_space():
    """Wait for a new terminal key press; never accepts piped or queued input."""
    if not sys.stdin.isatty():
        raise ValueError("执行需要交互终端：按空格开始，不能通过管道自动触发")
    descriptor = sys.stdin.fileno()
    original = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        termios.tcflush(descriptor, termios.TCIFLUSH)
        print("已就绪。按空格开始移动；Esc 或 Ctrl+C 取消。等待期间不发送动作。", flush=True)
        while True:
            key = sys.stdin.read(1)
            if key == " ":
                print("开始执行。", flush=True)
                return
            if key in ("", "\x1b", "\x03"):
                raise KeyboardInterrupt
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)


def make_plan(config, chain, positions):
    # Reject bad telemetry once, before trying target orientations/path variants.
    try:
        current_state_command(positions, feedback_limit_tolerance_deg=feedback_tolerance(config))
        chain.check_feedback(positions[config["arm"]][:6], np.deg2rad(feedback_tolerance(config)))
    except ValueError as error:
        raise InitialFeedbackError(f"{config['arm']} 起始反馈不符合模型/桥范围：{error}") from error
    if config.get("orientation_mode") != "downward":
        return _make_plan(config, chain, positions)
    # Search complete, timed paths rather than accepting a steeper endpoint
    # whose approach path is unsolved. Coarse search, then one-degree refinement.
    previous_goal = None
    last_error = None
    low, high = MotionParameters.from_execution_config(config).downward_angle_range_deg
    goals = list(np.arange(high, low, -5.)) + [low]
    for goal in goals:
        try:
            plan = _make_plan(config, chain, positions, downward_goal_deg=goal)
        except ValueError as error:
            previous_goal, last_error = goal, error
            continue
        if previous_goal is not None:
            for refined in np.arange(previous_goal-1, goal, -1.):
                try:
                    plan = _make_plan(config, chain, positions, downward_goal_deg=refined)
                    break
                except ValueError:
                    continue
        return plan
    raise ValueError(f"朝下 {low:g}–{high:g}°的路径搜索未成功：{last_error}") from last_error


def _make_plan(config, chain, positions, *, downward_goal_deg=90):
    try:
        return _plan_path(config, chain, positions, downward_goal_deg=downward_goal_deg)
    except ValueError as cartesian_error:
        if config.get("orientation_mode") != "downward":
            raise
        try:
            return _plan_path(config, chain, positions, downward_goal_deg=downward_goal_deg,
                              joint_guided=True)
        except ValueError as guided_error:
            raise ValueError(f"直线路径：{cartesian_error}；终点引导路径：{guided_error}") from guided_error


def _plan_path(config, chain, positions, *, downward_goal_deg=90, joint_guided=False):
    parameters = MotionParameters.from_execution_config(config)
    low_angle, high_angle = parameters.downward_angle_range_deg
    heading_tolerance = np.deg2rad(parameters.jaw_heading_tolerance_deg)
    position_only = config.get("orientation_mode", "fixed") == "position"
    downward = config.get("orientation_mode") == "downward"
    measured = chain.check_feedback(positions[config["arm"]][:6],
                                    np.deg2rad(parameters.feedback_limit_tolerance_deg))
    # Preserve observed FK/heading; use a feasible seed for command-space IK.
    q = np.clip(measured, chain.lower, chain.upper)
    start = chain.fk(measured)
    jaw_heading = chain.jaw_heading(start) if downward and parameters.preserve_jaw_heading else None
    check_tcp(config, start[:3, 3])
    direct_target = config.get("target_tcp_xyz_m") is not None
    target_xyz = (vector(config["target_tcp_xyz_m"], 3, "target TCP") if direct_target else
                  vector(config["bowl_rim_center_xyz_m"], 3, "bowl center")
                  + np.asarray(config["up_in_base"]) * config["hover_height_m"])
    target_rotation = (start[:3, :3] if config["target_rpy_rad"] is None else
                       transform([0, 0, 0], config["target_rpy_rad"])[:3, :3])
    samples = [q.copy()]
    for xyz in [*config["via_xyz_m"], target_xyz]:
        segment_start = chain.fk(q)
        angular = (np.zeros(3) if position_only or downward else
                   rotation_error(target_rotation, segment_start[:3, :3]))
        angle = np.linalg.norm(angular)
        # Keep a small numerical margin above the requested downward angle.
        # Internal margin keeps the numerical result inside both configured bounds.
        margin = min(.2, (high_angle-low_angle)/4)
        min_tilt = np.deg2rad(90-high_angle + (margin if high_angle < 90 else 0))
        cone_goal = max(min_tilt, np.deg2rad(max(0., 90-downward_goal_deg-margin)))
        cone_start = max(cone_goal, chain.downward_tilt(segment_start)) if downward else 0.
        distance = np.linalg.norm(np.asarray(xyz) - segment_start[:3, 3])
        if distance > 2:
            raise ValueError("Waypoint exceeds 2 m; verify units/frame")
        count = max(1, int(np.ceil(distance / .005)), int(np.ceil(angle / .025)))
        if downward:
            count = max(count, int(np.ceil((cone_start - cone_goal) / .025)))
        if joint_guided:
            endpoint = np.eye(4)
            endpoint[:3, 3] = xyz
            end_q = chain.solve(endpoint, q, downward_cone_rad=cone_goal, jaw_heading_rad=jaw_heading,
                                downward_min_tilt_rad=min_tilt,
                                jaw_heading_tolerance_rad=heading_tolerance * (.025 / np.deg2rad(2)))
            guide_start = q.copy()
            count = max(1, int(np.ceil(np.max(np.abs(end_q-guide_start)) / .025)))
        for index in range(1, count + 1):
            ratio = index / count
            target = np.eye(4)
            target[:3, 3] = segment_start[:3, 3] * (1 - ratio) + np.asarray(xyz) * ratio
            check_tcp(config, target[:3, 3])
            target[:3, :3] = (rotation(angular / angle, ratio * angle) @ segment_start[:3, :3]
                              if angle > 1e-8 else segment_start[:3, :3])
            cone = cone_start*(1-ratio) + cone_goal*ratio
            if joint_guided:
                # The joint-space guide avoids forcing an arbitrary Cartesian
                # line through a wrist singularity. Correct its finger heading
                # with IK instead of allowing the gripper to twist en route.
                guide_q = guide_start*(1-ratio) + end_q*ratio
                target = chain.fk(guide_q)
                cone = min(np.pi, max(cone_goal, chain.downward_tilt(target) + .001))
                if index == count:
                    target[:3, 3], cone = xyz, cone_goal
                check_tcp(config, target[:3, 3])
            try:
                if downward:
                    next_q = chain.solve(target, guide_q if joint_guided else q, downward_cone_rad=cone,
                                         jaw_heading_rad=jaw_heading,
                                         downward_min_tilt_rad=min_tilt * ratio,
                                         jaw_heading_tolerance_rad=heading_tolerance * (.025 / np.deg2rad(2)))
                else:
                    next_q = chain.solve(target, q, position_only=True) if position_only else chain.solve(target, q)
            except ValueError as error:
                raise ValueError(f"Path step {index}/{count}, target XYZ {target[:3, 3].round(4).tolist()}: {error}") from error
            # Check actual joint-interpolated TCPs, not just Cartesian IK endpoints.
            # A large change between solver waypoints near a wrist singularity
            # is not a dispatched jump: sample the connecting path and retime
            # it below. Judge that path's FK/heading and velocity, not an
            # arbitrary 0.15-rad cap on the numerical solver's waypoint spacing.
            divisions = max(1, int(np.ceil(np.max(np.abs(next_q - q)) / .003)))
            for fraction in np.linspace(0, 1, divisions + 1)[1:]:
                sample = q * (1 - fraction) + next_q * fraction
                sample_pose = chain.fk(sample)
                check_tcp(config, sample_pose[:3, 3])
                if jaw_heading is not None and abs(chain.jaw_heading_error(sample_pose, jaw_heading)) > heading_tolerance:
                    raise ValueError("路径无法保持起点夹爪朝向，未生成执行计划")
                samples.append(sample)
            q = next_q
    path = np.asarray(samples)
    if position_only or downward:
        target_rotation = chain.fk(path[-1])[:3, :3]
    final_downward_angle = 90-float(np.rad2deg(chain.downward_tilt(chain.fk(path[-1]))))
    if downward and not low_angle <= final_downward_angle <= high_angle:
        raise ValueError(f"目标夹爪未满足向下 {low_angle:g}–{high_angle:g}°，未生成执行计划")
    speeds = chain.velocity
    durations = np.maximum(.05, np.max(np.abs(np.diff(path, axis=0)) / speeds, axis=1))
    plan = {"arm": config["arm"], "frame": config["base_link"],
        "path_mode": "joint_guided" if joint_guided else "cartesian",
        "orientation_mode": "downward" if downward else ("position" if position_only else "fixed"),
        "preview_only": bool(config.get("preview_missing_fields")),
        "missing_execution_fields": config.get("preview_missing_fields", []),
        "reference_point": config.get("reference_point", "configured TCP"),
        "tcp_bounds_checked": config.get("tcp_bounds_m") is not None,
        "tcp_calibration_result": config.get("tcp_calibration_result"),
        "tip_to_reference_xyz_m": config["tcp_xyz_m"],
        "tip_to_reference_rpy_rad": config["tcp_rpy_rad"],
        "start_positions": positions, "target_tcp_xyz_m": target_xyz.tolist(),
        "target_tcp_rotation": target_rotation.tolist(), "joint_path_rad": path.tolist(),
        "tcp_path_m": [chain.fk(row)[:3, 3].tolist() for row in path],
        "durations_s": durations.tolist(), "nominal_duration_s": float(durations.sum()),
        "collision_checked": False,
        "source": "explicit target TCP in base frame" if direct_target else "manually measured bowl rim center"}
    correction = path[0] - measured
    if np.any(correction):
        plan["start_joint_limit_projection_rad"] = correction.tolist()
    if downward:
        plan.update(approach_axis_tip=[0, 0, 1], downward_angle_range_deg=[low_angle, high_angle],
                    preferred_downward_angle_deg=high_angle, downward_goal_deg=downward_goal_deg,
                    target_downward_angle_deg=final_downward_angle)
        if jaw_heading is not None:
            plan.update(jaw_axis_tip=[0, 1, 0], jaw_heading_rad=jaw_heading,
                        target_jaw_heading_deg=float(np.rad2deg(chain.jaw_heading(chain.fk(path[-1])))))
    return set_move_duration(plan, config, chain, parameters.move_duration_s)


def execute_plan(bridge, config, chain, plan, on_event=None, cancel_event=None):
    parameters = MotionParameters.from_execution_config(config)
    if not bridge.allow_motion:
        raise RuntimeError("Execution requires --execute")
    if plan.get("preview_only") or config.get("preview_missing_fields"):
        raise ValueError("A preview with missing calibration/configuration cannot be executed")
    side = config["arm"]
    other = "right" if side == "left" else "left"
    initial = plan["start_positions"]
    path = np.asarray(plan["joint_path_rad"])
    timeline = np.r_[0., np.cumsum(plan["durations_s"])]
    if len(path) != len(timeline) or len(path) < 2 or np.any(np.diff(timeline) <= 0):
        raise ValueError("Invalid trajectory timing")
    started = time.monotonic()
    sent = False
    last_command = None
    final_command_sent = False
    positions, raw, goal = None, None, path[0]
    elapsed, tracking = 0., 0.

    def emit(event, **data):
        if on_event:
            on_event({"event": event, "elapsed_s": elapsed, "arm": side, **data})

    def diagnostic():
        measured = np.asarray(positions[side][:6]) if positions else path[0]
        moved = float(np.max(np.abs(measured - path[0])))
        return (f"已运行 {elapsed:.2f}s，目标/反馈最大差 {np.rad2deg(tracking):.2f}°，"
                f"从起点测得最大关节变化 {np.rad2deg(moved):.2f}°")

    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Motion cancelled")

    try:
        check_cancelled()
        positions, raw = bridge.observation()
        for name in SIDES:
            if np.max(np.abs(np.asarray(positions[name]) - initial[name])) > .005:
                raise ValueError("Robot moved since planning; generate a new plan")
        previous_stamp = raw["joint_timestamps_ns"][side]
        started = time.monotonic()
        emit("start", positions=positions, nominal_duration_s=float(timeline[-1]))
        while True:
            check_cancelled()
            now = time.monotonic()
            elapsed = now - started
            # Stream the reviewed trajectory on its time axis. Do not wait at
            # each ~0.003 rad sample: those steps may fall inside driver deadband.
            index = min(int(np.searchsorted(timeline, elapsed, side="right") - 1), len(path)-2)
            ratio = float(np.clip((elapsed-timeline[index]) / (timeline[index+1]-timeline[index]), 0, 1))
            goal = path[index]*(1-ratio) + path[index+1]*ratio
            q = chain.check_feedback(positions[side][:6], np.deg2rad(parameters.feedback_limit_tolerance_deg))
            tracking = float(np.max(np.abs(goal-q)))
            actual = chain.fk(q)
            check_tcp(config, actual[:3, 3])
            emit("feedback", path_index=index, goal_rad=goal.tolist(), positions=positions,
                 joint_timestamps_ns=raw["joint_timestamps_ns"], tracking_error_rad=tracking,
                 measured_tcp_xyz_m=actual[:3, 3].tolist())
            if elapsed > config["max_run_s"]:
                raise TimeoutError("总执行时间超限；" + diagnostic())
            if last_command is not None and now-last_command > .25:
                raise TimeoutError("Control interval exceeded bridge watchdog budget; " + diagnostic())
            if np.max(np.abs(np.asarray(positions[other][:6])-initial[other][:6])) > .02:
                raise RuntimeError("Other arm moved; possible competing controller")
            if any(abs(gripper_hold_value(positions[name][6])-gripper_hold_value(initial[name][6])) > .002 for name in SIDES):
                raise RuntimeError("Gripper changed; aborting")
            # This is the next scheduled target, not a previously acknowledged
            # target. Its gap from feedback includes command/driver latency and
            # startup acceleration. Log it, but do not use it as an instantaneous
            # arrival test while streaming. Verify the endpoint below instead.
            fresh = raw["joint_timestamps_ns"][side] > previous_stamp
            if final_command_sent and fresh:
                error = float(np.linalg.norm(actual[:3, 3]-plan["target_tcp_xyz_m"]))
                angular = float(np.linalg.norm(rotation_error(np.asarray(plan["target_tcp_rotation"]), actual[:3, :3])))
                measured_downward = 90-float(np.rad2deg(chain.downward_tilt(actual)))
                angle_low, angle_high = parameters.downward_angle_range_deg
                orientation_ok = (angle_low <= measured_downward <= angle_high
                                  if plan.get("orientation_mode") == "downward" else angular <= parameters.orientation_tolerance_rad)
                if plan.get("jaw_heading_rad") is not None:
                    orientation_ok = orientation_ok and abs(chain.jaw_heading_error(actual, plan["jaw_heading_rad"])) <= np.deg2rad(parameters.jaw_heading_tolerance_deg)
                if error <= parameters.position_tolerance_m and orientation_ok:
                    result = {"measured_tcp_xyz_m": actual[:3, 3].tolist(), "position_error_m": error,
                              "orientation_error_rad": angular,"measured_joints_rad":q.tolist()}
                    if plan.get("orientation_mode") == "downward":
                        result["downward_angle_deg"] = 90-float(np.rad2deg(chain.downward_tilt(actual)))
                    if plan.get("jaw_heading_rad") is not None:
                        result["jaw_heading_error_deg"] = float(np.rad2deg(chain.jaw_heading_error(actual, plan["jaw_heading_rad"])))
                    emit("completed", **result)
                    return result
            if elapsed > timeline[-1]+parameters.settle_timeout_s:
                raise TimeoutError(f"终点反馈在 {parameters.settle_timeout_s:g} 秒内未收敛；" + diagnostic())
            command = current_state_command(positions, side, goal,
                                            feedback_limit_tolerance_deg=parameters.feedback_limit_tolerance_deg)
            check_cancelled()
            sent = True  # A lost acknowledgement may still mean the bridge received the command.
            previous_stamp = raw["joint_timestamps_ns"][side]
            last_command = time.monotonic()
            emit("command_send", command_positions=command)
            bridge.action(command)
            final_command_sent = elapsed >= timeline[-1]
            emit("command_ack", command_positions=command)
            time.sleep(.02)
            positions, raw = bridge.observation()
    except BaseException as error:
        emit("failed", error=str(error), error_type=type(error).__name__, positions=positions,
             goal_rad=goal.tolist(), tracking_error_rad=tracking)
        raise
    finally:
        if sent:
            try:
                positions, _ = bridge.observation()
                command = current_state_command(positions,
                                                feedback_limit_tolerance_deg=parameters.feedback_limit_tolerance_deg)
                bridge.action(command)
                emit("hold_ack", command_positions=command)
                print("已通过现有桥发送当前状态保持指令；桥不提供停止确认。", file=sys.stderr)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                emit("hold_failed", error=str(error))
                print("无法确认保持指令；请使用机器人现场停止控制。", file=sys.stderr)


def reviewed_plan(config, chain, saved, positions):
    """Reconstruct a reviewed trajectory, rejecting changed inputs or robot start."""
    initial = saved["start_positions"]
    for side in SIDES:
        before = vector(initial[side], 7, "reviewed start")
        now = vector(positions[side], 7, "current start")
        if np.max(np.abs(now[:6] - before[:6])) > .005 or abs(gripper_hold_value(now[6]) - gripper_hold_value(before[6])) > .001:
            raise ValueError("Robot moved since webpage planning; refresh image and calculate again")
    rebuilt = make_plan(config, chain, initial)
    # Execute a freshly reconstructed plan, never arbitrary joint arrays from a file.
    if rebuilt != saved:
        raise ValueError("Reviewed plan/configuration changed; calculate again in webpage")
    return rebuilt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, help="offline get_observation JSON; cannot execute")
    parser.add_argument("--output", type=Path, default=Path("workspace/cobotmagic-ik/plan.json"))
    parser.add_argument("--execute", action="store_true", help="send physical robot commands")
    parser.add_argument("--reviewed-plan", type=Path, help="require the exact webpage-reviewed plan and start")
    args = parser.parse_args(argv)
    if args.execute and args.snapshot:
        parser.error("--execute cannot use an offline snapshot")
    return run_plan(args.config, snapshot=args.snapshot, reviewed_path=args.reviewed_plan,
                    output=args.output, execute=args.execute)


def run_plan(config_path, *, snapshot=None, reviewed_path=None,
             output=Path("workspace/cobotmagic-ik/plan.json"), execute=False):
    """Shared reviewed-plan runner. Opening motion transport requires execute=True."""
    if execute and snapshot is not None:
        raise ValueError("Cannot execute using an offline snapshot")
    config_path, output = Path(config_path), Path(output)
    snapshot = Path(snapshot) if snapshot is not None else None
    reviewed_path = Path(reviewed_path) if reviewed_path is not None else None
    bridge = None
    try:
        config, chain = load_setup(config_path.resolve(), preview=not execute)
        if config["preview_missing_fields"]:
            print("只读预览：执行配置缺少 " + ", ".join(config["preview_missing_fields"]), flush=True)
            print(f"参考点：{config['reference_point']}；不会将该假设写回配置。", flush=True)
            if "tcp_bounds_m" in config["preview_missing_fields"]:
                print("未配置 TCP 范围，本次预览不检查该范围；生成轨迹禁止执行。", flush=True)
        bridge = MotionBridge(config, allow_motion=execute)
        if snapshot:
            positions = decode_positions(json.loads(snapshot.read_text()), config["max_feedback_age_s"], arm=config["arm"],
                                         feedback_limit_tolerance_deg=feedback_tolerance(config))
        else:
            positions, _ = bridge.observation()
        plan = (reviewed_plan(config, chain, json.loads(reviewed_path.read_text()), positions)
                if reviewed_path else make_plan(config, chain, positions))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(f"{plan['arm']} 臂 / {plan['frame']} / 参考点 {plan['reference_point']}: {plan['target_tcp_xyz_m']} m")
        print(f"轨迹: {output}，名义用时 {plan['nominal_duration_s']:.1f} 秒；未做碰撞检查。")
        if not execute:
            if plan["preview_only"]:
                print("预览完成，未发送任何动作。补齐执行配置后需重新规划，当前预览不能执行。")
            else:
                print("仅规划完成，未发送任何动作。人工核对模型、TCP、坐标及整条机械臂路径后才使用 --execute。")
            return 0
        if plan["nominal_duration_s"] >= config["max_run_s"]:
            raise ValueError("Plan exceeds maximum run time")
        bridge.close()  # Reconnect for fresh feedback after an arbitrarily long keyboard wait.
        wait_for_space()
        trace_path = output.with_suffix(".trace.jsonl")
        print(f"执行日志：{trace_path}", flush=True)
        with trace_path.open("w") as trace:
            def record(event):
                trace.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
                trace.flush()
            result = execute_plan(bridge, config, chain, plan, on_event=record)
        output.with_suffix(".result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ParseError) as error:
        print(f"拒绝/中止: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("用户取消。", file=sys.stderr)
        return 130
    finally:
        if bridge:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
