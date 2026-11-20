"""Deterministic, evidence-preserving S1/S2 and independent S7 fusion context."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Mapping


@dataclass(frozen=True)
class Relation:
    subject_id: str
    predicate: str
    object_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if not self.predicate.strip() or not self.evidence_refs or any(not x.strip() for x in self.evidence_refs):
            raise ValueError("relation needs predicate and evidence")


@dataclass(frozen=True)
class Fact:
    fact_id: str
    subject_id: str
    predicate: str
    value: str
    evidence_refs: tuple[str, ...]
    kind: str = "attribute"

    def __post_init__(self):
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if (not all((self.fact_id.strip(), self.predicate.strip(), self.value.strip(), self.evidence_refs))
                or any(not x.strip() for x in self.evidence_refs)):
            raise ValueError("fact needs id, predicate, value, evidence")


@dataclass(frozen=True)
class Description:
    object_id: str
    schema_version: str
    observation_id: str
    map_version: str
    source_kind: str
    coordinate_frame: str
    length_unit: str
    identity_scope: str
    label: str
    bbox_center: tuple[float, float, float]
    bbox_extent: tuple[float, float, float]
    evidence_refs: tuple[str, ...]
    facts: tuple[Fact, ...]
    unknown_fields: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    status: str
    snapshot_metadata: dict[str, Any]
    selected_object_id: str | None
    candidate_ids: tuple[str, ...]
    objects: tuple[Description, ...]
    relations: tuple[Relation, ...]
    facts: tuple[Fact, ...]
    missing_fact_ids: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    unknown_fields: tuple[str, ...]
    needs_followup: bool
    utf8_bytes: int
    budget_kind: str = "utf8_byte_upper_bound"

    def context_json(self) -> str:
        """Exact compact UTF-8 context payload measured by utf8_bytes.

        Budget diagnostics (utf8_bytes, budget_kind) and CLI descriptions are
        outside this payload and never claimed to fit its byte bound.
        """
        payload = asdict(self)
        payload.pop("utf8_bytes")
        payload.pop("budget_kind")
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SceneMemory:
    def __setattr__(self, name, value):
        if self.__dict__.get('_immutable_scene', False):
            raise AttributeError('derived task scene is immutable')
        object.__setattr__(self, name, value)

    @property
    def base_map_version(self):
        return getattr(self.snapshot, 'base_map_version', self.snapshot.map_version)

    def evolve_verified(self, memory, *, session_id, task_id, event_id, related_call_id,
                        execution_record_id, observation_record_id, verification_record_id,
                        expected_parent, observation_sources, verification_sources,
                        execution_sources, allow_synthetic=False, max_age_seconds=300, now=None):
        from .task_effect.evolution import evolve_verified
        return evolve_verified(self, memory, session_id=session_id, task_id=task_id, event_id=event_id,
            related_call_id=related_call_id, execution_record_id=execution_record_id,
            observation_record_id=observation_record_id, verification_record_id=verification_record_id,
            expected_parent=expected_parent, observation_sources=observation_sources,
            verification_sources=verification_sources, execution_sources=execution_sources,
            allow_synthetic=allow_synthetic, max_age_seconds=max_age_seconds, now=now)

    def __init__(self, snapshot: Any, relations: tuple[Relation, ...], facts: tuple[Fact, ...]):
        self.snapshot = snapshot
        self.relations = relations
        self.facts = facts
        self.objects = {obj.object_id: obj for obj in snapshot.objects}

    @classmethod
    def from_snapshot(cls, snapshot: Any, *, relations: list[Relation], facts: list[Fact]) -> "SceneMemory":
        from core.stage2_contracts import validate_stage_snapshot

        checked = validate_stage_snapshot(dict(snapshot) if isinstance(snapshot, Mapping) else snapshot)
        return cls._from_validated(checked, relations=relations, facts=facts)

    @classmethod
    def from_fused_snapshot(cls, snapshot: Any, *, relations: list[Relation], facts: list[Fact]) -> "SceneMemory":
        """Accept explicit rendered or point-cloud fusion without relabeling sources."""
        from core.fusion_contracts import FusedSnapshot

        schema = snapshot.get("schema_version") if isinstance(snapshot, Mapping) else getattr(snapshot, "schema_version", None)
        contract = FusedSnapshot
        if schema == "pointcloud-fusion.v1":
            from core.point_fusion_contracts import PointCloudSnapshot
            contract = PointCloudSnapshot
        if schema == "automatic-point-replay.v1":
            from core.automatic_point_replay_contracts import AutomaticPointReplaySnapshot
            contract = AutomaticPointReplaySnapshot
            # Reconstruct even an existing model to reject model_copy/duck-type bypasses.
            snapshot = snapshot.model_dump(mode="python") if isinstance(snapshot, contract) else snapshot
        checked = contract.model_validate(dict(snapshot) if isinstance(snapshot, Mapping) else snapshot)
        return cls._from_validated(checked, relations=relations, facts=facts)

    @classmethod
    def _from_validated(cls, checked: Any, *, relations: list[Relation], facts: list[Fact]) -> "SceneMemory":
        if checked.schema_version == "automatic-point-replay.v1" and (relations or facts):
            raise ValueError("automatic offline replay cannot invent semantic facts or relations")
        ids = {o.object_id for o in checked.objects}
        for relation in relations:
            if relation.subject_id not in ids or relation.object_id not in ids:
                raise ValueError("relation endpoint outside snapshot")
        for fact in facts:
            if fact.subject_id not in ids:
                raise ValueError("fact subject outside snapshot")
        if len({f.fact_id for f in facts}) != len(facts):
            raise ValueError("duplicate fact id")
        return cls(checked, tuple(relations), tuple(facts))

    def describe(self, object_id: str) -> Description:
        obj = self.objects[object_id]
        facts = tuple(sorted((f for f in self.facts if f.subject_id == object_id), key=lambda f: f.fact_id))
        known = {f.predicate for f in facts}
        unknown = tuple(x for x in ("color", "support", "graspable") if x not in known)
        if self.snapshot.schema_version in ("automatic-point-replay.v1", "task-scene.v1"):
            unknown = tuple(sorted(getattr(self.snapshot, "unobserved_fields", unknown)))
        return Description(
            object_id=obj.object_id,
            schema_version=self.snapshot.schema_version,
            observation_id=self.snapshot.observation_id,
            map_version=self.snapshot.map_version,
            source_kind=self.snapshot.source_kind,
            coordinate_frame=self.snapshot.coordinate_frame,
            length_unit=self.snapshot.length_unit,
            identity_scope=self.snapshot.identity_scope,
            label=obj.label,
            bbox_center=tuple(obj.bbox_center),
            bbox_extent=tuple(obj.bbox_extent),
            evidence_refs=tuple(obj.evidence_refs),
            facts=facts,
            unknown_fields=unknown,
        )

    def query_context(self, task: Mapping[str, Any], region_id: str, token_budget: int, *,
                      context_object_ids: tuple[str, ...] = ()) -> QueryResult:
        """Query a target, optionally retaining explicit same-source role objects.

        Extra context IDs do not bind an ambiguous target or assert capabilities.
        They enter the existing evidence closure and its original byte budget.
        """
        if (type(context_object_ids) is not tuple or any(type(i) is not str or not i.strip() for i in context_object_ids)
                or len(context_object_ids)!=len(set(context_object_ids))
                or not set(context_object_ids).issubset(self.objects)):
            raise ValueError("context object IDs must be unique original IDs from this Memory")
        if not isinstance(token_budget, int) or token_budget < 0:
            raise ValueError("token_budget must be a nonnegative integer byte-limit proxy")
        if region_id != self.snapshot.region_id:
            return self._result("region_missing", missing=("region",))
        capabilities = tuple(task.get("required_capabilities", ()))
        if capabilities:
            return self._result("unsupported_capability", missing_capabilities=capabilities)
        target_id = task.get("target_id")
        if target_id:
            candidates = (target_id,) if target_id in self.objects else ()
        else:
            label = task.get("target_label")
            candidates = tuple(sorted(o.object_id for o in self.objects.values() if o.label == label))
        if not candidates:
            return self._result("target_missing", missing=("target",))
        attributes = task.get("attributes", {})
        if attributes and len(candidates) > 1:
            # A candidate lacking evidence may also satisfy the requested
            # property; only complete evidence for all candidates can bind it.
            if all(all(len({f.value for f in self.facts if f.subject_id == candidate
                            and f.predicate == key}) == 1 for key in attributes)
                       for candidate in candidates):
                candidates = tuple(candidate for candidate in candidates if all(
                    any(f.subject_id == candidate and f.predicate == key and f.value == value
                        for f in self.facts) for key, value in attributes.items()))
        if len(candidates) > 1:
            requested = set(task.get("required_attributes", ())) | set(attributes)
            unknown = tuple(sorted(key for key in requested if any(
                len({f.value for f in self.facts if f.subject_id == candidate and f.predicate == key}) != 1
                for candidate in candidates)))
            required_ids = set(task.get("required_fact_ids", ()))
            included = set(candidates) | set(context_object_ids) | {f.subject_id for f in self.facts
                                        if f.kind == "protection" or f.fact_id in required_ids}
            chosen_relations: list[Relation] = []
            changed = True
            while changed:
                changed = False
                for relation in self.relations:
                    if relation.subject_id in included or relation.object_id in included:
                        if relation not in chosen_relations:
                            chosen_relations.append(relation)
                        for endpoint in (relation.subject_id, relation.object_id):
                            if endpoint not in included:
                                included.add(endpoint)
                                changed = True
            # Keep every candidate's original evidence, including contradictions;
            # related and protected objects remain inspectable too. Never bind one.
            chosen_facts = tuple(sorted((f for f in self.facts if f.subject_id in included),
                                        key=lambda f: f.fact_id))
            missing = tuple(sorted(required_ids - {f.fact_id for f in self.facts}))
            result = self._result("ambiguous", candidates=candidates,
                                  objects=tuple(self.describe(i) for i in sorted(included)),
                                  relations=tuple(chosen_relations), facts=chosen_facts,
                                  missing=missing, unknown=unknown, followup=True)
            if result.utf8_bytes > token_budget:
                return self._result("needs_more_budget", candidates=candidates,
                                    missing=tuple(sorted(required_ids | {f.fact_id for f in chosen_facts})),
                                    unknown=unknown, followup=True)
            return result
        if not candidates:
            return self._result("target_missing", missing=("attribute_match",))
        selected = candidates[0]
        conflicting_attributes = tuple(sorted(key for key in attributes if len({
            f.value for f in self.facts if f.subject_id == selected and f.predicate == key}) > 1))
        if conflicting_attributes:
            return self._result("attribute_conflict", candidates=candidates,
                                unknown=conflicting_attributes, followup=True)
        absent_attributes = tuple(sorted(key for key in attributes if not any(
            f.subject_id == selected and f.predicate == key for f in self.facts)))
        if absent_attributes:
            return self._result("missing_facts", selected=selected, candidates=candidates,
                                unknown=absent_attributes, followup=True)
        mismatched_attributes = tuple(sorted(key for key, value in attributes.items() if not any(
            f.subject_id == selected and f.predicate == key and f.value == value for f in self.facts)))
        if mismatched_attributes:
            return self._result("attribute_mismatch", candidates=candidates,
                                unknown=mismatched_attributes, followup=True)
        # Required properties must have one distinct evidenced value, not merely
        # be present. Keep all conflicting facts; never pick one by list order.
        required_attributes = set(task.get("required_attributes", ()))
        conflicting_required = {key for key in required_attributes if len({
            f.value for f in self.facts if f.subject_id == selected and f.predicate == key}) > 1}
        required_ids = set(task.get("required_fact_ids", ()))
        required_ids.update(f.fact_id for f in self.facts if f.kind == "protection")
        required_ids.update(f.fact_id for f in self.facts if f.subject_id == selected
                            and f.predicate in conflicting_required)
        required_ids.update(f.fact_id for f in self.facts if f.subject_id == selected
                            and f.predicate in attributes and attributes[f.predicate] == f.value)
        fact_map = {f.fact_id: f for f in self.facts}
        missing = tuple(sorted(required_ids - fact_map.keys()))
        chosen_facts = tuple(sorted((fact_map[i] for i in required_ids if i in fact_map), key=lambda f: f.fact_id))
        selected_ids = {selected} | set(context_object_ids) | {f.subject_id for f in chosen_facts}
        chosen_relations: list[Relation] = []
        # Closure from the target and mandatory facts; no relation endpoint is elided.
        changed = True
        while changed:
            changed = False
            for rel in self.relations:
                if rel.subject_id in selected_ids or rel.object_id in selected_ids:
                    if rel not in chosen_relations:
                        chosen_relations.append(rel)
                    for endpoint in (rel.subject_id, rel.object_id):
                        if endpoint not in selected_ids:
                            selected_ids.add(endpoint)
                            changed = True
        objects = tuple(self.describe(i) for i in sorted(selected_ids))
        unknown = tuple(sorted((required_attributes - {f.predicate for f in self.facts if f.subject_id == selected})
                               | conflicting_required))
        status = "attribute_conflict" if conflicting_required else "missing_facts" if missing or unknown else "ready"
        result = self._result(status, selected=selected, candidates=candidates, objects=objects,
                              relations=tuple(chosen_relations), facts=chosen_facts, missing=missing,
                              unknown=unknown, followup=bool(missing or unknown))
        if result.utf8_bytes > token_budget:
            return self._result("needs_more_budget", selected=selected, candidates=candidates,
                                missing=tuple(sorted(required_ids)), unknown=unknown, followup=True)
        return result

    def _result(self, status: str, *, selected: str | None = None, candidates: tuple[str, ...] = (),
                objects: tuple[Description, ...] = (), relations: tuple[Relation, ...] = (),
                facts: tuple[Fact, ...] = (), missing: tuple[str, ...] = (),
                missing_capabilities: tuple[str, ...] = (), unknown: tuple[str, ...] = (),
                followup: bool = False) -> QueryResult:
        metadata = self.snapshot.model_dump(mode="json", exclude={"objects"})
        if self.snapshot.schema_version in ("fusion.v1", "pointcloud-fusion.v1", "automatic-point-replay.v1", "task-scene.v1"):
            included = set(candidates)
            if selected is not None:
                included.add(selected)
            included.update(obj.object_id for obj in objects)
            included.update(fact.subject_id for fact in facts)
            for relation in relations:
                included.update((relation.subject_id, relation.object_id))
            included.update(fact.subject_id for fact in self.facts if fact.kind == "protection")
            known_facts = {fact.fact_id: fact for fact in self.facts}
            included.update(known_facts[fact_id].subject_id for fact_id in missing
                            if fact_id in known_facts)
            metadata["object_provenance"] = {
                obj.object_id: {
                    "support_observation_ids": list(obj.support_observation_ids),
                    "support_point_count": obj.support_point_count,
                    "voxel_count": obj.voxel_count,
                    "evidence_refs": list(obj.evidence_refs),
                }
                for obj in self.snapshot.objects
                if obj.object_id in included
            }
            supported = {observation_id for obj in self.snapshot.objects
                         if obj.object_id in included
                         for observation_id in obj.support_observation_ids}
            if self.snapshot.schema_version in ("automatic-point-replay.v1", "task-scene.v1"):
                # Task objects close normally; automatic source competition stays whole.
                metadata["source_objects"] = metadata.get("source_objects", [obj.model_dump(mode="json") for obj in self.snapshot.objects])
            else:
                metadata["observations"] = [ref for ref in metadata["observations"]
                                            if ref["observation_id"] in supported]
            metadata["context_scope"] = {
                "policy": "task_evidence_closure.v1",
                "snapshot_object_count": len(self.snapshot.objects),
                "included_object_count": len(metadata["object_provenance"]),
                "snapshot_observation_count": len(self.snapshot.observations),
                "included_observation_count": len(metadata["observations"]),
            }
        payload = dict(status=status, snapshot_metadata=metadata,
                       selected_object_id=selected, candidate_ids=candidates,
                       objects=objects, relations=relations, facts=facts, missing_fact_ids=missing,
                       missing_capabilities=missing_capabilities, unknown_fields=unknown,
                       needs_followup=followup)
        provisional = QueryResult(**payload, utf8_bytes=0)
        return QueryResult(**payload, utf8_bytes=len(provisional.context_json().encode("utf-8")))


__all__ = ["SceneMemory", "Fact", "Relation", "Description", "QueryResult"]
