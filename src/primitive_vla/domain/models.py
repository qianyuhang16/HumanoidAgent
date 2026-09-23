"""Shared messages. No model, simulator or robot SDK dependencies."""

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any
from uuid import uuid4


class Truth(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class ExecutionStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class EffectStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Fact:
    value: Truth
    source: str
    timestamp: float = field(default_factory=time)
    confidence: float = 1.0
    ttl_s: float | None = 5.0

    def current(self, now: float | None = None, confidence_threshold: float = 0.5) -> Truth:
        now = time() if now is None else now
        if self.confidence < confidence_threshold or (self.ttl_s is not None and now - self.timestamp > self.ttl_s):
            return Truth.UNKNOWN
        return self.value


@dataclass(frozen=True)
class SceneObject:
    id: str
    kind: str
    description: str


@dataclass
class Observation:
    facts: dict[str, Fact]
    timestamp: float = field(default_factory=time)
    images: dict[str, str] = field(default_factory=dict)  # Saved frame paths/URIs.
    proprioception: dict[str, Any] = field(default_factory=dict)
    frames: dict[str, str] = field(default_factory=dict)
    sensors: tuple[str, ...] = ()


def skill_signature(name: str, version: str, bindings: dict[str, str]) -> str:
    return f"{name}/{version}:" + ":".join(f"{k}={v}" for k, v in sorted(bindings.items()))


@dataclass(frozen=True)
class SkillCall:
    skill: str
    bindings: dict[str, str]
    state_version: int
    version: str = "1"
    call_id: str = field(default_factory=lambda: uuid4().hex)
    deadline: float = field(default_factory=lambda: time() + 30)

    @property
    def signature(self) -> str:
        return skill_signature(self.skill, self.version, self.bindings)


@dataclass(frozen=True)
class SkillFeedback:
    call_id: str
    generation: int
    phase: str
    control_healthy: bool = True
    evidence: str = ""


@dataclass
class SkillResult:
    call_id: str
    execution: ExecutionStatus
    effect: EffectStatus
    reason: str
    evidence: dict[str, str] = field(default_factory=dict)
    changes: dict[str, str] = field(default_factory=dict)
    needs_observation: bool = False
    # Wall-clock execution start for rejecting pre-action evidence in Verifier.
    started_at: float | None = None


@dataclass(frozen=True)
class Goal:
    id: str
    conditions: dict[str, Truth]
    after: tuple[str, ...] = ()


@dataclass
class Task:
    instruction: str
    goals: list[Goal]
    forbidden_skills: tuple[str, ...] = ()
    max_steps: int = 24
    timeout_s: float = 30.0
    max_observations: int = 2

    def pending(self, values: dict[str, Truth]) -> list[Goal]:
        return [g for g in self.goals if any(values.get(k, Truth.UNKNOWN) != v for k, v in g.conditions.items())]

    def allows(self, effects: dict[str, Truth], values: dict[str, Truth]) -> bool:
        goals = {g.id: g for g in self.goals}
        for goal in self.goals:
            if any(effects.get(k) == v for k, v in goal.conditions.items()):
                for dependency in goal.after:
                    if any(values.get(k, Truth.UNKNOWN) != v for k, v in goals[dependency].conditions.items()):
                        return False
        return True
