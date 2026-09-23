"""Deterministic lifecycle, control ownership, monitoring and evidence log."""

import asyncio
from dataclasses import asdict
from time import monotonic, time

from primitive_vla.agent.contracts import EventSink, Observer
from primitive_vla.backends.base import ExecutionBackend
from primitive_vla.execution.control import ControlAuthority
from primitive_vla.storage.events import EventLog
from primitive_vla.domain.models import EffectStatus, ExecutionStatus, SkillCall, SkillFeedback, SkillResult, Truth
from primitive_vla.domain.skills import Registry, check_conditions
from primitive_vla.domain.state import State


class Executive:
    """Own dispatch safety and lifecycle, never the physical outcome verdict.

    Observations here guard preconditions and runtime invariants. The Agent
    obtains post-action evidence and asks its Verifier to assess effects.
    """

    def __init__(self, registry: Registry, state: State, backend: ExecutionBackend, log: EventSink | None = None,
                 poll_s: float = 0.02, stop_timeout_s: float = 1.0, *, observer: Observer | None = None):
        self.registry, self.state, self.backend = registry, state, backend
        # Combined legacy backends remain usable by direct callers.
        self.observer = backend if observer is None else observer
        self.log = log or EventLog()
        self.control = ControlAuthority()
        self.poll_s, self.stop_timeout_s = poll_s, stop_timeout_s
        self.requests: dict[str, tuple[SkillCall, asyncio.Task]] = {}
        self.cancellations: dict[str, asyncio.Event] = {}

    async def execute(self, call: SkillCall) -> SkillResult:
        previous = self.requests.get(call.call_id)
        if previous is not None:
            if previous[0] != call:
                return self._reject(call, "Call ID reused with a different payload")
            return await asyncio.shield(previous[1])
        self.cancellations[call.call_id] = asyncio.Event()
        worker = asyncio.create_task(self._execute(call))
        self.requests[call.call_id] = (call, worker)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.cancel(call.call_id)
            await asyncio.shield(worker)
            raise

    def cancel(self, call_id: str) -> bool:
        if call_id not in self.cancellations or self.requests[call_id][1].done():
            return False
        self.cancellations[call_id].set()
        return True

    def _reject(self, call: SkillCall, reason: str) -> SkillResult:
        result = SkillResult(call.call_id, ExecutionStatus.REJECTED, EffectStatus.UNKNOWN, reason, needs_observation=True)
        self.log.write("rejected", call=asdict(call), result=asdict(result))
        return result

    async def _stop(self, backend: ExecutionBackend, call: SkillCall, runner: asyncio.Task, reason: str) -> bool:
        self.control.revoke()  # Reject late inference before sending the stop.
        stopping = asyncio.create_task(backend.stop(call.call_id, reason))
        done, _ = await asyncio.wait({stopping}, timeout=self.stop_timeout_s)
        acknowledged = False
        if stopping in done:
            try:
                acknowledged = stopping.result() is True
            except Exception as error:
                self.log.write("stop_error", call_id=call.call_id, reason=str(error))
        else:
            stopping.cancel()
        runner.cancel()
        # No unbounded wait for an uncooperative remote policy.
        await asyncio.wait({runner}, timeout=self.stop_timeout_s)
        self.log.write("stopped", call_id=call.call_id, acknowledged=acknowledged, reason=reason)
        return acknowledged

    async def _execute(self, call: SkillCall) -> SkillResult:
        try:
            candidate = self.registry.resolve(call, self.state.objects)
            if call.state_version != self.state.version:
                raise ValueError("Stale state version; replan from current observation")
            if call.deadline <= time():
                raise ValueError("Call deadline has expired")
            backend = self.backend
            lease = self.control.acquire(call, candidate)
        except (ValueError, KeyError) as error:
            return self._reject(call, str(error))

        before = self.state.snapshot()
        before_values = self.state.values()
        started = time()
        deadline = monotonic() + min(candidate.spec.timeout_s, call.deadline - started)
        released = True
        runner = None
        execution = ExecutionStatus.FAILED
        reason = ""
        unhealthy_feedback = False

        def feedback(message: SkillFeedback) -> None:
            nonlocal unhealthy_feedback
            if self.control.accepts(message.call_id, message.generation):
                unhealthy_feedback |= not message.control_healthy
                self.log.write("feedback", **asdict(message))

        async def run():
            self.log.write("running", call_id=call.call_id)
            return await backend.run(call, candidate, lease, self.control, feedback)

        self.log.write("acquired", call=asdict(call), generation=lease.generation,
                       resources=lease.resources, backend=type(backend).__name__,
                       model_version=backend.model_version, before=before)
        try:
            self.state.update(await asyncio.wait_for(self.observer.observe(), timeout=max(0.001, deadline - monotonic())))
            feasibility = check_conditions(candidate.preconditions, self.state.values())
            self.log.write("checked", call_id=call.call_id, verdict=feasibility.verdict, reasons=feasibility.reasons)
            if feasibility.verdict != Truth.TRUE:
                execution, reason = ExecutionStatus.REJECTED, "; ".join(feasibility.reasons)
            elif self.cancellations[call.call_id].is_set():
                execution, reason = ExecutionStatus.CANCELLED, "Cancelled before dispatch"
            else:
                runner = asyncio.create_task(run())
                while True:
                    if self.cancellations[call.call_id].is_set():
                        execution, reason = ExecutionStatus.CANCELLED, "Cancellation requested"
                        break
                    if monotonic() >= deadline:
                        execution, reason = ExecutionStatus.TIMED_OUT, "Skill deadline exceeded"
                        break
                    done, _ = await asyncio.wait({runner}, timeout=min(self.poll_s, deadline - monotonic()))
                    if runner in done:
                        execution = runner.result()
                        reason = "Backend finished"
                        break
                    self.state.update(await asyncio.wait_for(self.observer.observe(), timeout=max(0.001, deadline - monotonic())))
                    invariant = check_conditions(candidate.spec.bind(candidate.spec.invariants, call.bindings), self.state.values())
                    if unhealthy_feedback or invariant.verdict != Truth.TRUE:
                        execution, reason = ExecutionStatus.FAILED, "Runtime invariant/health lost: " + "; ".join(invariant.reasons)
                        break
                if execution != ExecutionStatus.COMPLETED or unhealthy_feedback:
                    if unhealthy_feedback:
                        execution, reason = ExecutionStatus.FAILED, "Backend reported unhealthy control"
                    released = await self._stop(backend, call, runner, reason)
                else:
                    self.control.revoke()  # Finished calls cannot dispatch more chunks.
        except (Exception, asyncio.CancelledError) as error:
            execution = (ExecutionStatus.CANCELLED if isinstance(error, asyncio.CancelledError) else
                         ExecutionStatus.TIMED_OUT if isinstance(error, asyncio.TimeoutError) else ExecutionStatus.FAILED)
            reason = f"{type(error).__name__}: {error}"
            if runner is not None:
                released = await self._stop(backend, call, runner, reason)
        finally:
            if released:
                self.control.release()
            else:
                reason += "; stop unacknowledged, control remains locked"

        changes = {k: v.value for k, v in self.state.values().items() if before_values.get(k) != v}
        result = SkillResult(call.call_id, execution, EffectStatus.UNKNOWN, reason,
                             changes=changes, needs_observation=True, started_at=started)
        self.log.write("result", call=asdict(call), result=asdict(result), after=self.state.snapshot(), control_released=released)
        return result
