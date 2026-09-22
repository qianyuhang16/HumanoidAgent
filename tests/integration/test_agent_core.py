import asyncio
import unittest
from types import SimpleNamespace

from primitive_vla.agent.contracts import AgentContext, Decision, DecisionKind, VerificationResult
from primitive_vla.agent.core import Agent
from primitive_vla.agent.memory import InMemory
from primitive_vla.domain.models import ExecutionStatus, Fact, Observation, SkillCall, SkillResult, Task, Truth, Goal, SceneObject
from primitive_vla.domain.skills import Candidate, SkillSpec
from primitive_vla.domain.state import State
from primitive_vla.storage.events import EventLog


class FakeObserver:
    def __init__(self, state):
        self.state = state
        self.calls = 0

    async def observe(self):
        self.calls += 1
        now = float(self.calls)
        return Observation({"done": Fact(Truth.TRUE if self.calls > 1 else Truth.FALSE, "fake", now, ttl_s=None)}, now)


class FakeCatalog:
    def __init__(self, candidate):
        self.candidate = candidate

    def candidates(self, objects):
        return [self.candidate]

    def resolve(self, call, objects):
        return self.candidate


class FakeDecision:
    async def decide(self, context):
        if context.task.pending(context.state.values()):
            return Decision(DecisionKind.EXECUTE, context.candidates[0])
        return Decision(DecisionKind.FINISH)


class FakeExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, call):
        self.calls.append(call)
        return SkillResult(call.call_id, ExecutionStatus.COMPLETED, None, "ok")

    async def stop(self, call_id, reason):
        from primitive_vla.agent.contracts import StopResult
        return StopResult(True)

    async def close(self):
        pass


class FakeVerifier:
    async def verify(self, request):
        return VerificationResult("success")


class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_observe_decide_execute_verify(self):
        state = State({"object": SceneObject("object", "thing", "thing")})
        spec = SkillSpec(name="DONE", version="1", parameters={}, preconditions={}, invariants={},
                         effects={"done": "true"}, resources=[], description="done")
        candidate = Candidate(spec, {})
        context = AgentContext(state=state, observer=FakeObserver(state), catalog=FakeCatalog(candidate),
                               decision_model=FakeDecision(), executor=FakeExecutor(), verifier=FakeVerifier(),
                               memory=InMemory(), log=EventLog())
        task = Task("finish", [Goal("done", {"done": Truth.TRUE})], max_steps=3, max_observations=3)
        report = await Agent(max_steps=3, max_observations=3).run(task, context)
        self.assertEqual(report.status, "completed")
        self.assertEqual(len(context.executor.calls), 1)
