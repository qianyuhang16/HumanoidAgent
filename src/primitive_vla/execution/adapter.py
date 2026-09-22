"""Executor adapter that hides the legacy control machinery from Agent."""

from primitive_vla.agent.contracts import StopResult
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

    async def stop(self, call_id: str, reason: str) -> bool:
        acknowledged = self.executive.cancel(call_id)
        return StopResult(acknowledged, reason if not acknowledged else "")

    async def close(self) -> None:
        return None


class MockExecutor(ExecutiveExecutor):
    """Named composition adapter for the deterministic Mock scene."""


class SapienExecutor(ExecutiveExecutor):
    """Named composition adapter for the SAPIEN scene and optional policy."""
