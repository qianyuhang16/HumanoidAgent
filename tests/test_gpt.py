import asyncio
import base64
from importlib.util import find_spec
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from primitive_vla.vlm import Function, ImageInput, VLMRequest
from primitive_vla.vlm.config import VLMConfig

HTTP_AVAILABLE = find_spec('httpx') is not None
if HTTP_AVAILABLE:
    import httpx
    from primitive_vla.vlm.gpt import GPTVLM


def reply(*items, status='completed'):
    return httpx.Response(200, json={'status': status, 'output': list(items)})


@unittest.skipUnless(HTTP_AVAILABLE, 'install primitive-vla[vlm]')
class GPTTests(unittest.IsolatedAsyncioTestCase):
    def test_model_config_loads_without_platform_or_workspace(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'vlm.json'
            path.write_text(json.dumps({'provider': 'gpt', 'model': 'standalone', 'api_key': 'test-key'}))
            config = VLMConfig.load(path)
            self.assertEqual(config.connect().model, 'standalone')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    async def test_responses_functions_images_and_caller_prompt(self):
        schema = {'type': 'object', 'properties': {'query': {'type': 'string'}}}

        async def respond(request):
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/responses')
            self.assertEqual(request.headers['Authorization'], 'Bearer test-key')
            body = json.loads(request.content)
            self.assertEqual(body['instructions'], 'Caller instructions')
            self.assertEqual(body['tools'][0]['parameters'], schema)
            self.assertFalse(body['tools'][0]['strict'])
            self.assertFalse(body['parallel_tool_calls'])
            self.assertEqual(body['tool_choice'], 'required')
            self.assertFalse(body['store'])
            self.assertNotIn('thinking', body)
            image = body['input'][0]['content'][1]['image_url']
            self.assertEqual(base64.b64decode(image.split(',')[1]), b'image bytes')
            return reply({'type': 'reasoning', 'summary': []},
                         {'type': 'function_call', 'name': 'lookup', 'arguments': '{"query":"cup"}'})

        vlm = GPTVLM('test-key', model='test-gpt', transport=httpx.MockTransport(respond))
        result = await vlm.generate(VLMRequest('Caller instructions', 'Find a cup',
                                   (Function('lookup', schema),), (ImageInput(b'image bytes', 'image/png'),)))
        self.assertEqual(result.calls[0].name, 'lookup')
        self.assertEqual(result.calls[0].arguments, {'query': 'cup'})

    async def test_plain_text_and_configured_endpoint(self):
        async def respond(request):
            self.assertEqual(str(request.url), 'http://localhost:9000/v1/responses')
            body = json.loads(request.content)
            self.assertNotIn('tools', body)
            self.assertEqual(body['max_output_tokens'], 8192)
            return reply({'type': 'message', 'content': [{'type': 'output_text', 'text': 'answer'}]})

        vlm = GPTVLM('test-key', model='test-gpt', base_url='http://localhost:9000/v1/',
                     max_tokens=8192, transport=httpx.MockTransport(respond))
        self.assertEqual((await vlm.generate(VLMRequest('Translate', 'text'))).text, 'answer')

    async def test_incomplete_refusal_and_bad_arguments_are_errors(self):
        responses = [reply(status='incomplete'), reply(status='failed'),
                     reply({'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No'}]}),
                     reply({'type': 'function_call', 'name': 'lookup', 'arguments': '{'})]
        for response in responses:
            with self.subTest(response=response.json()):
                vlm = GPTVLM('key', model='test-gpt', transport=httpx.MockTransport(lambda _: response))
                with self.assertRaises(ValueError):
                    await vlm.generate(VLMRequest('instruction', 'input'))

    async def test_transport_failure_is_not_retried(self):
        seen = []

        async def respond(request):
            seen.append(request)
            return httpx.Response(429)

        vlm = GPTVLM('key', model='test-gpt', transport=httpx.MockTransport(respond))
        with self.assertRaises(httpx.HTTPStatusError):
            await vlm.generate(VLMRequest('instruction', 'input'))
        self.assertEqual(len(seen), 1)

    async def test_cancel_releases_request(self):
        started, stopped = asyncio.Event(), asyncio.Event()

        async def respond(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        vlm = GPTVLM('key', model='test-gpt', transport=httpx.MockTransport(respond))
        task = asyncio.create_task(vlm.generate(VLMRequest('instruction', 'input')))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    def test_model_config_is_independent_and_does_not_repr_secret(self):
        with patch.dict(os.environ, {'CUSTOM_KEY': 'environment-key'}, clear=True):
            for provider, class_name in [('deepseek', 'DeepSeekVLM'), ('gpt', 'GPTVLM')]:
                config = VLMConfig(provider, 'selected-model', api_key='local-secret', api_key_env='CUSTOM_KEY')
                model = config.connect()
                self.assertEqual(type(model).__name__, class_name)
                self.assertEqual(model._api_key, 'environment-key')
                self.assertEqual(model.model, 'selected-model')
                self.assertNotIn('local-secret', repr(config))
            with self.assertRaises(ValueError):
                VLMConfig('unknown', 'model').connect()
            with self.assertRaises(ValueError):
                VLMConfig('gpt', 'model').connect()
