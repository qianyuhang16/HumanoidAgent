from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Policy(Settings):
    checkpoint: str
    description: str
    task: str
    duration_s: float = Field(default=30, gt=0, le=300)
    max_duration_s: float = Field(default=60, gt=0, le=300)

    @model_validator(mode='after')
    def valid(self):
        if self.duration_s > self.max_duration_s:
            raise ValueError('Invalid policy duration')
        return self

class VLASettings(Settings):
    python_executable: str
    source: str
    device: str = 'cuda'
    fps: float = Field(default=20, gt=0, le=100)
    load_timeout_s: float = Field(default=300, gt=0, le=900)
    execution_horizon: int = Field(default=13, ge=1)
    max_guidance_weight: float = Field(default=5, gt=0)
    prefix_attention_schedule: Literal['EXP', 'LINEAR', 'ONES', 'ZEROS'] = 'EXP'
    queue_threshold: int = Field(default=25, ge=1)
    preparation_tolerance_rad: float = Field(default=.05, gt=0, le=.2)
    used_joint_threshold_rad: float = Field(default=.03, gt=0, le=.2)
    used_gripper_threshold_m: float = Field(default=.003, gt=0, le=.02)
    policies: dict[str, Policy] = Field(min_length=1)

class ResetSettings(Settings):
    # Only the selected arm and its gripper reset; None keeps that gripper held.
    joints_rad: dict[Literal['left', 'right'], list[float]] = Field(default_factory=lambda: {'left':[0.]*6,'right':[0.]*6})
    gripper_position_m: dict[Literal['left', 'right'], float | None] = Field(default_factory=lambda: {'left':0.,'right':0.})
    duration_s: float = Field(default=3, gt=0, le=60)
    settle_timeout_s: float = Field(default=10, gt=0, le=60)
    joint_tolerance_rad: float = Field(default=.0872664626, gt=0, le=.1)
    gripper_tolerance_m: float = Field(default=.005, gt=0, le=.02)
    feedback_limit_tolerance_deg: float = Field(default=5., ge=0, le=10)
    fps: float = Field(default=20, gt=0, le=100)

    @model_validator(mode='after')
    def valid(self):
        import math
        if set(self.joints_rad) != {'left','right'}:
            raise ValueError('Reset targets must contain both arms')
        if set(self.gripper_position_m) != {'left','right'}:
            raise ValueError('Reset gripper targets must contain both arms')
        if any(value is not None and (not math.isfinite(value) or not 0<=value<=.08)
               for value in self.gripper_position_m.values()):
            raise ValueError('Reset gripper target must be null (hold) or within [0, 0.08] meters')
        limits=[(-2.6179,2.6179),(0,3.14),(-2.967,0),(-1.745,1.745),(-1.22,1.22),(-2.09439,2.09439)]
        for values in self.joints_rad.values():
            if len(values)!=6 or any(not math.isfinite(v) or not a<=v<=b for v,(a,b) in zip(values,limits)):
                raise ValueError('Reset requires six finite joint targets within bridge limits')
        return self
