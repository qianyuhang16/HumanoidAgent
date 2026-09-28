"""Diagnose incomplete provider replies without dispatching or logging secrets."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from primitive_vla.config.schema import VLMConfig
from primitive_vla.providers.base import ProviderOutputError
from primitive_vla.providers.vlm import VLMClient
from primitive_vla.storage.events import EventLog


class VLMOutputTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, content, finish_reason='stop', usage=None):
        log = EventLog()
        body = {'choices': [{'finish_reason': finish_reason,
                            'message': {'content': content, 'reasoning_content': 'PRIVATE_REASONING'}}],
                'usage': usage or {}}
        with TemporaryDirectory() as temp:
            client = VLMClient(VLMConfig(model='fixture', max_tokens=4096), Path(temp),
                               api_key='SECRET_KEY', log=log,
                               transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
            try:
                result = await client.complete('tool_step', 'Return JSON', {})
                return result, log.events
            except ProviderOutputError as error:
                return error, log.events
            finally:
                await client.close()

    async def test_reasoning_exhaustion_is_reported_as_truncation(self):
        usage = {'completion_tokens': 4096, 'completion_tokens_details': {'reasoning_tokens': 4096}}
        error, events = await self.request('', 'length', usage)
        self.assertIsInstance(error, ProviderOutputError)
        self.assertIn('length', str(error))
        self.assertIn('4096', str(error))
        failed = next(e for e in events if e['event'] == 'model_failed')
        self.assertEqual(failed['finish_reason'], 'length')
        self.assertEqual(failed['usage'], usage)
        self.assertEqual(failed['content_chars'], 0)
        text = json.dumps(events)
        self.assertNotIn('PRIVATE_REASONING', text)
        self.assertNotIn('SECRET_KEY', text)

    async def test_even_parseable_json_is_rejected_if_provider_reports_truncation(self):
        result, _ = await self.request('{"calls":[{"name":"robot.move"}]}', 'length')
        self.assertIsInstance(result, ProviderOutputError)

    async def test_empty_and_malformed_content_have_distinct_diagnostics(self):
        for content, kind in [('', 'empty_content'), (None, 'empty_content'),
                              ('not JSON', 'invalid_json')]:
            with self.subTest(content=content):
                error, events = await self.request(content)
                self.assertIsInstance(error, ProviderOutputError)
                self.assertEqual(events[-1]['output_error'], kind)

    async def test_multiple_objects_are_not_silently_reduced_to_a_motion_command(self):
        content = '{"calls":[{"name":"robot.move"}]}\n{"calls":[],"final":null,"memory":null}'
        error, events = await self.request(content)
        self.assertIsInstance(error, ProviderOutputError)
        self.assertIn('一个 JSON 对象', str(error))
        self.assertEqual(events[-1]['output_error'], 'extra_content')
        self.assertEqual(events[-1]['json_error_line'], 2)

    async def test_wrapped_json_is_accepted_only_when_the_whole_response_is_one_object(self):
        wrapped = '```json\n{"calls":[{"name":"robot.move"}]}\n```'
        result, _ = await self.request(wrapped)
        self.assertEqual(result['calls'][0]['name'], 'robot.move')
        error, events = await self.request(wrapped + '\n{"calls":[]}')
        self.assertIsInstance(error, ProviderOutputError)
        self.assertEqual(events[-1]['output_error'], 'extra_content')
