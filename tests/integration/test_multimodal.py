import json
from time import monotonic
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator
from pydantic import Field, JsonValue, ValidationError

from primitive_vla.agent.model import response_schema
from primitive_vla.agent.memory import Session
from primitive_vla.capabilities.contracts import StateResult
from primitive_vla.capabilities.runtime import StateCapabilities
from primitive_vla.contracts import Message
from primitive_vla.execution.runtime import ExecutionManager
from primitive_vla.media import Artifact, Artifacts, image_refs
from primitive_vla.state.contracts import Observation
from primitive_vla.state.journal import StateJournal
from primitive_vla.state.tools import state_tools
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import CallContext, FunctionTool, ToolCall, ToolDefinition, ToolResult
from primitive_vla.verification.contracts import GoalSpec, VerificationPolicy
from primitive_vla.verification.model import JSONOutcomeReviewer
from primitive_vla.verification.runtime import Verification


class MultimodalTests(unittest.IsolatedAsyncioTestCase):
    def result(self):
        return ToolResult(data=None, observations=[Observation(
            id="rgbd", sensor=None, captured_at=100.0, data=[0, False, None], artifacts={
                "rgb": Artifact(kind="image", uri="https://camera/frame?id=42"),
                "cloud": Artifact(kind="point_cloud", uri="cloud.ply",
                                  metadata={"frame": None, "unit": "m", "shape": [300, 3]}),
                "depth": Artifact(kind="depth", uri="depth.png"),
                "mask": None})])

    async def test_mixed_observation_roundtrip_read_and_snapshot(self):
        state = StateJournal(clock=lambda: 100.0)
        result = ToolResult.model_validate_json(self.result().model_dump_json())
        state.record_result(ToolCall(name="capture", id="capture"), result)
        self.assertEqual(state.images, {"capture:rgbd:rgb": "https://camera/frame?id=42"})
        self.assertEqual(state.validity("capture:rgbd:rgb"), "current")
        self.assertEqual(state.read("capture:rgbd:rgb")["evidence"], ["rgbd"])
        reader = state_tools(state, type("Capabilities", (), {"bindings": {}})())[1]
        read = await reader.call({"id": "rgbd"}, CallContext("read", monotonic() + 1))
        self.assertEqual(read.artifacts["cloud"].metadata["shape"], [300, 3])
        self.assertIsNone(read.artifacts["mask"])
        self.assertEqual(read.images, {"rgb": "https://camera/frame?id=42"})
        restored = StateJournal()
        restored.restore(json.loads(json.dumps(state.snapshot())))
        self.assertEqual(restored.read("rgbd")["payload"], state.read("rgbd")["payload"])
        self.assertEqual(restored.validity("rgbd"), "historical")

    def test_nulls_empty_values_and_compact_descriptors(self):
        self.assertEqual(Artifact(kind="point_cloud", uri="cloud.pcd").model_dump(),
                         {"kind": "point_cloud", "uri": "cloud.pcd"})
        self.assertIn("metadata", Artifact(kind="image", uri="x", metadata=None).model_dump())
        self.assertEqual(ToolResult(artifacts=None).artifacts, {})
        self.assertIsNone(ToolResult(data=None).data)
        for data in (False, 0, [], {}, "", [None]):
            self.assertEqual(ToolResult(data=data).model_dump()["data"], data)
        with self.assertRaises(ValidationError):
            Artifact(kind="point_cloud", uri="")
        with self.assertRaises(ValidationError):
            ToolResult(data=object())

    def test_legacy_paths_and_modality_override(self):
        self.assertEqual(image_refs({"old": "view.JPG?token=1", "missing": None,
                                     "cloud": {"kind": "point_cloud", "uri": "cloud.png"}}),
                         {"old": "view.JPG?token=1"})
        result = self.result()
        legacy = ToolResult(observations=result.observations, images={"front": "https://camera/frame?id=42"})
        self.assertEqual(list(legacy.images), ["front"])

    async def test_backend_consumes_mixed_resources_and_returns_resources_without_claims(self):
        state = StateJournal(clock=lambda: 100.0)
        state.record_result(ToolCall(name="capture", id="capture"), self.result())
        requests = []

        class Geometry:
            async def estimate(self, request, context):
                requests.append(request)
                return StateResult(artifacts={
                    "segmented": Artifact(kind="point_cloud", uri="cup.ply", metadata={"frame": None}),
                    "preview": Artifact(kind="image", uri="segmented.png")}, data=[{"score": None}])

        capabilities = StateCapabilities(state)
        capabilities.register("geometry", ["position.v1"], [Geometry()])
        runtime = ExecutionManager(state_tools(state, capabilities), state=state)
        call = ToolCall(name="state.estimate", id="segment", arguments={"backend": "geometry",
                       "subject": "cup", "property": "position.v1", "evidence": ["rgbd"]})
        result = await runtime.invoke(call)
        self.assertEqual(result.status, "ok", result.text)
        state.record_result(call, result)
        self.assertEqual(requests[0].artifacts["rgbd:cloud"].kind, "point_cloud")
        self.assertEqual(requests[0].artifacts["rgbd:rgb"].kind, "image")
        self.assertIsNone(requests[0].artifacts["rgbd:mask"])
        self.assertEqual(result.data["result"], [{"score": None}])
        self.assertEqual(result.data["state_refs"], [])
        self.assertEqual(state.query(), [])
        # Derived files are not newly captured physical observations.
        self.assertEqual(state.validity("segment:artifact:preview"), "unknown")
        await capabilities.estimate("geometry", "cup", "position.v1", ["segment"],
                                    CallContext("consume", monotonic() + 1))
        self.assertEqual(requests[1].artifacts["segment:segmented"].uri, "cup.ply")

    async def test_tool_input_schema_and_array_output_work_through_dispatch_and_model_schema(self):
        class Inputs(Message):
            artifacts: Artifacts = Field(default_factory=dict)
            hint: str | None = None
            options: JsonValue = None

        seen = []

        async def function(args, context):
            parsed = Inputs.model_validate(args)
            seen.append(parsed)
            return ToolResult(data=[False, None, 0])

        definition = ToolDefinition(name="geometry.consume", description="Consume mixed sensor inputs",
                                    parameters=Inputs.model_json_schema(),
                                    output_schema={"type": "array"})
        arguments = {"artifacts": self.result().observations[0].model_dump()["artifacts"],
                     "hint": None, "options": {"threshold": None}}
        Draft202012Validator(response_schema([definition])).validate({
            "calls": [{"name": definition.name, "arguments": arguments}], "final": None, "memory": None})
        runtime = ExecutionManager([FunctionTool(definition, function)])
        result = await runtime.invoke(ToolCall(name=definition.name, arguments=arguments))
        self.assertEqual(result.status, "ok", result.text)
        self.assertEqual(result.data, [False, None, 0])
        self.assertEqual(seen[0].artifacts["cloud"].kind, "point_cloud")
        arguments["artifacts"]["cloud"]["uri"] = 42
        rejected = await runtime.invoke(ToolCall(name=definition.name, arguments=arguments))
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(len(seen), 1)

    def test_empty_capability_success_is_not_invented(self):
        for payload in ({}, {"artifacts": None}, {"artifacts": {"cloud": None}}):
            with self.assertRaises(ValueError):
                StateResult(**payload)
        self.assertIs(StateResult(data=False).data, False)

    async def test_reviewer_receives_all_descriptors_but_only_images_are_rendered(self):
        received = []

        class Client:
            async def complete(self, purpose, prompt, payload, images):
                received.append((payload, images))
                return {"verdict": "satisfied", "reason": "fixture judgment", "evidence": ["rgbd"]}

        reviewer = JSONOutcomeReviewer.__new__(JSONOutcomeReviewer)
        reviewer.config = SimpleNamespace(max_images=4)
        reviewer.client = Client()
        for captured in (100.0, None):
            with self.subTest(captured=captured):
                session = Session()
                session.state.clock = lambda: 100.0
                result = self.result()
                result.observations[0].captured_at = captured
                session.record_result(ToolCall(name="capture", id="capture"), result)
                verification = Verification(session, [], EventLog(), reviewer=reviewer,
                    policy=VerificationPolicy(goals={"placed": GoalSpec(description="Cup placed")}, completion=["placed"]))
                record = verification.outcomes.track(None, "Cup placed", "placed")
                await verification.assess(record, session.results, monotonic() + 1)
                payload, images = received[-1]
                self.assertEqual(list(images.values()), ["https://camera/frame?id=42"])
                artifacts = payload["evidence"][0]["observations"][0]["artifacts"]
                self.assertEqual(artifacts["cloud"]["kind"], "point_cloud")
                self.assertIsNone(artifacts["cloud"]["metadata"]["frame"])
                self.assertEqual(verification.policy.check(["placed"]) is None, captured is not None)
