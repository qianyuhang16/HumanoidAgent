"""Task-level rule and VLM verdicts without changing observed state."""

from dataclasses import asdict

from primitive_vla.config.schema import VerificationConfig
from primitive_vla.domain.models import Task, Truth
from primitive_vla.domain.state import State
from primitive_vla.providers.base import ModelClient


class TaskVerifier:
    def __init__(self, config: VerificationConfig, client: ModelClient):
        self.config, self.client = config, client

    async def verify(self, task: Task, report: dict, state: State) -> dict:
        mode = self.config.mode
        values = state.values()
        rules = [{"id": g.id, "status": "success" if all(values.get(k, Truth.UNKNOWN) == v for k, v in g.conditions.items()) else "unknown",
                  "evidence_refs": ["fact:" + k for k in g.conditions]} for g in task.goals]
        if mode == "rules":
            return {"mode": mode, "status": "success" if report["status"] == "completed" else "unknown", "criteria": rules}
        context = {"instruction": task.instruction, "goals": [asdict(g) for g in task.goals],
                   "facts": state.snapshot()["facts"], "execution_results": report["results"],
                   "evidence_ids": ["fact:" + k for k in state.facts] + ["image:" + k for k in state.observation.images]}
        prompt = self.config.prompt + '\nReturn {"criteria":[{"id":"goal ID","status":"success|failure|unknown","reason":"brief evidence basis","evidence_refs":["fact:... or image:..."]}]}. Include every goal exactly once. Never change observed facts.'
        try:
            reply = await self.client.complete("verify", prompt, context, state.observation.images)
            criteria = reply["criteria"]
            if len(criteria) != len(task.goals) or {c["id"] for c in criteria} != {g.id for g in task.goals}:
                raise ValueError("Missing or duplicate criteria")
            for criterion in criteria:
                if criterion["status"] not in ("success", "failure", "unknown"):
                    raise ValueError("Invalid criterion status")
                refs = criterion.get("evidence_refs", [])
                if any(ref not in context["evidence_ids"] for ref in refs) or (criterion["status"] == "success" and not refs):
                    raise ValueError("Unsupported evidence reference")
            status = "success" if all(c["status"] == "success" for c in criteria) else "failure" if any(c["status"] == "failure" for c in criteria) else "unknown"
            return {"mode": mode, "status": status, "criteria": criteria, "rule_criteria": rules}
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            return {"mode": mode, "status": "unknown", "reason": str(error), "criteria": [], "rule_criteria": rules}
