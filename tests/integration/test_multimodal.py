import json
from time import monotonic
import unittest

from pydantic import ValidationError

from primitive_vla.media import Artifact, image_refs
from primitive_vla.state.contracts import Observation
from primitive_vla.state.journal import StateJournal
from primitive_vla.state.tools import state_tools
from primitive_vla.tools import CallContext, ToolCall, ToolResult


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
