"""Evidence adoption checks; judgment generation remains replaceable."""


class GoalPolicy:
    def __init__(self, state, config):
        self.state, self.config = state, config

    def _evidence(self, record, goal):
        payload = record["payload"]
        references = payload.get("evidence", [])
        if not references or record["historical"] or record["epoch"] != self.state.epoch:
            return None
        evidence = [self.state.records.get(ref) for ref in references]
        if any(e is None or e["kind"] not in ("observation", "claim") for e in evidence):
            return None
        if any(self.state.validity(e["id"], max_age_s=goal.max_age_s) != "current" for e in evidence):
            return None
        for item in evidence:
            if item["kind"] == "claim":
                claim = item["payload"]
                if claim["basis"] == "predicted" or claim["status"] != "known":
                    return None
                if goal.subject is not None and claim["subject"] != goal.subject:
                    return None
                current = self.state.query(claim["subject"], claim["property"])
                if not any(r["id"] == item["id"] and r["validity"] == "current" for r in current):
                    return None
        call_id = payload.get("call_id")
        if call_id:
            starts = [r["received_at"] for r in self.state.records.values()
                      if r["kind"] == "execution" and r["execution_id"] == call_id
                      and r["payload"]["event"] == "tool_started"]
            if not starts or any(e["captured_at"] < max(starts) for e in evidence):
                return None
        return min(e["captured_at"] for e in evidence)

    def check(self, goal_ids):
        for goal_id in goal_ids:
            goal = self.config.goals[goal_id]
            latest = {}
            unavailable = {}
            for reference in self.state.assessments:
                record = self.state.records[reference]
                if record["payload"].get("goal_id") != goal_id:
                    continue
                observed = self._evidence(record, goal)
                if observed is None:
                    if not record["historical"] and record["epoch"] == self.state.epoch:
                        unavailable[record["source"]] = record["received_at"]
                    continue
                rank = (observed, record["received_at"])
                source = record["source"]
                if source not in latest or rank >= latest[source][0]:
                    latest[source] = (rank, record)
            revoked = any(source not in latest or timestamp >= latest[source][1]["received_at"]
                          for source, timestamp in unavailable.items())
            if revoked or not latest or any(r["payload"]["verdict"] != "satisfied" for _, r in latest.values()):
                return f"Goal {goal_id} requires current, non-conflicting evidence of: {goal.description}"
        return None

    def before_call(self, call, definition):
        return self.check(self.config.before_tools.get(call.name, []))

    def finish(self, answer):
        return self.check(self.config.completion) if answer.status == "completed" else None
