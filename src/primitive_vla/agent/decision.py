"""DecisionModel implementations independent of the Agent loop."""

import asyncio
import heapq
import itertools
from typing import Any

from primitive_vla.domain.models import Truth
from primitive_vla.domain.skills import check_conditions

from .contracts import Decision, DecisionContext, DecisionKind


class RuleDecisionModel:
    def __init__(self, max_nodes: int = 12000, **_):
        self.max_nodes = max_nodes

    async def decide(self, context: DecisionContext) -> Decision:
        values = context.state.values()
        if not context.task.pending(values):
            return Decision(DecisionKind.FINISH, reason="All goals are already satisfied")
        candidates = [c for c in context.candidates if c.spec.name not in context.task.forbidden_skills]
        needed = {(k, v) for goal in context.task.goals for k, v in goal.conditions.items()}
        relevant, remaining = [], list(candidates)
        while True:
            selected = [c for c in remaining if needed.intersection(c.effects.items())]
            if not selected:
                break
            for candidate in selected:
                needed.update(candidate.preconditions.items())
                remaining.remove(candidate)
                relevant.append(candidate)

        def key(facts):
            return tuple(sorted(facts.items()))

        serial = itertools.count()
        queue = [(len(context.task.pending(values)), 0, next(serial), values, None)]
        visited = {key(values): 0}
        expanded = 0
        while queue and expanded < self.max_nodes:
            _, cost, _, facts, first = heapq.heappop(queue)
            if visited[key(facts)] < cost:
                continue
            if not context.task.pending(facts):
                return Decision(DecisionKind.EXECUTE, first, f"symbolic plan found, expanded {expanded} states")
            expanded += 1
            if expanded % 100 == 0:
                await asyncio.sleep(0)
            if cost >= context.remaining_steps:
                continue
            for candidate in relevant:
                if check_conditions(candidate.preconditions, facts).verdict != Truth.TRUE:
                    continue
                if not context.task.allows(candidate.effects, facts):
                    continue
                updated = facts | candidate.effects
                signature = key(updated)
                if visited.get(signature, context.remaining_steps + 1) <= cost + 1:
                    continue
                visited[signature] = cost + 1
                heapq.heappush(queue, (len(context.task.pending(updated)), cost + 1,
                                       next(serial), updated, first or candidate))
        if any(check_conditions(c.preconditions, values).verdict == Truth.UNKNOWN for c in relevant):
            return Decision(DecisionKind.OBSERVE, reason="Relevant entry conditions are unconfirmed")
        return Decision(DecisionKind.STOP, reason=f"No plan within search budget ({expanded} states)")


class VLMDecisionModel:
    def __init__(self, client, prompt: str, history_limit: int = 4, **_):
        self.client, self.prompt, self.history_limit = client, prompt, history_limit

    async def decide(self, context: DecisionContext) -> Decision:
        values = context.state.values()
        if not context.task.pending(values):
            return Decision(DecisionKind.FINISH, reason="All goals are already satisfied")
        legal = [c for c in context.candidates
                 if c.spec.name not in context.task.forbidden_skills
                 and check_conditions(c.preconditions, values).verdict == Truth.TRUE
                 and context.task.allows(c.effects, values)]
        payload = {
            "instruction": context.task.instruction,
            "remaining_budget": {"steps": context.remaining_steps, "seconds": context.remaining_seconds,
                                  "observations": context.remaining_observations},
            "remaining_goals": [{"id": g.id, "conditions": g.conditions, "after": g.after}
                                for g in context.task.pending(values)],
            "facts": values,
            "objects": {k: {"kind": o.kind, "description": o.description}
                        for k, o in context.state.objects.items()},
            "recent_results": list(context.history[-self.history_limit:]),
            "candidates": [{"index": i, "skill": c.spec.name, "version": c.spec.version,
                            "bindings": c.bindings, "effects": c.effects}
                           for i, c in enumerate(legal)],
        }
        reply = await self.client.complete("plan", self.prompt, payload, context.state.observation.images)
        action = reply.get("action")
        if action == "skill" and type(reply.get("index")) is int and 0 <= reply["index"] < len(legal):
            return Decision(DecisionKind.EXECUTE, legal[reply["index"]], str(reply.get("reason", "")))
        if action == "observe":
            return Decision(DecisionKind.OBSERVE, reason=reply.get("reason", ""))
        if action == "stop":
            return Decision(DecisionKind.STOP, reason=reply.get("reason", ""))
        raise ValueError("Decision model must select a legal candidate or request observe/stop")
