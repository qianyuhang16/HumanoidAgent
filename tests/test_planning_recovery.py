"""Generic regressions distilled from real decision failures; no platform imports."""
import json
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.contracts import Domain, Record
from primitive_vla.memory import Memory
from primitive_vla.planning import Decision, ModelPlanner
from primitive_vla.runtime.agent import Agent
from primitive_vla.skills import SkillStore
from primitive_vla.tools.contracts import ToolResult
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.vlm import FunctionCall, VLMResponse
from tests.support import ScriptedPlanner, Provider, spec


class PlanningRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.memory = Memory(self.directory.name)
        self.skills = SkillStore()
        self.skills.add('---\nid: state.inspect\nversion: "1"\ndescription: Inspect a resource\n---\n'
                        '# Usage\nRead the identifier from the observation result.')
        self.registry = ToolRegistry()
        self.agent = None

    async def asyncTearDown(self):
        if self.agent:
            await self.agent.close()
        else:
            self.memory.close()
        self.directory.cleanup()

    def make_agent(self, planner):
        self.agent = Agent(planner, self.registry, self.memory, self.skills, ResourceManager())
        return self.agent.create_session('Inspect the observations and report the evidence', 'r', 'test')

    async def test_progress_flood_preserves_action_arguments_and_image_reference(self):
        session = self.make_agent(ScriptedPlanner())
        image = self.memory.put_resource(b'before motion', 'image/png')
        await self.agent.observations.publish(session_id=session.id, task_id=session.task_id,
                                              source='state.camera', payload={}, resource_refs=(image,))
        started = self.memory.write(Record(session.id, session.task_id, 'execution.adjust', Domain.EXECUTION,
            'tool_started', 'running', {'arguments': {'turn': -0.5, 'steps': 20}}, call_id='motion'))
        for step in range(40):
            self.memory.write(Record(session.id, session.task_id, 'execution.adjust', Domain.EXECUTION,
                'tool_progress', 'running', {'step': step, 'telemetry': 'x' * 1100}, call_id='motion'))
        finished = self.memory.write(Record(session.id, session.task_id, 'execution.adjust', Domain.EXECUTION,
            'tool_finished', 'completed', {'result': {'payload': {'ended': True}}, 'error': None}, call_id='motion'))
        self.agent._feedback(session, {'event_version': 42})
        context = self.agent.context.build(session, [])
        self.assertEqual(self.memory.evidence(started.id).payload['arguments'], {'turn': -0.5, 'steps': 20})
        self.assertNotIn('telemetry', json.dumps(context.data))
        call = context.data['recent_calls'][0]
        self.assertEqual(call['arguments'], {'turn': -0.5, 'steps': 20})
        self.assertEqual(call['evidence_id'], finished.id)
        self.assertEqual(context.data['images'][0]['ref'], 'image_1')
        await self.agent.observations.publish(session_id=session.id, task_id=session.task_id,
                                              source='state.camera', payload={}, resource_refs=(image,))
        self.assertEqual(self.agent.context.build(session, []).data['images'][0]['ref'], 'image_1')

    async def test_context_compacts_progress_without_deleting_audit_evidence(self):
        session = self.make_agent(ScriptedPlanner())
        records = [self.memory.write(Record(session.id, session.task_id, 'execution.adjust', Domain.EXECUTION,
                    'tool_progress', 'running', {'step': index}, call_id='active')) for index in range(30)]
        selected = self.memory.search(filters={'session_id': session.id}, collapse_progress=True)
        self.assertEqual([r.id for r in selected if r.event_type == 'tool_progress'], [records[-1].id])
        self.assertEqual(len(self.memory.search(filters={'session_id': session.id, 'event_type': 'tool_progress'}, limit=100)), 30)
        self.assertEqual(self.memory.evidence(records[0].id).payload, {'step': 0})

    async def test_inspection_includes_skill_usage_with_schema(self):
        self.registry.register(spec(), Provider())
        planner = ScriptedPlanner(Decision('inspect_tool', {'name': 'state.inspect'}),
                                  Decision('ask_user', {'question': 'Continue?'}))
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        inspected = planner.contexts[1].data['last_result']
        self.assertIn('input_schema', inspected)
        self.assertIn('Read the identifier', inspected['skill']['content'])

    async def test_skill_body_horizontal_rule_is_not_treated_as_registration_metadata(self):
        body = '---\n# Usage\nKeep this horizontal rule.'
        self.skills.add('---\nid: reference\nversion: "1"\ndescription: Knowledge\n---\n' + body)
        planner = ScriptedPlanner(Decision('read_skill', {'id': 'reference'}),
                                  Decision('ask_user', {'question': 'Continue?'}))
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        self.assertEqual(planner.contexts[1].data['last_result']['content'], body)
        self.assertEqual(self.skills.read('reference', offset=4, limit=7)['content'], body[4:11])

    async def test_boolean_property_schema_does_not_break_image_reference_discovery(self):
        provider = Provider()
        self.registry.register(spec(input_schema={'type': 'object', 'properties': {'value': True}}), provider)
        session = self.make_agent(ScriptedPlanner(
            Decision('call_tool', {'name': 'state.inspect', 'arguments': {'value': 1}}),
            Decision('ask_user', {'question': 'Continue?'})))
        await self.agent.run(session.id)
        self.assertEqual(session.status, 'waiting_user')
        self.assertEqual(provider.executions, 1)

    async def test_image_and_skill_survive_protocol_and_action_errors(self):
        first = self.memory.put_resource(b'first image', 'image/png')
        second = self.memory.put_resource(b'second image', 'image/png')
        self.registry.register(spec('state.front'), Provider(ToolResult({'value': 1}, (first,))))
        self.registry.register(spec('state.side'), Provider(ToolResult({'value': 1}, (second,))))
        calls = iter([
            FunctionCall('decide', {'action': 'call_tool', 'arguments': {'name': 'state.front', 'arguments': {'value': 1}}}),
            FunctionCall('decide', {'action': 'read_skill', 'arguments': {'id': 'state.inspect'}}),
            # Raw response shape observed in the real API case: inner action used as function name.
            FunctionCall('memory_search', {'query': 'previous observations'}),
            FunctionCall('decide', {'action': 'call_tool', 'arguments': {'name': 'state.side', 'arguments': {'value': 1}}}),
            FunctionCall('decide', {'action': 'decide_recover', 'arguments': {}}),
            FunctionCall('decide', {'action': 'ask_user', 'arguments': {'question': 'Continue?'}}),
        ])
        requests = []

        class Model:
            async def generate(self, request):
                requests.append(request)
                return VLMResponse((next(calls),))

        session = self.make_agent(ModelPlanner(Model(), read_resource=self.memory.read_resource))
        await self.agent.run(session.id)
        self.assertEqual(session.status, 'waiting_user')
        self.assertEqual(session.tool_calls, 2)
        for index in (1, 2, 3):
            self.assertEqual([image.data for image in requests[index].images], [b'first image'])
        after_protocol_error = json.loads(requests[3].text)
        self.assertIn('Read the identifier', json.dumps(after_protocol_error['capabilities']))
        feedback = after_protocol_error['last_result']
        self.assertEqual(feedback['received_calls'][0]['name'], 'memory_search')
        self.assertIn('decide', feedback['error'])
        self.assertIn('action', feedback['error'])
        after_action_error = json.loads(requests[5].text)
        self.assertEqual({image.data for image in requests[5].images}, {b'first image', b'second image'})
        self.assertNotIn('Failed validating', after_action_error['last_result']['error'])
        self.assertLess(len(json.dumps(after_action_error['last_result'])), 1000)
        self.assertEqual(after_action_error['last_result']['attempted_action']['action'], 'decide_recover')
        self.agent.sessions.clear()
        reloaded = self.agent.load_session(session.id)
        context = self.agent.context.build(reloaded, [])
        self.assertEqual({image['id'] for image in context.image_resources}, {first.id, second.id})

    async def test_returned_images_are_bounded_and_not_repeated(self):
        images = [self.memory.put_resource(str(i).encode(), 'image/png') for i in range(3)]
        for index, image in enumerate(images):
            self.registry.register(spec(f'state.camera{index}'), Provider(ToolResult({'value': 1}, (image,))))
        planner = ScriptedPlanner(
            *(Decision('call_tool', {'name': f'state.camera{i}', 'arguments': {'value': 1}}) for i in (0, 1, 0, 2)),
            Decision('ask_user', {'question': 'Continue?'}),
        )
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        context = planner.contexts[-1]
        self.assertEqual([ref['id'] for ref in context.image_resources], [images[2].id, images[0].id])
        self.assertEqual(len(session.images), 2)
        size = len(json.dumps(context.data, ensure_ascii=False)) + len(json.dumps(context.action_schema, ensure_ascii=False))
        self.assertLessEqual(size, self.agent.limits.context_chars)

    async def test_followup_remains_in_model_request_after_tool_history_and_reload(self):
        image = self.memory.put_resource(b'prior observation', 'image/png')
        self.registry.register(spec(), Provider(ToolResult({'value': 1}, (image,))))
        session = self.make_agent(ScriptedPlanner(
            Decision('call_tool', {'name': 'state.inspect', 'arguments': {'value': 1}}),
            Decision('finish', {'outcome': 'failed', 'message': 'Target not visible'}),
        ))
        await self.agent.run(session.id)
        self.agent.respond(session.id, 'Change the viewpoint before continuing')
        for index in range(30):
            self.agent._feedback(session, {'observation': index})
        self.memory.save_session(session.to_dict())
        self.agent.sessions.clear()
        session = self.agent.load_session(session.id)
        requests = []

        class Model:
            async def generate(self, request):
                requests.append(request)
                return VLMResponse((FunctionCall('decide', {'action': 'ask_user',
                                                           'arguments': {'question': 'Continue?'}}),))

        self.agent.planner = ModelPlanner(Model(), read_resource=self.memory.read_resource)
        await self.agent.run(session.id)
        data = json.loads(requests[0].text)
        self.assertEqual(data['user_input']['answer'], 'Change the viewpoint before continuing')
        self.assertEqual(data['goal'], session.goal)
        self.assertEqual([item.data for item in requests[0].images], [b'prior observation'])
        self.assertNotIn('session', data)
        self.assertEqual(session.decisions, 3)
        self.assertEqual(data['last_result'], {'observation': 29})
        self.assertLessEqual(len(json.dumps(data, ensure_ascii=False)) + len(json.dumps(self.agent.context.build(session, []).action_schema, ensure_ascii=False)), self.agent.limits.context_chars)

    async def test_invalid_model_calls_are_never_dispatched(self):
        provider = Provider()
        self.registry.register(spec('execution.act', Domain.EXECUTION), provider)
        action = {'action': 'call_tool', 'arguments': {'name': 'execution.act', 'arguments': {'value': 1}}}
        replies = iter([
            VLMResponse((FunctionCall('call_tool', action['arguments']),)),
            VLMResponse((FunctionCall('decide', action), FunctionCall('decide', action))),
            VLMResponse((FunctionCall('decide', {'action': 'ask_user', 'arguments': {'question': 'Continue?'}}),)),
        ])

        class Model:
            async def generate(self, request):
                return next(replies)

        session = self.make_agent(ModelPlanner(Model()))
        await self.agent.run(session.id)
        self.assertEqual(session.status, 'waiting_user')
        self.assertEqual(session.tool_calls, 0)
        self.assertEqual(provider.executions, 0)

    async def test_rejected_tool_arguments_remain_in_feedback(self):
        provider = Provider()
        self.registry.register(spec(), provider)
        planner = ScriptedPlanner(
            Decision('call_tool', {'name': 'state.inspect', 'arguments': {'x': 12}}),
            Decision('ask_user', {'question': 'Continue?'}),
        )
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        error = planner.contexts[1].data['last_result']['error']
        self.assertEqual(error['kind'], 'interface_error')
        self.assertEqual(error['evidence']['arguments'], {'x': 12})
        self.assertEqual(provider.executions, 0)

    async def test_resource_id_error_explains_record_contract_and_can_be_corrected(self):
        resource = self.memory.put_resource(b'evidence', 'application/json')
        record = self.memory.write(Record('s', 't', 'verification.inspect', Domain.VERIFICATION,
                                         'verification', 'completed', {'conclusion': 'failure'},
                                         resource_refs=(resource,)))
        planner = ScriptedPlanner(
            Decision('finish', {'message': 'Observed failure', 'outcome': 'failed',
                                'evidence_ids': [resource.id]}),
            Decision('finish', {'message': 'Observed failure', 'outcome': 'failed',
                                'evidence_ids': [record.id]}),
        )
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        error = planner.contexts[1].data['last_result']['error']
        self.assertIn('journal record ID', error)
        self.assertIn(resource.id, error)
        self.assertEqual(session.status, 'failed')
        finished = self.memory.search(filters={'session_id': session.id, 'event_type': 'task_finished'})
        self.assertEqual(finished[0].payload['evidence_ids'], [record.id])

    async def test_empty_search_explains_literal_matching_and_allows_narrower_query(self):
        self.memory.write(Record('s', 't', 'state.inspect', Domain.STATE, 'observation', 'observed',
                                 {'description': 'cable on a shelf'}))
        planner = ScriptedPlanner(
            Decision('memory_search', {'query': 'cable location previous attempts'}),
            Decision('memory_search', {'query': 'cable'}),
            Decision('ask_user', {'question': 'Continue?'}),
        )
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        first = planner.contexts[1].data['last_result']
        self.assertEqual(first['records'], [])
        self.assertIn('AND', first['query_semantics'])
        self.assertIn('keyword', first['hint'])
        self.assertEqual(len(planner.contexts[2].data['last_result']['records']), 1)
