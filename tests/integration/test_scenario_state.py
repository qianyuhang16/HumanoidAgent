"""Domain-neutral input records for navigation, manipulation and inspection."""

import json
import unittest

from primitive_vla.agent.memory import Session
from primitive_vla.capabilities.contracts import StateRequest
from primitive_vla.capabilities.runtime import StateCapabilities
from primitive_vla.execution.runtime import ExecutionManager
from primitive_vla.media import Artifact
from primitive_vla.state.contracts import Observation
from primitive_vla.state.journal import StateJournal
from primitive_vla.state.tools import state_tools
from primitive_vla.tools import ToolCall, ToolResult


class ScenarioStateTests(unittest.IsolatedAsyncioTestCase):
    def record(self, state, observation, call_id=None):
        return state.record_result(ToolCall(name="sensors.read", id=call_id or "call-" + observation.id),
                                   ToolResult(observations=[observation]))

    async def test_s02_to_s08_payloads_survive_without_robot_specific_envelope_fields(self):
        cases = [
            ("arms", "touch", "left_finger", {"pressure": [[0.0, None], [1.2, 0.4]], "deformation": None}),
            ("rover", "odometry", "floor_2", {"xy": [1.0, 2.0], "yaw": 0.5, "unit": "m", "covariance": None}),
            ("mobile_arm", "wrist", "tool", {"gripper_width_m": 0.02, "payload": {"liquid": None}}),
            ("go2", "imu", "base", {"angular_velocity": [0.1, 0.2, 0.3], "contacts": [True, None, False, True]}),
            ("gauge-6", "thermal", None, {"temperature_c": 34.0, "reading": None, "unit": None}),
            ("g1", "joints", "base", {"names": ["hip", "knee"], "positions": [0.2, 0.4], "effort": None}),
            ("door", "wrench", "wrist", {"force": [1.0, 2.0, 3.0], "hinge": None}),
        ]
        state = StateJournal(clock=lambda: 100.0)
        for index, (subject, sensor, frame, data) in enumerate(cases):
            with self.subTest(subject=subject):
                observation = Observation(id=f"obs-{index}", subject=subject, sensor=sensor, frame=frame,
                    captured_at=100.0, data=data, metadata={"vendor_field": {"optional": None}},
                    artifacts={"geometry": Artifact(kind="point_cloud", uri=f"{index}.pcd"), "rgb": None})
                self.record(state, ToolResult.model_validate_json(ToolResult(observations=[observation]).model_dump_json()).observations[0])
                record = state.read(observation.id)
                request = StateRequest(subject, "adapter.contract", (record,))
                self.assertEqual(request.evidence[0]["payload"]["data"], data)
                self.assertEqual(request.evidence[0]["payload"]["frame"], frame)
                self.assertIsNone(request.evidence[0]["payload"]["metadata"]["vendor_field"]["optional"])
                self.assertEqual(request.artifacts[f"{observation.id}:geometry"].kind, "point_cloud")
        self.assertEqual(state.query(), [])

    async def test_raw_observations_are_discoverable_after_conversation_truncation(self):
        session = Session(history_limit=1)
        session.state.clock = lambda: 100.0
        for name in ("map", "camera"):
            session.record_result(ToolCall(name="sensors.read", id=name), ToolResult(observations=[
                Observation(id=name + "-obs", subject="rover", sensor=name, captured_at=100.0,
                            data={"large": [1] * 1000}, metadata={"optional": None})]))
        session.add("feedback", text="Recent event without observations")
        context = session.context(remaining_turns=3, remaining_calls=3, remaining_seconds=3)
        self.assertEqual(len(context.observations), 1)
        self.assertNotIn("data", context.observations[0])
        self.assertEqual(context.state, ())
        runtime = ExecutionManager(state_tools(session.state, StateCapabilities(session.state)), state=session.state)
        try:
            found = await runtime.invoke(ToolCall(name="state.query", arguments={
                "kind": "observations", "subject": "rover", "sensor": "map"}))
            ref = found.data["observations"][0]["id"]
            read = await runtime.invoke(ToolCall(name="state.read", arguments={"id": ref}))
            payload = json.loads(read.data["content"])["payload"]
            self.assertEqual(payload["data"]["large"], [1] * 1000)
            rejected = await runtime.invoke(ToolCall(name="state.query", arguments={"sensor": "map"}))
            self.assertEqual(rejected.status, "rejected")
        finally:
            await runtime.close()

    def test_sensor_updates_are_scoped_to_the_observed_subject(self):
        state = StateJournal(clock=lambda: 100.0)
        for ref, subject, captured in (("a1", "robot-a", 98.0), ("b1", "robot-b", 99.0), ("a2", "robot-a", 100.0)):
            self.record(state, Observation(id=ref, sensor="front", subject=subject, captured_at=captured))
        self.assertEqual(state.validity("a1"), "stale")
        self.assertEqual(state.validity("b1"), "current")
        self.assertEqual({r["id"] for r in state.observations()}, {"a2", "b1"})

    def test_old_observation_defaults_can_be_reused_without_rewriting_history(self):
        state = StateJournal(clock=lambda: 100.0)
        observation = Observation(id="old", sensor="front", captured_at=99.0)
        self.record(state, observation)
        snapshot = state.snapshot()
        old = next(r for r in snapshot["records"] if r["id"] == "old")
        for name in ("subject", "frame", "metadata"):
            old["payload"].pop(name)
        state.restore(snapshot)
        self.record(state, observation, "reuse")
        self.assertEqual(state.validity("old"), "historical")
        self.assertNotIn("subject", state.read("old")["payload"])
        with self.assertRaises(ValueError):
            self.record(state, observation.model_copy(update={"frame": "invented"}), "rewrite")
