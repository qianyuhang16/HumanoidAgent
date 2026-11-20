"""Frozen S1 frame-local observation contract (ARCHITECTURE §8)."""

from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class StageObject(_StrictModel):
    object_id: StrictStr
    label: StrictStr
    bbox_center: tuple[float, float, float] = Field(strict=False)
    bbox_extent: tuple[float, float, float] = Field(strict=False)
    evidence_refs: list[StrictStr] = Field(min_length=1)

    @field_validator("object_id", "label")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a nonempty string")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def evidence_nonblank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence references must be nonempty")
        return values

    @field_validator("bbox_center", "bbox_extent")
    @classmethod
    def finite_vector(cls, values: tuple[float, float, float]) -> tuple[float, float, float]:
        if any(not isfinite(v) for v in values):
            raise ValueError("bbox coordinates must be finite")
        return values

    @field_validator("bbox_extent")
    @classmethod
    def nonnegative_extent(cls, values: tuple[float, float, float]) -> tuple[float, float, float]:
        if any(v < 0 for v in values):
            raise ValueError("bbox extent must be nonnegative full size")
        return values


class StageSnapshot(_StrictModel):
    schema_version: Literal["stage1.v1"]
    dataset_id: StrictStr
    session_id: StrictStr
    map_version: StrictStr
    region_id: StrictStr
    observation_id: StrictStr
    observed_at_ns: int = Field(ge=0)
    coordinate_frame: Literal["camera_optical", "world"]
    length_unit: Literal["m"]
    source_kind: Literal["manual_fixture", "synthetic", "real_cached_mask", "model_prediction"]
    identity_scope: Literal["frame_local"]
    status: Literal["ok", "empty", "no_valid_depth"]
    calibration_id: StrictStr | None = Field(...)
    pose_source: StrictStr | None = Field(...)
    objects: list[StageObject]

    @field_validator("dataset_id", "session_id", "map_version", "region_id", "observation_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a nonempty string")
        return value

    @field_validator("calibration_id", "pose_source")
    @classmethod
    def nullable_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be nonempty when provided")
        return value

    @model_validator(mode="after")
    def consistent(self) -> "StageSnapshot":
        if (self.status == "ok") != bool(self.objects):
            raise ValueError("ok requires objects; empty/no_valid_depth require none")
        if self.coordinate_frame == "world" and (not self.calibration_id or not self.pose_source):
            raise ValueError("world requires calibration_id and pose_source")
        if self.source_kind in ("real_cached_mask", "model_prediction") and self.observed_at_ns == 0:
            raise ValueError("real source requires positive observed_at_ns")
        ids = [obj.object_id for obj in self.objects]
        if len(set(ids)) != len(ids):
            raise ValueError("object_id must be unique within snapshot")
        prefix = f"{self.session_id}/{self.observation_id}/"
        if any(not item.startswith(prefix) or not item[len(prefix):].strip() or "/" in item[len(prefix):]
               for item in ids):
            raise ValueError("object_id must be session_id/observation_id/local_id")
        return self
