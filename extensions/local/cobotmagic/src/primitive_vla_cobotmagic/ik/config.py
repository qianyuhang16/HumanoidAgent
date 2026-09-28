"""Validated user-facing settings shared by pixel planning and motion execution."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_FEEDBACK_LIMIT_TOLERANCE_DEG = .5


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MotionParameters(SettingsModel):
    move_duration_s: float = Field(default=3., gt=0, le=300)
    downward_angle_range_deg: tuple[float, float] = (40., 90.)
    preserve_jaw_heading: bool = True
    jaw_heading_tolerance_deg: float = Field(default=2., gt=0, lt=90)
    speed_percent: int = Field(default=30, ge=1, le=100, strict=True)
    settle_timeout_s: float = Field(default=3., ge=0, le=300)
    position_tolerance_m: float = Field(default=.008, gt=0, le=.1)
    orientation_tolerance_rad: float = Field(default=.04, gt=0, le=1)
    request_timeout_s: float = Field(default=.20, gt=0, le=.22)
    max_feedback_age_s: float = Field(default=.20, gt=0, le=.25)
    # Encoder feedback can sit just beyond a command limit at the zero stop.
    # This does not expand the limits used for IK or outgoing commands.
    feedback_limit_tolerance_deg: float = Field(default=DEFAULT_FEEDBACK_LIMIT_TOLERANCE_DEG, ge=0, le=2)
    max_run_s: float = Field(default=120., ge=1, le=300)

    @model_validator(mode="after")
    def validate_ranges(self):
        low, high = self.downward_angle_range_deg
        if not 0 <= low < high <= 90:
            raise ValueError("downward_angle_range_deg must satisfy 0 <= min < max <= 90")
        if self.move_duration_s >= self.max_run_s:
            raise ValueError("move_duration_s must be less than max_run_s")
        return self

    @classmethod
    def from_execution_config(cls, config):
        # Saved execution bundles contain robot/target fields as well.
        return cls.model_validate({k: config[k] for k in cls.model_fields if k in config})


class RobotModel(SettingsModel):
    urdf: str
    base_link: str = "base_link"
    tip_link: str = "link6"
    joint_names: list[str] = Field(default_factory=lambda: [f"joint{i}" for i in range(1, 7)], min_length=6, max_length=6)


class PixelIKSettings(SettingsModel):
    host: str
    port: int = Field(ge=1, le=65535)
    calibration_workspace: str
    output_workspace: str
    target_base_z_m: float = .17
    forward_compensation_m: dict[Literal["left", "right"], float] = Field(default_factory=lambda: {"left": .10, "right": .15})
    orientation: Literal["downward", "position", "current", "rpy"] = "downward"
    rpy_deg: tuple[float, float, float] = (0., 0., 0.)
    motion: MotionParameters = Field(default_factory=MotionParameters)
    robot: RobotModel | None = None
    # Compatibility for old locally saved configs; the new ik.yaml is standalone.
    hover_configs: dict[Literal["left", "right"], str] | None = None

    @model_validator(mode="after")
    def validate_robot(self):
        if self.robot is None and self.hover_configs is None:
            raise ValueError("robot model is required")
        if set(self.forward_compensation_m) != {"left", "right"}:
            raise ValueError("forward_compensation_m needs both left and right")
        if any(v < 0 for v in self.forward_compensation_m.values()):
            raise ValueError("forward compensation must be nonnegative")
        return self


def load_pixel_settings(path):
    path = Path(path).resolve()
    seen = set()
    while True:
        if path in seen:
            raise ValueError("Circular IK config redirect")
        seen.add(path)
        raw = yaml.safe_load(path.read_text())
        if isinstance(raw, dict) and set(raw) == {"extends"}:
            path = (path.parent / raw["extends"]).resolve()
            continue
        return path, PixelIKSettings.model_validate(raw)
