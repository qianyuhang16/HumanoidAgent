"""Synthetic execution config for offline tests; independent of user settings."""
from pathlib import Path


def robot_config():
    return {
        "host": "invalid.test", "port": 1, "arm": "right",
        "urdf": str(Path(__file__).resolve().parents[1] / "assets/reference/piper_description.urdf"),
        "base_link": "base_link", "tip_link": "link6",
        "joint_names": [f"joint{i}" for i in range(1, 7)],
        "tcp_calibration_result": None, "tcp_xyz_m": [0, 0, .1], "tcp_rpy_rad": [0, 0, 0],
        "bowl_rim_center_xyz_m": None, "up_in_base": [0, 0, 1],
        "hover_height_m": .1, "target_rpy_rad": None, "via_xyz_m": [],
        "require_tcp_bounds": False, "tcp_bounds_m": None,
        "speed_percent": 30, "max_joint_speed_rad_s": .6,
        "request_timeout_s": .2, "max_feedback_age_s": .2, "max_run_s": 120,
    }
