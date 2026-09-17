"""Small goal-driven reference planner; model planners share the same interface."""

import heapq
import itertools
import asyncio
from dataclasses import dataclass
from typing import Callable, Protocol

from primitive_vla.domain.models import Task, Truth
from primitive_vla.domain.skills import Candidate, check_conditions
from primitive_vla.domain.state import State


@dataclass
class Decision:
    kind: str  # skill / observe / done / stop
    candidate: Candidate | None = None
    reason: str = ""


class Planner(Protocol):
    async def decide(self, task: Task, state: State, candidates: list[Candidate], history: list[dict]) -> Decision: ...


class GoalPlanner:
    """Bounded A* on contract effects, replanned after every real observation.

    Hypothetical effects exist ONLY in the search; they never update State.
    This is a small symbolic debug planner, not a physical feasibility solver.
    """

    def __init__(self, max_nodes: int = 12000):
        self.max_nodes = max_nodes

    async def decide(self, task: Task, state: State, candidates: list[Candidate], history: list[dict]) -> Decision:
        initial = state.values()
        if not task.pending(initial):
            return Decision("done")
        candidates = [c for c in candidates if c.spec.name not in task.forbidden_skills]
        # Backward relevance closure keeps unrelated objects/skills out of search.
        needed = {(k, v) for g in task.goals for k, v in g.conditions.items()}
        relevant = []
        remaining = list(candidates)
        while True:
            selected = [c for c in remaining if needed.intersection(c.effects.items())]
            if not selected:
                break
            for c in selected:
                needed.update(c.preconditions.items())
                remaining.remove(c)
                relevant.append(c)
        grounded = [(c, c.preconditions, c.effects) for c in relevant]

        def key(values: dict[str, Truth]) -> tuple:
            return tuple(sorted(values.items()))

        def heuristic(values: dict[str, Truth]) -> int:
            return len(task.pending(values))

        serial = itertools.count()
        queue = [(heuristic(initial), 0, next(serial), initial, None)]
        visited = {key(initial): 0}
        expanded = 0
        while queue and expanded < self.max_nodes:
            _, cost, _, values, first = heapq.heappop(queue)
            if visited[key(values)] < cost:
                continue
            if not task.pending(values):
                return Decision("skill", first, f"symbolic plan found, expanded {expanded} states")
            expanded += 1
            if expanded % 100 == 0:
                await asyncio.sleep(0)  # Keep cancellation and the dashboard responsive.
            if cost >= task.max_steps:
                continue
            for candidate, preconditions, effects in grounded:
                if check_conditions(preconditions, values).verdict != Truth.TRUE or not task.allows(effects, values):
                    continue
                updated = values | effects
                signature = key(updated)
                if visited.get(signature, task.max_steps + 1) <= cost + 1:
                    continue
                visited[signature] = cost + 1
                heapq.heappush(queue, (cost + 1 + heuristic(updated), cost + 1, next(serial), updated, first or candidate))
        if any(check_conditions(pre, initial).verdict == Truth.UNKNOWN for _, pre, _ in grounded):
            return Decision("observe", reason="Relevant entry conditions are unconfirmed")
        return Decision("stop", reason=f"No plan within capabilities/search budget ({expanded} states)")


class StructuredPlanner:
    """Inject an async model client that chooses an index from legal candidates.

    The client receives structured context and returns {action: 'skill', index: N}
    or {action: 'observe'|'stop', reason: '...'}. No generated code is executed.
    """

    def __init__(self, client: Callable, history_limit: int = 4, adaptive_memory: bool = False):
        self.client = client
        self.history_limit = history_limit
        self.adaptive_memory = adaptive_memory
        self.working_memory = {"hypothesis": "", "trials": [], "uncertainties": []}

    async def decide(self, task: Task, state: State, candidates: list[Candidate], history: list[dict]) -> Decision:
        values = state.values()
        if not task.pending(values):
            return Decision("done")
        legal = [c for c in candidates if c.spec.name not in task.forbidden_skills
                 and check_conditions(c.preconditions, values).verdict == Truth.TRUE and task.allows(c.effects, values)]
        context = {"instruction": task.instruction,
                   "remaining_budget": {"steps": task.max_steps, "seconds": task.timeout_s, "observations": task.max_observations},
                   "remaining_goals": [{"id": g.id, "conditions": g.conditions, "after": g.after} for g in task.pending(values)],
                   "state_version": state.version, "facts": values,
                   "objects": {k: {"kind": o.kind, "description": o.description} for k, o in state.objects.items()},
                   "recent_results": history[-self.history_limit:] if self.history_limit else [],
                   "skill_library": [{"name": s.name, "version": s.version, "description": s.description,
                                      "instructions": s.instructions, "policy_prompt": s.policy_prompt}
                                     for s in {(c.spec.name, c.spec.version): c.spec for c in candidates}.values()],
                   "candidates": [{"index": i, "skill": c.spec.name, "version": c.spec.version,
                                   "bindings": c.bindings, "effects": c.effects, "capability": c.spec.capability,
                                   "policy_prompt": c.spec.policy_prompt.format(**c.bindings)}
                                  for i, c in enumerate(legal)]}
        if self.adaptive_memory:
            context["working_memory"] = self.working_memory
        reply = await self.client(context)
        if self.adaptive_memory:
            memory = reply.get("working_memory")
            if not isinstance(memory, dict):
                raise ValueError("Adaptive planning requires an explicit working-memory summary")
            self.working_memory = memory
        if reply.get("action") == "skill" and type(reply.get("index")) is int and 0 <= reply["index"] < len(legal):
            return Decision("skill", legal[reply["index"]], str(reply.get("reason", "")))
        if reply.get("action") in ("observe", "stop"):
            return Decision(reply["action"], reason=reply.get("reason", ""))
        raise ValueError("Planner output must select a legal candidate or request observe/stop")
