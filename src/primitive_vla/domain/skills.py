"""Versioned skill contracts, grounding and the single feasibility boundary."""

import itertools
from dataclasses import dataclass, field
from typing import Any, Literal

from primitive_vla.domain.models import SceneObject, SkillCall, Truth, skill_signature


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    parameters: dict[str, list[str]]
    preconditions: dict[str, str]
    invariants: dict[str, str]
    effects: dict[str, str]
    resources: list[str]
    description: str
    boundary: Literal["atomic"] = "atomic"
    timeout_s: float = 2.0
    max_attempts: int = 2
    irreversible: bool = False
    # Advisory metadata for planners/documentation, never executable recovery or stop policies.
    capability: dict = field(default_factory=dict,
                             metadata={"description": "Advisory planner metadata; not a calibrated capability score."})
    recovery: str = field(default="Reobserve and replan from measured state.",
                          metadata={"description": "Documentation only; does not schedule recovery actions."})
    cancellation: str = field(default="Backend-specific stop with acknowledgement; preserve held objects.",
                              metadata={"description": "Documentation only; stop behavior is implemented by the backend."})
    instructions: str = ""
    execution: dict[str, Any] = field(default_factory=lambda: {"type": "program", "steps": []})
    mock_faults: list[str] = field(default_factory=list)

    def bind(self, templates: dict[str, str], bindings: dict[str, str]) -> dict[str, Truth]:
        return {key.format(**bindings): Truth(value) for key, value in templates.items()}


@dataclass(frozen=True)
class Candidate:
    spec: SkillSpec
    bindings: dict[str, str]

    @property
    def preconditions(self) -> dict[str, Truth]:
        return self.spec.bind(self.spec.preconditions, self.bindings)

    @property
    def effects(self) -> dict[str, Truth]:
        return self.spec.bind(self.spec.effects, self.bindings)

    @property
    def signature(self) -> str:
        return skill_signature(self.spec.name, self.spec.version, self.bindings)


@dataclass(frozen=True)
class Feasibility:
    verdict: Truth
    reasons: tuple[str, ...] = ()


def check_conditions(conditions: dict[str, Truth], values: dict[str, Truth]) -> Feasibility:
    wrong = [k for k, v in conditions.items() if values.get(k, Truth.UNKNOWN) not in (v, Truth.UNKNOWN)]
    unknown = [k for k in conditions if values.get(k, Truth.UNKNOWN) == Truth.UNKNOWN]
    if wrong:
        return Feasibility(Truth.FALSE, tuple(f"unsatisfied: {k}" for k in wrong))
    if unknown:
        return Feasibility(Truth.UNKNOWN, tuple(f"unconfirmed: {k}" for k in unknown))
    return Feasibility(Truth.TRUE)


class Registry:
    def __init__(self, specs: list[SkillSpec]):
        if any(s.boundary != "atomic" for s in specs):
            raise ValueError("Only atomic skill boundaries are supported")
        self.specs = {(s.name, s.version): s for s in specs}
        if len(self.specs) != len(specs):
            raise ValueError("Duplicate skill name/version")

    def resolve(self, call: SkillCall, objects: dict[str, SceneObject] | Any = None) -> Candidate:
        if objects is None:
            raise ValueError("Skill resolution requires scene objects")
        if hasattr(objects, "objects"):
            objects = objects.objects
        spec = self.specs.get((call.skill, call.version))
        if spec is None:
            raise ValueError(f"Unknown skill/version: {call.skill}/{call.version}")
        if set(call.bindings) != set(spec.parameters):
            raise ValueError("Bindings must match the skill parameters")
        for parameter, kinds in spec.parameters.items():
            value = call.bindings[parameter]
            if parameter == "hand":
                if value not in kinds:
                    raise ValueError(f"Unsupported hand: {value}")
            elif value not in objects or objects[value].kind not in kinds:
                raise ValueError(f"Unsupported object binding: {parameter}={value}")
        return Candidate(spec, call.bindings)

    def candidates(self, objects: dict[str, SceneObject] | Any) -> list[Candidate]:
        if hasattr(objects, "objects"):
            objects = objects.objects
        result = []
        for spec in self.specs.values():
            domains = [kinds if param == "hand" else [o.id for o in objects.values() if o.kind in kinds]
                       for param, kinds in spec.parameters.items()]
            for combination in itertools.product(*domains):
                result.append(Candidate(spec, dict(zip(spec.parameters, combination))))
        return result
