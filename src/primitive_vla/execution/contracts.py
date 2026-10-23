"""Physical lifecycle is independent of transport success and semantic completion."""

from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from primitive_vla.contracts import Message


class ExecutionUpdate(Message):
    phase: Literal["accepted", "running", "finished", "stopped", "unknown"] = "unknown"
    control: Literal["held", "released", "unknown"] = "unknown"
    handle: str | None = None
    detail: str = ""

    @model_validator(mode="after")
    def consistent_control(self):
        if self.phase in ("accepted", "running") and self.control == "released":
            raise ValueError("An active execution cannot certify released control")
        return self


class ExecutionBackend(Protocol):
    """Use call_id as a fenced identity, including when start loses its reply."""

    async def start(self, arguments: dict[str, Any], context: Any) -> ExecutionUpdate: ...
    async def inspect(self, call_id: str, context: Any) -> ExecutionUpdate: ...
    async def stop(self, call_id: str, reason: str) -> ExecutionUpdate: ...
