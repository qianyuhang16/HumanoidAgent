"""Tools describe their own capabilities; the runtime knows no robot vocabulary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Awaitable, Callable, Literal, Protocol
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from primitive_vla.contracts import Message
from primitive_vla.media import Artifacts, image_refs
from primitive_vla.state.contracts import Observation, StateClaim
from primitive_vla.execution.contracts import ExecutionUpdate


class ToolDefinition(Message):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
    description: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False})
    output_schema: dict[str, Any] | None = None
    read_only: bool = True
    resources: list[str] = Field(default_factory=list)
    timeout_s: float = Field(default=30.0, gt=0)
    # Request an outcome record; independent mode also calls its reviewer.
    review_outcome: bool = False


class ToolCall(Message):
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)


class ToolResult(Message):
    status: Literal["ok", "error", "rejected", "timed_out", "cancelled"] = "ok"
    data: JsonValue = Field(default_factory=dict)
    artifacts: Artifacts = Field(default_factory=dict)
    text: str = ""
    images: dict[str, str] = Field(default_factory=dict)
    before_images: dict[str, str] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    claims: list[StateClaim] = Field(default_factory=list)
    execution: ExecutionUpdate | None = None
    # False means the provider cannot certify that control has stopped.
    control_released: bool = True

    @model_validator(mode="after")
    def project_images(self):
        # Existing transports consume images. Preserve explicit labels and avoid
        # sending a resource twice when an adapter also supplies legacy images.
        candidates = {f"artifact:{name}": uri for name, uri in image_refs(self.artifacts).items()}
        for observation in self.observations:
            candidates.update({f"{observation.id}:{name}": uri
                               for name, uri in image_refs(observation.artifacts).items()})
        for name, uri in candidates.items():
            if name not in self.images and uri not in self.images.values():
                self.images[name] = uri
        return self


@dataclass(frozen=True)
class PluginContext:
    workspace: Path
    config_dir: Path
    log: Any
    api_key: str | None = field(default=None, repr=False)
    transport: Any = field(default=None, repr=False)


@dataclass
class CallContext:
    call_id: str
    deadline: float
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)

    def check_active(self) -> None:
        """Call immediately before dispatch; remote drivers must fence too."""
        if self.cancelled.is_set() or monotonic() >= self.deadline:
            raise asyncio.CancelledError()


class Tool(Protocol):
    definition: ToolDefinition

    async def call(self, arguments: dict, context: CallContext) -> ToolResult: ...
    async def stop(self, call_id: str, reason: str) -> bool: ...


class FunctionTool:
    """Wrap an async function without a framework-specific subclass."""

    def __init__(self, definition: ToolDefinition,
                 function: Callable[[dict, CallContext], Awaitable[ToolResult]],
                 stop: Callable[[str, str], Awaitable[bool]] | None = None):
        if not definition.read_only and stop is None:
            raise ValueError(f"{definition.name}: action tools need an acknowledged stop function")
        self.definition, self.function, self.stop_function = definition, function, stop

    async def call(self, arguments, context):
        return await self.function(arguments, context)

    async def stop(self, call_id, reason):
        return True if self.stop_function is None else await self.stop_function(call_id, reason)
