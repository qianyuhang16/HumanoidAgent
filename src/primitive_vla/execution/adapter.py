"""Executor adapter that hides the legacy control machinery from Agent."""

import asyncio

from primitive_vla.domain.contracts import StopResult
from primitive_vla.execution.executive import Executive


class BackendObserver:
    """Observer adapter that exposes only the observation lifecycle."""

    def __init__(self, backend):
        self.backend = backend

    async def observe(self):
        return await self.backend.observe()

    async def warmup(self):
        warmup = getattr(self.backend, "warmup", None)
        if warmup:
            await warmup()

    async def close(self):
        await self.backend.close()


class ExecutiveExecutor:
    def __init__(self, executive: Executive):
        self.executive = executive

    async def execute(self, call, candidate=None):
        return await self.executive.execute(call)

    async def stop(self, call_id: str, reason: str) -> StopResult:
        entry = self.executive.requests.get(call_id)
        if entry is None:
            return StopResult(False, "Unknown call")
        self.executive.cancel(call_id)
        done, _ = await asyncio.wait({entry[1]}, timeout=self.executive.stop_timeout_s)
        acknowledged = bool(done) and self.executive.control.lease is None
        return StopResult(acknowledged, "" if acknowledged else reason)

    async def close(self) -> None:
        return None


class MockExecutor(ExecutiveExecutor):
    """Named composition adapter for the deterministic Mock scene."""


class SapienExecutor(ExecutiveExecutor):
    """Named composition adapter for the SAPIEN scene and optional policy."""
