import asyncio
from dataclasses import replace

from primitive_vla.contracts import Domain
from primitive_vla.tools.contracts import ToolResult, ToolSpec


class Provider:
    def __init__(self, result=None, *, blocked=False, confirms_stop=True, error=None):
        self.result = result or ToolResult({"value": 1})
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.confirms_stop = confirms_stop
        self.error = error
        self.executions = 0
        self.cancellations = 0

    async def execute(self, arguments, context):
        self.executions += 1
        self.started.set()
        await context.progress({"fraction": 0.5}, ())
        await self.release.wait()
        if self.error:
            raise self.error
        return self.result

    async def cancel(self, context):
        self.cancellations += 1
        return self.confirms_stop


def spec(name="state.inspect", domain=Domain.STATE, **changes):
    return replace(ToolSpec(
        name=name, domain=domain, description="Test capability", provider="tests",
        input_schema={"type": "object", "properties": {"value": {"type": "integer"}},
                      "required": ["value"], "additionalProperties": False},
        output_schema={"type": "object", "properties": {"value": {"type": "integer"}},
                       "required": ["value"]},
    ), **changes)


class ScriptedPlanner:
    """A test driver, not a model and not shipped as a runtime policy."""

    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.contexts = []

    async def decide(self, context):
        self.contexts.append(context)
        return next(self.decisions)
