"""Evidence journal and conservative current view, separate from model conversation."""

from copy import deepcopy
from time import time
from uuid import uuid4

from primitive_vla.state.contracts import Observation, StateClaim
from primitive_vla.state.schema import StateSchemas
from primitive_vla.media import artifact_uri
from primitive_vla.verification.images import paired_images


class StateJournal:
    def __init__(self, *, clock=time, schemas=None):
        self.clock = clock
        self.schemas = schemas or StateSchemas()
        self.records = {}
        self.results = []
        self.outcomes = []
        self.assessments = []
        self.images = {}
        self.evidence_images = {}
        self.epoch = 0
        self.invalidated_at = 0.0

    def _record(self, kind, payload, source, *, record_id=None, captured_at=None,
                evidence=(), execution_id=None, epoch=None):
        record_id = record_id or uuid4().hex
        if record_id in self.records:
            raise ValueError(f"Evidence ID already exists: {record_id}")
        record = dict(id=record_id, kind=kind, payload=deepcopy(payload), source=source,
                      received_at=self.clock(), captured_at=captured_at, evidence=list(evidence),
                      execution_id=execution_id, epoch=self.epoch if epoch is None else epoch,
                      historical=False)
        self.records[record_id] = record
        return record_id

    def invalidate(self, reason, execution_id=None):
        """Physical dispatch invalidates the live view, never deletes evidence."""
        self.epoch += 1
        self.invalidated_at = self.clock()
        self.images = {}
        self.evidence_images = {}
        self._record("invalidation", {"reason": reason}, "execution", execution_id=execution_id)

    def read(self, reference):
        if reference not in self.records:
            raise ValueError(f"Unknown evidence: {reference}")
        return deepcopy(self.records[reference])

    def record_execution(self, event, call, **data):
        return self._record("execution", {"event": event, "call": call, **data}, "execution",
                            execution_id=call["id"])

    def record_assessment(self, payload, source):
        """Judgment history never replaces a controller's execution facts."""
        reference = self._record("assessment", payload, source, evidence=payload.get("evidence", []),
                                 execution_id=payload.get("call_id"))
        self.assessments.append(reference)
        return reference

    def validate_claims(self, claims, *, additional=()):
        available = set(self.records) | set(additional)
        for item in claims:
            claim = StateClaim.model_validate(item)
            self.schemas.validate(claim)
            if not set(claim.evidence) <= available:
                raise ValueError("State evidence must reference recorded observations or results")

    def record_claims(self, claims, source, *, execution_id=None):
        claims = [StateClaim.model_validate(c) for c in claims]
        self.validate_claims(claims)
        ids = []
        for claim in claims:
            evidence = [self.records[ref] for ref in claim.evidence]
            captured, epoch = self._evidence_time(evidence)
            reference = self._record("claim", claim.model_dump(), source, captured_at=captured,
                                     evidence=claim.evidence, execution_id=execution_id, epoch=epoch)
            if any(e["historical"] for e in evidence):
                self.records[reference]["historical"] = True
            ids.append(reference)
        return ids

    def is_reference(self, reference):
        """Reference-only provenance never establishes a fresh physical outcome."""
        record = self.records[reference]
        if record["kind"] == "observation" and record["payload"].get("lifetime") == "reference":
            return True
        return (record["kind"] in ("observation", "claim") and bool(record["evidence"])
                and all(self.is_reference(ref) for ref in record["evidence"]))

    def _evidence_time(self, evidence):
        # A static map can explain a fresh localization without making its
        # measurement time as old as the map. The map remains a checked dependency.
        basis = [r for r in evidence if not self.is_reference(r["id"])] or evidence
        times = [r["captured_at"] for r in basis]
        captured = min(times) if times and all(t is not None for t in times) else None
        return captured, min((r["epoch"] for r in basis), default=self.epoch)

    def validate_result(self, call, result):
        """Validate a complete tool contribution without changing the journal."""
        item = {"call_id": call.id, "tool": call.name, "arguments": deepcopy(call.arguments),
                **result.model_dump()}
        if call.id in self.records:
            if any(r == item for r in self.results):
                return item, [], [], {}  # An already recorded deduplicated result.
            raise ValueError("Call ID already belongs to different evidence")
        observations = [Observation.model_validate(o) for o in result.observations]
        aliases = {f"{call.id}:{name}": ref for name, ref in result.images.items()}
        aliases.update(paired_images(call.id, result.before_images, result.images))
        for observation in observations:
            previous = self.records.get(observation.id)
            if previous is not None and (previous["kind"] != "observation"
                    or Observation.model_validate(previous["payload"]).model_dump() != observation.model_dump()):
                raise ValueError("Observation ID reused with different content")
        new_observations = [o for o in observations if o.id not in self.records]
        ids = [call.id, *aliases, *(o.id for o in new_observations)]
        if len(ids) != len(set(ids)) or set(ids) & self.records.keys() or len({o.id for o in observations}) != len(observations):
            raise ValueError("Observation IDs must be unique")
        self.validate_claims(result.claims, additional=ids)
        return item, observations, new_observations, aliases

    def record_result(self, call, result):
        """Keep legacy call/image references while accepting typed contributions atomically."""
        item, observations, new_observations, aliases = self.validate_result(call, result)
        if call.id in self.records:
            return item
        self._record("result", item, call.name, record_id=call.id, execution_id=call.id)
        for observation in new_observations:
            self._record("observation", observation.model_dump(), call.name, record_id=observation.id,
                         captured_at=observation.captured_at, execution_id=call.id)
        for label, ref in aliases.items():
            origins = [self.records[o.id] for o in observations
                       if ref in (artifact_uri(value) for value in o.artifacts.values())]
            captured, epoch = self._evidence_time(origins)
            self._record("observation", {"artifacts": {"image": ref}}, call.name,
                         record_id=label, execution_id=call.id, evidence=[o["id"] for o in origins],
                         captured_at=captured, epoch=epoch)
            if any(o["historical"] for o in origins):
                self.records[label]["historical"] = True
        self.record_claims(result.claims, call.name, execution_id=call.id)
        self.results.append(deepcopy(item))
        for source, reference in result.images.items():
            self.images[f"{call.id}:{source}"] = reference
        if result.before_images and result.images:
            self.evidence_images = paired_images(call.id, result.before_images, result.images)
        return item

    def validity(self, reference, *, max_age_s=None):
        record = self.records.get(reference)
        if record is None:
            return "unknown"
        if record["historical"]:
            return "historical"
        reference_only = self.is_reference(reference)
        derived = record["kind"] in ("observation", "claim") and bool(record["evidence"])
        if not reference_only and not derived and record["epoch"] < self.epoch:
            return "stale"
        captured = record["captured_at"]
        if captured is None or captured > self.clock():
            return "unknown"
        if not reference_only and not derived and captured < self.invalidated_at:
            return "stale"
        sensor = record["payload"].get("sensor") if record["kind"] == "observation" else None
        if sensor and any(other["kind"] == "observation" and not other["historical"]
                          and other["payload"].get("sensor") == sensor
                          and other["payload"].get("subject") == record["payload"].get("subject")
                          and other["captured_at"] is not None
                          and captured < other["captured_at"] <= self.clock()
                          for other in self.records.values()):
            return "stale"
        ttl = record["payload"].get("ttl_s") if record["kind"] in ("claim", "observation") else None
        # Goal freshness constrains measurements; references have their own TTL.
        limits = [v for v in (ttl, None if reference_only else max_age_s) if v is not None]
        if limits and self.clock() - captured > min(limits):
            return "stale"
        if any(self.validity(ref, max_age_s=max_age_s) != "current" for ref in record["evidence"]):
            return "stale"
        return "current"

    def observations(self, subject=None, sensor=None, *, limit=50):
        """Discover canonical observations without inventing environment claims.

        Large data and metadata stay in state.read; aliases remain retrievable by
        ID but do not duplicate the canonical observation in this index.
        """
        latest = {}
        for record in self.records.values():
            payload = record["payload"]
            if record["kind"] != "observation" or "id" not in payload:
                continue
            if subject is not None and payload.get("subject") != subject:
                continue
            if sensor is not None and payload.get("sensor") != sensor:
                continue
            key = (record["source"], payload.get("subject"), payload.get("sensor") or record["id"])
            captured = record["captured_at"]
            rank = (not record["historical"], captured if captured is not None else -1, record["received_at"])
            if key not in latest or rank > latest[key][0]:
                latest[key] = (rank, record)
        rows = []
        for _, record in sorted(latest.values(), key=lambda item: item[0]):
            payload = record["payload"]
            rows.append(dict(id=record["id"], subject=payload.get("subject"), sensor=payload.get("sensor"),
                frame=payload.get("frame"), source=record["source"], captured_at=record["captured_at"],
                lifetime=payload.get("lifetime", "scene"), validity=self.validity(record["id"]),
                artifacts={name: value.get("kind") if isinstance(value, dict) else "reference" if value else None
                           for name, value in (payload.get("artifacts") or {}).items()}))
        return deepcopy(rows[-limit:])

    def query(self, subject=None, property=None, *, limit=50):
        # A late response cannot supersede a newer observation from the same source.
        latest = {}
        for record in self.records.values():
            if record["kind"] != "claim":
                continue
            payload = record["payload"]
            if subject is not None and payload["subject"] != subject:
                continue
            if property is not None and payload["property"] != property:
                continue
            key = (payload["subject"], payload["property"], record["source"])
            rank = (not record["historical"], record["captured_at"] or -1, record["received_at"])
            if key not in latest or rank > latest[key][0]:
                latest[key] = (rank, record)
        view = []
        for _, record in latest.values():
            p = record["payload"]
            view.append(dict(id=record["id"], subject=p["subject"], property=p["property"], value=p["value"],
                             status=p["status"], basis=p["basis"], source=record["source"],
                             lifetime="reference" if self.is_reference(record["id"]) else "scene",
                             validity=self.validity(record["id"]), evidence=record["evidence"]))
        candidates = deepcopy(view)
        for row in view:
            peers = [v for v in candidates if (v["subject"], v["property"]) == (row["subject"], row["property"])
                     and v["validity"] == "current" and v["status"] == "known" and v["basis"] != "predicted"]
            if row["status"] == "known" and row["validity"] == "current" and any(v["value"] != row["value"] for v in peers):
                row["validity"] = "conflict"
        return deepcopy(view[-limit:])

    def snapshot(self):
        return deepcopy(dict(version=1, records=list(self.records.values()), results=self.results,
                             outcomes=self.outcomes, assessments=self.assessments,
                             epoch=self.epoch, invalidated_at=self.invalidated_at))

    def restore(self, snapshot):
        if snapshot.get("version", 1) != 1:
            raise ValueError("Unsupported state snapshot version")
        self.records = {r["id"]: {**deepcopy(r), "historical": True} for r in snapshot.get("records", [])}
        self.results = deepcopy(snapshot.get("results", []))
        self.outcomes = [{**deepcopy(r), "historical": True} for r in snapshot.get("outcomes", [])]
        self.assessments = list(snapshot.get("assessments", []))
        self.epoch = snapshot.get("epoch", 0) + 1
        self.invalidated_at = self.clock()
        self.images = {}
        self.evidence_images = {}
