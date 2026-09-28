"""Offline TCP math, acquisition and unified-workspace tests; never connect to ROS."""
import asyncio
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import numpy as np
from PIL import Image

from primitive_vla_cobotmagic.calibration.core import DEFAULT_CONFIG, read_config, write_json
from primitive_vla_cobotmagic.calibration.web import create_workspace_app
from primitive_vla_cobotmagic.calibration.workspace import CalibrationWorkspace, read_result
from primitive_vla_cobotmagic.ik.kinematics import transform
from primitive_vla_cobotmagic.calibration.tcp import TCP_POLICY, TCPCollector, TCPDataset, fit_tcp, solve_tcp_session


class TCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = read_config(DEFAULT_CONFIG)
        image = BytesIO(); Image.new('RGB', (320,240), 'gray').save(image, 'JPEG')
        self.jpeg = image.getvalue()

    def reply(self, index=0, q=None, opening=.02):
        stamp = 10_000_000_000 + index*200_000_000
        return {'ok':True, 'timestamp_ns':stamp,
            'images':{'front':base64.b64encode(self.jpeg).decode()},
            'image_timestamps_ns':{'front':stamp-10_000_000},
            'positions':{'right':list(q if q is not None else [.1,.8,-.8,.1,.3,-.1])+[opening]},
            'joint_timestamps_ns':{'right':stamp-5_000_000}}

    def settled(self, collector):
        for i in range(7):
            with patch('primitive_vla_cobotmagic.calibration.tcp.time.monotonic', return_value=i*.2):
                collector.ingest(self.reply(i), .001)
                if i==0: self.assertFalse(collector.state()['ready'])

    def test_exact_fixed_point_recovery_and_degenerate_rejection(self):
        tcp = np.array([.012,-.018,.05]); fixed = np.array([.3,.2,.4])
        rng = np.random.default_rng(19)
        poses=[]
        for angles in rng.uniform(-.7,.7,(8,3)):
            pose=transform([0,0,0],angles);pose[:3,3]=fixed-pose[:3,:3]@tcp;poses.append(pose)
        actual,point,_,_=fit_tcp(poses)
        np.testing.assert_allclose(actual,tcp,atol=1e-12)
        np.testing.assert_allclose(point,fixed,atol=1e-12)
        for degenerate in ([np.eye(4)]*4,[transform([0,0,0],[0,0,i*.2]) for i in range(5)]):
            with self.assertRaisesRegex(ValueError,'姿态'):
                fit_tcp(degenerate)

    def test_capture_needs_no_board_checks_gripper_freshness_and_repeats(self):
        dataset=TCPDataset(self.root/'tcp',self.config); c=TCPCollector(dataset)
        self.settled(c)
        with patch('primitive_vla_cobotmagic.calibration.tcp.time.monotonic', return_value=1.2):
            self.assertTrue(c.state()['ready'], c.message)
            sample_id=c.record('train')
            with self.assertRaisesRegex(ValueError,'已经记录'): c.record('validation')
        self.assertNotIn('ids',c.latest['sample'])
        row=json.loads((dataset.folder/'samples'/sample_id/'sample.json').read_text())
        self.assertEqual(row['gripper_position_m'],.02)
        for i in range(7,14):
            with patch('primitive_vla_cobotmagic.calibration.tcp.time.monotonic', return_value=i*.2):
                c.ingest(self.reply(i,opening=.03),.001)
        with patch('primitive_vla_cobotmagic.calibration.tcp.time.monotonic', return_value=2.6):
            self.assertFalse(c.state()['ready']);self.assertIn('开口',c.message)
        with patch('primitive_vla_cobotmagic.calibration.tcp.time.monotonic', return_value=4):
            with self.assertRaisesRegex(ValueError,'过期'): c.record('train')
        for bad in (float('nan'),float('inf')):
            reply=self.reply(20);reply['positions']['right'][0]=bad
            with self.assertRaises(ValueError): c.ingest(reply,.001)
        # Resume retains saved opening and samples; board size/quality does not affect TCP.
        config=deepcopy(self.config);config['board']['square_m']*=.9
        self.assertEqual(len(TCPDataset(dataset.folder,config).samples),1)

    def populate_fixed_point(self, dataset):
        tcp=np.array([.012,-.018,.05]); q0=np.array([.1,.8,-.8,.1,.3,-.1])
        base=dataset.chain.fk(q0);fixed=base[:3,:3]@tcp+base[:3,3]
        angles=[(0,0,0),(.25,0,0),(-.25,0,0),(0,.25,0),(0,-.25,0),
                (.2,.2,.1),(-.2,.2,-.1),(.15,-.15,.15),(.12,.1,-.15),(-.12,-.1,.12)]
        for index,rotation in enumerate(angles):
            target=base.copy();target[:3,:3]=base[:3,:3]@transform([0,0,0],rotation)[:3,:3]
            target[:3,3]=fixed-target[:3,:3]@tcp
            q=dataset.chain.solve(target,q0)
            sample={'arm':self.config['arm'],'joints_rad':q.tolist(),'gripper_position_m':.02,
                    'joint_timestamp_ns':index+1,'T_base_from_tip':dataset.chain.fk(q).tolist()}
            dataset.save(sample,self.jpeg,'train' if index<7 else 'validation')
        return tcp

    def test_real_urdf_solve_and_validation_do_not_refit(self):
        dataset=TCPDataset(self.root/'tcp',self.config)
        expected=self.populate_fixed_point(dataset)
        result=solve_tcp_session(dataset.folder)
        self.assertTrue(result['accepted'],result)
        np.testing.assert_allclose(result['tcp_xyz_m'],expected,atol=.004)
        self.assertEqual(result['tcp_rpy_rad'],[0,0,0])
        self.assertEqual(len(result['validation_ids']),3)
        original=result['tcp_xyz_m']
        path=dataset.folder/'samples'/result['validation_ids'][0]/'sample.json'
        row=json.loads(path.read_text());row['joints_rad'][0]+=.15;write_json(path,row)
        bad=solve_tcp_session(dataset.folder)
        self.assertFalse(bad['accepted'])
        self.assertEqual(bad['tcp_xyz_m'],original)
        # Audit raw joint feedback again when solving, not the cached pose in sample.json.
        row['gripper_position_m']=.05;write_json(path,row)
        with self.assertRaisesRegex(ValueError,'开口'):solve_tcp_session(dataset.folder)
        self.assertFalse(json.loads((dataset.folder/'result.json').read_text())['accepted'])

    def test_failed_solve_clears_old_success_and_model_mismatch_rejected(self):
        dataset=TCPDataset(self.root/'tcp',self.config)
        write_json(dataset.folder/'result.json',{'accepted':True})
        with self.assertRaisesRegex(ValueError,'至少需要'):solve_tcp_session(dataset.folder)
        self.assertFalse(read_result(dataset.folder)['accepted'])
        other=deepcopy(self.config);other['arm']='left'
        with self.assertRaisesRegex(ValueError,'改变'):TCPDataset(dataset.folder,other)

    def test_15mm_threshold_reuses_5mm_session_without_changing_fit(self):
        with patch.dict(TCP_POLICY, {'max_point_error_m':.005}):
            dataset=TCPDataset(self.root/'old-5mm',self.config)
        self.populate_fixed_point(dataset)
        row=next(r for r in dataset.samples if r['split']=='validation')
        path=dataset.folder/'samples'/row['id']/'sample.json'
        row['joints_rad'][0]+=.03
        write_json(path,row)
        manifest_before=dataset.manifest_path.read_bytes()
        strict=solve_tcp_session(dataset.folder,max_point_error_m=.005)
        self.assertFalse(strict['accepted'])
        self.assertTrue(read_result(dataset.folder,{'max_point_error_m':.015})['thresholds_changed'])
        resumed=TCPDataset(dataset.folder,self.config)
        self.assertEqual(resumed.policy['max_point_error_m'],.015)
        relaxed=solve_tcp_session(dataset.folder)
        self.assertTrue(relaxed['accepted'],relaxed)
        self.assertEqual(relaxed['thresholds']['max_point_error_m'],.015)
        self.assertEqual(relaxed['tcp_xyz_m'],strict['tcp_xyz_m'])
        self.assertEqual(relaxed['validation_residuals'],strict['validation_residuals'])
        self.assertEqual(dataset.manifest_path.read_bytes(),manifest_before)

    def test_unified_web_tcp_does_not_require_intrinsics_and_routes_solver(self):
        w=CalibrationWorkspace(self.root,DEFAULT_CONFIG)
        async def run():
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_workspace_app(w)),base_url='http://test') as client:
                    state=(await client.get('/api/state')).json()
                    self.assertIn('tcp_right',[s['id'] for s in state['steps']])
                    r=await client.post('/api/select',json={'revision':state['revision'],'step':'tcp_right','new':True})
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertEqual(r.json()['capture']['phase'],'tcp')
                    c=w.collector;self.settled(c)
                    revision=r.json()['revision']
                    with patch('primitive_vla_cobotmagic.calibration.tcp.time.monotonic',return_value=1.2):
                        r=await client.post('/api/record',json={'revision':revision,'split':'train'})
                        self.assertEqual(r.status_code,200,r.text)
                    r=await client.post('/api/solve',json={'revision':revision})
                    self.assertEqual(r.status_code,409,r.text)
                    self.assertIn('TCP 至少需要',r.json()['detail'])
                    r=await client.post('/api/select',json={'revision':revision,'step':'tcp_left','new':True})
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertIsNone(w.collector.latest)
                    self.assertEqual(w.collector.config['arm'],'left')
                    self.assertIn('tcp/left-tool',str(w.collector.dataset.folder))
                    self.assertEqual((await client.post('/api/record',json={'revision':revision,'split':'train'})).status_code,409)
            finally: await w.close()
        asyncio.run(run())

if __name__=='__main__':unittest.main()
