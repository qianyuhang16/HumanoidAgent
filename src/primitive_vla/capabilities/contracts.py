"""State estimators provide claims; the runtime owns adoption and persistence."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from primitive_vla.contracts import Message
from primitive_vla.state.contracts import StateClaim


@dataclass(frozen=True)
class StateRequest:
    subject: str
    property: str
    evidence: tuple[dict[str, Any], ...]


class StateResult(Message):
    status: Literal["ok", "unsupported", "error"] = "ok"
    claims: list[StateClaim] = Field(default_factory=list)
    reason: str = ""
    artifacts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def claims_only_on_success(self):
        if self.status != "ok" and self.claims:
            raise ValueError("Failed or unsupported estimates cannot supply claims")
        if self.status == "ok" and not self.claims:
            raise ValueError("Successful estimates must supply a known or unknown claim")
        return self


class StateBackend(Protocol):
    async def estimate(self, request: StateRequest, context: Any) -> StateResult: ...
