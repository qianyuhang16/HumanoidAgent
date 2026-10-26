"""State estimation interfaces; backends return contributions without modifying the journal."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from primitive_vla.contracts import Message
from primitive_vla.media import Artifacts
from primitive_vla.state.contracts import StateClaim


@dataclass(frozen=True)
class StateRequest:
    subject: str
    property: str
    evidence: tuple[dict[str, Any], ...]

    @property
    def artifacts(self) -> Artifacts:
        """Typed resources keyed by evidence ID and slot; no loading or copying blobs."""
        resources = {}
        for record in self.evidence:
            payload = record["payload"]
            resources.update({f"{record['id']}:{name}": value
                              for name, value in (payload.get("artifacts") or {}).items()})
            for observation in payload.get("observations", []):
                resources.update({f"{observation['id']}:{name}": value
                                  for name, value in (observation.get("artifacts") or {}).items()})
        return TypeAdapter(Artifacts).validate_python(resources)


class StateResult(Message):
    status: Literal["ok", "unsupported", "error"] = "ok"
    claims: list[StateClaim] = Field(default_factory=list)
    reason: str = ""
    artifacts: Artifacts = Field(default_factory=dict)
    data: JsonValue = None

    @model_validator(mode="after")
    def claims_only_on_success(self):
        if self.status != "ok" and self.claims:
            raise ValueError("Failed or unsupported estimates cannot supply claims")
        if (self.status == "ok" and not self.claims and self.data is None
                and not any(value is not None for value in self.artifacts.values())):
            raise ValueError("Successful estimates must supply claims, data or available artifacts")
        return self


class StateBackend(Protocol):
    async def estimate(self, request: StateRequest, context: Any) -> StateResult: ...
