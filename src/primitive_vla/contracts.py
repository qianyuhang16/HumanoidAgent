from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4
import time


def new_id() -> str:
    return str(uuid4())


class Domain(str, Enum):
    STATE = "state"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    PLANNING = "planning"


class Failure(str, Enum):
    INTERFACE = "interface_error"
    PRECONDITION = "skill_condition"
    RESOURCE = "resource_conflict"
    PERMISSION = "permission_denied"
    EXECUTION = "execution_error"
    TIMEOUT = "timeout"
    EFFECT = "effect_failed"
    UNCERTAIN = "state_uncertain"


class ToolError(Exception):
    def __init__(self, kind: Failure, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.evidence = evidence or {}

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "message": str(self), "evidence": self.evidence}


@dataclass(frozen=True)
class ResourceRef:
    uri: str
    media_type: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class Record:
    session_id: str
    task_id: str
    source: str
    domain: Domain
    event_type: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    resource_refs: tuple[ResourceRef, ...] = ()
    call_id: str | None = None
    lifetime: str = "short"
    expires_at: float | None = None
    id: str = field(default_factory=new_id)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Record:
        return cls(**{**data, "domain": Domain(data["domain"]),
                      "resource_refs": tuple(ResourceRef(**ref) for ref in data["resource_refs"])})
