"""Optional examples exercise the same public interfaces as external robot plugins."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.config.loader import load_config, save_config
from primitive_vla.runtime.service import TaskService


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_battery_loop_persists_observations_and_trials(self):
        config = Path(__file__).resolve().parents[1] / "configs/battery.yaml"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_config(load_config(config), path)
            service = TaskService(Path(tmp), path)
            try:
                task = await service.start("装入电池，检查电源表，需要时调整方向。")
                await service.worker
                record = service.get_task(task["id"])
                self.assertEqual(record["status"], "completed")
                trials = [r for r in record["report"]["results"] if r["tool"] == "battery.adjust"]
                self.assertEqual({r["arguments"]["side"] for r in trials}, {"left", "right"})
                self.assertTrue(record["state"]["history"])
            finally:
                await service.close()

    async def test_mock_tools_run_as_an_external_provider(self):
        from time import monotonic
        from primitive_vla.storage.events import EventLog
        from primitive_vla.tools import PluginContext, ToolCall
        from primitive_vla.tools.runtime import ToolRuntime
        from primitive_vla_demo.mock import MockRobot

        with TemporaryDirectory() as tmp:
            provider = MockRobot({"arms": ["test_arm"]}, PluginContext(Path(tmp), Path(tmp), EventLog()))
            runtime = ToolRuntime(provider.tools)
            try:
                await runtime.invoke(ToolCall(name="robot.test_arm.move", arguments={"position": [.1, .2, .3]}), monotonic() + 2)
                result = await runtime.invoke(ToolCall(name="robot.observe"), monotonic() + 2)
                self.assertEqual(result.data["arms"]["test_arm"]["position"], [.1, .2, .3])
            finally:
                await runtime.close()
                await provider.close()
