from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest

from primitive_vla.state.backends import StateResult
from primitive_vla.state.estimation import StateEstimator
from primitive_vla.config.schema import AdapterSpec, AppConfig, StateConfig, StateBackendSpec
from primitive_vla.runtime.session import Runtime
from primitive_vla.state.contracts import Observation, StateClaim
from primitive_vla.state.journal import StateJournal
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import CallContext, ToolCall, ToolResult


class Unsupported:
    def __init__(self, *args):
        self.calls = 0
    async def estimate(self, request, context):
        self.calls += 1
        return StateResult(status="unsupported", reason="No coverage")


class Holding:
    def __init__(self, *args):
        self.calls = 0
    async def estimate(self, request, context):
        self.calls += 1
        return StateResult(claims=[StateClaim(subject=request.subject, property=request.property, value=True,
                                              evidence=[r["id"] for r in request.evidence])])


class Malformed(Holding):
    async def estimate(self, request, context):
        return StateResult(claims=[StateClaim(subject="wrong", property=request.property, value=True, evidence=["fake"])])


class StateEstimationTests(unittest.IsolatedAsyncioTestCase):
    def state(self):
        state = StateJournal()
        state.record_result(ToolCall(name="camera", id="camera"), ToolResult(
            observations=[Observation(id="obs", sensor="front", captured_at=state.clock())]))
        return state

    async def test_explicit_fallback_and_unsupported_scope(self):
        runtime = StateEstimator(self.state())
        first, second = Unsupported(), Holding()
        runtime.register("holding", ["holding.v1"], [first, second])
        context = CallContext("estimate", monotonic() + 1)
        result, attempts = await runtime.estimate("holding", "cup", "holding.v1", ["obs"], context)
        self.assertEqual(result.status, "ok")
        self.assertEqual([a["status"] for a in attempts], ["unsupported", "ok"])
        result, _ = await runtime.estimate("holding", "cup", "position.v1", ["obs"], context)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual((first.calls, second.calls), (1, 1))

    async def test_bad_semantics_do_not_silently_fallback(self):
        runtime = StateEstimator(self.state())
        second = Holding()
        runtime.register("holding", ["holding.v1"], [Malformed(), second])
        result, _ = await runtime.estimate("holding", "cup", "holding.v1", ["obs"], CallContext("estimate", monotonic() + 1))
        self.assertEqual(result.status, "error")
        self.assertEqual(second.calls, 0)

    async def test_runtime_backend_binding_and_main_agent_use_the_same_state_contract(self):
        config = AppConfig(model=AdapterSpec(adapter="tests.integration.test_core_boundary:TextModel"),
            state=StateConfig(backends={"detector": StateBackendSpec(adapter=__name__ + ":Holding", supports=["holding.v1"])}))
        with TemporaryDirectory() as tmp:
            runtime = Runtime(config, Path(tmp), EventLog())
            try:
                await runtime.start()
                runtime.session.state.restore(self.state().snapshot())
                runtime.session.record_result(ToolCall(name="camera", id="fresh"), ToolResult(
                    observations=[Observation(id="new", sensor="front", captured_at=runtime.session.state.clock())]))
                call = ToolCall(name="state.estimate", arguments={"backend": "detector", "subject": "cup",
                    "property": "holding.v1", "evidence": ["new"]})
                result = await runtime.tools.invoke(call)
                runtime.session.record_result(call, result)
                runtime.session.state.record_claims([StateClaim(subject="cup", property="holding.v1", value=False,
                                                                evidence=["new"])], "main_agent")
                rows = runtime.session.state.query("cup")
                self.assertEqual([r["validity"] for r in rows], ["conflict", "conflict"])
                self.assertEqual(rows[0]["source"], "state:detector:0")
                self.assertTrue(result.data["state_refs"])
            finally:
                await runtime.close()
