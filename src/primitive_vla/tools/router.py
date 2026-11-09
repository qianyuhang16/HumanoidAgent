from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any
import time
import json

from jsonschema import ValidationError

from primitive_vla.contracts import Domain, Failure, Record, ResourceRef, ToolError, new_id
from primitive_vla.tools.contracts import CallContext, ToolResult
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.hooks import Hooks
from primitive_vla.tools.registry import RegisteredTool, ToolRegistry

TERMINAL = {"completed", "failed", "cancelled"}


@dataclass
class Call:
    id: str
    name: str
    context: CallContext
    status: str = "running"
    result: ToolResult | None = None
    error: ToolError | None = None
    started_at: float = field(default_factory=time.time)
    stop_requested: bool = False
    worker: asyncio.Task | None = None
    supervisor: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    available: asyncio.Event = field(default_factory=asyncio.Event)
    recovered: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {"call_id": self.id, "name": self.name, "status": self.status,
                "result": asdict(self.result) if self.result else None,
                "error": self.error.to_dict() if self.error else None}


class ToolRouter:
    def __init__(self, registry: ToolRegistry, resources: ResourceManager, hooks: Hooks,
                 cancel_timeout: float = 5.0):
        if cancel_timeout <= 0:
            raise ValueError("cancel_timeout must be positive")
        self.registry = registry
        self.resources = resources
        self.hooks = hooks
        self.cancel_timeout = cancel_timeout
        self.calls: dict[str, Call] = {}

    def restore(self) -> None:
        """Quarantine unfinished calls after a restart; never replay an action."""
        for record in self.hooks.memory.unfinished_calls():
            if record.call_id in self.calls:
                continue
            self.registry.get(record.source)

            async def progress(payload: dict, refs: tuple = ()) -> None:
                raise RuntimeError("recovered calls cannot publish progress through an old handle")

            context = CallContext(record.call_id, record.session_id, record.task_id,
                                  record.payload["robot_id"], asyncio.Event(), progress)
            call = Call(record.call_id, record.source, context, status="stop_unconfirmed", recovered=True)
            self.resources.acquire(context.robot_id, tuple(record.payload["required_resources"]), call.id)
            self.calls[call.id] = call

    async def invoke(self, name: str, arguments: dict[str, Any], *, session_id: str,
                     task_id: str, robot_id: str, robot_type: str,
                     allowed_tools: tuple[str, ...] | None = None) -> Call:
        call_id = new_id()

        async def progress(payload: dict[str, Any], refs: tuple[ResourceRef, ...] = ()) -> None:
            if call.status not in TERMINAL:
                await self._emit(call, entry.spec.domain, "on_progress", "tool_progress",
                                 payload, refs, wake=False, ttl=300)

        context = CallContext(call_id, session_id, task_id, robot_id, asyncio.Event(), progress)
        call = Call(call_id, name, context)
        self.calls[call_id] = call
        domain = Domain.PLANNING
        try:
            entry = self.registry.get(name)
            domain = entry.spec.domain
            if allowed_tools is not None and name not in allowed_tools:
                raise ToolError(Failure.PERMISSION, f"tool not allowed: {name}")
            if entry.spec.supported_robots and robot_type not in entry.spec.supported_robots:
                raise ToolError(Failure.PERMISSION, f"tool does not support robot type: {robot_type}")
            entry.input_validator.validate(arguments)
            self.resources.acquire(robot_id, entry.spec.required_resources, call_id)
            # Ownership is acquired before any extension hook can yield.
            await self._emit(call, domain, "before_call", "tool_started",
                             {"arguments": arguments, "robot_id": robot_id,
                              "required_resources": list(entry.spec.required_resources)},
                             wake=False)
        except (ToolError, ValidationError) as error:
            call.error = error if isinstance(error, ToolError) else ToolError(Failure.INTERFACE, error.message)
            await self._finish(call, domain, "failed")
            return call
        except Exception as error:
            call.error = ToolError(Failure.INTERFACE, f"before_call failed: {error}")
            await self._finish(call, domain, "failed")
            return call
        except asyncio.CancelledError:
            # No Provider has started yet, so interrupted preparation can release ownership.
            await self._finish(call, domain, "cancelled")
            raise

        if call.stop_requested:  # Cancellation can arrive while a before_call hook is awaiting.
            await self._finish(call, domain, "cancelled")
            return call
        call.worker = asyncio.create_task(entry.provider.execute(arguments, context))
        call.supervisor = asyncio.create_task(self._supervise(call, entry))
        if entry.spec.execution_mode == "sync":
            await call.available.wait()
        return call

    async def _supervise(self, call: Call, entry: RegisteredTool) -> None:
        ready, _ = await asyncio.wait({call.worker}, timeout=entry.spec.timeout)
        if not ready:
            await self.cancel(call.id, ToolError(Failure.TIMEOUT, "tool execution timed out"))
        if call.status in TERMINAL:
            return
        try:
            result = await asyncio.shield(call.worker)
        except asyncio.CancelledError:
            # Only our confirmed-stop path cancels a worker. A provider cancelling itself
            # must still prove that the robot stopped.
            if call.status not in TERMINAL:
                await self.cancel(call.id, ToolError(Failure.EXECUTION, "provider task cancelled"))
            return
        except Exception as error:
            failure = error if isinstance(error, ToolError) else ToolError(Failure.EXECUTION, str(error))
            if entry.spec.domain == Domain.EXECUTION:
                await self.cancel(call.id, failure)
            else:
                call.error = failure
                await self._finish(call, entry.spec.domain, "failed")
            return

        # A returned result is the Provider's confirmation that the underlying operation ended.
        try:
            json.dumps(asdict(result), allow_nan=False)
            entry.output_validator.validate(result.payload)
            if entry.spec.domain == Domain.VERIFICATION:
                verification = result.verification
                if verification is None or verification.conclusion not in {"success", "failure", "uncertain"}:
                    raise ToolError(Failure.INTERFACE, "verification requires success/failure/uncertain")
                if not verification.subject or not verification.reason:
                    raise ToolError(Failure.INTERFACE, "verification requires subject and reason")
            elif result.verification is not None:
                raise ToolError(Failure.INTERFACE, "only verification tools may return a verification conclusion")
        except (ValidationError, ToolError, AttributeError, TypeError, ValueError) as error:
            call.error = ToolError(Failure.INTERFACE, f"invalid provider output: {error}")
            await self._finish(call, entry.spec.domain, "failed")
            return
        call.result = result
        await self._finish(call, entry.spec.domain,
                           "failed" if call.error else "cancelled" if call.stop_requested else "completed")

    async def cancel(self, call_id: str, reason: ToolError | None = None) -> Call:
        call = self.calls[call_id]
        async with call.lock:
            if call.status in TERMINAL:
                return call
            call.stop_requested = True
            call.error = reason or call.error
            call.context.cancel_requested.set()
            if call.worker is None and not call.recovered:
                # invoke owns startup and will release after its pre-call hook returns.
                return call
            entry = self.registry.get(call.name)
            call.status = "stopping"
            await self._emit(call, entry.spec.domain, "on_progress", "stop_requested", {})
            try:
                confirmed = await asyncio.wait_for(entry.provider.cancel(call.context), self.cancel_timeout)
            except Exception as error:
                confirmed = False
                call.error = call.error or ToolError(Failure.EXECUTION, f"stop confirmation failed: {error}")
            if confirmed is True:
                if call.worker is not None:
                    call.worker.cancel()
                await self._finish(call, entry.spec.domain, "failed" if call.error else "cancelled")
            elif call.status not in TERMINAL:
                call.status = "stop_unconfirmed"
                call.available.set()
                await self._emit(call, entry.spec.domain, "on_error", "stop_unconfirmed",
                                 {"error": call.error.to_dict() if call.error else None})
        return call

    async def _finish(self, call: Call, domain: Domain, status: str) -> None:
        if call.status in TERMINAL:
            return
        call.status = status
        payload = call.snapshot()
        payload["duration_ms"] = round((time.time() - call.started_at) * 1000)
        refs = () if call.result is None else call.result.resource_refs
        if call.result and call.result.verification:
            refs += call.result.verification.evidence
        # Record the terminal fact before releasing ownership and waking the planner.
        record = Record(
            session_id=call.context.session_id, task_id=call.context.task_id, call_id=call.id,
            source=call.name, domain=domain,
            event_type="verification" if call.result and call.result.verification else "tool_finished",
            status=status, payload=payload, resource_refs=refs,
        )
        self.resources.release(call.id)
        call.done.set()
        call.available.set()
        await self.hooks.emit("on_error" if call.error else "after_call", record)

    async def _emit(self, call: Call, domain: Domain, stage: str, event: str,
                    payload: dict[str, Any], refs: tuple[ResourceRef, ...] = (), *,
                    wake: bool = True, ttl: float | None = None) -> None:
        await self.hooks.emit(stage, Record(
            session_id=call.context.session_id, task_id=call.context.task_id, call_id=call.id,
            source=call.name, domain=domain, event_type=event, status=call.status,
            payload=payload, resource_refs=refs,
            expires_at=time.time() + ttl if ttl else None,
        ), wake=wake)

    def active(self, session_id: str) -> list[Call]:
        return [call for call in self.calls.values()
                if call.context.session_id == session_id and call.status not in TERMINAL]

    async def cancel_session(self, session_id: str) -> list[Call]:
        return list(await asyncio.gather(*(self.cancel(call.id) for call in self.active(session_id))))

    async def close(self) -> None:
        active = [call for call in self.calls.values() if call.status not in TERMINAL]
        await asyncio.gather(*(self.cancel(call.id) for call in active))
        unresolved = [call.id for call in active if call.status not in TERMINAL]
        if unresolved:
            raise RuntimeError(f"stop unconfirmed; runtime must remain alive: {unresolved}")
        tasks = [task for call in self.calls.values() for task in (call.worker, call.supervisor) if task]
        await asyncio.gather(*tasks, return_exceptions=True)
