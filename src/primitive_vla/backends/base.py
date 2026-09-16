"""Contract implemented by observation and action backends."""

from typing import Callable, Protocol

from primitive_vla.domain.models import ExecutionStatus, Observation, SkillCall, SkillFeedback
from primitive_vla.domain.skills import Candidate
from primitive_vla.execution.control import ControlAuthority, Lease


class Backend(Protocol):
    model_version: str

    async def observe(self) -> Observation: ...

    async def prepare(self, call: SkillCall, candidate: Candidate, lease: Lease, authority: ControlAuthority) -> None:
        """Local-interaction entry transfer only; never called for atomic skills."""
        ...

    async def run(self, call: SkillCall, candidate: Candidate, lease: Lease, authority: ControlAuthority,
                  feedback: Callable[[SkillFeedback], None]) -> ExecutionStatus: ...

    async def stop(self, call_id: str, reason: str) -> bool:
        """Acknowledge backend-specific safe handoff, without indiscriminate release."""
        ...
