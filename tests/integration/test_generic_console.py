"""Interactive API uses user prompts, persists runs and owns cancellation."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from primitive_vla.web.generic_app import create_app

ROOT=Path(__file__).resolve().parents[2]


class GenericConsoleTests(unittest.TestCase):
    def test_prompt_submission_conflict_cancel_and_persisted_history(self):
        received=[]
        async def runner(config,directory):
            received.append(config)
            directory.mkdir()
            (directory/'events.jsonl').write_text(json.dumps({'event':'decision','reason':'test'})+'\n')
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                (directory/'summary.json').write_text(json.dumps({'status':'cancelled',
                    'agent_declared_complete':False,'physical_success':None}))
        with tempfile.TemporaryDirectory() as workspace:
            app=create_app(ROOT,workspace,runner=runner)
            with TestClient(app) as client:
                self.assertEqual(client.get('/').status_code,200)
                self.assertEqual(len(client.get('/api/scenes').json()['scenes']),2)
                payload={'scene':'cover','instruction':'只操作左侧目标','seed':7,'planner_prompt':'One step, then verify.'}
                response=client.post('/api/runs',json=payload)
                self.assertEqual(response.status_code,202)
                identifier=response.json()['id']
                self.assertEqual(received[0]['instruction'],payload['instruction'])
                self.assertEqual(Path(received[0]['planner_prompt']).read_text(),payload['planner_prompt'])
                self.assertEqual(received[0]['camera_shader'],'default')
                self.assertEqual(client.post('/api/runs',json=payload).status_code,409)
                rows=client.get(f'/api/runs/{identifier}/events').json()
                self.assertEqual(rows['cursor'],1)
                self.assertEqual(client.get(f'/api/runs/{identifier}/events?after=1').json()['events'],[])
                self.assertEqual(client.post(f'/api/runs/{identifier}/cancel').status_code,200)
            with TestClient(create_app(ROOT,workspace,runner=runner)) as client:
                history=client.get('/api/runs').json()
                self.assertIsNone(history['active_id'])
                self.assertEqual(history['runs'][0]['status'],'cancelled')
                self.assertEqual(history['runs'][0]['instruction'],payload['instruction'])
                self.assertEqual(client.get('/api/runs/not-a-run').status_code,404)

    def test_invalid_requests_do_not_launch_robot(self):
        async def forbidden(*args):
            self.fail('Invalid request launched a robot')
        with tempfile.TemporaryDirectory() as workspace, TestClient(create_app(ROOT,workspace,runner=forbidden)) as client:
            self.assertEqual(client.post('/api/runs',json={'scene':'unknown','instruction':'x'}).status_code,422)
            self.assertEqual(client.post('/api/runs',json={'scene':'cover','instruction':'   '}).status_code,422)
            self.assertEqual(client.post('/api/runs',json={'scene':'cover','instruction':'x','planner_prompt':' '}).status_code,422)
            self.assertEqual(client.post('/api/runs',headers={'Origin':'https://elsewhere.invalid'},json={'scene':'cover','instruction':'x'}).status_code,403)
