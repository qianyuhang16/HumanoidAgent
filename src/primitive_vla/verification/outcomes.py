"""An evidence-linked journal of judgments, with no dispatch or completion policy."""

from copy import deepcopy

from .images import paired_images


class Outcomes:
    def __init__(self, session, log):
        self.session, self.log = session, log

    def result(self, call_id):
        return next((r for r in self.session.results if r["call_id"] == call_id), None)

    def find(self, call_id):
        return next((r for r in self.session.outcomes if r["call_id"] == call_id), None)

    def track(self, result, goal):
        record = self.find(result["call_id"])
        if record is None:
            record = {"call_id": result["call_id"], "tool": result["tool"],
                      "arguments": deepcopy(result["arguments"]), "historical": False,
                      "goal": goal, "verdict": "unknown", "reason": "Not assessed yet",
                      "evidence": [], "source": None, "revision": 0}
            self.session.outcomes.append(record)
        return record

    def validate(self, assessments, definitions):
        """Validate the whole response before recording any judgment or dispatching tools."""
        available = set()
        for result in self.session.results:
            call_id = result["call_id"]
            available.add(call_id)
            available.update(f"{call_id}:{name}" for name in result.get("images", {}))
            available.update(paired_images(call_id, result.get("before_images", {}), result.get("images", {})))
        seen = set()
        for assessment in assessments:
            result = self.result(assessment.call_id)
            definition = definitions.get(result["tool"]) if result else None
            if result is None or definition is None or definition.read_only:
                raise ValueError("Assessment call_id must reference an existing action result")
            if assessment.call_id in seen:
                raise ValueError("Return at most one assessment per action in a response")
            if not set(assessment.evidence) <= available:
                raise ValueError("Assessment evidence must reference recorded calls or image labels")
            seen.add(assessment.call_id)

    def publish(self, record, assessment, source):
        data = assessment.model_dump(exclude={"call_id"}, exclude_none=True)
        record.update(data)
        record.update(source=source, revision=record.get("revision", 0) + 1)
        # Revisions stay in append-only history even when the current view changes.
        event = {"call_id": record["call_id"], "goal": record["goal"], **data,
                 "source": source, "revision": record["revision"]}
        self.session.add("outcome_review", **event)
        self.log.write("outcome_review_finished", **event)
