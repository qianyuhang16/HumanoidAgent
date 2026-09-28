"""Judgments and evidence shared by the main Agent and optional reviewers."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import Field

from primitive_vla.tools.contracts import Message


class OutcomeAssessment(Message):
    verdict: Literal["satisfied", "unsatisfied", "unknown"]
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    goal: str | None = Field(default=None, min_length=1)


class ActionAssessment(OutcomeAssessment):
    call_id: str = Field(min_length=1)


@dataclass(frozen=True)
class OutcomeContext:
    task: str
    instructions: tuple[str, ...]
    call: dict[str, Any]
    tool_description: str
    result: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    images: dict[str, str]


class OutcomeReviewer(Protocol):
    async def assess(self, context: OutcomeContext) -> OutcomeAssessment: ...
    async def close(self) -> None: ...
