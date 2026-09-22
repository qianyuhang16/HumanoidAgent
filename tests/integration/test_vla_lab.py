"""Persistent VLA lab keeps weights and stops cooperatively between chunks."""
import asyncio
import io
import time
import numpy as np
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

from primitive_vla.backends.rmbench_tools import RMBenchTools
from primitive_vla.simulation.vla_lab_world import VLALabWorld
from primitive_vla.simulation.rmbench_worker import World


class VLALabTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_after_inference_never_dispatches_predicted_actions(self):
        with tempfile.TemporaryDirectory() as path:
            stopped=False
            async def request(op,**kw):
                nonlocal stopped
                if op=='observe':return {'images':{},'proprioception':{'remaining_action_steps':100}}
                if op=='begin':return {'started':True}
                if op=='infer':
                    stopped=True
                    return {'actions':[[0]*14]*15}
                self.fail('No chunk should be dispatched after stop')
            backend=SimpleNamespace(config={'python':'unused','mode':'vla','observation_mode':'sensors',
                'perception':{'min_mask_score':.7},'max_skill_steps':450},
                worker=SimpleNamespace(request=AsyncMock(side_effect=request)),stop=AsyncMock(return_value=True))
            tools=RMBenchTools(backend,Path(path),SimpleNamespace(write=lambda *a,**k:None))
            result=await tools.execute('literal user instruction',450,lambda:stopped)
            self.assertEqual(result['actions'],0)
            backend.stop.assert_awaited_once()
            self.assertFalse(tools.locked)

    async def test_direct_command_can_continue_past_early_end_votes(self):
        # Exercise the real chunk loop and outer tool loop together.
        for stop_on_end, expected in [(False, 20), (True, 2)]:
            with self.subTest(stop_on_end=stop_on_end), tempfile.TemporaryDirectory() as path:
                world=VLALabWorld.__new__(VLALabWorld)
                world.np=np;world.active=None;world.generation=0;world.chunks=0
                world.config={'end_threshold':2,'max_skill_steps':450}
                world.policy=SimpleNamespace(end_count=0)
                world.env=SimpleNamespace(take_action_cnt=0,step_lim=100)
                def take_action(action, **kw):world.env.take_action_cnt+=1
                def update(*args):world.policy.end_count+=1
                world.env.take_action=take_action
                world.env.get_obs=lambda:{}
                world.policy.update=update
                world.capture=lambda:None
                world.trace=io.StringIO();world.frame='frame.png';world.ik_moves=0
                async def request(op, **kw):
                    if op=='observe':return {'images':{},'proprioception':{'remaining_action_steps':100}}
                    if op=='begin':
                        world.active=kw['call_id'];world.generation=kw['generation']
                        world.prompt=kw['prompt'];world.deadline=time.time()+60;world.skill_start=0
                        return {'started':True}
                    if op=='infer':return {'actions':[[0]*14]*15}
                    if op=='chunk':return world.chunk(**kw)
                    self.fail(op)
                backend=SimpleNamespace(config={'python':'unused','mode':'vla','observation_mode':'sensors',
                    'perception':{'min_mask_score':.7},'max_skill_steps':450},
                    worker=SimpleNamespace(request=AsyncMock(side_effect=request)),stop=AsyncMock(return_value=True))
                tools=RMBenchTools(backend,Path(path),SimpleNamespace(write=lambda *a,**kw:None))
                result=await tools.execute('literal user instruction',20,stop_on_end=stop_on_end)
                self.assertEqual(result['actions'],expected)
                self.assertTrue(result['learned_end'])
                self.assertEqual(result['budget_exhausted'],not stop_on_end)

    def test_reset_reuses_executor_and_reseeds_without_warmup(self):
        world=VLALabWorld.__new__(VLALabWorld)
        model=object()
        policy=SimpleNamespace(executor=model,reset=Mock(),seed=Mock())
        world.policy=policy;world.active=None;world.config={'seed':1};world.directory=Path('/tmp/lab/scene-000')
        world.scene_index=0;world.close=Mock()
        def initialize(self,config,directory):
            self.policy=None;self.config=config;self.directory=directory;self.active=None
        world.observe=Mock(return_value={'facts':{}})
        with patch.object(World,'__init__',initialize):
            result=world.reset_scene(23)
        self.assertIs(world.policy.executor,model)
        self.assertEqual(result['model_identity'],id(model))
        self.assertEqual(result['scene_index'],1)
        policy.seed.assert_called_once_with(23)
        policy.reset.assert_called_once()
