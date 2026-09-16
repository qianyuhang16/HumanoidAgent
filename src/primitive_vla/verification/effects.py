"""Verify skill effects against fresh observed facts."""

from primitive_vla.domain.models import EffectStatus, Truth
from primitive_vla.domain.state import State


def verify(effects: dict[str, Truth], state: State, since: float) -> tuple[EffectStatus, dict[str, str]]:
    evidence = {}
    verdicts = []
    for key, expected in effects.items():
        fact = state.facts.get(key)
        actual = Truth.UNKNOWN if fact is None or fact.timestamp < since else fact.current(confidence_threshold=state.confidence_threshold)
        verdicts.append(actual == expected if actual != Truth.UNKNOWN else None)
        evidence[key] = f"expected={expected.value}, observed={actual.value}, source={fact.source if fact else 'missing'}"
    if False in verdicts:
        return EffectStatus.FAILURE, evidence
    if None in verdicts:
        return EffectStatus.UNKNOWN, evidence
    return EffectStatus.SUCCESS, evidence
