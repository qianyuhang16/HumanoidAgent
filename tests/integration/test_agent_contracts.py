import asyncio
import unittest

from primitive_vla.agent.contracts import AgentContext, Decision, DecisionKind, StopResult, VerificationResult
from primitive_vla.agent.core import Agent
from primitive_vla.agent.memory import InMemory
from primitive_vla.domain.models import ExecutionStatus, Fact, Goal, Observation, SceneObject, SkillResult, Task, Truth
from primitive_vla.domain.skills import Candidate, SkillSpec
from primitive_vla.domain.state import State
from primitive_vla.storage.events import EventLog


class Observer:
    def __init__(self): self.calls = 0
    async def observe(self):
        self.calls += 1
        return Observation({"done": Fact(Truth.TRUE if self.calls > 1 else Truth.FALSE, "fake", self.calls, ttl_s=None)})


class Catalog:
    def __init__(self, candidate): self.candidate = candidate
    def candidates(self, objects): return [self.candidate]
    def resolve(self, call, objects): return self.candidate


class Executor:
    def __init__(self, status=ExecutionStatus.COMPLETED): self.calls=[]; self.status=status; self.stops=[]
    async def execute(self, call):
        self.calls.append(call)
        return SkillResult(call.call_id, self.status, "unknown", "result")
    async def stop(self, call_id, reason):
        self.stops.append((call_id, reason)); return StopResult(True)
    async def close(self): pass


class FakeDecision:
    async def decide(self, context):
        return Decision(DecisionKind.EXECUTE, context.candidates[0]) if context.task.pending(context.state.values()) else Decision(DecisionKind.FINISH)


class Verifier:
    def __init__(self, status="success"): self.status=status; self.requests=[]
    async def verify(self, request): self.requests.append(request); return VerificationResult(self.status)


def context(verifier=None, executor=None):
    state = State({"object": SceneObject("object", "thing", "thing")})
    spec = SkillSpec("DONE", "1", {}, {}, {}, {"done": "true"}, [], "done", max_attempts=2)
    candidate = Candidate(spec, {})
    return AgentContext(state, Observer(), Catalog(candidate), FakeDecision(), executor or Executor(), verifier or Verifier(), InMemory(), EventLog())


class AgentContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_completion_and_memory_order(self):
        ctx = context(); report = await Agent(max_steps=3, max_observations=3).run(Task("x", [Goal("done", {"done": Truth.TRUE})], max_steps=3, max_observations=3), ctx)
        self.assertEqual(report.status, "completed")
        self.assertEqual(len(ctx.executor.calls), 1)
        self.assertEqual([e.name for e in ctx.memory.events], ["observed", "decision", "executed", "observed", "verified"])

    async def test_failed_verification_retries_then_fails(self):
        verifier = Verifier("failure"); ctx = context(verifier=verifier)
        report = await Agent(max_steps=4, max_observations=5).run(Task("x", [Goal("done", {"done": Truth.TRUE})], max_steps=4, max_observations=5), ctx)
        self.assertEqual(report.status, "failed")
        self.assertEqual(len(ctx.executor.calls), 2)

    async def test_unknown_verification_does_not_claim_success(self):
        ctx = context(verifier=Verifier("unknown"))
        report = await Agent(max_steps=1, max_observations=3).run(Task("x", [Goal("done", {"done": Truth.TRUE})], max_steps=1, max_observations=3), ctx)
        self.assertNotEqual(report.status, "completed")
