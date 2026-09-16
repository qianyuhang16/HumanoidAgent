"""Contract implemented by observation and action backends."""

from typing import Callable, Protocol

from primitive_vla.domain.models import ExecutionStatus, Observation, SkillCall, SkillFeedback
from primitive_vla.domain.skills import Candidate
from primitive_vla.execution.control import ControlAuthority, Lease


class Backend(Protocol):
    model_version: str

    async def observe(self) -> Observation: ...

    async def warmup(self) -> None:
        """Prepare optional model resources; stateless backends do nothing."""
        ...

    async def close(self) -> None:
        """Release task-owned resources; safe to call more than once."""
        ...

    async def run(self, call: SkillCall, candidate: Candidate, lease: Lease, authority: ControlAuthority,
                  feedback: Callable[[SkillFeedback], None]) -> ExecutionStatus: ...

    async def stop(self, call_id: str, reason: str) -> bool:
        """Acknowledge backend-specific safe handoff, without indiscriminate release."""
        ...
