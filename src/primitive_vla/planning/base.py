from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class PlanningContext:
    data: dict[str, Any]
    action_schema: dict[str, Any]


@dataclass(frozen=True)
class Decision:
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)


class Planner(Protocol):
    async def decide(self, context: PlanningContext) -> Decision:
        """Choose one next action using a replaceable VLM/LLM adapter.

        No planner implementation, model client, or action policy is bundled with the core.
        """
        ...
