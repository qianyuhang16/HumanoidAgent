"""Validated dispatch, call deduplication, resource ownership and bounded stopping."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import inspect
from time import monotonic

from jsonschema import Draft202012Validator
from referencing import Registry

from .contracts import CallContext, ToolCall, ToolDefinition, ToolResult


def validator(schema):
    Draft202012Validator.check_schema(schema)
    # Schemas are self-contained; never fetch remote references during dispatch.
    return Draft202012Validator(schema, registry=Registry())


class ToolRuntime:
    def __init__(self, tools=(), *, log=None, denied=(), stop_timeout_s=2.0):
        self.log, self.denied, self.stop_timeout_s = log, frozenset(denied), stop_timeout_s
        self.tools, self.schemas, self.outputs, self.leases = {}, {}, {}, {}
        self.calls, self.active = {}, {}
        self.control_locked = False
        self.closed = False
        for tool in tools:
            self.register(tool)

    def register(self, tool):
        definition = ToolDefinition.model_validate(tool.definition).model_copy(deep=True)
        if definition.name in self.tools:
            raise ValueError(f"Duplicate tool: {definition.name}")
        if not inspect.iscoroutinefunction(getattr(tool, "call", None)):
            raise TypeError(f"{definition.name}: call() must be async")
        if not definition.read_only and (not definition.resources or not inspect.iscoroutinefunction(getattr(tool, "stop", None))):
            raise ValueError(f"{definition.name}: actions require resources and async stop()")
        if definition.parameters.get("type") != "object":
            raise ValueError(f"{definition.name}: parameters must be an object schema")
        if definition.review_outcome and definition.read_only:
            raise ValueError(f"{definition.name}: review_outcome is for action tools")
        self.schemas[definition.name] = validator(definition.parameters)
        self.outputs[definition.name] = validator(definition.output_schema) if definition.output_schema else None
        self.tools[definition.name] = (tool, definition)

    def definitions(self):
        return tuple(d.model_copy(deep=True) for name, (_, d) in self.tools.items() if name not in self.denied)

    def _emit(self, event, **payload):
        if self.log:
            self.log.write(event, **payload)

    async def invoke(self, call: ToolCall, deadline: float | None = None) -> ToolResult:
        call = ToolCall.model_validate(call).model_copy(deep=True)
        previous = self.calls.get(call.id)
        if previous:
            if previous[0] != call:
                return ToolResult(status="rejected", text="Call ID reused with a different payload")
            return (await asyncio.shield(previous[1])).model_copy(deep=True)
        task = asyncio.create_task(self._invoke(call, deadline))
        self.calls[call.id] = (call, task)
        try:
            return (await asyncio.shield(task)).model_copy(deep=True)
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            raise

    async def _invoke(self, call, deadline):
        def reject(reason):
            return ToolResult(status="rejected", text=reason)

        if self.closed:
            return reject("Tool runtime is closed")
        if call.name not in self.tools or call.name in self.denied:
            return reject("Tool is unavailable or not permitted")
        tool, definition = self.tools[call.name]
        try:
            json.dumps(call.arguments, allow_nan=False)
            self.schemas[call.name].validate(call.arguments)
        except Exception as error:
            return reject(f"Invalid arguments: {getattr(error, 'message', str(error))}")
        if not definition.read_only and self.control_locked:
            return reject("Control is locked: a previous action has no confirmed stop")
        if any(resource in self.leases for resource in definition.resources):
            return reject("Requested control resources are occupied")
        deadline = min(float("inf") if deadline is None else deadline, monotonic() + definition.timeout_s)
        if deadline <= monotonic():
            return reject("Call deadline expired")
        context = CallContext(call.id, deadline)
        # Persist intent before creating any task that could dispatch an action.
        self._emit("tool_started", call=call.model_dump(), read_only=definition.read_only)
        for resource in definition.resources:
            self.leases[resource] = call.id
        runner = asyncio.create_task(tool.call(deepcopy(call.arguments), context))
        self.active[call.id] = (tool, definition, context, runner)
        released = definition.read_only
        result = None
        try:
            done, _ = await asyncio.wait({runner}, timeout=max(0, deadline - monotonic()))
            if not done:
                raise asyncio.TimeoutError()
            result = ToolResult.model_validate(runner.result()).model_copy(deep=True)
            json.dumps(result.model_dump(), allow_nan=False)
            if result.status == "ok" and self.outputs[call.name]:
                self.outputs[call.name].validate(result.data)
            released = result.control_released
            if (result.status != "ok" or not released) and not definition.read_only:
                released = await self._stop(tool, definition, context, runner, result.text)
            if not released:
                self.control_locked = True
        except asyncio.CancelledError:
            released = await asyncio.shield(self._stop(tool, definition, context, runner, "Cancelled"))
            result = ToolResult(status="cancelled", text="Call cancelled", control_released=released)
            raise
        except Exception as error:
            released = await self._stop(tool, definition, context, runner, type(error).__name__)
            result = ToolResult(status="timed_out" if isinstance(error, asyncio.TimeoutError) else "error",
                                text=f"{type(error).__name__}: {error}", control_released=released)
        finally:
            context.cancelled.set()
            self.active.pop(call.id, None)
            if released:
                for resource in definition.resources:
                    if self.leases.get(resource) == call.id:
                        del self.leases[resource]
            else:
                self.control_locked = True
            if result is not None:
                result = result.model_copy(update={"control_released": released})
                self._emit("tool_finished", call=call.model_dump(), result=result.model_dump())
        return result

    async def _stop(self, tool, definition, context, runner, reason):
        context.cancelled.set()
        acknowledged = definition.read_only
        if not definition.read_only:
            stopping = asyncio.create_task(tool.stop(context.call_id, reason))
            done, _ = await asyncio.wait({stopping}, timeout=self.stop_timeout_s)
            if done:
                try:
                    acknowledged = stopping.result() is True
                except BaseException:
                    acknowledged = False
            else:
                stopping.cancel()
                stopping.add_done_callback(self._consume)
        runner.cancel()
        await asyncio.wait({runner}, timeout=self.stop_timeout_s)
        runner.add_done_callback(self._consume)
        # An uncooperative local task could still dispatch after a remote stop.
        if not definition.read_only and not runner.done():
            acknowledged = False
        # The caller records the acknowledgement in tool_finished after updating
        # ownership. Logging must never interrupt stop and release bookkeeping.
        return acknowledged

    @staticmethod
    def _consume(task):
        if not task.cancelled():
            task.exception()

    async def close(self):
        self.closed = True
        pending = [task for _, task in self.calls.values() if not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
