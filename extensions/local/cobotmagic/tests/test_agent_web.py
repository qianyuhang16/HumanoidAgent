"""Agent UI lifecycle, automatic arm choice and command visibility; no real motion."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from primitive_vla.config.loader import load_config
from primitive_vla.config.schema import AdapterSpec
from primitive_vla.runtime.session import Runtime
from primitive_vla_cobotmagic.agent.provider import CobotTools
from primitive_vla_cobotmagic.agent.web import create_app
from primitive_vla_cobotmagic.ik.pixel import PixelIK
from test_ik_web import fixture, ReadOnlyBridge
from test_pixel_ik import standalone_config
from test_agent import Inference, ROOT


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        path,raw,_=fixture(self.root);standalone_config(path)
        self.camera=ReadOnlyBridge(raw);self.inference=Inference();self.requests=0;self.execute=False
        self.config=load_config(ROOT/'configs/cameras.yaml')
        self.config.model.options['require_api_key']=False
        self.config.tool_providers=[AdapterSpec(adapter='primitive_vla_cobotmagic.agent.provider:CobotTools',
            options={'ik_config':str(path),'arm':'auto','inference_timeout_s':30})]
        def model(request):
            self.requests+=1
            provider=next(r for r in self.app.state.service.runtime.resources if isinstance(r,CobotTools))
            n=self.requests
            if n==1:name,args='camera.observe',{}
            elif n==2:name,args='perception.segment',{'observation_id':provider.observation['observation_id'],'query':'pink bowl'}
            elif n in (3,4):name,args='robot.plan_hover',{'detection_id':next(iter(provider.detections)),'arm':'right' if n==3 else 'left'}
            elif n==5 and self.execute:name,args='robot.execute',{'plan_id':provider.last_plan['plan_id']}
            if n<(6 if self.execute else 5):reply={'calls':[{'name':name,'arguments':args}],'final':None}
            else:reply={'calls':[],'final':{'status':'completed','reason':'执行完成。' if self.execute else '右臂规划失败；左臂规划通过，选择左臂。尚未移动。','evidence':[]}}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(reply)}}]})
        original=PixelIK.calculate
        def calculate(planner,body):
            if body.arm=='right':return {'arm':'right','solved':False,'execution_ready':False,'blockers':['test unreachable'],'command':None}
            return original(planner,body)
        patches=[
            patch('primitive_vla_cobotmagic.ik.pixel.CameraBridge',return_value=self.camera),
            patch('primitive_vla_cobotmagic.agent.provider.inference_process',return_value=self.inference),
            patch('primitive_vla_cobotmagic.agent.web.agent_config',return_value=self.config),
            patch('primitive_vla.runtime.service.Runtime',side_effect=lambda *a,**kw:Runtime(*a,**kw,transport=httpx.MockTransport(model))),
            patch.object(PixelIK,'calculate',calculate),
        ]
        for p in patches:p.start();self.addCleanup(p.stop)
        self.app=create_app(self.root/'web')
        self.life=self.app.router.lifespan_context(self.app);await self.life.__aenter__()
        self.addAsyncCleanup(self.life.__aexit__,None,None,None)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def start(self):
        r=await self.client.post('/api/tasks',json={'instruction':'让 AI 选择手臂并规划碗上方悬停'})
        self.assertEqual(r.status_code,202,r.text)
        return r.json()['id']

    async def test_auto_arm_model_switches_to_feasible_arm_and_exports_only_after_completion(self):
        with patch('primitive_vla_cobotmagic.ik.execution.MotionBridge.action') as action,patch('socket.create_connection') as socket:
            task_id=await self.start()
            before=(await self.client.get('/api/tasks/'+task_id)).json()
            self.assertIsNone(before['command'])
            await self.app.state.service.worker
            result=(await self.client.get('/api/tasks/'+task_id)).json()
            self.assertEqual(result['status'],'completed',result)
            self.assertEqual(result['plan']['arm'],'left')
            self.assertIn('--execute',result['command'])
            self.assertEqual([s['arguments']['arm'] for s in result['steps'] if s['name']=='robot.plan_hover'],['right','left'])
            self.assertIn('front',result['images'])
            r=await self.client.get('/api/media/'+result['images']['front']);self.assertEqual(r.status_code,200)
            action.assert_not_called();socket.assert_not_called()
        for endpoint in ('/api/execute','/api/action','/api/config'):
            self.assertEqual((await self.client.post(endpoint,json={})).status_code,404)

    async def test_rejected_manual_arm_cross_origin_and_static_pages(self):
        self.assertEqual((await self.client.post('/api/tasks',json={'instruction':'test','arm':'right'})).status_code,422)
        self.assertEqual((await self.client.post('/api/tasks',json={'instruction':'test'},headers={'origin':'https://elsewhere.test'})).status_code,403)
        page=await self.client.get('/')
        self.assertEqual(page.status_code,200)
        self.assertNotIn('id="arm"',page.text)
        self.assertIn('独立 IK',page.text)
        self.assertNotIn('chosenArm',page.text)
        for path in ['/static/app.js','/static/style.css']:
            self.assertEqual((await self.client.get(path)).status_code,200)
        outside=self.root/'secret.png';outside.write_text('private')
        (self.root/'web/media/escape.png').symlink_to(outside)
        self.assertEqual((await self.client.get('/api/media/escape.png')).status_code,404)

    async def test_agent_can_execute_through_tool_runtime_and_consumes_command(self):
        self.execute=True
        def completed(run):
            run.sent=True;run.completed=True
            return {'executed':True,'arrival_verified':True,'nominal_duration_s':3.,'position_error_m':.001}
        with patch('primitive_vla_cobotmagic.agent.motion.MotionRun.run',completed),patch('socket.create_connection') as connect:
            task_id=await self.start();await self.app.state.service.worker
            result=(await self.client.get('/api/tasks/'+task_id)).json()
            self.assertEqual(result['status'],'completed',result)
            step=next(s for s in result['steps'] if s['name']=='robot.execute')
            self.assertTrue(step['data']['arrival_verified'])
            self.assertEqual(step['data']['nominal_duration_s'],3.)
            self.assertIsNone(result['command']);self.assertEqual(result['plan'],{})
            self.assertNotIn('arm',result)
            events=self.app.state.service.store.events(task_id)
            start=next(e for e in events if e['event']=='tool_started' and e['call']['name']=='robot.execute')
            self.assertFalse(start['read_only'])
            self.assertFalse(self.app.state.service.store.control_locked)
            connect.assert_not_called()

    async def test_cancel_and_busy_do_not_leave_a_command(self):
        entered=asyncio.Event()
        async def slow(runtime,*args,**kwargs):
            entered.set();await asyncio.Event().wait()
        with patch.object(Runtime,'run',slow):
            task_id=await self.start();await entered.wait()
            self.assertEqual((await self.client.post('/api/tasks',json={'instruction':'second'})).status_code,409)
            self.assertEqual((await self.client.post(f'/api/tasks/{task_id}/cancel')).status_code,200)
            await self.app.state.service.worker
            data=(await self.client.get('/api/tasks/'+task_id)).json()
            self.assertEqual(data['status'],'cancelled');self.assertIsNone(data['command'])

    async def test_general_question_can_finish_without_any_robot_or_perception_tools(self):
        def answer(request):
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({
                'calls':[], 'final':{'status':'completed','reason':'这是一个普通问题的回答。','evidence':[]}})}}]})
        with patch('primitive_vla.runtime.service.Runtime',side_effect=lambda *a,**kw:Runtime(*a,**kw,transport=httpx.MockTransport(answer))):
            task_id=await self.start()
            await self.app.state.service.worker
        result=(await self.client.get('/api/tasks/'+task_id)).json()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['steps'],[])
        self.assertEqual(result['images'],{})

    async def test_action_and_goal_verdict_are_separate_visible_steps(self):
        store=self.app.state.service.store
        store.create('review-fixture','完成两个目标',{})
        call={'id':'action','name':'vla.run','arguments':{}}
        store.event('review-fixture',{'event':'tool_finished','call':call,'result':{
            'status':'ok','data':{'task_success':None},'before_images':{'front':'before.jpg'},
            'images':{'front':'after.jpg'}}})
        store.event('review-fixture',{'event':'outcome_review_started','call_id':'action'})
        store.event('review-fixture',{'event':'outcome_review_finished','call_id':'action',
            'goal':'物体放好且已松开','verdict':'satisfied','reason':'前后图支持放置完成',
            'evidence':['action:after:front']})
        store.finish('review-fixture',{'status':'completed','reason':'done'}, {})
        result=(await self.client.get('/api/tasks/review-fixture')).json()
        self.assertEqual([s['name'] for s in result['steps']],['vla.run','outcome.assess'])
        self.assertEqual(result['steps'][1]['data']['verdict'],'satisfied')
        self.assertEqual(result['steps'][1]['data']['source'],'independent')
        self.assertEqual(result['images']['before:front'],'before.jpg')
        self.assertEqual(result['images']['after:front'],'after.jpg')

    async def test_main_agent_judgment_and_revision_remain_visible_without_review_request(self):
        store=self.app.state.service.store
        store.create('main-review-fixture','判断结果',{})
        for revision,verdict in [(1,'unknown'),(2,'satisfied')]:
            store.event('main-review-fixture',{'event':'outcome_review_finished','call_id':'action',
                'goal':'物体放好','verdict':verdict,'reason':'查看前后图片',
                'evidence':['action'],'source':'main_agent','revision':revision})
        store.finish('main-review-fixture',{'status':'completed','reason':'done'}, {})
        result=(await self.client.get('/api/tasks/main-review-fixture')).json()
        self.assertEqual([s['data']['revision'] for s in result['steps']],[1,2])
        self.assertTrue(all(s['data']['source']=='main_agent' for s in result['steps']))
        self.assertEqual(self.requests,0)

    async def test_stopped_turn_exposes_partial_progress_and_format_error_count(self):
        store=self.app.state.service.store
        store.create('stopped-fixture','先放碗，再倒蓝杯',{})
        store.finish('stopped-fixture',{'status':'stopped','reason':'Turn budget exhausted'},
                     {'working_memory':'已运行抓碗；蓝杯倾倒尚未执行。','history':[
                         {'kind':'user','instruction':'先放碗，再倒蓝杯'},
                         {'kind':'feedback','text':'Invalid model response: empty content'}]})
        result=(await self.client.get('/api/tasks/stopped-fixture')).json()
        self.assertEqual(result['turns'][0]['model_errors'],1)
        self.assertIn('蓝杯倾倒尚未执行',result['turns'][0]['progress'])
        self.assertIsNone(result['command'])
        self.assertEqual(store.get('stopped-fixture')['report']['reason'],'Turn budget exhausted')
        self.assertIsNone(result['command'])
        self.assertEqual(self.camera.calls,0)
        self.assertEqual(self.inference.calls,[])

    async def test_refresh_invalidates_a_previous_plan_in_journal_projection(self):
        task_id=await self.start();await self.app.state.service.worker
        service=self.app.state.service
        service.store.event(task_id,{'event':'tool_started','call':{'id':'new','name':'camera.observe','arguments':{}},'read_only':True})
        service.store.event(task_id,{'event':'tool_finished','call':{'id':'new','name':'camera.observe','arguments':{}},'result':{'status':'error','text':'offline','data':{},'images':{}}})
        result=(await self.client.get('/api/tasks/'+task_id)).json()
        self.assertIsNone(result['command']);self.assertEqual(result['images'],{});self.assertNotIn('arm',result)

    async def test_followup_keeps_history_but_does_not_reuse_live_plan(self):
        first=await self.start();await self.app.state.service.worker
        observed=[]
        def answer(request):
            wire=json.loads(request.content)
            context=json.loads(wire['messages'][1]['content'][0]['text'])
            observed.append(context)
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({
                'final':{'status':'completed','reason':'上一次已找到粉色碗，但尚未移动。','evidence':[]}})}}]})
        before_calls=self.camera.calls
        with patch('primitive_vla.runtime.service.Runtime',side_effect=lambda *a,**kw:Runtime(*a,**kw,transport=httpx.MockTransport(answer))):
            response=await self.client.post('/api/tasks',json={'instruction':'上次做了什么？','parent_id':first})
            self.assertEqual(response.status_code,202,response.text)
            second=response.json()['id'];await self.app.state.service.worker
        result=(await self.client.get('/api/tasks/'+second)).json()
        self.assertEqual(result['conversation_id'],first)
        self.assertEqual(len(result['turns']),2)
        self.assertEqual(result['steps'],[]);self.assertIsNone(result['command'])
        self.assertEqual(self.camera.calls,before_calls)
        self.assertTrue(any(e.get('tool')=='camera.observe' for e in observed[0]['history']))
        provider=next(r for r in self.app.state.service.runtime.resources if isinstance(r,CobotTools))
        self.assertIsNone(provider.last_plan)
        self.assertEqual((await self.client.post('/api/tasks',json={'instruction':'过期轮次','parent_id':first})).status_code,409)
        self.assertEqual((await self.client.post('/api/tasks',json={'instruction':'不存在','parent_id':'missing'})).status_code,404)
