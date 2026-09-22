"""Canonical observe, decide, execute, verify Agent loop."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import monotonic, time

from primitive_vla.domain.models import ExecutionStatus, SkillCall, Task

from .contracts import (
    AgentContext, AgentEvent, AgentReport, Decision, DecisionContext, DecisionKind,
    VerificationRequest,
)


class Agent:
    """Task-independent closed-loop Agent composed entirely from ports."""

    def __init__(self, max_steps: int = 24, timeout_s: float = 180.0, max_observations: int = 2):
        self.max_steps = max_steps
        self.timeout_s = timeout_s
        self.max_observations = max_observations

    async def run(self, task: Task, context: AgentContext) -> AgentReport:
        deadline = monotonic() + min(self.timeout_s, task.timeout_s)
        state = context.state
        history: list[dict] = []
        observations = 0
        attempts: dict[str, int] = {}
        active_call: SkillCall | None = None
        status, reason = "stopped", "Step budget exhausted"
        context.log.write("task_started", task=asdict(task))

        async def observe() -> None:
            nonlocal observations
            if observations >= min(task.max_observations, self.max_observations):
                raise RuntimeError("Observation budget exhausted")
            observation = await asyncio.wait_for(context.observer.observe(), timeout=max(.001, deadline - monotonic()))
            state.update(observation)
            observations += 1
            event = AgentEvent("observed", {"version": state.version})
            context.memory.update(event)
            context.log.write("reobserved", state=state.snapshot())

        try:
            await observe()
            for step in range(min(task.max_steps, self.max_steps) + 1):
                if not task.pending(state.values()):
                    status, reason = "completed", "All goals confirmed by observation"
                    break
                remaining = deadline - monotonic()
                if remaining <= 0 or step >= min(task.max_steps, self.max_steps):
                    reason = "Task time or step budget exhausted"
                    break
                candidates = [candidate for candidate in context.catalog.candidates(state)
                              if candidate.spec.name not in task.forbidden_skills
                              and attempts.get(candidate.signature, 0) < (1 if candidate.spec.irreversible else candidate.spec.max_attempts)]
                decision = await asyncio.wait_for(context.decision_model.decide(
                    DecisionContext(task, state, tuple(candidates), tuple(history),
                                    min(task.max_steps, self.max_steps) - step, remaining,
                                    min(task.max_observations, self.max_observations) - observations)), timeout=remaining)
                context.log.write("decision", kind=decision.kind.value, reason=decision.reason,
                                  candidate=decision.candidate.signature if decision.candidate else None)
                context.memory.update(AgentEvent("decision", {"kind": decision.kind.value, "reason": decision.reason}))
                if decision.kind is DecisionKind.OBSERVE:
                    await observe()
                    continue
                if decision.kind in (DecisionKind.STOP, DecisionKind.FINISH):
                    if decision.kind is DecisionKind.FINISH and not task.pending(state.values()):
                        status = "completed"
                        reason = decision.reason or "Decision model finished"
                    else:
                        status = "stopped"
                        reason = decision.reason or "Decision model stopped before all goals were observed"
                    break
                if decision.kind is not DecisionKind.EXECUTE or decision.candidate is None:
                    reason = decision.reason or "Invalid decision"
                    break
                candidate = decision.candidate
                if candidate.signature not in {item.signature for item in candidates}:
                    reason = "Decision model returned a candidate outside the catalog"
                    break
                call = SkillCall(candidate.spec.name, candidate.bindings, state.version, candidate.spec.version,
                                 deadline=time() + max(0, remaining))
                active_call = call
                result = await context.executor.execute(call)
                attempts[candidate.signature] = attempts.get(candidate.signature, 0) + 1
                item = {"skill": call.skill, "bindings": call.bindings, **asdict(result)}
                history.append(item)
                context.memory.update(AgentEvent("executed", item))
                if result.execution in (ExecutionStatus.CANCELLED, ExecutionStatus.TIMED_OUT):
                    reason = result.reason
                    break
                if result.execution is ExecutionStatus.FAILED:
                    reason = result.reason or "Execution failed"
                    limit = 1 if candidate.spec.irreversible else candidate.spec.max_attempts
                    if attempts[candidate.signature] >= limit:
                        status = "failed"
                        break
                    active_call = None
                    continue
                await observe()
                verification = await context.verifier.verify(VerificationRequest(
                    task, state, result, candidate, {"results": history}))
                context.memory.update(AgentEvent("verified", asdict(verification)))
                if verification.status == "failure":
                    reason = verification.reason or "Verification failed"
                    limit = 1 if candidate.spec.irreversible else candidate.spec.max_attempts
                    if attempts[candidate.signature] >= limit:
                        status = "failed"
                        break
                    active_call = None
                    continue
                active_call = None
        except asyncio.CancelledError:
            if active_call is not None:
                stop = await asyncio.shield(context.executor.stop(active_call.call_id, "Agent cancelled"))
                if not getattr(stop, "acknowledged", stop):
                    context.log.write("stop_unacknowledged", call_id=active_call.call_id)
            raise
        except (ValueError, RuntimeError, asyncio.TimeoutError) as error:
            if active_call is not None:
                stop = await asyncio.shield(context.executor.stop(active_call.call_id, "Agent budget expired"))
                if not getattr(stop, "acknowledged", stop):
                    context.log.write("stop_unacknowledged", call_id=active_call.call_id)
            status = "stopped"
            reason = f"{type(error).__name__}: {error}"
        report = AgentReport(status, reason, len(history), observations,
                             tuple(goal.id for goal in task.pending(state.values())), tuple(history))
        context.log.write("task_finished", **asdict(report))
        return report
