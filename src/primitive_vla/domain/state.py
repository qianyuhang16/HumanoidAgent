"""Measured state updated exclusively from observations."""

from dataclasses import asdict

from primitive_vla.domain.models import Fact, Observation, SceneObject, Truth


class State:
    def __init__(self, objects: dict[str, SceneObject], confidence_threshold: float = 0.5):
        self.objects = objects
        self.confidence_threshold = confidence_threshold
        self.facts: dict[str, Fact] = {}
        self.version = 0
        self.observation = Observation({}, timestamp=0.0)

    def update(self, observation: Observation) -> None:
        # An omitted/occluded fact is retained until its TTL expires. Older
        # messages cannot overwrite newer evidence, including event evidence.
        for key, fact in observation.facts.items():
            previous = self.facts.get(key)
            if previous is None or fact.timestamp >= previous.timestamp:
                self.facts[key] = fact
        if observation.timestamp >= self.observation.timestamp:
            self.observation = observation
        self.version += 1

    def values(self) -> dict[str, Truth]:
        return {key: fact.current(confidence_threshold=self.confidence_threshold) for key, fact in self.facts.items()}

    def snapshot(self) -> dict:
        return {"version": self.version, "facts": {k: asdict(v) for k, v in self.facts.items()},
                "observation": asdict(self.observation)}
