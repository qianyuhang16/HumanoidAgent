"""Backend contributions; identity, provenance and receipt time belong to the journal."""

from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from primitive_vla.contracts import Message
from primitive_vla.media import Artifacts


class Observation(Message):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    sensor: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    frame: str | None = Field(default=None, min_length=1)
    captured_at: float | None = Field(default=None, ge=0)
    artifacts: Artifacts = Field(default_factory=dict)
    data: JsonValue = Field(default_factory=dict)
    metadata: dict[str, JsonValue] | None = None


class StateClaim(Message):
    subject: str = Field(min_length=1)
    property: str = Field(min_length=1)
    value: JsonValue = None
    status: Literal["known", "partial", "unknown"] = "known"
    basis: Literal["observed", "inferred", "predicted"] = "inferred"
    evidence: list[str] = Field(default_factory=list)
    ttl_s: float | None = Field(default=None, gt=0)
    reason: str | None = None

    @model_validator(mode="after")
    def meaningful_value(self):
        if self.status in ("known", "partial") and ("value" not in self.model_fields_set or not self.evidence):
            raise ValueError("Known and partial claims require an explicit value and evidence")
        if self.status == "partial" and self.value is None:
            raise ValueError("Partial claims require some information; use unknown for a missing value")
        if self.status == "unknown" and self.value is not None:
            raise ValueError("Unknown claims must not carry a known value")
        return self
