"""Real executor against fake feedback only. Never connect to physical ROS."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from primitive_vla.tools import ToolCall
from primitive_vla.tools.runtime import ToolRuntime
from primitive_vla_cobotmagic.agent.motion import MotionRun
from primitive_vla_cobotmagic.ik.execution import execute_plan
import test_agent
from test_hover import FakeBridge, Clock


class MotionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_agent.AgentToolsTests.asyncSetUp
    call=test_agent.AgentToolsTests.call
    detect=test_agent.AgentToolsTests.detect

    async def plan(self):
        d=await self.detect()
        result=await self.provider.plan({'detection_id':d.data['detection_id'],'arm':'left'},self.call())
        self.assertTrue(result.data['execution_ready'],result)
        self.assertEqual(result.data['duration_s'],3.)  # Fixture requests 5; Agent always uses 3.
        return result.data

    async def test_execute_streams_three_seconds_and_returns_feedback_without_keyboard(self):
        plan=await self.plan()
        positions=deepcopy(self.camera.raw['positions'])
        bridge=FakeBridge(positions);bridge.close=lambda:None
        clock=Clock()
        with patch('primitive_vla_cobotmagic.agent.motion.MotionBridge',return_value=bridge), \
             patch('primitive_vla_cobotmagic.ik.execution.time',SimpleNamespace(monotonic=clock.monotonic,sleep=clock.sleep)), \
             patch('primitive_vla_cobotmagic.ik.execution.wait_for_space') as keyboard,patch('socket.create_connection') as connect:
            result=await self.provider.execute({'plan_id':plan['plan_id']},self.call())
        self.assertTrue(result.data['arrival_verified']);self.assertEqual(result.data['nominal_duration_s'],3.)
        self.assertTrue(result.control_released)
        self.assertTrue(bridge.commands)
        for command in bridge.commands:
            self.assertEqual(command['right'],positions['right'])
            self.assertEqual(command['left'][6],positions['left'][6])
        events=[json.loads(line) for line in Path(result.data['trace']).read_text().splitlines()]
        self.assertGreaterEqual(next(e['elapsed_s'] for e in events if e['event']=='completed'),3.)
        keyboard.assert_not_called();connect.assert_not_called()
        with self.assertRaises(ValueError):await self.provider.execute({'plan_id':plan['plan_id']},self.call())
        self.assertIsNone(self.provider.observation)

    async def test_unowned_plan_and_changed_robot_never_dispatch(self):
        plan=await self.plan()
        with patch('primitive_vla_cobotmagic.agent.motion.MotionBridge') as bridge:
            with self.assertRaises(ValueError):await self.provider.execute({'plan_id':'invented'},self.call())
            bridge.assert_not_called()
        positions=deepcopy(self.camera.raw['positions']);positions['left'][0]+=.05
        bridge=FakeBridge(positions);bridge.close=lambda:None
        runtime=ToolRuntime(self.provider.tools)
        with patch('primitive_vla_cobotmagic.agent.motion.MotionBridge',return_value=bridge):
            result=await runtime.invoke(ToolCall(name='robot.execute',arguments={'plan_id':plan['plan_id']}))
        self.assertEqual(result.status,'error');self.assertTrue(result.control_released)
        self.assertFalse(bridge.commands);self.assertFalse(runtime.control_locked)
        self.assertIsNone(self.provider.last_plan)
        await runtime.close()

    async def test_refresh_invalidates_plan_before_motion(self):
        plan=await self.plan()
        await self.provider.observe({},self.call())
        with patch('primitive_vla_cobotmagic.agent.motion.MotionBridge') as bridge:
            with self.assertRaises(ValueError):await self.provider.execute({'plan_id':plan['plan_id']},self.call())
            bridge.assert_not_called()

    async def test_cancel_stops_worker_and_does_not_claim_physical_stop(self):
        plan=await self.plan()
        bridge=FakeBridge(self.camera.raw['positions']);bridge.close=lambda:None
        sent=threading.Event()
        clock=Clock()
        def sleep(duration):
            clock.sleep(duration)
            sent.set()
            # Worker yields to the test; cancellation is cooperatively checked by the real loop.
            release.wait(1)
        release=threading.Event()
        runtime=ToolRuntime(self.provider.tools)
        with patch('primitive_vla_cobotmagic.agent.motion.MotionBridge',return_value=bridge), \
             patch('primitive_vla_cobotmagic.ik.execution.time',SimpleNamespace(monotonic=clock.monotonic,sleep=sleep)):
            task=asyncio.create_task(runtime.invoke(ToolCall(name='robot.execute',arguments={'plan_id':plan['plan_id']},id='motion')))
            self.assertTrue(await asyncio.to_thread(sent.wait,5))
            task.cancel()
            # Let runtime's stop callback mark cancellation before the in-flight sample returns.
            for _ in range(100):
                if self.provider.motion_runs['motion'].cancelled.is_set():break
                await asyncio.sleep(.001)
            release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        count=len(bridge.commands)
        await asyncio.sleep(.05)
        self.assertEqual(len(bridge.commands),count)
        self.assertTrue(self.provider.motion_runs['motion'].task.done())
        self.assertTrue(runtime.control_locked)  # Existing bridge cannot acknowledge a physical stop.
        self.assertFalse(self.provider.motion_runs['motion'].completed)
        await runtime.close()
