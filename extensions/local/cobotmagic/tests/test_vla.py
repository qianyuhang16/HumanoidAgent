"""VLA prerequisites, cancellation protocol and single-arm reset; fake hardware only."""
import asyncio
from copy import deepcopy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from primitive_vla.tools import ToolCall
from primitive_vla.tools.runtime import ToolRuntime
from primitive_vla_cobotmagic.agent.arm_state import ArmState
from primitive_vla_cobotmagic.agent.reset import ResetRun
from primitive_vla_cobotmagic.vla.config import ResetSettings
from primitive_vla_cobotmagic.vla.worker import Server
import test_agent
from test_hover import Clock,FakeBridge
from primitive_vla_cobotmagic.ik.execution import current_state_command,BRIDGE_LIMITS
import numpy as np

class VLATests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_agent.AgentToolsTests.asyncSetUp
    call=test_agent.AgentToolsTests.call
    detect=test_agent.AgentToolsTests.detect

    async def test_vla_needs_executed_ik_and_receipt_is_single_use(self):
        self.provider.options['vla']={'policies':{'blue':{'max_duration_s':60}}}
        self.provider.vla=SimpleNamespace(run=AsyncMock(return_value={
            'executed':True,'actions_sent':40,'control_released':True,'used_arms':['right'],
            'task_success':None,'images':{'front':'final.jpg'},
            'before_images':{'front':'initial.jpg'}}),finish_turn=AsyncMock())
        args={'policy_id':'blue','duration_s':2,'preparation_id':'invented'}
        with self.assertRaises(ValueError):await self.provider.run_vla(args,self.call())
        d=await self.detect()
        plan=(await self.provider.plan({'detection_id':d.data['detection_id'],'arm':'left'},self.call())).data
        with self.assertRaises(ValueError):await self.provider.run_vla(args,self.call())
        self.provider.vla.run.assert_not_awaited()
        def complete(run):
            run.completed=True
            return {'arrival_verified':True,'arm':'left'}
        with patch('primitive_vla_cobotmagic.agent.motion.MotionRun.run',complete):
            receipt=(await self.provider.execute({'plan_id':plan['plan_id']},self.call())).data['preparation_id']
        args['preparation_id']=receipt
        result=await self.provider.run_vla(args,self.call())
        self.assertEqual(result.images,{'front':'final.jpg'})
        self.assertEqual(result.before_images,{'front':'initial.jpg'})
        self.assertNotIn('before_images',result.data)
        self.assertEqual(self.provider.arm_state.dirty(),{'left','right'})  # IK left, VLA actually right.
        with self.assertRaises(ValueError):await self.provider.run_vla(args,self.call())
        self.provider.vla.run.assert_awaited_once()

    async def test_switch_requires_single_arm_reset_and_persists_across_turns(self):
        self.provider.arm_state.used(['left'])
        self.assertEqual(ArmState(self.root).dirty(),{'left'})
        detection=await self.detect()
        with self.assertRaisesRegex(ValueError,'reset_arm'):
            await self.provider.plan({'detection_id':detection.data['detection_id'],'arm':'right'},self.call())
        self.provider.preparations['old']={'arm':'left'}
        def complete(run):
            run.completed=True
            return {'arrival_verified':True,'arm':run.arm,'reset_completed':True}
        with patch('primitive_vla_cobotmagic.agent.reset.ResetRun.run',complete):
            result=await self.provider.reset_arm({'arm':'left'},self.call())
        self.assertTrue(result.data['arrival_verified'])
        self.assertEqual(ArmState(self.root).dirty(),set());self.assertEqual(self.provider.preparations,{})
        self.provider.arm_state.require(['right'])

    async def test_failed_reset_keeps_reset_requirement(self):
        self.provider.arm_state.used(['left'])
        with patch('primitive_vla_cobotmagic.agent.reset.ResetRun.run',side_effect=RuntimeError('test failure')):
            runtime=ToolRuntime(self.provider.tools)
            result=await runtime.invoke(ToolCall(name='robot.reset_arm',arguments={'arm':'left'}))
            self.assertEqual(result.status,'error')
            self.assertEqual(self.provider.arm_state.dirty(),{'left'})
            await runtime.close()

    async def test_reset_changes_selected_joints_and_gripper_using_fresh_feedback(self):
        original=deepcopy(self.camera.raw['positions']);original['left'][0]=.2
        bridge=FakeBridge(original);bridge.close=lambda:None
        clock=Clock()
        run=ResetRun({},ResetSettings().model_dump(),'left',self.root/'reset')
        class Event:
            def is_set(self):return False
            def wait(self,t):clock.sleep(t)
        run.cancelled=Event()
        with patch('primitive_vla_cobotmagic.agent.reset.MotionBridge',return_value=bridge), \
             patch('primitive_vla_cobotmagic.agent.reset.time',SimpleNamespace(monotonic=clock.monotonic)):
            result=run.run()
        self.assertTrue(result['arrival_verified']);self.assertTrue(run.control_released)
        self.assertGreaterEqual(clock.monotonic(),3)
        self.assertEqual(bridge.commands[-1]['left'][:6],[0.]*6)
        self.assertEqual(bridge.commands[-1]['left'][6],0.)
        for command in bridge.commands:
            self.assertEqual(command['right'],original['right'])
            self.assertTrue(0<=command['left'][6]<=original['left'][6])

    async def test_reset_from_encoder_zero_boundary_uses_bounded_commands(self):
        original=deepcopy(self.camera.raw['positions'])
        for side in ('left','right'):original[side][1:3]=[-.002,.0065]
        expected_hold=current_state_command(original)
        bridge=FakeBridge(original);bridge.close=lambda:None
        clock=Clock()
        settings=ResetSettings(gripper_position_m={'left':None,'right':None}).model_dump()
        run=ResetRun({},settings,'left',self.root/'reset-boundary')
        class Event:
            def is_set(self):return False
            def wait(self,t):clock.sleep(t)
        run.cancelled=Event()
        with patch('primitive_vla_cobotmagic.agent.reset.MotionBridge',return_value=bridge), \
             patch('primitive_vla_cobotmagic.agent.reset.time',SimpleNamespace(monotonic=clock.monotonic)):
            result=run.run()
        self.assertTrue(result['arrival_verified'])
        self.assertEqual(bridge.commands[-1]['left'][:6],[0.]*6)
        for command in bridge.commands:
            self.assertEqual(command['right'],expected_hold['right'])
            self.assertEqual(command['left'][6],expected_hold['left'][6])
            self.assertTrue(np.all(np.asarray(command['left'][:6])>=BRIDGE_LIMITS[:,0]))
            self.assertTrue(np.all(np.asarray(command['left'][:6])<=BRIDGE_LIMITS[:,1]))

class WorkerTests(unittest.TestCase):
    def test_cancel_is_serviced_while_backend_runs_and_next_run_starts_clean(self):
        entered=threading.Event();messages=[];seen=[]
        class Backend:
            def __init__(self,*args):pass
            def run(self,message,cancel):
                seen.append(message['call_id']);entered.set()
                if not cancel.wait(2):raise RuntimeError('Cancel never delivered')
                return {'control_released':True,'cancelled':True}
        server=Server(messages.append,Backend)
        server.handle({'id':0,'op':'init','options':{},'connection':{},'media':'/tmp'})
        server.handle({'id':1,'op':'run','call_id':'first'});self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeError):server.handle({'id':2,'op':'run','call_id':'overlap'})
        server.handle({'id':3,'op':'cancel','call_id':'first'});server.thread.join(2)
        self.assertFalse(server.thread.is_alive());self.assertIsNone(server.active)
        # Cancellation racing ahead of the run request must prevent dispatch too.
        server.handle({'id':4,'op':'cancel','call_id':'second'})
        server.handle({'id':5,'op':'run','call_id':'second'});server.thread.join(2)
        self.assertEqual(seen,['first','second'])
        self.assertTrue(next(m for m in messages if m['id']==5)['result']['cancelled'])
        server.handle({'id':6,'op':'close'})

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_is_reused_across_turns_and_closed_by_workspace(self):
        import tempfile
        from pathlib import Path
        from primitive_vla_cobotmagic.vla.client import client_for,close_workspace
        process=SimpleNamespace(close=AsyncMock())
        with tempfile.TemporaryDirectory() as directory,patch('primitive_vla_cobotmagic.vla.client.JsonProcess',return_value=process) as factory:
            options={'source':'/tmp/source','python_executable':'/tmp/python'}
            first=client_for(options,Path(directory),{'port':1})
            await first.finish_turn(set())
            second=client_for(options,Path(directory),{'port':1})
            self.assertIs(first,second);factory.assert_called_once();process.close.assert_not_awaited()
            await close_workspace(directory);process.close.assert_awaited_once()
