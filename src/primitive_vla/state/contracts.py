"""Backend contributions; identity, provenance and receipt time belong to the journal."""

import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from primitive_vla.contracts import Message


class Observation(Message):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    sensor: str = Field(min_length=1)
    captured_at: float | None = Field(default=None, ge=0)
    artifacts: dict[str, str] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class StateClaim(Message):
    subject: str = Field(min_length=1)
    property: str = Field(min_length=1)
    value: Any = None
    status: Literal["known", "unknown"] = "known"
    basis: Literal["observed", "inferred", "predicted"] = "inferred"
    evidence: list[str] = Field(default_factory=list)
    ttl_s: float | None = Field(default=None, gt=0)
    reason: str = ""

    @model_validator(mode="after")
    def meaningful_value(self):
        json.dumps(self.value, allow_nan=False)
        if self.status == "known" and (self.value is None or not self.evidence):
            raise ValueError("Known claims require a value and evidence")
        if self.status == "unknown" and self.value is not None:
            raise ValueError("Unknown claims must not carry a known value")
        return self
