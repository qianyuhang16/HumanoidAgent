"""Compatibility messages for the optional symbolic scene tools and rule verifier."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .models import Observation, SkillResult, Task
from .skills import Candidate
from .state import State


class Observer(Protocol):
    async def observe(self) -> Observation: ...


class EventSink(Protocol):
    def write(self, event: str, **payload: Any) -> None: ...


@dataclass(frozen=True)
class StopResult:
    acknowledged: bool
    reason: str = ""


class DecisionKind(str, Enum):
    OBSERVE = "observe"
    EXECUTE = "execute"
    FINISH = "finish"
    STOP = "stop"


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    candidate: Candidate | None = None
    reason: str = ""


@dataclass(frozen=True)
class DecisionContext:
    task: Task
    state: State
    candidates: tuple[Candidate, ...]
    history: tuple[dict[str, Any], ...]
    remaining_steps: int
    remaining_seconds: float
    remaining_observations: int


@dataclass(frozen=True)
class VerificationRequest:
    task: Task
    state: State
    execution: SkillResult | None = None
    candidate: Candidate | None = None
    report: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationResult:
    status: str
    criteria: tuple[dict[str, Any], ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class CompileContext:
    state: State
    skills: tuple[Any, ...] = ()
    task_data: dict[str, Any] | None = None
