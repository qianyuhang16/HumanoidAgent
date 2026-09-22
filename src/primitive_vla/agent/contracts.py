"""Stable ports shared by the canonical Agent and all adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, Protocol

from primitive_vla.domain.models import Observation, SkillCall, SkillResult, Task
from primitive_vla.domain.skills import Candidate
from primitive_vla.domain.state import State


SceneCommand = dict[str, Any]
SceneResult = dict[str, Any]
PolicyRequest = dict[str, Any]
ActionChunk = Any


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


# Named contract vocabulary.  The domain message remains the wire shape used
# by existing adapters, while callers depend on these stable names.
ExecutionResult = SkillResult


@dataclass(frozen=True)
class StopResult:
    acknowledged: bool
    reason: str = ""


@dataclass(frozen=True)
class MemorySnapshot:
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=monotonic)


class Observer(Protocol):
    async def observe(self) -> Observation: ...


class DecisionModel(Protocol):
    async def decide(self, context: DecisionContext) -> Decision: ...


class SkillCatalog(Protocol):
    def candidates(self, state: State) -> list[Candidate]: ...
    def resolve(self, call: SkillCall) -> Candidate: ...


class Executor(Protocol):
    async def execute(self, call: SkillCall) -> ExecutionResult: ...
    async def stop(self, call_id: str, reason: str) -> StopResult: ...
    async def close(self) -> None: ...


class Verifier(Protocol):
    async def verify(self, request: VerificationRequest) -> VerificationResult: ...


class TaskCompiler(Protocol):
    async def compile(self, instruction: str, context: "CompileContext") -> Task: ...


@dataclass(frozen=True)
class CompileContext:
    state: State
    skills: tuple[Any, ...] = ()
    task_data: dict[str, Any] | None = None


class Memory(Protocol):
    def read(self) -> MemorySnapshot: ...
    def update(self, event: AgentEvent) -> None: ...


class EventSink(Protocol):
    def write(self, event: str, **payload: Any) -> None: ...


class ScenePort(Protocol):
    async def observe(self) -> Observation: ...
    async def apply(self, command: SceneCommand) -> SceneResult: ...
    async def stop(self, command_id: str, reason: str) -> StopResult: ...
    async def close(self) -> None: ...


class PolicyPort(Protocol):
    async def reset(self) -> None: ...
    async def predict(self, request: PolicyRequest) -> ActionChunk: ...
    async def close(self) -> None: ...


@dataclass
class AgentContext:
    state: State
    observer: Observer
    catalog: SkillCatalog
    decision_model: DecisionModel
    executor: Executor
    verifier: Verifier
    memory: Memory
    log: EventSink


@dataclass(frozen=True)
class AgentReport:
    status: str
    reason: str
    steps: int
    observations: int
    remaining_goals: tuple[str, ...]
    results: tuple[dict[str, Any], ...] = ()
    verification: VerificationResult | None = None
