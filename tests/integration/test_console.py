"""Integration tests for configuration, persistence, cancellation and VLM boundaries."""

import asyncio
import base64
import json
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from primitive_vla.agent.task_parser import DEFAULT_INSTRUCTION
from primitive_vla.storage.events import EventLog
from primitive_vla.domain.models import ExecutionStatus, SkillCall, Truth
from primitive_vla.execution.control import ControlAuthority
from primitive_vla.agent.task_parser import parse_instruction
from primitive_vla.providers.vlm import ProviderError, VLMClient
from primitive_vla.runtime.session import Runtime
from primitive_vla.backends.http import HTTPBackend
from primitive_vla.web.app import create_app
from primitive_vla.config.schema import BackendConfig
from primitive_vla.config.loader import default_config
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.state import State

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9X8AAAAASUVORK5CYII='


def wait_task(client, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = client.get('/api/tasks/' + task_id).json()
        if task['status'] not in ('running', 'cancelling'):
            return task
        time.sleep(0.01)
    raise AssertionError('Task did not terminate')


class ConsoleTests(unittest.TestCase):
    def test_saved_config_snapshot_and_history_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with TestClient(create_app(workspace)) as client:
                config = client.get('/api/config').json()['config']
                config['backends']['mock']['delay_s'] = 0.03
                self.assertEqual(client.put('/api/config', json=config).status_code, 200)
                task_id = client.post('/api/tasks', json={'instruction': DEFAULT_INSTRUCTION}).json()['id']
                conflict = client.post('/api/tasks', json={'instruction': DEFAULT_INSTRUCTION})
                self.assertEqual(conflict.status_code, 409)
                config['name'] = 'Next task configuration'
                del config['scene']['facts']['reachable:red_fruit']
                client.put('/api/config', json=config)
                task = wait_task(client, task_id)
                self.assertEqual(task['status'], 'completed')
                self.assertEqual(task['report']['steps'], 7)
                self.assertNotEqual(task['config']['name'], config['name'])
                events = client.get(f'/api/tasks/{task_id}/events').json()['events']
                self.assertTrue(any(e['event'] == 'task_verdict' for e in events))
                last = events[-1]['seq']
                self.assertEqual(client.get(f'/api/tasks/{task_id}/events?after={last}').json()['events'], [])
                client.post('/api/vlm/key', json={'api_key': 'test-memory-only-secret'})
                self.assertNotIn('test-memory-only-secret', (workspace/'config.json').read_text())
                self.assertNotIn('test-memory-only-secret', client.get('/api/config').text)
            with TestClient(create_app(workspace)) as restarted:
                self.assertEqual(restarted.get('/api/config').json()['config']['name'], 'Next task configuration')
                self.assertNotIn('reachable:red_fruit', restarted.get('/api/config').json()['config']['scene']['facts'])
                self.assertEqual(restarted.get('/api/tasks/'+task_id).json()['status'], 'completed')

    def test_web_cancel_preserves_physical_holding_state(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory))) as client:
            config = client.get('/api/config').json()['config']
            config['backends']['mock']['delay_s'] = 0.4
            client.put('/api/config', json=config)
            task_id = client.post('/api/tasks', json={'instruction': DEFAULT_INSTRUCTION}).json()['id']
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if any(e['event'] == 'running' for e in client.get(f'/api/tasks/{task_id}/events').json()['events']):
                    break
                time.sleep(.005)
            self.assertEqual(client.post(f'/api/tasks/{task_id}/cancel').status_code, 200)
            task = wait_task(client, task_id)
            self.assertEqual(task['status'], 'cancelled')
            self.assertEqual(task['state']['facts']['holding:right:spoon']['value'], 'true')
            self.assertFalse(task['report']['control_locked'])

    def test_image_upload_and_configuration_boundary(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory))) as client:
            uploaded = client.post('/api/images', json={'data_url': 'data:image/png;base64,' + PNG})
            self.assertEqual(uploaded.status_code, 201)
            self.assertEqual(client.get(uploaded.json()['url']).content, base64.b64decode(PNG))
            self.assertEqual(client.post('/api/images', json={'data_url': 'data:image/png;base64,aGVsbG8='}).status_code, 422)
            config = client.get('/api/config').json()['config']
            config['skills'][0]['backend'] = 'unregistered'
            self.assertEqual(client.put('/api/config', json=config).status_code, 422)
            self.assertEqual(client.post('/api/tasks', json={'instruction': DEFAULT_INSTRUCTION}, headers={'Origin':'http://foreign.test'}).status_code, 403)


class VLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_gateway_identity_polling_and_stop_ack(self):
        config=default_config()
        call=SkillCall('PLACE',{'object':'spoon','target':'spoon_home','hand':'right'},0)
        candidate=Registry(config.skills).resolve(call,{o.id:o for o in config.scene.objects})
        authority=ControlAuthority()
        lease=authority.acquire(call,candidate)
        polls=0
        acknowledge=False

        def service(request):
            nonlocal polls
            if request.url.path=='/calls' and request.method=='POST':
                body=json.loads(request.content)
                self.assertEqual(body['generation'],lease.generation)
                self.assertIn('right_hand',body['resources'])
                return httpx.Response(200,json={'call_id':call.call_id})
            if request.url.path.endswith('/cancel'):
                return httpx.Response(200,json={'call_id':call.call_id,'stopped':acknowledge})
            polls+=1
            return httpx.Response(200,json={'call_id':call.call_id,'generation':lease.generation,
                                            'status':'running' if polls==1 else 'completed','control_healthy':True})

        backend=HTTPBackend(BackendConfig(type='http',poll_s=.001))
        await backend.http.aclose()
        backend.http=httpx.AsyncClient(base_url='http://gateway.test',transport=httpx.MockTransport(service))
        try:
            status=await backend.run(call,candidate,lease,authority,lambda feedback:None)
            self.assertEqual(status,ExecutionStatus.COMPLETED)
            self.assertEqual(polls,2)
            self.assertFalse(await backend.stop(call.call_id,'test stop'))
            acknowledge=True
            self.assertTrue(await backend.stop(call.call_id,'test stop'))
        finally:
            await backend.close()

    async def test_multimodal_payload_and_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory)
            (media/'front.png').write_bytes(base64.b64decode(PNG))
            calls = []

            def service(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'}}]})

            config = default_config().vlm
            config.model = 'local-vision-model'
            client = VLMClient(config, media, api_key='synthetic-test-key', transport=httpx.MockTransport(service))
            self.assertEqual(await client.complete('test','test',{'goal':'observe'},{'front':'front.png'}), {'ok':True})
            image = calls[0]['messages'][1]['content'][-1]
            self.assertEqual(image['type'], 'image_url')
            self.assertTrue(image['image_url']['url'].startswith('data:image/png;base64,'))
            self.assertEqual(calls[0]['response_format'], {'type':'json_object'})
            with self.assertRaises(ProviderError):
                client.image_url('../outside.png')
            await client.close()

            def malformed(request):
                return httpx.Response(200, json={'choices':[{'message':{'content':'execute arbitrary code'}}]})

            client = VLMClient(config, media, transport=httpx.MockTransport(malformed))
            with self.assertRaises(ProviderError):
                await client.complete('test','test',{})
            await client.close()

    async def test_vlm_parse_plan_and_criterion_verification_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            config = default_config()
            config.parser.type = config.planner.type = 'vlm'
            config.vlm.model = 'test-vision-model'
            config.verification.mode = 'enforce'
            config.backends['mock'].delay_s = .001
            state = State({o.id:o for o in config.scene.objects})
            # Use declared scene keys solely to construct the model fixture.
            from primitive_vla.domain.models import Fact, Observation
            state.update(Observation({k:Fact(v,'fixture') for k,v in config.scene.facts.items()}))
            task = parse_instruction(DEFAULT_INSTRUCTION,state)
            expected = [('PLACE',{'object':'spoon','target':'spoon_home'}),('GRASP',{'object':'blue_cup'}),
                        ('POUR',{}),('PLACE',{'object':'blue_cup','target':'cup_home'}),('GRASP',{'object':'spoon'}),
                        ('STIR',{}),('PLACE',{'object':'spoon','target':'spoon_home'})]
            plan_calls = 0

            def service(request):
                nonlocal plan_calls
                body = json.loads(request.content)
                context = json.loads(body['messages'][1]['content'][0]['text'])
                if 'schema' in context:
                    result = {'goals':[asdict(g) for g in task.goals]}
                elif 'candidates' in context:
                    skill, bindings = expected[plan_calls]
                    selected = next(c for c in context['candidates'] if c['skill']==skill and all(c['bindings'].get(k)==v for k,v in bindings.items()))
                    result = {'action':'skill','index':selected['index'],'reason':'fixture-selected legal candidate'}
                    plan_calls += 1
                else:
                    result = {'criteria':[{'id':g.id,'status':'success','evidence_refs':['fact:'+k for k in g.conditions]} for g in task.goals]}
                return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(result)}}], 'usage':{'total_tokens':10}})

            runtime = Runtime(config, Path(directory), EventLog(), transport=httpx.MockTransport(service))
            try:
                report = await runtime.run('请帮我完成厨房中的倒料、搅拌与工具归位')
                self.assertEqual(report['status'],'completed')
                self.assertEqual(plan_calls,7)
                self.assertEqual(report['verification']['status'],'success')
            finally:
                await runtime.close()

    async def test_enforce_unknown_does_not_override_successful_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            config=default_config()
            config.vlm.model='test-model'
            config.verification.mode='enforce'
            config.backends['mock'].delay_s=.001

            def service(request):
                context=json.loads(json.loads(request.content)['messages'][1]['content'][0]['text'])
                result={'criteria':[{'id':g['id'],'status':'unknown','evidence_refs':[]} for g in context['goals']]}
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result)}}]})

            runtime=Runtime(config,Path(directory),EventLog(),transport=httpx.MockTransport(service))
            try:
                report=await runtime.run(DEFAULT_INSTRUCTION)
                self.assertEqual(report['status'],'stopped')
                self.assertEqual(report['verification']['status'],'unknown')
                self.assertEqual(runtime.state.values()['event:poured:blue_cup:bowl'],Truth.TRUE)
                self.assertEqual(runtime.backends['mock'].executions.count('POUR'),1)
            finally:
                await runtime.close()


if __name__ == '__main__':
    unittest.main()
