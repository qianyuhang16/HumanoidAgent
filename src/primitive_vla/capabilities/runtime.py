"""Configured state backend selection, explicit fallback and evidence validation."""

import asyncio
from copy import deepcopy
from time import monotonic

from primitive_vla.capabilities.contracts import StateRequest, StateResult


class StateCapabilities:
    def __init__(self, state):
        self.state = state
        self.bindings = {}

    def register(self, name, supports, backends, fallback_on=("unsupported",)):
        if name in self.bindings:
            raise ValueError(f"Duplicate state backend: {name}")
        if not supports or not set(supports) <= self.state.schemas.schemas.keys():
            raise ValueError("State backend must declare supported, registered contracts")
        if not backends or any(not callable(getattr(b, "estimate", None)) for b in backends):
            raise TypeError("State backend must implement estimate(request, context)")
        self.bindings[name] = (tuple(supports), tuple(backends), frozenset(fallback_on))

    def descriptions(self):
        return {name: list(binding[0]) for name, binding in self.bindings.items()}

    async def estimate(self, name, subject, property, evidence, context):
        if name not in self.bindings:
            raise ValueError("Unknown state backend")
        supports, backends, fallback_on = self.bindings[name]
        if property not in supports:
            return StateResult(status="unsupported", reason=f"{name} does not support {property}"), []
        records = tuple(self.state.read(ref) for ref in evidence)
        attempts = []
        for index, backend in enumerate(backends):
            context.check_active()
            try:
                result = StateResult.model_validate(await asyncio.wait_for(backend.estimate(
                    StateRequest(subject, property, deepcopy(records)), context),
                    timeout=max(.001, context.deadline - monotonic())))
                for claim in result.claims:
                    if claim.subject != subject or claim.property != property:
                        raise ValueError("Backend changed the requested subject or contract")
                    if not set(claim.evidence) <= set(evidence):
                        raise ValueError("Backend cited evidence outside its request")
                    self.state.schemas.validate(claim)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                result = StateResult(status="error", reason=f"{type(error).__name__}: {error}")
            attempts.append({"backend": name, "implementation": index, "status": result.status,
                             "reason": result.reason})
            if result.status not in fallback_on:
                break
        return result, attempts
