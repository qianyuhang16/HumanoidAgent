"""Operator recovery preserves history and requires fresh stationary feedback."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from time import monotonic
from unittest.mock import AsyncMock, patch

from primitive_vla.storage.tasks import TaskStore
from primitive_vla_cobotmagic.agent.recovery import stationary_feedback
import test_agent_web


class Clock:
    def __init__(self):self.now=monotonic()
    def monotonic(self):return self.now
    async def sleep(self,seconds):self.now+=seconds


class Feedback:
    def __init__(self,clock,mode='still'):
        self.clock,self.mode=clock,mode
        self.calls=0
        self.close=AsyncMock()

    async def observation(self,context):
        context.check_active()
        self.calls+=1
        stamp=1_000_000_000+round(self.clock.now*1e9)
        joints={side:[0.]*7 for side in ('left','right')}
        if self.mode=='moving':joints['left'][0]=self.clock.now*.05
        if self.mode=='gripper':joints['right'][6]=self.clock.now*.01
        stamps={side:stamp for side in joints}
        if self.mode=='stale':stamps={side:stamp-1_000_000_000 for side in joints}
        if self.mode=='frozen':stamps={side:1_000_000_000 for side in joints}
        return {'ok':True,'timestamp_ns':stamp,'joint_timestamps_ns':stamps,'positions':joints}


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_still_requires_full_fresh_window_and_rejects_bad_feedback(self):
        for mode in ('still','moving','gripper','stale','frozen'):
            clock=Clock();bridge=Feedback(clock,mode)
            with self.subTest(mode=mode), \
                 patch('primitive_vla_cobotmagic.agent.recovery.monotonic',clock.monotonic), \
                 patch('primitive_vla_cobotmagic.agent.recovery.sleep',clock.sleep):
                if mode=='still':
                    evidence=await stationary_feedback(bridge,timeout_s=2.)
                    self.assertGreaterEqual(evidence['window_s'],1.)
                    self.assertGreaterEqual(evidence['samples'],5)
                else:
                    with self.assertRaises(ValueError):await stationary_feedback(bridge,timeout_s=2.)


class RecoveryWebTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_agent_web.WebTests.asyncSetUp

    def lock(self):
        store=self.app.state.service.store
        store.create('uncertain','old task',{})
        store.event('uncertain',{'event':'tool_started','read_only':False,'call':{'id':'old-call'}})
        store.event('uncertain',{'event':'tool_finished','call':{'id':'old-call'},'result':{'control_released':False}})
        store.finish('uncertain',{'status':'blocked','reason':'stop unconfirmed'},{'saved':'history'})
        return store

    async def test_recovery_fences_worker_reads_feedback_and_preserves_tasks_on_restart(self):
        store=self.lock();before=deepcopy(store.get('uncertain'))
        clock=Clock();feedback=Feedback(clock);order=[]
        async def fence(*args):order.append('fenced')
        def camera(*args,**kwargs):
            self.assertEqual(order,['fenced'])
            return feedback
        # No robot model or motion transport may be invoked during recovery.
        with patch('primitive_vla_cobotmagic.agent.recovery.close_workspace',side_effect=fence), \
             patch('primitive_vla_cobotmagic.agent.recovery.CameraBridge',side_effect=camera), \
             patch('primitive_vla_cobotmagic.agent.recovery.monotonic',clock.monotonic), \
             patch('primitive_vla_cobotmagic.agent.recovery.sleep',clock.sleep), \
             patch('primitive_vla_cobotmagic.ik.execution.MotionBridge.action') as action:
            reply=await self.client.post('/api/control/recover',json={'confirmed_stopped':True})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertTrue(reply.json()['recovered'])
        self.assertFalse(store.control_locked)
        self.assertEqual(store.get('uncertain'),before)
        self.assertEqual(store.events('uncertain')[-1]['event'],'control_recovered')
        action.assert_not_called();feedback.close.assert_awaited_once()
        reopened=TaskStore(self.root/'web/tasks.sqlite3')
        try:self.assertFalse(reopened.control_locked)
        finally:reopened.close()
        status=(await self.client.get('/api/status')).json()
        self.assertFalse(status['control_locked']);self.assertIsNone(status['active_id'])

    async def test_no_confirmation_busy_or_foreign_origin_never_contacts_robot(self):
        store=self.lock()
        with patch('primitive_vla_cobotmagic.agent.recovery.CameraBridge') as camera, \
             patch('primitive_vla_cobotmagic.agent.recovery.close_workspace',new=AsyncMock()) as fence:
            for body,expected in [({},422),({'confirmed_stopped':'true'},422),({'confirmed_stopped':False},409)]:
                response=await self.client.post('/api/control/recover',json=body)
                self.assertEqual(response.status_code,expected)
            response=await self.client.post('/api/control/recover',json={'confirmed_stopped':True},headers={'Origin':'https://foreign.test'})
            self.assertEqual(response.status_code,403)
            service=self.app.state.service
            service.worker=asyncio.get_running_loop().create_future()
            try:
                response=await self.client.post('/api/control/recover',json={'confirmed_stopped':True})
                self.assertEqual(response.status_code,409)
            finally:service.worker.cancel();service.worker=None
            camera.assert_not_called();fence.assert_not_awaited()
        self.assertTrue(store.control_locked)

    async def test_failed_check_keeps_lock_and_recovery_blocks_new_task_and_double_click(self):
        store=self.lock();entered=asyncio.Event();release=asyncio.Event()
        async def check(bridge):
            entered.set();await release.wait();raise ValueError('feedback is moving')
        bridge=SimpleNamespace(close=AsyncMock())
        with patch('primitive_vla_cobotmagic.agent.recovery.close_workspace',new=AsyncMock()), \
             patch('primitive_vla_cobotmagic.agent.recovery.CameraBridge',return_value=bridge), \
             patch('primitive_vla_cobotmagic.agent.recovery.stationary_feedback',side_effect=check):
            first=asyncio.create_task(self.client.post('/api/control/recover',json={'confirmed_stopped':True}))
            await entered.wait()
            self.assertEqual((await self.client.post('/api/control/recover',json={'confirmed_stopped':True})).status_code,409)
            self.assertEqual((await self.client.post('/api/tasks',json={'instruction':'move'})).status_code,409)
            release.set();response=await first
        self.assertEqual(response.status_code,409)
        self.assertTrue(store.control_locked)
        self.assertIn('feedback is moving',response.json()['detail'])
        bridge.close.assert_awaited_once()

    async def test_store_recovery_refuses_running_task_and_keeps_unrelated_metadata(self):
        store=self.lock()
        store.db.execute("INSERT INTO metadata VALUES ('unrelated','kept')")
        store.db.commit()
        store.create('running','task',{})
        with self.assertRaises(RuntimeError):store.record_control_recovery({'source':'test'})
        self.assertTrue(store.control_locked)
        store.finish('running',{'status':'cancelled'}, {})
        store.record_control_recovery({'source':'test'})
        self.assertEqual(store.db.execute("SELECT value FROM metadata WHERE key='unrelated'").fetchone()[0],'kept')


if __name__=='__main__':unittest.main()
