"""Tool images are model inputs, not a second model-selected resource operation."""
import json
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.contracts import Domain, Record
from primitive_vla.memory import Memory
from primitive_vla.planning import ModelPlanner
from primitive_vla.planning.actions import ARGUMENT_SCHEMAS
from primitive_vla.runtime.agent import Agent
from primitive_vla.skills import SkillStore
from primitive_vla.tools.contracts import ToolResult, Verification
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.vlm import FunctionCall, VLMResponse
from tests.support import Provider, spec


def pixels(request):
    return [image.data for message in request.messages for image in message.images]


class ToolImageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.memory = Memory(self.directory.name)
        self.registry = ToolRegistry()
        self.requests = []
        self.calls = []
        test = self

        class Model:
            async def generate(self, request):
                test.requests.append(request)
                return VLMResponse((test.calls.pop(0),))

        self.agent = Agent(ModelPlanner(Model(), read_resource=self.memory.read_resource),
                           self.registry, self.memory, SkillStore(), ResourceManager())
        self.session = self.agent.create_session('Inspect the returned images', 'r', 'test')

    async def asyncTearDown(self):
        await self.agent.close()
        self.directory.cleanup()

    def call(self, name):
        return FunctionCall('decide', {'action': 'call_tool',
                                      'arguments': {'name': name, 'arguments': {'value': 1}}})

    def stop(self):
        return FunctionCall('decide', {'action': 'ask_user', 'arguments': {'question': 'Continue?'}})

    async def test_tool_returns_pixels_on_next_decision_without_resource_action(self):
        image = self.memory.put_resource(b'current pixels', 'image/png')
        depth = self.memory.put_resource(b'depth array', 'application/x-npy')
        self.registry.register(spec(), Provider(ToolResult({'value': 1, 'large': 'x' * 30000},
                                                           (image, depth))))
        self.calls = [self.call('state.inspect'), self.stop()]
        await self.agent.run(self.session.id)
        self.assertEqual(self.session.decisions, 2)
        self.assertEqual(pixels(self.requests[1]), [b'current pixels'])
        call, result = self.requests[1].messages[-2:]
        data = json.loads(result.text)
        self.assertEqual(call.call_id, result.call_id)
        attached = data['images'][0]
        internal = self.session.images[image.id]
        record = self.memory.evidence(internal['record_id'])
        self.assertEqual(attached, {'ref': 'image_1', 'description': ''})
        self.assertEqual(internal['call_id'], record.call_id)
        self.assertEqual(internal['recorded_at'], record.timestamp)
        self.assertEqual(record.resource_refs, (image, depth))
        self.assertEqual(call.call.arguments['arguments']['arguments'], {'value': 1})
        self.assertIn('preview', data)
        self.assertEqual(data['record_id'], record.id)
        self.assertNotIn('get_resource', ARGUMENT_SCHEMAS)

    async def test_model_packet_omits_bookkeeping_without_stripping_business_fields(self):
        image = self.memory.put_resource(b'pixels', 'image/png', 'Front camera')
        business = {'value': 1, 'id': 'target-7', 'timestamp': 123, 'frame': 'camera',
                    'point': [1, 2, 3], 'session_id': 'business-session',
                    'event_type': 'business-event', 'payload': {'measured': True}}
        self.registry.register(spec(), Provider(ToolResult(business, (image,))))
        self.calls = [self.call('state.inspect'), self.stop()]
        await self.agent.run(self.session.id)
        request = self.requests[1]
        data = json.loads(request.messages[-1].text)
        visible = '\n'.join(message.text for message in request.messages)
        self.assertEqual(data['output'], business)
        self.assertEqual(visible.count('target-7'), 1)
        for private_value in (self.session.id, self.session.task_id, image.uri):
            self.assertNotIn(private_value, visible)
        for key in ('recorded_at', 'media_type', 'expires_at', 'active_seconds', 'tool_calls'):
            self.assertNotIn('"' + key + '"', visible)
        record = self.memory.evidence(data['evidence_id'])
        self.assertEqual(record.session_id, self.session.id)
        self.assertGreater(record.timestamp, 0)
        self.assertEqual(record.payload['result']['payload'], business)
        self.assertEqual(pixels(request), [b'pixels'])

    async def test_camera_result_contains_only_image_label_and_pixels(self):
        image = self.memory.put_resource(b'camera pixels', 'image/png', 'Front camera', metadata={
            'camera': 'front', 'width': 320, 'height': 240, 'episode_id': 'private-episode',
            'simulation_steps': 75})
        self.registry.register(spec(output_schema={'type': 'object'}), Provider(ToolResult({}, (image,))))
        self.calls = [self.call('state.inspect'), self.stop()]
        await self.agent.run(self.session.id)
        request = self.requests[1]
        data = json.loads(request.messages[-1].text)
        self.assertEqual(data, {'images': [{'ref': 'image_1', 'camera': 'front', 'width': 320, 'height': 240}]})
        self.assertEqual(request.messages[-1].role, 'tool')
        self.assertEqual(pixels(request), [b'camera pixels'])
        for value in (image.id, image.uri, 'private-episode', 'simulation_steps'):
            self.assertNotIn(value, request.messages[-1].text)

    async def test_image_reference_survives_reordering_eviction_and_session_reload(self):
        images = [self.memory.put_resource(str(i).encode(), 'image/png', metadata={'camera': 'front'})
                  for i in range(3)]
        for i, image in enumerate(images):
            self.registry.register(spec(f'state.camera{i}', output_schema={'type': 'object'}),
                                   Provider(ToolResult({}, (image,))))
        received = []

        class Locator(Provider):
            async def execute(self, arguments, context):
                received.append(arguments)
                return ToolResult({'value': 1})

        self.registry.register(spec('state.locate', input_schema={
            'type': 'object', 'properties': {'image': {'type': 'string', 'format': 'image-ref'}},
            'required': ['image'], 'additionalProperties': False}), Locator())
        self.calls = [self.call('state.camera0'), self.call('state.camera1'), self.stop()]
        await self.agent.run(self.session.id)
        self.agent.sessions.clear()
        self.session = self.agent.load_session(self.session.id)
        self.agent.respond(self.session.id, 'Use the first picture')
        self.calls = [self.call('state.camera2'), FunctionCall('decide', {'action': 'call_tool',
            'arguments': {'name': 'state.locate', 'arguments': {'image': 'image_1'}}}), self.stop()]
        await self.agent.run(self.session.id)
        self.assertEqual(received, [{'image': images[0].id}])
        messages = self.requests[-1].messages
        self.assertEqual(pixels(self.requests[-1]), [b'1', b'2'])
        self.assertFalse(any(images[0].id in message.text for message in messages))
        correction = next(i for i, message in enumerate(messages) if message.text == 'Use the first picture')
        self.assertEqual(messages[correction - 1].text, 'Continue?')
        self.assertEqual(messages[correction + 1].call.arguments['arguments']['name'], 'state.camera2')
        self.assertEqual(messages[-2].call.arguments['arguments']['arguments'], {'image': 'image_1'})
        self.assertEqual(self.session.resolve_image('image_1'), images[0].id)
        self.assertEqual(len(self.session.image_references), 3)
        self.agent.respond(self.session.id, 'Check an invalid reference')
        self.calls = [FunctionCall('decide', {'action': 'call_tool', 'arguments': {
            'name': 'state.locate', 'arguments': {'image': 'image_99'}}}), self.stop()]
        await self.agent.run(self.session.id)
        self.assertEqual(len(received), 1)
        self.assertIn('unknown image reference', json.loads(self.requests[-1].messages[-1].text)['message'])

    async def test_image_reference_can_be_shown_to_user_and_cited_as_evidence(self):
        image = self.memory.put_resource(b'evidence pixels', 'image/png')
        self.registry.register(spec(output_schema={'type': 'object'}), Provider(ToolResult({}, (image,))))
        self.calls = [self.call('state.inspect'), FunctionCall('decide', {'action': 'ask_user',
            'arguments': {'question': 'This picture?', 'resource_ids': ['image_1']}})]
        await self.agent.run(self.session.id)
        self.assertEqual(self.session.last_result['resources'][0]['id'], image.id)
        record_id = self.session.resolve_image('image_1', evidence=True)
        self.agent.respond(self.session.id, 'Yes')
        self.calls = [FunctionCall('decide', {'action': 'memory_write', 'arguments': {
            'title': 'Observation', 'content': 'Evidence retained', 'source_ids': ['image_1']}}),
            FunctionCall('decide', {'action': 'finish', 'arguments': {
                'message': 'Inspected', 'outcome': 'failed', 'evidence_ids': ['image_1']}})]
        await self.agent.run(self.session.id)
        notes = self.memory.search(filters={'session_id': self.session.id, 'event_type': 'note'})
        self.assertEqual(notes[0].payload['source_ids'], [record_id])
        finished = self.memory.search(filters={'session_id': self.session.id, 'event_type': 'task_finished'})
        self.assertEqual(finished[0].payload['evidence_ids'], [record_id])

    async def test_async_completion_attaches_image_when_wait_resumes(self):
        image = self.memory.put_resource(b'async pixels', 'image/png')
        provider = Provider(ToolResult({'value': 1}, (image,)), blocked=True)
        self.registry.register(spec(execution_mode='async'), provider)
        self.calls = [self.call('state.inspect'),
                      FunctionCall('decide', {'action': 'wait', 'arguments': {'seconds': 1}}), self.stop()]
        original_generate = self.agent.planner.vlm.generate

        async def generate(request):
            if len(self.requests) == 1:
                provider.release.set()
            return await original_generate(request)

        self.agent.planner.vlm.generate = generate
        await self.agent.run(self.session.id)
        self.assertEqual(pixels(self.requests[1]), [])
        self.assertEqual(pixels(self.requests[2]), [b'async pixels'])
        request = self.requests[2]
        update = next(message for message in request.messages if message.images)
        self.assertEqual(update.role, 'user')
        self.assertTrue(update.text.startswith('Runtime update (state.inspect)'))
        wait, result = request.messages[-2:]
        self.assertEqual(wait.call.arguments['action'], 'wait')
        self.assertEqual(wait.call_id, result.call_id)
        self.assertFalse(result.images)

    async def test_execution_and_verification_images_follow_same_contract(self):
        for name, domain in [('execution.inspect', Domain.EXECUTION),
                             ('verification.inspect', Domain.VERIFICATION)]:
            image = self.memory.put_resource(name.encode(), 'image/png')
            verification = Verification('target', 'uncertain', 'occluded', (image,)) if domain == Domain.VERIFICATION else None
            refs = () if verification else (image,)
            self.registry.register(spec(name, domain), Provider(ToolResult({'value': 1}, refs, verification)))
            self.calls.append(self.call(name))
        self.calls.append(self.stop())
        await self.agent.run(self.session.id)
        self.assertEqual(pixels(self.requests[1]), [b'execution.inspect'])
        self.assertEqual(pixels(self.requests[2]),
                         [b'execution.inspect', b'verification.inspect'])

    async def test_execution_returning_only_image_keeps_command_and_completion(self):
        image = self.memory.put_resource(b'after motion', 'image/png')
        self.registry.register(spec('execution.adjust', Domain.EXECUTION, output_schema={'type': 'object'}),
                               Provider(ToolResult({}, (image,))))
        self.calls = [self.call('execution.adjust'), self.stop()]
        await self.agent.run(self.session.id)
        call, observation = self.requests[1].messages[-2:]
        result = json.loads(observation.text)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(call.call.arguments['arguments']['arguments'], {'value': 1})
        self.assertEqual(result['images'][0]['ref'], 'image_1')
        self.assertIn('call_id', result)

    async def test_reading_historical_evidence_includes_its_image(self):
        image = self.memory.put_resource(b'historical pixels', 'image/png')
        record = self.memory.write(Record('previous-session', 'previous-task', 'state.inspect', Domain.STATE,
                                         'tool_finished', 'completed', resource_refs=(image,)))
        self.calls = [FunctionCall('decide', {'action': 'evidence_get', 'arguments': {'id': record.id}}),
                      self.stop()]
        await self.agent.run(self.session.id)
        self.assertEqual(pixels(self.requests[0]), [])
        self.assertEqual(pixels(self.requests[1]), [b'historical pixels'])

    async def test_stream_images_persist_without_waking_unsubscribed_planner(self):
        image = self.memory.put_resource(b'stream pixels', 'image/png')
        cursor = self.agent.events.version(self.session.id)
        await self.agent.observations.publish(session_id=self.session.id, task_id=self.session.task_id,
                                              source='state.camera', payload={}, resource_refs=(image,))
        self.assertEqual(self.agent.events.version(self.session.id), cursor)
        self.agent.sessions.clear()
        self.session = self.agent.load_session(self.session.id)
        self.calls = [self.stop()]
        await self.agent.run(self.session.id)
        self.assertEqual(pixels(self.requests[0]), [b'stream pixels'])

    async def test_unseen_stream_frames_do_not_accumulate_model_references(self):
        for index in range(20):
            image = self.memory.put_resource(str(index).encode(), 'image/png')
            await self.agent.observations.publish(session_id=self.session.id, task_id=self.session.task_id,
                                                  source='state.camera', payload={}, resource_refs=(image,))
        self.assertEqual(self.session.image_references, {})
        self.calls = [self.stop()]
        await self.agent.run(self.session.id)
        self.assertEqual(len(self.session.image_references), 2)
        self.assertEqual(pixels(self.requests[0]), [b'18', b'19'])
