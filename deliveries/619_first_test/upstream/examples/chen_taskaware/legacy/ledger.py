"""Narrow v4/v4_1 semantic port; provenance is in SOURCE_PROVENANCE.json.

Only successful, independently verified containment can advance a scene. Current retention
and historical replay are distinct boundaries: the former checks freshness BEFORE append;
the latter rechecks original bytes and structure without renewing or re-aging old evidence.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import math
import time

from primitive_vla.contracts import Domain, Record

from .evidence import check_resources, digest, judge, make_context

SOURCE = "chen.scene-retention"
PLACE = "execution.chen.place"
OBSERVE = "state.chen.observe"
VERIFY = "verification.chen.verify"


def validate_task(task):
    keys = {"task_id", "instruction", "scene_version", "map_version", "objects", "goals",
            "protected_object_ids", "movable_object_ids", "retain_on_publish",
            "max_evidence_age_s", "constraints"}
    if not isinstance(task, dict) or set(task) != keys:
        raise ValueError("task fields do not match the example contract")
    for name in ("task_id", "instruction", "scene_version", "map_version"):
        if not isinstance(task[name], str) or not task[name].strip():
            raise ValueError("nonempty string required: " + name)
    objects = task["objects"]
    if (not isinstance(objects, list) or not objects
            or any(not isinstance(o, dict) or set(o) != {"object_id", "label"}
                   or any(not isinstance(v, str) or not v for v in o.values()) for o in objects)):
        raise ValueError("objects require object_id and label")
    ids = {o["object_id"] for o in objects}
    if len(ids) != len(objects):
        raise ValueError("duplicate object identities")
    for key in ("protected_object_ids", "movable_object_ids"):
        values = task[key]
        if (not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                or len(set(values)) != len(values) or not set(values) <= ids):
            raise ValueError("invalid " + key)
    if set(task["protected_object_ids"]) & set(task["movable_object_ids"]):
        raise ValueError("protected and movable overlap")
    goals = task["goals"]
    if not isinstance(goals, list) or len(goals) != 2:
        raise ValueError("this narrow Li adapter requires two named item goals")
    goal_keys = {"goal_id", "object_id", "container_id", "subject", "li_item", "visit"}
    for goal in goals:
        if (not isinstance(goal, dict) or set(goal) != goal_keys
                or any(not isinstance(goal[k], str) or not goal[k]
                       for k in goal_keys - {"visit"})):
            raise ValueError("invalid named goal")
        if (goal["object_id"] not in task["movable_object_ids"]
                or goal["container_id"] not in ids
                or goal["object_id"] == goal["container_id"]
                or type(goal["visit"]) is not int or goal["visit"] < 1
                or goal["subject"] != f'in({goal["object_id"]},{goal["container_id"]})'):
            raise ValueError("goal identity, protection or subject mismatch")
    for key in ("goal_id", "object_id", "visit"):
        if len({g[key] for g in goals}) != 2:
            raise ValueError("duplicate goal " + key)
    if ({g["li_item"] for g in goals} != {"甲", "乙"}
            or len({g["container_id"] for g in goals}) != 1):
        raise ValueError("Li roles must be distinct and share one container")
    retain = task["retain_on_publish"]
    gids = {g["goal_id"] for g in goals}
    if not isinstance(retain, dict) or not set(retain) <= gids:
        raise ValueError("invalid retention declarations")
    for trigger, kept in retain.items():
        if (not isinstance(kept, list) or any(not isinstance(v, str) for v in kept)
                or len(set(kept)) != len(kept) or not set(kept) <= gids - {trigger}):
            raise ValueError("retention must name distinct other goals")
    age = task["max_evidence_age_s"]
    if type(age) not in (int, float) or not math.isfinite(age) or not 0 < age <= 300:
        raise ValueError("evidence age must be finite and in (0, 300]")
    if not isinstance(task["constraints"], list):
        raise ValueError("constraints must be a list of public declarations")
    digest(task)
    return deepcopy(task)


class RevisionError(ValueError):
    pass


class Ledger:
    def __init__(self, memory, task, session, *, clock=time.time):
        self.memory, self.task, self.session = memory, deepcopy(task), session
        self.original_sha, self.clock = digest(task), clock
        binding = [r for r in self.events() if r.event_type == "task_binding"]
        if binding:
            if len(binding) != 1 or binding[0].payload != task:
                raise RevisionError("original task or binding changed on reopen")
            self.verify_chain()
        else:
            self.append("task_binding", task)

    def events(self):
        result, offset = [], 0
        while True:
            page = self.memory.history(self.session.id, limit=100, offset=offset)
            result.extend(page)
            if len(page) < 100:
                return sorted(result, key=lambda r: (r.timestamp, r.id))
            offset += len(page)

    def append(self, event, payload):
        return self.memory.write(Record(
            self.session.id, self.session.task_id, SOURCE, Domain.STATE, event,
            "refused" if event == "scene_publication_refused" else "recorded", deepcopy(payload),
        ))

    def goal(self, goal_id):
        return next(g for g in self.task["goals"] if g["goal_id"] == goal_id)

    def publications(self):
        return sorted((r for r in self.events() if r.source == SOURCE
                       and r.event_type == "scene_revision_published"),
                      key=lambda r: r.payload["after"]["ordinal"])

    def initial(self):
        return {"revision": self.task["scene_version"], "ordinal": 0,
                "relations": [], "effects": {}, "moved_geometry_unknown": []}

    def state(self):
        if digest(self.task) != self.original_sha:
            raise RevisionError("original task was mutated")
        chain = self.publications()
        return deepcopy(chain[-1].payload["after"] if chain else self.initial())

    def calls(self):
        rows = self.events()
        starts = {r.call_id: r for r in rows if r.event_type == "tool_started"}
        ends = {r.call_id: r for r in rows
                if r.event_type in {"tool_finished", "verification"}}
        return starts, ends

    def view(self, exclude_call=None):
        state = self.state()
        starts, ends = self.calls()
        starts.pop(exclude_call, None)
        goals = []
        for goal in self.task["goals"]:
            placements = [r for r in starts.values() if r.source == PLACE
                          and r.payload["arguments"]["goal_id"] == goal["goal_id"]]
            placements.sort(key=lambda r: (r.timestamp, r.id))
            latest = placements[-1] if placements else None
            effect = state["effects"].get(goal["goal_id"])
            row = {**deepcopy(goal), "status": "verified" if effect and effect["valid"]
                   else "stale" if effect else "pending", "placement_call_id": None,
                   "observation_call_id": None, "verification_call_id": None,
                   "verification_conclusion": None, "attempt": len(placements) + 1}
            if latest and latest.payload["arguments"]["scene_revision"] == state["revision"]:
                terminal = ends.get(latest.call_id)
                row.update(attempt=latest.payload["arguments"]["attempt"])
                if terminal and terminal.status == "completed":
                    row["placement_call_id"] = latest.call_id
                    observations = [r for r in starts.values() if r.source == OBSERVE
                                    and r.payload["arguments"]["related_call_id"] == latest.call_id
                                    and r.call_id in ends
                                    and ends[r.call_id].status == "completed"]
                    if observations:
                        row["observation_call_id"] = observations[-1].call_id
                        # Native terminal Verification for this exact observation, if any;
                        # failure/uncertain stays visible and never advances the scene.
                        verdicts = [r for r in starts.values() if r.source == VERIFY
                                    and r.payload["arguments"]["observation_call_id"]
                                    == row["observation_call_id"]
                                    and r.call_id in ends
                                    and ends[r.call_id].status == "completed"]
                        if verdicts:
                            verdicts.sort(key=lambda r: (r.timestamp, r.id))
                            terminal = ends[verdicts[-1].call_id]
                            row["verification_call_id"] = terminal.call_id
                            row["verification_conclusion"] = (
                                terminal.payload["result"]["verification"]["conclusion"])
            goals.append(row)
        return {"external_task_id": self.task["task_id"], "original_task": deepcopy(self.task),
                "scene": state, "goals": goals,
                "remaining": [g["goal_id"] for g in goals if g["status"] != "verified"]}

    def proof(self, verification):
        starts, ends = self.calls()
        vargs = starts[verification.call_id].payload["arguments"]
        ex = ends[vargs["related_call_id"]]
        ob = ends[vargs["observation_call_id"]]
        records = {"execution": ex, "observation": ob, "verification": verification}
        records.update({k + "_start": starts[r.call_id] for k, r in list(records.items())})
        return {"goal_id": vargs["goal_id"], "scene_revision": vargs["scene_revision"],
                "records": {k: r.id for k, r in records.items()},
                "digests": {k: digest(r.to_dict()) for k, r in records.items()}}

    def check_proof(self, proof, *, fresh):
        """Trust boundary for native evidence; never consumes model-written notes."""
        records = {k: self.memory.evidence(rid) for k, rid in proof["records"].items()}
        if set(records) != {"execution", "observation", "verification",
                            "execution_start", "observation_start", "verification_start"}:
            raise RevisionError("incomplete proof")
        goal = self.goal(proof["goal_id"])
        now, age = self.clock(), self.task["max_evidence_age_s"]
        for key, record in records.items():
            if digest(record.to_dict()) != proof["digests"][key]:
                raise RevisionError("native proof digest changed: " + key)
            if (record.session_id, record.task_id) != (self.session.id, self.session.task_id):
                raise RevisionError("native proof belongs to another session/task")
            check_resources(self.memory, record.resource_refs)
            if fresh and (not 0 <= now - record.timestamp <= age
                          or record.expires_at is not None and record.expires_at <= now):
                raise RevisionError("proof expired or in future; no renewal permitted")
        ex, ob, ver = (records[k] for k in ("execution", "observation", "verification"))
        if fresh:
            starts, ends = self.calls()
            placements = [r for r in starts.values() if r.source == PLACE
                          and r.payload["arguments"]["goal_id"] == goal["goal_id"]
                          and r.call_id in ends and ends[r.call_id].status == "completed"]
            latest = max(placements, key=lambda r: (r.timestamp, r.id), default=None)
            if latest is None or latest.call_id != ex.call_id:
                raise RevisionError("retained proof is not the goal's latest completed placement")
        expected = (("execution", PLACE, Domain.EXECUTION),
                    ("observation", OBSERVE, Domain.STATE),
                    ("verification", VERIFY, Domain.VERIFICATION))
        for key, source, domain in expected:
            record, start = records[key], records[key + "_start"]
            args = start.payload["arguments"]
            if (record.source != source or start.source != source or record.domain != domain
                    or start.domain != domain or record.status != "completed"
                    or start.event_type != "tool_started" or start.call_id != record.call_id
                    or start.timestamp > record.timestamp
                    or record.event_type != ("verification" if key == "verification"
                                             else "tool_finished")):
                raise RevisionError("proof source/domain/terminal/call mismatch")
            if (args["external_task_id"] != self.task["task_id"]
                    or args["scene_revision"] != proof["scene_revision"]
                    or args["goal_id"] != goal["goal_id"] or args["visit"] != goal["visit"]):
                raise RevisionError("proof goal/visit/revision mismatch")
        eargs = records["execution_start"].payload["arguments"]
        identity = {k: eargs[k] for k in
                    ("external_task_id", "scene_revision", "goal_id", "visit", "attempt")}
        identity.update(session_id=self.session.id, task_id=self.session.task_id)
        for key in ("execution", "observation", "verification"):
            payload = records[key].payload["result"]["payload"]
            if payload["identity"] != identity or payload["synthetic"] is not True:
                raise RevisionError("provider proof identity or synthetic marker differs")
            if records[key + "_start"].payload["arguments"]["attempt"] != identity["attempt"]:
                raise RevisionError("attempt differs")
        oargs = records["observation_start"].payload["arguments"]
        vargs = records["verification_start"].payload["arguments"]
        if (oargs["related_call_id"] != ex.call_id or vargs["related_call_id"] != ex.call_id
                or vargs["observation_call_id"] != ob.call_id):
            raise RevisionError("cross-call observation or Verification")
        observation = ob.payload["result"]["payload"]
        case = observation["case"]
        if case["task_context"] != make_context(
                self.task, goal, identity, ex.payload["result"]["payload"]["ended_at"], ex.call_id):
            raise RevisionError("Li task context differs from native placement")
        own = {r.id: asdict(r) for r in ob.resource_refs}
        if (len(case["resources"]) != len(own)
                or {r["id"]: r for r in case["resources"]} != own):
            raise RevisionError("case resources differ from native observation resources")
        if not ex.timestamp < ob.timestamp <= ver.timestamp:
            raise RevisionError("execution/observation/Verification order differs")
        for observed in case["observations"]:
            captured = observed["captured_at"]
            if not ex.timestamp < captured <= ob.timestamp:
                raise RevisionError("capture must follow native execution receipt")
            if fresh and not 0 <= now - captured <= age:
                raise RevisionError("capture expired; no renewal permitted")
            # Each observation payload is backed by an independently stored blob.
            for rid in observed["resource_refs"]:
                raw = json_resource(self.memory, rid)
                if (raw["payload"] != observed["payload"] or raw["captured_at"] != captured
                        or raw["synthetic"] is not True):
                    raise RevisionError("observation blob does not support its payload")
        result = ver.payload["result"]
        verdict = judge(observation)
        verification = result["verification"]
        if (verdict != result["payload"]["verdict"] or verdict["status"] != "ok"
                or verdict["conclusion"] != "success"
                or result["payload"]["observation_call_id"] != ob.call_id
                or verification["conclusion"] != "success"
                or verification["subject"] != goal["subject"]
                or verification["related_call_id"] != ex.call_id
                or verification["failure_type"] is not None):
            raise RevisionError("independent named Verification does not support the effect")
        native_refs = check_resources(self.memory, ver.resource_refs)
        if not verification["evidence"] or any(
                native_refs.get(r["id"]) != r for r in verification["evidence"]):
            raise RevisionError("Verification citations are not intact native resources")
        return {"chen_in_proposition": goal["subject"],
                "li_proposition_id": verdict["subject"],
                "counterpart_object_subject": goal["object_id"]}

    def advance(self, before, proof, *, fresh):
        if proof["scene_revision"] != before["revision"]:
            raise RevisionError("proof is not on the current parent revision")
        subjects = self.check_proof(proof, fresh=fresh)
        goal = self.goal(proof["goal_id"])
        after = deepcopy(before)
        revision = "task-scene-" + digest({"parent": before["revision"], "proof": proof})
        retained = self.task["retain_on_publish"].get(goal["goal_id"], [])
        for gid in retained:
            effect = after["effects"].get(gid)
            kept = self.goal(gid)
            if (not effect or not effect["valid"]
                    or goal["object_id"] in {kept["object_id"], kept["container_id"]}):
                raise RevisionError("retention must cover an unaffected valid effect")
            self.check_proof(effect["proof"], fresh=fresh)
        for gid, effect in after["effects"].items():
            effect["valid"] = effect["valid"] and gid in retained
        after["effects"][goal["goal_id"]] = {
            "valid": True, "proof": deepcopy(proof), "subjects": subjects,
        }
        after["relations"] = [r for r in after["relations"]
                              if goal["object_id"] not in (r["subject"], r["object"])]
        after["relations"].append({"subject": goal["object_id"], "predicate": "inside",
                                   "object": goal["container_id"],
                                   "verification_record_id": proof["records"]["verification"]})
        after["moved_geometry_unknown"] = sorted(
            set(after["moved_geometry_unknown"]) | {goal["object_id"]})
        after.update(revision=revision, ordinal=before["ordinal"] + 1)
        return after

    def publish_pending(self, active_calls, router_active):
        if active_calls or router_active or any(
                r.session_id == self.session.id for r in self.memory.unfinished_calls()):
            return
        rows = self.events()
        if any(r.event_type == "hook_error" for r in rows):
            raise RevisionError("hook fault blocks further publication/planning")
        published = {r.payload["proof"]["records"]["verification"] for r in self.publications()}
        candidates = [r for r in rows if r.source == VERIFY and r.event_type == "verification"
                      and r.status == "completed" and r.id not in published
                      and r.payload["result"]["verification"]["conclusion"] == "success"]
        for record in candidates:
            before = self.state()
            try:
                proof = self.proof(record)
                after = self.advance(before, proof, fresh=True)
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.append("scene_publication_refused", {
                    "revision": before["revision"], "verification_record_id": record.id,
                    "error_type": type(error).__name__, "detail": str(error),
                    "chain_unchanged": True, "validity_extended": False,
                })
                raise RevisionError("publication refused before append: " + str(error)) from error
            # No await between validation and publication; one owner/event loop only.
            self.append("scene_revision_published", {
                "before_sha256": digest(before), "proof": proof, "after": after,
            })

    def verify_chain(self):
        before = self.initial()
        for row in self.publications():
            if row.payload["before_sha256"] != digest(before):
                raise RevisionError("revision parent digest differs")
            after = self.advance(before, row.payload["proof"], fresh=False)
            if after != row.payload["after"]:
                raise RevisionError("revision cannot be reproduced from original evidence")
            before = after
        return before


def json_resource(memory, rid):
    return json.loads(memory.read_resource(rid))
