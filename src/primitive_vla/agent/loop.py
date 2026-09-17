"""Observe → plan one skill → execute → verify → replan."""

import asyncio
from collections import Counter
from dataclasses import asdict, replace
from time import monotonic, time

from primitive_vla.backends.base import Backend
from primitive_vla.execution.executive import Executive
from primitive_vla.domain.models import ExecutionStatus, SkillCall, Task
from primitive_vla.agent.planner import Planner


class Agent:
    def __init__(self, planner: Planner, executive: Executive, observer: Backend):
        self.planner, self.executive, self.observer = planner, executive, observer

    async def run(self, task: Task) -> dict:
        deadline = monotonic() + task.timeout_s
        state = self.executive.state
        attempts: Counter = Counter()
        history = []
        observations = 0
        status, reason = "stopped", "Step budget exhausted"

        async def observe() -> None:
            nonlocal observations
            if observations >= task.max_observations:
                raise RuntimeError("Reobservation budget exhausted")
            state.update(await asyncio.wait_for(self.observer.observe(), timeout=max(0.001, deadline - monotonic())))
            observations += 1
            self.executive.log.write("reobserved", state=state.snapshot())

        self.executive.log.write("task_started", task=asdict(task))
        try:
            state.update(await asyncio.wait_for(self.observer.observe(), timeout=task.timeout_s))
            for step in range(task.max_steps + 1):
                if self.executive.control.lease is not None:
                    reason = "Control is locked pending confirmed backend stop"
                    break
                if not task.pending(state.values()):
                    status, reason = "completed", "All goals confirmed by observation"
                    break
                if step == task.max_steps:
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    reason = "Task time budget exhausted"
                    break
                candidates = [c for c in self.executive.registry.candidates(state.objects)
                              if c.spec.name not in task.forbidden_skills
                              and attempts[c.signature] < (1 if c.spec.irreversible else c.spec.max_attempts)]
                planning_task = replace(task, max_steps=task.max_steps - step, timeout_s=remaining,
                                        max_observations=task.max_observations - observations)
                decision = await asyncio.wait_for(self.planner.decide(planning_task, state, candidates, history), timeout=remaining)
                self.executive.log.write("decision", kind=decision.kind, reason=decision.reason,
                                         candidate=decision.candidate.signature if decision.candidate else None)
                if decision.kind == "observe":
                    await observe()
                    continue
                if decision.kind != "skill" or decision.candidate is None:
                    reason = decision.reason or "Planner stopped before goals were verified"
                    break
                candidate = decision.candidate
                if candidate.signature not in {c.signature for c in candidates} or not task.allows(candidate.effects, state.values()):
                    reason = "Planner requested a disallowed skill or violated goal order"
                    break
                call = SkillCall(candidate.spec.name, candidate.bindings, state.version, candidate.spec.version,
                                 deadline=time() + max(0, deadline - monotonic()))
                result = await self.executive.execute(call)
                history.append({"skill": call.skill, "bindings": call.bindings,
                                "policy_prompt": candidate.spec.policy_prompt.format(**call.bindings), **asdict(result)})
                if result.execution != ExecutionStatus.REJECTED:
                    attempts[call.signature] += 1
                if self.executive.control.lease is not None:
                    reason = "Stop was not acknowledged; control remains locked"
                    break
                if result.needs_observation:
                    await observe()
                # Irreversible calls stay excluded even if new observation says
                # no effect. A human/new task must decide whether repetition is valid.
                if result.execution in (ExecutionStatus.CANCELLED, ExecutionStatus.TIMED_OUT):
                    reason = f"Execution {result.execution.value}; observed actual state before stopping"
                    break
        except (ValueError, RuntimeError, asyncio.TimeoutError) as error:
            reason = f"{type(error).__name__}: {error}"
        report = {"status": status, "reason": reason, "steps": len(history), "reobservations": observations,
                  "remaining_goals": [g.id for g in task.pending(state.values())], "results": history}
        self.executive.log.write("task_finished", **report)
        return report
