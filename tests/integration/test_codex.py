"""Exercise the CLI protocol, full runtime wiring, and subprocess cancellation."""

import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from primitive_vla.config.loader import default_config
from primitive_vla.providers.base import ProviderError
from primitive_vla.providers.codex import CodexClient
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog


class CodexTests(unittest.IsolatedAsyncioTestCase):
    def executable(self, root, body):
        path = root / "fake-codex"
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return str(path)

    async def test_parse_plan_verify_images_and_public_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media").mkdir()
            (root / "media/front.png").write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9X8AAAAASUVORK5CYII="))
            executable = self.executable(root, '''import json, sys, time
from pathlib import Path
args=sys.argv
payload=json.load(sys.stdin)
context=payload['context']
assert '--ignore-user-config' in args and '--image' in args
assert args[args.index('--model')+1]=='gpt-5.5'
assert 'model_reasoning_effort="medium"' in args
assert args[args.index('--sandbox')+1]=='read-only'
print(json.dumps({'type':'turn.started'}),flush=True)
print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE_SENTINEL'}}),flush=True)
time.sleep(.08)
if 'skills' in context:
    result={'goals':[{'id':'home','conditions':[{'predicate':'at:spoon:spoon_home','value':'true'}],'after':[]}],'forbidden_skills':[]}
elif 'candidates' in context:
    candidate=next(c for c in context['candidates'] if c['skill']=='PLACE' and c['bindings'].get('target')=='spoon_home' and c['bindings'].get('object')=='spoon')
    result={'action':'skill','index':candidate['index'],'reason':'Return the held spoon to its rack.'}
else:
    result={'criteria':[{'id':'home','status':'success','reason':'Observed on rack','evidence_refs':['fact:at:spoon:spoon_home']}]}
Path(args[args.index('-o')+1]).write_text(json.dumps(result))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':42,'output_tokens':12}}),flush=True)
''')
            config = default_config()
            config.vlm.provider, config.vlm.model = "codex", "gpt-5.5"
            config.vlm.codex.executable = executable
            config.parser.type = config.planner.type = "vlm"
            config.verification.mode = "enforce"
            config.execution.observation_poll_s = .005
            config.state.mock_fact_ttl_s = .05
            config.backends["mock"].delay_s = .001
            config.scene.images = {"front": "front.png"}
            log = EventLog()
            runtime = Runtime(config, root, log)
            try:
                report = await runtime.run("Return the spoon")
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["verification"]["status"], "success")
                self.assertEqual(report["steps"], 1)
                responses = [e for e in log.events if e["event"] == "model_response"]
                self.assertEqual([r["purpose"] for r in responses], ["parse", "plan", "verify"])
                self.assertEqual(responses[0]["result"]["goals"][0]["conditions"], {"at:spoon:spoon_home": "true"})
                self.assertTrue(all(r["usage"]["input_tokens"] == 42 for r in responses))
                self.assertNotIn("PRIVATE_SENTINEL", json.dumps(log.events))
                self.assertFalse(runtime.vlm.processes)
            finally:
                await runtime.close()

    async def test_cancel_and_timeout_reap_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = default_config().vlm
            config.model = "gpt-5.5"
            config.codex.executable = self.executable(root, "import sys,time\nsys.stdin.read()\ntime.sleep(30)\n")
            log = EventLog()
            client = CodexClient(config, root, log)
            pending = asyncio.create_task(client.complete("connection_test", "test", {}))
            for _ in range(100):
                if client.processes:
                    break
                await asyncio.sleep(.01)
            process = next(iter(client.processes))
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            with self.assertRaises(ProcessLookupError):
                os.kill(process.pid, 0)
            self.assertFalse(client.processes)
            self.assertEqual(log.events[-1]["event"], "model_cancelled")
            config.timeout_s = .1
            with self.assertRaisesRegex(ProviderError, "超时"):
                await client.complete("connection_test", "test", {})
            self.assertFalse(client.processes)
            await client.close()

    async def test_bad_json_and_outside_image_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = default_config().vlm
            config.model = "gpt-5.5"
            config.codex.executable = self.executable(root, '''import sys
from pathlib import Path
sys.stdin.read()
Path(sys.argv[sys.argv.index('-o')+1]).write_text('not JSON')
''')
            client = CodexClient(config, root)
            with self.assertRaises(ProviderError):
                await client.complete("connection_test", "test", {})
            with self.assertRaisesRegex(ProviderError, "inside"):
                await client.complete("connection_test", "test", {}, {"front": "../outside.png"})
            await client.close()
