"""Symbolic backend for local task execution and fault simulation."""

import asyncio
from time import time
from typing import Callable

from primitive_vla.domain.models import Fact, Truth
from primitive_vla.domain.models import ExecutionStatus, Observation, SkillCall, SkillFeedback
from primitive_vla.domain.skills import Candidate
from primitive_vla.execution.control import ControlAuthority, Lease


class MockBackend:
    """Contract simulator using its own world; State is updated only by observe().

    Faults: one missed grasp; one ambiguous POUR observation; stalled GRASP.
    The simulator applies declared effects and does not establish real capability.
    """

    model_version = "symbolic-mock-v1"

    def __init__(self, initial: dict[str, Truth], fault: str = "none", delay_s: float = 0.01,
                 fact_ttl_s: float = 5.0, images: dict[str, str] | None = None):
        self.world = dict(initial)
        self.fact_ttl_s = fact_ttl_s
        self.images = images or {}
        self.fault = fault
        self.delay_s = delay_s
        self.used_fault = False
        self.hidden_once: set[str] = set()
        self.executions: list[str] = []
        self.stop_calls: list[str] = []

    async def observe(self) -> Observation:
        now = time()
        facts = {k: Fact(Truth.UNKNOWN if k in self.hidden_once else v, "mock:ground_truth", now,
                         ttl_s=None if k.startswith("event:") else self.fact_ttl_s) for k, v in self.world.items()}
        self.hidden_once.clear()
        return Observation(facts, timestamp=now, sensors=("symbolic_ground_truth",), images=dict(self.images))

    async def warmup(self) -> None:
        pass

    async def run(self, call: SkillCall, candidate: Candidate, lease: Lease, authority: ControlAuthority,
                  feedback: Callable[[SkillFeedback], None]) -> ExecutionStatus:
        self.executions.append(call.skill)
        feedback(SkillFeedback(call.call_id, lease.generation, "running"))
        injected = self.fault in candidate.spec.mock_faults
        if injected and self.fault == "timeout":
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay_s)
        if injected and self.fault == "missed_grasp" and not self.used_fault:
            self.used_fault = True
            return ExecutionStatus.COMPLETED  # Motion done, object not grasped.
        if not authority.apply(call.call_id, lease.generation, lambda: self.world.update(candidate.effects)):
            return ExecutionStatus.CANCELLED
        if injected and self.fault == "uncertain_pour" and not self.used_fault:
            self.used_fault = True
            self.hidden_once = {k for k in candidate.effects if k.startswith("event:")}
        feedback(SkillFeedback(call.call_id, lease.generation, "finished", evidence="symbolic transition applied"))
        return ExecutionStatus.COMPLETED

    async def stop(self, call_id: str, reason: str) -> bool:
        self.stop_calls.append(call_id)
        # This mock has no queued hardware commands. Physical adapters must
        # confirm a contact-aware stop before returning True.
        return True

    async def close(self) -> None:
        pass
