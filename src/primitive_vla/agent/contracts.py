"""Model-directed Agent messages, independent of scene predicates and robot shape."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from primitive_vla.tools.contracts import Message, ToolCall, ToolDefinition


class FinalAnswer(Message):
    status: Literal["completed", "blocked", "failed"]
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class ModelReply(Message):
    calls: list[ToolCall] = Field(default_factory=list, max_length=16)
    final: FinalAnswer | None = None
    memory: str | None = None

    @model_validator(mode="after")
    def one_kind(self):
        if bool(self.calls) == (self.final is not None):
            raise ValueError("Return tool calls OR a final answer")
        if len({call.id for call in self.calls}) != len(self.calls):
            raise ValueError("Tool call IDs must be unique within a turn")
        return self


@dataclass(frozen=True)
class ModelContext:
    task: str
    instructions: tuple[str, ...]
    history: tuple[dict[str, Any], ...]
    history_count: int
    working_memory: str
    images: dict[str, str]
    remaining_turns: int
    remaining_calls: int
    remaining_seconds: float
    metadata: dict[str, Any]


class Model(Protocol):
    async def step(self, context: ModelContext, tools: tuple[ToolDefinition, ...]) -> ModelReply: ...
    async def close(self) -> None: ...


class FinishCheck(Protocol):
    async def check(self, answer: FinalAnswer, context: ModelContext) -> str | None:
        """Return a rejection reason, or None to accept."""
        ...


@dataclass
class AgentContext:
    model: Model
    tools: Any
    session: Any
    log: Any
    finish_check: FinishCheck | None = None


class AgentReport(Message):
    status: Literal["completed", "blocked", "failed", "stopped", "cancelled"]
    reason: str
    turns: int = 0
    steps: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    control_locked: bool = False
