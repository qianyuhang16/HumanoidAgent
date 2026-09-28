"""Read-only ChArUco capture and offline intrinsic / eye-to-hand calibration.

Transforms use T_destination_from_source. Robot frame is the URDF tip (flange),
NOT a guessed gripper TCP. Never imports motion or enabling code.
"""

import asyncio
import base64
from collections import deque
import hashlib
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

import cv2
import numpy as np
import yaml

from primitive_vla.tools import CallContext
from ..bridge import CameraBridge, decode_frames, timestamp
from ..ik.kinematics import SerialIK, vector

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs/calibration.yaml"
ACCEPTANCE_KEYS = ("max_validation_translation_m", "max_validation_rotation_deg")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_config(path, *, arm=None):
    config = yaml.safe_load(Path(path).read_text())
    # The shared file describes calibration, while the selected step chooses the arm.
    # Retain old single-arm config input for previously saved offline workflows.
    config["arm"] = arm if arm is not None else config.get("arm", "right")
    if config["camera"] not in ("front", "left", "right") or config["arm"] not in ("left", "right"):
        raise ValueError("Invalid camera/arm")
    if config["camera"] != "front":
        raise ValueError("This workflow is eye-to-hand: use the FIXED front camera, not a wrist camera")
    if not isinstance(config["host"], str) or not 0 < int(config["port"]) < 65536:
        raise ValueError("Invalid bridge address")
    for section in ("capture", "quality"):
        if any(type(v) not in (int, float) or not np.isfinite(v) or v <= 0
               for v in config[section].values()):
            raise ValueError(f"{section} values must be finite and positive")
    if config["quality"]["min_train"] < 6 or config["quality"]["min_validation"] < 3:
        raise ValueError("Require at least 6 training and 3 independent validation poses")
    urdf = Path(config["urdf"]).expanduser()
    config["urdf"] = str((Path(path).resolve().parent / urdf).resolve())
    Board(config["board"])
    return config


class Board:
    def __init__(self, options):
        self.options = options
        sx, sy = options["squares_x"], options["squares_y"]
        square, marker = options["square_m"], options["marker_m"]
        if type(sx) is not int or type(sy) is not int or not (3 <= sx <= 20 and 3 <= sy <= 20):
            raise ValueError("Board dimensions must be integers from 3 to 20")
        if not (0 < marker < square < .3) or not np.isfinite([marker, square]).all():
            raise ValueError("Invalid metric board dimensions")
        name = options["dictionary"]
        if not name.startswith("DICT_") or not hasattr(cv2.aruco, name):
            raise ValueError("Unknown ArUco dictionary")
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))
        self.board = cv2.aruco.CharucoBoard((sx, sy), square, marker, dictionary)
        self.board.setLegacyPattern(bool(options.get("legacy_pattern", False)))
        self.detector = cv2.aruco.CharucoDetector(self.board)

    def detect(self, jpeg):
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Invalid JPEG")
        corners, ids, _, _ = self.detector.detectBoard(image)
        display = image.copy()
        if ids is not None:
            cv2.aruco.drawDetectedCornersCharuco(display, corners, ids)
        _, encoded = cv2.imencode(".jpg", display)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return {"ids": [] if ids is None else ids.flatten().tolist(),
                "corners": [] if corners is None else corners.reshape(-1, 2).tolist(),
                "image_size": [image.shape[1], image.shape[0]], "blur_variance": blur}, encoded.tobytes()

    def points(self, sample):
        ids = np.asarray(sample["ids"], np.int32)
        corners = np.asarray(sample["corners"], np.float32)
        if len(ids) < 6 or len(set(ids.tolist())) != len(ids) or corners.shape != (len(ids), 2):
            raise ValueError("Invalid/insufficient ChArUco corners")
        points = self.board.getChessboardCorners()
        if np.any(ids < 0) or np.any(ids >= len(points)) or not np.isfinite(corners).all():
            raise ValueError("Invalid ChArUco IDs or image points")
        objects = points[ids]
        if np.linalg.matrix_rank(objects[:, :2] - objects[:, :2].mean(axis=0)) < 2:
            raise ValueError("ChArUco corners are collinear")
        return objects, corners

    def export(self, output):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        sx, sy = self.options["squares_x"], self.options["squares_y"]
        image = self.board.generateImage((sx * 100, sy * 100), marginSize=0, borderBits=1)
        width, height = sx * self.options["square_m"] * 1000, sy * self.options["square_m"] * 1000
        if width > 190 or height > 257:
            raise ValueError("Board too large for this A4 exporter; reduce square size")
        cv2.imwrite(str(output / "board.png"), cv2.copyMakeBorder(image, 50, 50, 50, 50, cv2.BORDER_CONSTANT, value=255))
        # Vector runs, grouped over identical rows. Physical SVG dimensions are in mm.
        elements = []
        y = 0
        while y < len(image):
            end = y + 1
            while end < len(image) and np.array_equal(image[y], image[end]):
                end += 1
            black = image[y] == 0
            edges = np.diff(np.r_[False, black, False].astype(int))
            for left, right in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
                elements.append(f'<rect x="{left}" y="{y}" width="{right-left}" height="{end-y}"/>')
            y = end
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" viewBox="0 0 210 297">'
               '<rect width="210" height="297" fill="white"/>'
               f'<g transform="translate({(210-width)/2},{(297-height)/2}) scale({width/image.shape[1]})" fill="black">'
               + ''.join(elements) + '</g>'
               '<text x="105" y="18" text-anchor="middle" font-family="sans-serif" font-size="4">ChArUco - Print at 100%, no fit-to-page</text>'
               f'<text x="105" y="280" text-anchor="middle" font-family="sans-serif" font-size="3.5">Square: {self.options["square_m"]*1000:g} mm; verify with a ruler</text></svg>')
        (output / "board-a4.svg").write_text(svg)
        write_json(output / "board.json", {**self.options, "opencv_version": cv2.__version__})


def pose_matrix(rotation, translation):
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = rotation, np.asarray(translation).reshape(3)
    if not np.isfinite(matrix).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5) or np.linalg.det(rotation) < .999:
        raise ValueError("Invalid rigid transformation")
    return matrix


def board_pose(board, sample, intrinsics):
    if sample["image_size"] != intrinsics["image_size"]:
        raise ValueError("Image resolution differs from camera intrinsics")
    objects, pixels = board.points(sample)
    k, d = np.asarray(intrinsics["K"], float), np.asarray(intrinsics["D"], float)
    ok, rvec, tvec = cv2.solvePnP(objects, pixels, k, d, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise ValueError("Board pose estimation failed")
    matrix = pose_matrix(cv2.Rodrigues(rvec)[0], tvec)
    if np.any((objects @ matrix[:3, :3].T + matrix[:3, 3])[:, 2] <= 0):
        raise ValueError("Board pose behind camera")
    projected = cv2.projectPoints(objects, rvec, tvec, k, d)[0].reshape(-1, 2)
    error = float(np.sqrt(np.mean(np.sum((projected - pixels) ** 2, axis=1))))
    return matrix, error


def check_intrinsics(value, camera):
    k, d = np.asarray(value["K"], float), np.asarray(value["D"], float)
    if (value.get("accepted") is not True or value.get("camera") != camera or
            value.get("distortion_model") != "opencv_pinhole" or k.shape != (3, 3) or
            not np.isfinite(k).all() or not np.isfinite(d).all() or d.size not in (4, 5, 8, 12, 14)
            or k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1])):
        raise ValueError("Need accepted, matching-camera OpenCV pinhole intrinsics")
    size = value["image_size"]
    if len(size) != 2 or any(type(x) is not int or x <= 0 for x in size):
        raise ValueError("Invalid intrinsic image_size")
    return value


def measured_fk(chain, joints):
    """Evaluate read-only feedback without applying command-space joint limits.

    Range mismatches remain visible diagnostics. Never clamp/wrap measurements:
    changing them would change the pose paired with the captured image.
    """
    q = vector(joints, 6, "measured joint positions (radians)")
    violations = []
    for index, name in enumerate(chain.names):
        excess = max(chain.lower[index] - q[index], q[index] - chain.upper[index], 0.)
        if excess > 1e-7:
            violations.append({"joint": name, "position_rad": float(q[index]),
                "lower_rad": float(chain.lower[index]), "upper_rad": float(chain.upper[index]),
                "position_deg": float(np.rad2deg(q[index])),
                "lower_deg": float(np.rad2deg(chain.lower[index])),
                "upper_deg": float(np.rad2deg(chain.upper[index])),
                "excess_deg": float(np.rad2deg(excess))})
    return chain.fk(q), violations


def limit_warning(violations):
    detail = "; ".join(f"{v['joint']}={v['position_deg']:.2f}°，参考范围 "
                       f"[{v['lower_deg']:.2f}, {v['upper_deg']:.2f}]°，超出 {v['excess_deg']:.2f}°"
                       for v in violations)
    return f"URDF 范围提示：{detail}。只读标定保留原始反馈；需核对模型版本、关节顺序、弧度单位和零位。"


class Dataset:
    def __init__(self, folder, config, phase, intrinsics=None):
        self.folder, self.config, self.phase = Path(folder).resolve(), config, phase
        self.board, self.chain = Board(config["board"]), None
        self.intrinsics = None
        manifest = {"schema_version": 1, "phase": phase, "config": config, "opencv_version": cv2.__version__,
                    "intrinsics": None, "urdf_sha256": None, "robot_control": False}
        if phase == "handeye":
            if intrinsics is None:
                raise ValueError("handeye requires --intrinsics from the intrinsic solve")
            self.intrinsics = check_intrinsics(intrinsics, config["camera"])
            self.chain = SerialIK(config["urdf"], config["base_link"], config["tip_link"],
                                  config["joint_names"], np.eye(4))
            manifest.update(intrinsics=self.intrinsics, urdf_sha256=digest(Path(config["urdf"]).read_bytes()))
        self.folder.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.folder / "manifest.json"
        if self.manifest_path.exists():
            def session_identity(value):
                # Sampling and acceptance limits can change; geometry stays immutable.
                config = {k: v for k, v in value["config"].items() if k != "capture"}
                config["quality"] = {k: v for k, v in config["quality"].items() if k not in ACCEPTANCE_KEYS}
                return {**value, "config": config}
            if session_identity(json.loads(self.manifest_path.read_text())) != session_identity(manifest):
                raise ValueError("Session settings/model/intrinsics changed; choose a NEW --session directory")
        else:
            if any(self.folder.iterdir()):
                raise ValueError("Non-empty session has no manifest; choose a new directory")
            if phase == "handeye":
                shutil.copyfile(config["urdf"], self.folder / "robot.urdf")
            write_json(self.manifest_path, manifest)
        (self.folder / "samples").mkdir(exist_ok=True)
        self.samples = [json.loads(p.read_text()) for p in sorted((self.folder / "samples").glob("*/sample.json"))]

    def save(self, sample, jpeg, split):
        if split not in ("train", "validation"):
            raise ValueError("Invalid sample split")
        if self.samples and sample["image_size"] != self.samples[0]["image_size"]:
            raise ValueError("Image resolution changed; start a new session")
        for previous in self.samples:
            if sample["image_timestamp_ns"] == previous["image_timestamp_ns"]:
                raise ValueError("This frame has already been recorded")
            if self.phase == "handeye" and np.max(np.abs(np.array(sample["joints_rad"]) - previous["joints_rad"])) < .025:
                raise ValueError("Pose duplicates a saved sample; change position AND orientation")
            if self.phase == "intrinsics" and sample["ids"] == previous["ids"]:
                if np.max(np.linalg.norm(np.array(sample["corners"]) - previous["corners"], axis=1)) < 8:
                    raise ValueError("Board view too similar to an existing sample")
        sample = {**sample, "id": uuid4().hex, "split": split, "image_sha256": digest(jpeg),
                  "capture_policy": dict(self.config["capture"])}
        temporary = self.folder / (".pending-" + sample["id"])
        temporary.mkdir()
        try:
            (temporary / "image.jpg").write_bytes(jpeg)
            write_json(temporary / "sample.json", sample)
            temporary.rename(self.folder / "samples" / sample["id"])
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        self.samples.append(sample)
        return sample["id"]


class Collector:
    def __init__(self, dataset):
        self.dataset, self.config = dataset, dataset.config
        self.bridge = CameraBridge(self.config["host"], self.config["port"], connect_timeout_s=1,
                                   request_timeout_s=self.config["capture"]["request_timeout_s"])
        self.history = deque(maxlen=200)
        self.latest = None
        self.message = "等待 ROS 图像与关节反馈；程序只读，不发送动作。"
        self.last_saved = None

    async def poll(self):
        while True:
            try:
                start = time.monotonic()
                reply = await self.bridge.observation(CallContext(uuid4().hex,
                    start + self.config["capture"]["request_timeout_s"]))
                self.ingest(reply, time.monotonic() - start)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.latest = None
                self.history.clear()
                self.message = f"未就绪：{type(error).__name__}: {error}"
            await asyncio.sleep(.15)

    def ingest(self, reply, elapsed):
        limits = self.config["capture"]
        now = time.monotonic()
        _, frames = decode_frames(reply, [self.config["camera"]], limits["max_age_s"], 8 * 1024 * 1024)
        frame = frames[0]
        arm = self.config["arm"]
        stamp = timestamp(reply.get("joint_timestamps_ns", {}).get(arm), "joint timestamp")
        q = vector(reply.get("positions", {}).get(arm), 7, "joint feedback")[:6]
        joint_age = (reply["timestamp_ns"] - stamp) / 1e9
        if min(joint_age, frame.age_ms / 1000) < -.05 or max(joint_age, frame.age_ms / 1000) + elapsed > limits["max_age_s"]:
            raise ValueError("图像或关节反馈过期")
        skew = abs(stamp - frame.timestamp_ns) / 1e9
        if skew > limits["max_skew_s"]:
            raise ValueError(f"图像/关节时间差 {skew:.3f}s 超出采样上限 {limits['max_skew_s']:.3f}s")
        if self.history and (stamp < self.history[-1][1] or frame.timestamp_ns < self.history[-1][2]):
            raise ValueError("ROS timestamp moved backwards; restart capture")
        self.history.append((now, stamp, frame.timestamp_ns, q.copy()))
        while len(self.history) > 1 and self.history[1][0] < now - limits["settle_s"]:
            self.history.popleft()
        sample, overlay = self.dataset.board.detect(frame.jpeg)
        sample.update(image_timestamp_ns=frame.timestamp_ns, joint_timestamp_ns=stamp,
                      bridge_timestamp_ns=reply["timestamp_ns"], timestamp_skew_s=skew,
                      transport_elapsed_s=elapsed, joints_rad=q.tolist(), camera=self.config["camera"], arm=arm)
        reason = None
        if len(sample["ids"]) < limits["min_corners"]:
            reason = "标定板角点不足，请调整位置、光照或检查板规格"
        elif sample["blur_variance"] < limits["min_blur_variance"]:
            reason = "画面清晰度不足，请停稳后重试"
        else:
            self.dataset.board.points(sample)
        if self.dataset.phase == "handeye":
            matrix, violations = measured_fk(self.dataset.chain, q)
            sample["T_base_from_tip"] = matrix.tolist()
            sample["urdf_limit_exceedances"] = violations
            stable = (len(self.history) >= 3 and now - self.history[0][0] >= limits["settle_s"]
                      and stamp > self.history[0][1] and frame.timestamp_ns > self.history[0][2]
                      and np.max(np.ptp(np.array([h[3] for h in self.history]), axis=0)) <= limits["max_joint_motion_rad"])
            if not stable:
                reason = reason or "等待机械臂停稳，且图像与关节反馈持续更新"
            if reason is None:
                target_pose, error = board_pose(self.dataset.board, sample, self.dataset.intrinsics)
                sample.update(T_camera_from_board=target_pose.tolist(), reprojection_px=error)
                if error > self.config["quality"]["max_reprojection_px"]:
                    reason = "标定板重投影误差过大"
        self.latest = {"sample": sample, "jpeg": frame.jpeg, "overlay": overlay,
                       "received": now, "age_s": max(joint_age, frame.age_ms / 1000) + elapsed,
                       "reason": reason}
        self.message = reason or "可以记录：空格 = 训练样本；V = 独立验证样本"
        if sample.get("urdf_limit_exceedances"):
            self.message += "\n" + limit_warning(sample["urdf_limit_exceedances"])

    def record(self, split):
        latest = self.latest
        if latest is None:
            raise ValueError("ROS 未连接或没有有效数据")
        if time.monotonic() - latest["received"] + latest["age_s"] > self.config["capture"]["max_age_s"]:
            raise ValueError("显示帧已过期，请等待更新")
        if latest["reason"]:
            raise ValueError(latest["reason"])
        sample_id = self.dataset.save(latest["sample"], latest["jpeg"], split)
        self.last_saved = sample_id
        return sample_id

    def state(self):
        data = self.latest
        fresh = data is not None and time.monotonic() - data["received"] + data["age_s"] <= self.config["capture"]["max_age_s"]
        return {"phase": self.dataset.phase, "camera": self.config["camera"], "arm": self.config["arm"],
            "message": self.message if fresh or data is None else "显示帧已过期，等待新数据",
            "ready": bool(fresh and data["reason"] is None), "connected": fresh,
            "counts": {split: sum(s["split"] == split for s in self.dataset.samples) for split in ("train", "validation")},
            "image": None if data is None else "data:image/jpeg;base64," + base64.b64encode(data["overlay"]).decode(),
            "sample": None if data is None else data["sample"], "last_saved": self.last_saved}


def diversity(poses):
    vectors = [cv2.Rodrigues(p[:3, :3] @ poses[0][:3, :3].T)[0].reshape(3) for p in poses[1:]]
    singular = np.linalg.svd(np.array(vectors), compute_uv=False)
    if len(singular) < 2 or singular[1] < np.deg2rad(10) or singular[1] / max(singular[0], 1e-9) < .1:
        raise ValueError("Insufficient independent rotations: tilt the board about multiple axes")
    return singular.tolist()


def mean_pose(poses):
    u, _, vt = np.linalg.svd(sum(p[:3, :3] for p in poses))
    rotation = u @ np.diag([1, 1, np.linalg.det(u @ vt)]) @ vt
    return pose_matrix(rotation, np.mean([p[:3, 3] for p in poses], axis=0))


def fit_handeye(base_from_tip, camera_from_board):
    diversity(base_from_tip)
    tip_from_base = [np.linalg.inv(p) for p in base_from_tip]
    # Eye-to-hand: invert robot poses before passing the gripper2base slots.
    # OpenCV's output slots now represent base_from_camera, NOT camera_from_base.
    r, t = cv2.calibrateHandEye([p[:3, :3] for p in tip_from_base], [p[:3, 3] for p in tip_from_base],
        [p[:3, :3] for p in camera_from_board], [p[:3, 3] for p in camera_from_board], method=cv2.CALIB_HAND_EYE_PARK)
    base_from_camera = pose_matrix(r, t)
    tip_from_board = mean_pose([np.linalg.inv(b) @ base_from_camera @ c
                               for b, c in zip(base_from_tip, camera_from_board)])
    return base_from_camera, tip_from_board


def _solve_session(folder, acceptance_limits=None):
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    config, phase = manifest["config"], manifest["phase"]
    board = Board(config["board"])
    samples = []
    for path in sorted((folder / "samples").glob("*/sample.json")):
        sample = json.loads(path.read_text())
        if digest((path.parent / "image.jpg").read_bytes()) != sample["image_sha256"]:
            raise ValueError(f"Image checksum mismatch: {sample['id']}")
        board.points(sample)
        samples.append(sample)
    training = [s for s in samples if s["split"] == "train"]
    validation = [s for s in samples if s["split"] == "validation"]
    quality = dict(config["quality"])
    if acceptance_limits is not None:
        if (set(acceptance_limits) - set(ACCEPTANCE_KEYS) or
                any(type(v) not in (int, float) or not np.isfinite(v) or v <= 0
                    for v in acceptance_limits.values())):
            raise ValueError("Invalid calibration acceptance limits")
        quality.update(acceptance_limits)
    if len(training) < quality["min_train"] or len(validation) < quality["min_validation"]:
        raise ValueError(f"Need {quality['min_train']} training + {quality['min_validation']} validation samples; have {len(training)} + {len(validation)}")
    sizes = {tuple(s["image_size"]) for s in samples}
    if len(sizes) != 1:
        raise ValueError("Mixed image resolutions")
    result = {"schema_version": 1, "phase": phase, "camera": config["camera"], "arm": config["arm"],
              "board": config["board"], "opencv_version": cv2.__version__,
              "training_ids": [s["id"] for s in training], "validation_ids": [s["id"] for s in validation],
              "physical_accuracy_verified": False, "thresholds": quality}
    result["accepted_meaning"] = "Passes configured internal residual thresholds; physical robot accuracy is NOT verified"
    if phase == "intrinsics":
        matched = [board.points(s) for s in training]
        rms, k, d, _, _ = cv2.calibrateCamera([p[0] for p in matched], [p[1] for p in matched],
                                             tuple(training[0]["image_size"]), None, None)
        result.update(K=k.tolist(), D=d.flatten().tolist(), image_size=training[0]["image_size"],
                      distortion_model="opencv_pinhole", training_rms_px=float(rms))
        errors = [board_pose(board, s, result)[1] for s in validation]
        result.update(validation_reprojection_px=errors,
                      accepted=bool(np.isfinite(k).all() and np.isfinite(d).all() and k[0, 0] > 0 and k[1, 1] > 0
                                    and rms <= quality["max_reprojection_px"] and max(errors) <= quality["max_reprojection_px"]))
    else:
        if digest((folder / "robot.urdf").read_bytes()) != manifest["urdf_sha256"]:
            raise ValueError("Saved URDF checksum mismatch")
        intrinsics = check_intrinsics(manifest["intrinsics"], config["camera"])
        chain = SerialIK(folder / "robot.urdf", config["base_link"], config["tip_link"], config["joint_names"], np.eye(4))
        limit_violations = []
        def matrices(rows):
            base, camera, errors = [], [], []
            for row in rows:
                pose, violations = measured_fk(chain, row["joints_rad"])
                base.append(pose)
                if violations:
                    limit_violations.append({"sample_id": row["id"], "split": row["split"],
                                             "joints": violations})
                target, error = board_pose(board, row, intrinsics)
                camera.append(target)
                errors.append(error)
            return base, camera, errors
        bg, ct, train_errors = matrices(training)
        vb, vc, validation_errors = matrices(validation)
        result["urdf_limit_exceedances"] = limit_violations
        result["warnings"] = ([f"{len(limit_violations)} 个样本的实测关节角超出参考 URDF 范围；"
                               "已按原始反馈计算，详见 urdf_limit_exceedances。需核对模型与反馈约定；"
                               "内部残差通过不代表模型或实物精度已验证。"] if limit_violations else [])
        if max(train_errors + validation_errors) > quality["max_reprojection_px"]:
            raise ValueError("Board pose reprojection error exceeds threshold")
        bc, gt = fit_handeye(bg, ct)
        def residuals(bases, cameras):
            output = []
            for b, c in zip(bases, cameras):
                predicted, observed = b @ gt, bc @ c
                translation = np.linalg.norm(predicted[:3, 3] - observed[:3, 3])
                angle = np.linalg.norm(cv2.Rodrigues(predicted[:3, :3] @ observed[:3, :3].T)[0])
                output.append({"translation_m": float(translation), "rotation_deg": float(np.rad2deg(angle))})
            return output
        residual, train_residual = residuals(vb, vc), residuals(bg, ct)
        result.update(T_base_from_camera=bc.tolist(), T_tip_from_board=gt.tolist(),
            base_frame=config["base_link"], tip_frame=config["tip_link"], camera_frame=f"{config['camera']}_color_optical",
            camera_frame_source="logical label; bridge does not expose ROS header.frame_id",
            camera_axes="OpenCV optical coordinates: x right, y down, z forward",
            transform_convention="p_base = T_base_from_camera @ p_camera (homogeneous; metres)",
            urdf_sha256=manifest["urdf_sha256"], rotation_diversity=diversity(bg),
            training_residuals=train_residual, validation_residuals=residual,
            accepted=all(r["translation_m"] <= quality["max_validation_translation_m"] and
                         r["rotation_deg"] <= quality["max_validation_rotation_deg"] for r in residual + train_residual))
    write_json(folder / "result.json", result)
    return result


def solve_session(folder, acceptance_limits=None):
    try:
        return _solve_session(folder, acceptance_limits)
    except (ValueError, KeyError, OSError, cv2.error) as error:
        # A failed re-solve must not leave an older accepted calibration in result.json.
        if (Path(folder) / "manifest.json").is_file():
            write_json(Path(folder) / "result.json", {"accepted": False, "error": str(error),
                "physical_accuracy_verified": False})
        raise
