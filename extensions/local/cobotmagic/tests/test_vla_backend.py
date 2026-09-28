"""LeRobot-side contract tests; run in existing lerobot env, no weights or ROS."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec('lerobot'),'requires existing LeRobot environment')
class BackendTests(unittest.TestCase):
    def setUp(self):
        import torch
        import numpy as np
        from primitive_vla_cobotmagic.vla.backend import Backend
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=0.;self.cancel=threading.Event();self.robots=[];self.engines=[]
        owner=self
        self.keys=[f'{s}_{j}.pos' for s in ('left','right') for j in [*[f'joint_{i}' for i in range(1,7)],'gripper']]
        class Robot:
            def __init__(self,*a,**kw):
                self.is_connected=False;self.action_features={k:float for k in owner.keys}
                self.observation_features={**self.action_features,**{k:(2,2,3) for k in ('front','left','right')}}
                self.obs={k:0. for k in owner.keys};self.commands=[];self.robot_type='magicbot'
                self.name='magicbot';owner.robots.append(self)
                self.feedback_stamps={'left':1,'right':1}
            def connect(self):self.is_connected=True
            def disconnect(self):self.is_connected=False
            def get_observation(self):
                self.feedback_stamps={s:t+1 for s,t in self.feedback_stamps.items()}
                return {**self.obs,**{k:np.zeros((2,2,3),dtype=np.uint8) for k in ('front','left','right')}}
            def send_action(self,command):
                self.commands.append(deepcopy(command));self.obs.update(command)
                if owner.cancel_on_send:owner.cancel.set()
        class Engine:
            failed=False
            def __init__(self,*a,**kw):
                self.resets=0;self._rtc_thread=SimpleNamespace(is_alive=lambda:False);owner.engines.append(self)
            def reset(self):self.resets+=1
            def start(self):pass
            def resume(self):pass
            def stop(self):pass
            def notify_observation(self,obs):pass
            def get_action(self,frame):return torch.tensor([.1,0.,-.1,0.,0.,0.,.01,.2,0.,-.2,0.,0.,0.,.02])
        self.cancel_on_send=False
        self.backend=Backend({'policies':{'p':{'task':'pick'}},'preparation_tolerance_rad':.05,
            'fps':20,'device':'cpu','queue_threshold':25,'load_timeout_s':2},
            {'host':'unused','port':1,'speed_percent':30},self.tmp.name)
        self.backend.policy=SimpleNamespace(config=SimpleNamespace(action_feature_names=self.keys,
            output_features={'action':SimpleNamespace(shape=[14])},rtc_config=None))
        self.backend.pre=self.backend.post=None
        self.backend.load=lambda _:None
        def monotonic():self.now+=.01;return self.now
        def sleep(t):self.now+=t
        for p in [patch('lerobot.robots.magicbot.MagicBot',Robot),
                  patch('lerobot.rollout.inference.rtc.RTCInferenceEngine',Engine),
                  patch('primitive_vla_cobotmagic.vla.backend.time',SimpleNamespace(monotonic=monotonic,sleep=sleep,time=lambda:100+self.now)),
                  patch('socket.create_connection',side_effect=AssertionError('No real robot connection allowed'))]:
            p.start();self.addCleanup(p.stop)
        self.request={'policy_id':'p','duration_s':.1,'deadline':200.,'call_id':'test',
                      'prepared_joints':{'left':[0.]*6}}

    def test_full_dual_arm_actions_feedback_images_and_queue_reset(self):
        result=self.backend.run(self.request,self.cancel)
        self.assertTrue(result['executed'],result);self.assertTrue(result['control_released'],result)
        self.assertEqual(result['used_arms'],['left','right']);self.assertIsNone(result['task_success'])
        first=self.robots[0].commands[0]
        self.assertAlmostEqual(first['left_joint_1.pos'],.1)
        self.assertAlmostEqual(first['right_joint_1.pos'],.2)
        self.assertAlmostEqual(first['left_gripper.pos'],.01)
        self.assertAlmostEqual(first['right_gripper.pos'],.02)
        self.assertEqual(self.engines[0].resets,2)
        self.assertEqual(set(result['images']),{'front','left','right'})
        self.assertEqual(set(result['before_images']),{'front','left','right'})
        self.assertTrue(all(Path(self.tmp.name, ref).is_file() for ref in result['before_images'].values()))
        self.assertTrue(set(result['before_images'].values()).isdisjoint(result['images'].values()))
        self.assertFalse(self.robots[0].is_connected)
        result=self.backend.run({**self.request,'call_id':'next'},self.cancel)
        self.assertTrue(result['executed']);self.assertEqual(len(self.engines),2)

    def test_cancel_after_first_action_stops_policy_and_verifies_hold(self):
        self.cancel_on_send=True
        result=self.backend.run(self.request,self.cancel)
        self.assertEqual(result['stop_reason'],'cancelled')
        self.assertEqual(result['actions_sent'],1)
        self.assertEqual(len(self.robots[0].commands),2)  # one policy output, one measured hold
        self.assertTrue(result['control_released'])

    def test_changed_preparation_and_cancel_before_start_do_not_send(self):
        result=self.backend.run({**self.request,'prepared_joints':{'left':[.2]*6}},self.cancel)
        self.assertIn('moved since IK',result['error']);self.assertEqual(self.robots[0].commands,[])
        self.cancel.set();count=len(self.robots)
        result=self.backend.run(self.request,self.cancel)
        self.assertEqual(len(self.robots),count);self.assertTrue(result['control_released'])

if __name__=='__main__':unittest.main()
