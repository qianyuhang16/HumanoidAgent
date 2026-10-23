"""Model-directed real-camera hover planning with fake ROS and inference transports."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import sys
import yaml
from pathlib import Path
import tempfile
from time import monotonic
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from primitive_vla.config.loader import load_config
from primitive_vla.config.schema import AdapterSpec
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import CallContext, PluginContext
from primitive_vla_cobotmagic.agent.provider import CobotTools
from primitive_vla_cobotmagic.agent.cli import main
from primitive_vla_cobotmagic.agent.config import settings
from test_ik_web import fixture, ReadOnlyBridge
from test_pixel_ik import standalone_config

ROOT=Path(__file__).resolve().parents[1]


def settings_fixture(root, ik_config):
    """Only metadata is exercised; inference is replaced by the test transport."""
    models=root/'fixture-models'
    for name in ('grounding-dino','sam','depth-anything'):
        folder=models/name
        folder.mkdir(parents=True)
        (folder/'config.json').write_text('{}')
        (folder/'fixture.safetensors').write_bytes(b'not model weights; offline fixture only')
    path=root/'agent-fixture.yaml'
    path.write_text(yaml.safe_dump({'model_source':str(ROOT/'configs/cameras.yaml'),
        'ik_config':str(ik_config),'python_executable':sys.executable,'models_dir':str(models)}))
    return path


class Inference:
    def __init__(self):
        self.calls=[]
        self.found=True
        self.pixel=[320.,240.]
        self.closed=False
    async def request(self, op, **kwargs):
        args=kwargs['arguments']
        self.calls.append((op,args))
        if op=='segment':
            return {'found':self.found,'pixel_uv':self.pixel,'frame_ref':args['frame_ref']}
        return {'frame_ref':args['frame_ref'],'measured':False,'used_for_ik':False,'unit':'m'}
    async def close(self):self.closed=True


class AgentToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        path,raw,_=fixture(self.root)
        standalone_config(path)
        self.camera=ReadOnlyBridge(raw)
        self.inference=Inference()
        self.options={'ik_config':str(path),'arm':'auto','inference_timeout_s':30}
        for name,value in [('primitive_vla_cobotmagic.ik.pixel.CameraBridge',self.camera),
                           ('primitive_vla_cobotmagic.agent.provider.inference_process',self.inference)]:
            p=patch(name,return_value=value);p.start();self.addCleanup(p.stop)
        self.context=PluginContext(self.root,self.root,EventLog())
        self.provider=CobotTools(self.options,self.context)
        self.addAsyncCleanup(self.provider.close)

    def call(self):return CallContext('test',monotonic()+60)
    async def detect(self):
        obs=await self.provider.observe({},self.call())
        return await self.provider.segment({'observation_id':obs.data['observation_id'],'query':'pink bowl'},self.call())

    async def test_model_selects_tools_and_generates_plan_without_actions(self):
        config=load_config(ROOT/'configs/cameras.yaml')
        config.model.options['require_api_key']=False
        config.tool_providers=[AdapterSpec(adapter='primitive_vla_cobotmagic.agent.provider:CobotTools',options=self.options)]
        requests=[]
        def model(request):
            body=json.loads(request.content);requests.append(body)
            provider=next(r for r in runtime.resources if isinstance(r,CobotTools))
            n=len(requests)
            if n==1:name,args='camera.observe',{}
            elif n==2:name,args='perception.depth',{'observation_id':provider.observation['observation_id']}
            elif n==3:name,args='perception.segment',{'observation_id':provider.observation['observation_id'],'query':'pink bowl'}
            elif n==4:name,args='robot.plan_hover',{'detection_id':next(iter(provider.detections)),'arm':'left'}
            if n<5:reply={'calls':[{'name':name,'arguments':args}],'final':None}
            else:reply={'calls':[],'final':{'status':'completed','reason':'Hover plan ready; not executed','evidence':[]}}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(reply)}}]})
        runtime=Runtime(config,self.root,EventLog(),transport=httpx.MockTransport(model))
        self.addAsyncCleanup(runtime.close)
        with patch('primitive_vla_cobotmagic.ik.execution.MotionBridge.action') as action, patch('socket.create_connection') as connect:
            report=await runtime.run('Plan above the bowl')
            provider=next(r for r in runtime.resources if isinstance(r,CobotTools))
            self.assertEqual(report['status'],'completed',report)
            self.assertTrue(provider.last_plan['execution_ready'])
            self.assertEqual(provider.last_plan['arm'],'left')
            self.assertAlmostEqual(provider.last_plan['target_xyz_m'][2],.19)
            self.assertEqual(self.camera.calls,1)
            self.assertEqual([t.definition.name for t in provider.tools if not t.definition.read_only],['robot.execute','robot.reset_arm'])
            self.assertIn('data:image/jpeg;base64,',json.dumps(requests[1]))
            self.assertEqual([op for op,_ in self.inference.calls],['depth','segment'])
            action.assert_not_called();connect.assert_not_called()

    async def test_refresh_and_failed_detection_invalidate_previous_plan(self):
        d=await self.detect();token=d.data['detection_id']
        result=await self.provider.plan({'detection_id':token,'arm':'left'},self.call())
        self.assertTrue(result.data['execution_ready'])
        self.inference.found=False
        await self.provider.segment({'observation_id':self.provider.observation['observation_id'],'query':'pink bowl'},self.call())
        self.assertIsNone(self.provider.last_plan)
        with self.assertRaises(ValueError):await self.provider.plan({'detection_id':token,'arm':'left'},self.call())
        self.inference.found=True
        d=await self.detect()
        old=self.provider.observation['observation_id']
        await self.provider.observe({},self.call())
        with self.assertRaises(ValueError):await self.provider.depth({'observation_id':old},self.call())
        with self.assertRaises(ValueError):await self.provider.plan({'detection_id':d.data['detection_id'],'arm':'left'},self.call())

    async def test_expired_snapshot_and_invalid_pixel_cannot_plan(self):
        d=await self.detect()
        self.provider.ik.snapshot['captured']-=181
        with self.assertRaises(ValueError):await self.provider.plan({'detection_id':d.data['detection_id'],'arm':'left'},self.call())
        self.assertIsNone(self.provider.last_plan)
        self.inference.pixel=[-1,0]
        with self.assertRaises(ValueError):await self.detect()
        self.assertEqual(self.provider.detections,{})

    async def test_cancelled_call_does_not_accept_late_perception(self):
        obs=await self.provider.observe({},self.call())
        ctx=self.call();ctx.cancelled.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.provider.segment({'observation_id':obs.data['observation_id'],'query':'bowl'},ctx)
        self.assertEqual(self.inference.calls,[])
        self.assertIsNone(self.provider.last_plan)

    async def test_typed_detection_reuses_actual_observation_without_inventing_holding(self):
        from primitive_vla.agent.memory import Session
        from primitive_vla.tools import ToolCall
        session=Session()
        observed=await self.provider.observe({},self.call())
        session.record_result(ToolCall(name='camera.observe',id='observe'),observed)
        detected=await self.provider.segment({'query':'pink bowl'},self.call())
        session.record_result(ToolCall(name='perception.segment',id='detect'),detected)
        ref=observed.observations[0].id
        self.assertEqual(detected.observations[0].id,ref)
        self.assertEqual(session.state.read('detect:front')['captured_at'],observed.observations[0].captured_at)
        self.assertEqual(detected.claims[0].property,'detection.v1')
        self.assertEqual(detected.claims[0].value['unit'],'pixel')
        self.assertEqual(detected.claims[0].value['frame'],ref)
        self.assertEqual(session.state.query(property='holding.v1'),[])

    async def test_implicit_capture_also_registers_evidence(self):
        from primitive_vla.agent.memory import Session
        from primitive_vla.tools import ToolCall
        session=Session()
        result=await self.provider.segment({'query':'bowl'},self.call())
        session.record_result(ToolCall(name='perception.segment',id='detect'),result)
        self.assertEqual(session.state.read(result.claims[0].evidence[0])['kind'],'observation')


class EntryTests(unittest.TestCase):
    def test_cli_execution_requires_flag_completed_report_and_plan(self):
        plan={'arm':'right','target_xyz_m':[.3,0,.17],'command':'reviewed command','bundle':'/tmp/fake-plan'}
        for execute,status,present,expected in [(False,'completed',True,False),(True,'blocked',True,False),
                                                (True,'completed',False,False),(True,'completed',True,True)]:
            with self.subTest(execute=execute,status=status,present=present), \
                 patch('primitive_vla_cobotmagic.agent.cli.run_agent',new=AsyncMock(return_value=({'status':status},plan if present else None))), \
                 patch('primitive_vla_cobotmagic.agent.cli.execute_bundle',return_value=0) as run,redirect_stdout(StringIO()):
                main(['--execute'] if execute else [])
                self.assertEqual(run.called,expected)
                if expected:run.assert_called_once_with('/tmp/fake-plan',execute=True)

    def test_cli_refuses_image_or_web_motion(self):
        for argv in [['--web','--execute'],['--check-image','photo.jpg','--execute']]:
            with self.subTest(argv=argv),self.assertRaises(SystemExit),redirect_stderr(StringIO()):main(argv)

    def test_settings_validate_local_assets_without_requiring_deployment_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=settings_fixture(root,ROOT/'configs/ik.yaml')
            values=settings(path)
            self.assertTrue(Path(values['python_executable']).is_file())
            self.assertEqual(Path(values['models_dir']),root/'fixture-models')
            (root/'fixture-models/sam/fixture.safetensors').unlink()
            with self.assertRaisesRegex(ValueError,'Missing existing weights'):
                settings(path)


if __name__=='__main__':unittest.main()
