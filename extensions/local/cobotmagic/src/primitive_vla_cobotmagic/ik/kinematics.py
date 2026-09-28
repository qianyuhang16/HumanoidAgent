"""Small URDF serial-chain FK/IK for the manually launched hover experiment.

No ROS/CAN connections, no model-specific geometry, and no collision checking.
Only fixed joints and six bounded revolute joints are supported.
"""

from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def vector(value, size, label):
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError(f"{label}: expected {size} finite numbers")
    return result


def check_joint_limits(q, lower, upper, names, *, tolerance_rad=1e-7, label="joint positions"):
    """Validate without changing feedback; command projection is a separate step."""
    q = vector(q, 6, label)
    if not np.isfinite(tolerance_rad) or tolerance_rad < 0:
        raise ValueError("Invalid joint-limit tolerance")
    excess = np.maximum(np.maximum(lower-q, q-upper), 0.)
    if np.any(excess > tolerance_rad):
        detail = "; ".join(
            f"{names[i]}={np.rad2deg(q[i]):.3f}°, limit "
            f"[{np.rad2deg(lower[i]):.3f}, {np.rad2deg(upper[i]):.3f}]°, "
            f"excess {np.rad2deg(excess[i]):.3f}°"
            for i in np.flatnonzero(excess > tolerance_rad))
        raise ValueError(f"{label} exceeds configured/URDF limits: {detail}")
    return q


def rotation(axis, angle):
    x, y, z = axis
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + np.sin(angle) * skew + (1 - np.cos(angle)) * skew @ skew


def transform(xyz, rpy):
    result = np.eye(4)
    result[:3, 3] = vector(xyz, 3, "xyz")
    roll, pitch, yaw = vector(rpy, 3, "rpy")
    result[:3, :3] = (rotation([0, 0, 1], yaw) @ rotation([0, 1, 0], pitch)
                       @ rotation([1, 0, 0], roll))
    return result


def rotation_error(target, actual):
    matrix = target @ actual.T
    angle = np.arccos(np.clip((np.trace(matrix) - 1) / 2, -1, 1))
    skew = np.array([matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0],
                     matrix[1, 0] - matrix[0, 1]])
    if angle < 1e-6:
        return skew / 2
    if angle > np.pi - 1e-4:
        # Eigenvector handles the zero-skew case at exactly 180 degrees.
        _, axes = np.linalg.eigh((matrix + matrix.T) / 2)
        axis = axes[:, -1]
        if axis @ skew < 0:
            axis = -axis
        return angle * axis
    return angle * skew / (2 * np.sin(angle))


def downward_cone_error(axis, max_tilt, min_tilt=0.):
    """Smallest rotation bringing an approach axis inside a cone about base -Z."""
    down = np.array([0., 0., -1.])
    tilt = np.arccos(np.clip(axis @ down, -1, 1))
    if min_tilt <= tilt <= max_tilt:
        return np.zeros(3)
    cross = np.cross(axis, down)
    if np.linalg.norm(cross) < 1e-8:
        cross = np.cross(axis, [1., 0., 0.])
    nearest = min_tilt if tilt < min_tilt else max_tilt
    return cross / np.linalg.norm(cross) * (tilt - nearest)


class SerialIK:
    def __init__(self, urdf, base, tip, names, tcp):
        root = ET.parse(Path(urdf)).getroot()
        parents = {}
        for joint in root.findall("joint"):
            child = joint.find("child").attrib["link"]
            if child in parents:
                raise ValueError(f"URDF has duplicate parents for {child}")
            parents[child] = joint
        chain, seen = [], set()
        while tip != base:
            if tip in seen or tip not in parents:
                raise ValueError("URDF base/tip do not form a serial chain")
            seen.add(tip)
            joint = parents[tip]
            chain.append(joint)
            tip = joint.find("parent").attrib["link"]
        chain.reverse()
        actual = [j.attrib["name"] for j in chain if j.attrib["type"] != "fixed"]
        if len(names) != 6 or len(set(names)) != 6 or set(actual) != set(names):
            raise ValueError("Chain must contain exactly the six configured bridge joints")
        self.names, self.tcp, self.chain = list(names), tcp, []
        self.lower, self.upper = np.zeros(6), np.zeros(6)
        self.velocity = np.zeros(6)
        for joint in chain:
            origin = joint.find("origin")
            attrs = origin.attrib if origin is not None else {}
            pose = transform([float(x) for x in attrs.get("xyz", "0 0 0").split()],
                             [float(x) for x in attrs.get("rpy", "0 0 0").split()])
            if joint.attrib["type"] == "fixed":
                self.chain.append((pose, None, None))
                continue
            if joint.attrib["type"] != "revolute" or joint.find("mimic") is not None:
                raise ValueError("Only independent bounded revolute joints are supported")
            index = names.index(joint.attrib["name"])
            limit = joint.find("limit").attrib
            self.lower[index], self.upper[index] = float(limit["lower"]), float(limit["upper"])
            self.velocity[index] = float(limit["velocity"])
            node = joint.find("axis")
            axis = vector([float(x) for x in (node.attrib.get("xyz", "1 0 0")
                          if node is not None else "1 0 0").split()], 3, "axis")
            if np.linalg.norm(axis) < 1e-9:
                raise ValueError("Zero URDF joint axis")
            self.chain.append((pose, index, axis / np.linalg.norm(axis)))
        if (not np.isfinite([self.lower, self.upper, self.velocity]).all()
                or np.any(self.lower >= self.upper) or np.any(self.velocity <= 0)):
            raise ValueError("Invalid URDF joint limits")

    def check(self, q):
        return check_joint_limits(q, self.lower, self.upper, self.names)

    def check_feedback(self, q, tolerance_rad):
        return check_joint_limits(q, self.lower, self.upper, self.names,
                                  tolerance_rad=tolerance_rad, label="measured joints")

    def fk(self, q):
        pose = np.eye(4)
        for origin, index, axis in self.chain:
            pose = pose @ origin
            if index is not None:
                motion = np.eye(4)
                motion[:3, :3] = rotation(axis, q[index])
                pose = pose @ motion
        return pose @ self.tcp

    def approach_axis(self, pose):
        # The Piper gripper extends along link6 +Z. TCP calibration changes
        # the reference point, not the physical approach direction.
        return pose[:3, :3] @ self.tcp[:3, :3].T[:, 2]

    def downward_tilt(self, pose):
        return float(np.arccos(np.clip(-self.approach_axis(pose)[2], -1, 1)))

    def jaw_heading(self, pose):
        # Piper fingers translate along +/- link6 Y (joint7/8 origins rotate
        # their local Z axes by 90 degrees). Use that physical line, not TCP yaw.
        jaw = pose[:3, :3] @ self.tcp[:3, :3].T[:, 1]
        if np.linalg.norm(jaw[:2]) < 1e-4:
            raise ValueError("两指连线接近竖直，无法定义水平朝向，请先摆正夹爪")
        return float(np.arctan2(jaw[1], jaw[0]))

    def jaw_heading_error(self, pose, heading):
        delta = heading - self.jaw_heading(pose)
        return float(np.arctan2(np.sin(delta), np.cos(delta)))

    def solve(self, target, seed, *, position_only=False, downward_cone_rad=None, jaw_heading_rad=None,
              downward_min_tilt_rad=0., jaw_heading_tolerance_rad=.025):
        if downward_cone_rad is not None:
            if position_only or not np.isfinite(downward_cone_rad) or not 0 <= downward_cone_rad <= np.pi:
                raise ValueError("Invalid downward cone constraint")
            if not np.isfinite(downward_min_tilt_rad) or not 0 <= downward_min_tilt_rad <= downward_cone_rad:
                raise ValueError("Invalid downward angle band")
        if not np.isfinite(jaw_heading_tolerance_rad) or jaw_heading_tolerance_rad <= 0:
            raise ValueError("Invalid jaw heading tolerance")
        if jaw_heading_rad is not None and (downward_cone_rad is None or not np.isfinite(jaw_heading_rad)):
            raise ValueError("Jaw heading requires a finite angle and downward cone")
        def angular_error(pose):
            error = (rotation_error(target[:3, :3], pose[:3, :3]) if downward_cone_rad is None else
                     downward_cone_error(self.approach_axis(pose), downward_cone_rad, downward_min_tilt_rad))
            if jaw_heading_rad is not None:
                error = np.r_[error, self.jaw_heading_error(pose, jaw_heading_rad)]
            return error
        q = self.check(seed).copy()
        position_mm, angle_deg = float('inf'), float('inf')
        for _ in range(250):
            pose = self.fk(q)
            position = target[:3, 3] - pose[:3, 3]
            angular = angular_error(pose)
            position_mm = float(np.linalg.norm(position) * 1000)
            angle_deg = float(np.rad2deg(np.linalg.norm(angular)))
            angular_tolerance = .0001 if downward_cone_rad is not None else .015
            # Stay inside the planner's 2-degree finger-heading envelope with
            # interpolation margin. Forcing 0.29 degrees near a wrist singularity
            # can turn harmless heading error into large counter-rotating joints.
            heading_ok = jaw_heading_rad is None or abs(angular[3]) < jaw_heading_tolerance_rad
            if np.linalg.norm(position) < .001 and heading_ok and (position_only or np.linalg.norm(angular[:3]) < angular_tolerance):
                return self.check(q)
            # Numerical spatial Jacobian; metre and radian residuals are weighted.
            jacobian = np.empty((3 + len(angular), 6))
            for j in range(6):
                shifted = q.copy()
                shifted[j] += 1e-6
                next_pose = self.fk(shifted)
                jacobian[:3, j] = (next_pose[:3, 3] - pose[:3, 3]) / 1e-6
                jacobian[3:, j] = (.2 * rotation_error(next_pose[:3, :3], pose[:3, :3]) / 1e-6
                                    if downward_cone_rad is None else
                                    .2 * (angular - angular_error(next_pose)) / 1e-6)
            error = np.r_[position, .2 * angular]
            if position_only:
                jacobian, error = jacobian[:3], position
            # Solve the damped least-squares step *inside* the feasible box.
            # Clipping an unconstrained step afterwards can stall at joint limits.
            hessian = jacobian.T @ jacobian + 1e-5 * np.eye(6)
            gradient = jacobian.T @ error
            lower = np.maximum(self.lower - q, -.08)
            upper = np.minimum(self.upper - q, .08)
            delta = np.zeros(6)
            for _ in range(100):
                previous = delta.copy()
                for j in range(6):
                    delta[j] = np.clip(delta[j] + (gradient[j] - hessian[j] @ delta) / hessian[j, j],
                                       lower[j], upper[j])
                if np.max(np.abs(delta - previous)) < 1e-8:
                    break
            improved = False
            for scale in (1, .5, .25, .1):
                candidate = np.clip(q + scale * delta, self.lower, self.upper)
                next_pose = self.fk(candidate)
                residual = np.r_[target[:3, 3] - next_pose[:3, 3],
                    .2 * angular_error(next_pose)]
                if position_only:
                    residual = residual[:3]
                if np.linalg.norm(residual) < np.linalg.norm(error):
                    q, improved = candidate, True
                    break
            if not improved:
                break
        detail = f"position residual {position_mm:.1f} mm"
        if not position_only:
            detail += f", orientation residual {angle_deg:.1f} deg"
        raise ValueError(f"IK did not converge locally ({detail}); this is not proof of unreachability")
