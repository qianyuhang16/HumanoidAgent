"""Frozen S2 unknown-time TUM sample contract; S1 remains unchanged."""

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from core.stage_contracts import StageObject, StageSnapshot


NOMINAL_TUM_K = [[525.0, 0.0, 319.5], [0.0, 525.0, 239.5], [0.0, 0.0, 1.0]]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Stage2Snapshot(_StrictModel):
    schema_version: Literal["stage2.v1"]
    dataset_id: StrictStr
    session_id: StrictStr
    map_version: StrictStr
    region_id: StrictStr
    observation_id: StrictStr
    observed_at_ns: None = Field(...)
    coordinate_frame: Literal["camera_optical"]
    length_unit: Literal["m"]
    source_kind: Literal["real_cached_mask"]
    identity_scope: Literal["frame_local"]
    status: Literal["ok", "empty", "no_valid_depth"]
    calibration_id: StrictStr | None = Field(...)
    pose_source: None = Field(...)
    objects: list[StageObject]
    time_basis: Literal["unknown"]
    timestamp_source: StrictStr
    calibration_status: Literal["nominal_reference"]
    calibration_source: StrictStr
    annotation_source: Literal["human_polygon"]

    @field_validator("dataset_id", "session_id", "map_version", "region_id", "observation_id",
                     "timestamp_source", "calibration_source")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be nonempty")
        return value

    @field_validator("calibration_id")
    @classmethod
    def optional_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("calibration_id must be nonempty when provided")
        return value

    @model_validator(mode="after")
    def check_status_and_ids(self) -> "Stage2Snapshot":
        if (self.status == "ok") != bool(self.objects):
            raise ValueError("ok requires objects; empty/no_valid_depth require none")
        ids = [obj.object_id for obj in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("object_id must be unique within snapshot")
        prefix = f"{self.session_id}/{self.observation_id}/"
        if any(not item.startswith(prefix) or not item[len(prefix):].strip() or "/" in item[len(prefix):]
               for item in ids):
            raise ValueError("object_id must be session_id/observation_id/local_id")
        return self


class Stage2Manifest(_StrictModel):
    schema_version: Literal["stage2.input.v1"]
    dataset_id: StrictStr
    session_id: StrictStr
    map_version: StrictStr
    region_id: StrictStr
    observation_id: StrictStr
    rgb_path: StrictStr
    depth_path: StrictStr
    mask_path: StrictStr
    annotations_path: StrictStr
    calibration_id: StrictStr
    timestamp_source: StrictStr
    calibration_source: StrictStr
    registration_source: StrictStr
    observed_at_ns: None = Field(...)
    time_basis: Literal["unknown"]
    calibration_status: Literal["nominal_reference"]
    annotation_source: Literal["human_polygon"]
    source_kind: Literal["real_cached_mask"]
    rgb_depth_registered: Literal[True]
    K: list[list[float]] = Field(default_factory=lambda: [row.copy() for row in NOMINAL_TUM_K])
    depth_encoding: Literal["tum_u16_5000_per_m"]
    depth_scale_m: Literal[0.0002]

    @field_validator("dataset_id", "session_id", "map_version", "region_id", "observation_id",
                     "rgb_path", "depth_path", "mask_path", "annotations_path", "calibration_id",
                     "timestamp_source", "calibration_source", "registration_source")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be nonempty")
        return value

    @field_validator("K")
    @classmethod
    def valid_k(cls, value: list[list[float]]) -> list[list[float]]:
        try:
            k = np.asarray(value, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError("K must be numeric") from exc
        if k.shape != (3, 3) or not np.isfinite(k).all():
            raise ValueError("K must be finite 3x3")
        if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]):
            raise ValueError("K has invalid focal lengths or final row")
        if not np.isclose(k[0, 1], 0) or not np.isclose(k[1, 0], 0):
            raise ValueError("K must have zero skew")
        return value


def validate_stage_snapshot(payload: dict | StageSnapshot | Stage2Snapshot) -> StageSnapshot | Stage2Snapshot:
    """Validate either frozen delivery version without changing its semantics."""
    if isinstance(payload, StageSnapshot):
        return StageSnapshot.model_validate(payload)
    if isinstance(payload, Stage2Snapshot):
        return Stage2Snapshot.model_validate(payload)
    if not isinstance(payload, dict):
        raise TypeError("stage snapshot must be a dict or a validated model")
    version = payload.get("schema_version")
    if version == "stage1.v1":
        return StageSnapshot.model_validate(payload)
    if version == "stage2.v1":
        return Stage2Snapshot.model_validate(payload)
    raise ValueError(f"unsupported stage schema_version: {version!r}")
