from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from primitive_vla.contracts import Domain, ResourceRef


@dataclass(frozen=True)
class ToolSpec:
    name: str
    domain: Domain
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    provider: str
    supported_robots: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    execution_mode: str = "sync"
    timeout: float = 60.0
    skill_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Verification:
    subject: str
    conclusion: str  # success | failure | uncertain
    reason: str
    evidence: tuple[ResourceRef, ...] = ()
    related_call_id: str | None = None
    failure_type: str | None = None


@dataclass(frozen=True)
class ToolResult:
    payload: dict[str, Any] = field(default_factory=dict)
    resource_refs: tuple[ResourceRef, ...] = ()
    verification: Verification | None = None


@dataclass
class CallContext:
    call_id: str
    session_id: str
    task_id: str
    robot_id: str
    cancel_requested: asyncio.Event
    progress: Callable[[dict[str, Any], tuple[ResourceRef, ...]], Awaitable[None]]
