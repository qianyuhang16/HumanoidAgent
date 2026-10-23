"""Expose a start/inspect/stop backend through the existing Tool interface."""

import asyncio

from primitive_vla.execution.contracts import ExecutionUpdate
from primitive_vla.tools.contracts import ToolResult


class ExecutionTool:
    def __init__(self, definition, backend, *, poll_s=.1):
        if definition.read_only or not definition.resources:
            raise ValueError("Execution tools require control resources")
        if poll_s <= 0:
            raise ValueError("poll_s must be positive")
        self.definition, self.backend, self.poll_s = definition, backend, poll_s

    async def call(self, arguments, context):
        context.check_active()
        update = ExecutionUpdate.model_validate(await self.backend.start(arguments, context))
        while update.phase in ("accepted", "running"):
            await asyncio.sleep(self.poll_s)
            context.check_active()
            update = ExecutionUpdate.model_validate(await self.backend.inspect(context.call_id, context))
        return ToolResult(status="error" if update.phase == "unknown" else "ok", execution=update,
                          text=update.detail, control_released=update.control == "released")

    async def stop(self, call_id, reason):
        update = ExecutionUpdate.model_validate(await self.backend.stop(call_id, reason))
        return update.phase in ("finished", "stopped") and update.control == "released"
