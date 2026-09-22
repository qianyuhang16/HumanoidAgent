"""Independent rule verifier for skill effects and task goals."""

from primitive_vla.agent.contracts import VerificationRequest, VerificationResult
from primitive_vla.domain.models import Truth


class RuleVerifier:
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        values = request.state.values()
        if request.candidate is not None:
            criteria = []
            for key, expected in request.candidate.effects.items():
                actual = values.get(key, Truth.UNKNOWN)
                status = "success" if actual == expected else "failure" if actual != Truth.UNKNOWN else "unknown"
                criteria.append({"id": key, "status": status, "evidence_refs": ["fact:" + key]})
            status = "success" if criteria and all(c["status"] == "success" for c in criteria) else \
                "failure" if any(c["status"] == "failure" for c in criteria) else "unknown"
            return VerificationResult(status, tuple(criteria))
        criteria = []
        for goal in request.task.goals:
            statuses = [values.get(key, Truth.UNKNOWN) for key in goal.conditions]
            status = "success" if statuses and all(v == expected for v, expected in zip(statuses, goal.conditions.values())) else \
                "failure" if any(v != Truth.UNKNOWN and v != expected for v, expected in zip(statuses, goal.conditions.values())) else "unknown"
            criteria.append({"id": goal.id, "status": status,
                             "evidence_refs": ["fact:" + key for key in goal.conditions]})
        if criteria and all(item["status"] == "success" for item in criteria):
            status = "success"
        elif any(item["status"] == "failure" for item in criteria):
            status = "failure"
        else:
            status = "unknown"
        return VerificationResult(status, tuple(criteria))
