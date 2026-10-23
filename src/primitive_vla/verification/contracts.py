"""Judgments and evidence shared by the main Agent and optional reviewers."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from primitive_vla.contracts import Message


class OutcomeAssessment(Message):
    verdict: Literal["satisfied", "unsatisfied", "unknown"]
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    goal: str | None = Field(default=None, min_length=1)
    goal_id: str | None = Field(default=None, min_length=1)


class ActionAssessment(OutcomeAssessment):
    call_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def has_target(self):
        if not self.call_id and not self.goal_id:
            raise ValueError("Assessments need call_id or goal_id")
        return self


class AssessmentResult(Message):
    status: Literal["ok", "unsupported", "error"]
    assessment: OutcomeAssessment | None = None
    reason: str = ""

    @model_validator(mode="after")
    def has_assessment(self):
        if (self.status == "ok") != (self.assessment is not None):
            raise ValueError("Only successful assessment runs carry a judgment")
        return self


class GoalSpec(Message):
    description: str = Field(min_length=1)
    subject: str | None = None
    max_age_s: float = Field(default=30.0, gt=0)


class VerificationPolicy(Message):
    goals: dict[str, GoalSpec] = Field(default_factory=dict)
    before_tools: dict[str, list[str]] = Field(default_factory=dict)
    completion: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def known_goals(self):
        references = set(self.completion)
        for goals in self.before_tools.values():
            references.update(goals)
        if not references <= self.goals.keys():
            raise ValueError("Verification requirements must reference declared goals")
        return self


@dataclass(frozen=True)
class OutcomeContext:
    task: str
    instructions: tuple[str, ...]
    call: dict[str, Any]
    tool_description: str
    result: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    images: dict[str, str]
    goal_id: str | None = None
    goal: str | None = None


class OutcomeReviewer(Protocol):
    async def assess(self, context: OutcomeContext) -> OutcomeAssessment: ...
    async def close(self) -> None: ...
