from dataclasses import asdict, dataclass, field
from typing import Any
import time

from primitive_vla.contracts import new_id


@dataclass(frozen=True)
class Limits:
    max_decisions: int = 100
    max_tool_calls: int = 60
    max_consecutive_errors: int = 5
    max_active_seconds: float = 1800.0
    decision_timeout: float = 90.0
    context_chars: int = 24000

    def __post_init__(self):
        if min(self.max_decisions, self.max_tool_calls, self.max_consecutive_errors,
               self.max_active_seconds, self.decision_timeout) <= 0 or self.context_chars < 2048:
            raise ValueError("budgets must be positive and context_chars must be at least 2048")


@dataclass
class Session:
    goal: str
    robot_id: str
    robot_type: str
    allowed_tools: tuple[str, ...] | None = None
    id: str = field(default_factory=new_id)
    task_id: str = field(default_factory=new_id)
    status: str = "ready"
    decisions: int = 0
    tool_calls: int = 0
    consecutive_errors: int = 0
    active_seconds: float = 0.0
    created_at: float = field(default_factory=time.time)
    question: str | None = None
    response: str | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    last_result_record_id: str | None = None
    subscriptions: list[str] = field(default_factory=list)
    pending_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        allowed = data["allowed_tools"]
        return cls(**{**data, "allowed_tools": tuple(allowed) if allowed is not None else None})
