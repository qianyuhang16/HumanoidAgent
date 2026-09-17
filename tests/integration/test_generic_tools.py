"""Generic tool boundaries: no task menu, no stale coordinates, explicit control stop."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from primitive_vla.agent.tool_loop import ToolAgent
from primitive_vla.backends.rmbench_tools import RMBenchTools
from primitive_vla.providers.codex_schema import output_schema


class GenericToolsTests(unittest.IsolatedAsyncioTestCase):
    def make_tools(self, path):
        backend = SimpleNamespace(config={'python':'unused','mode':'vla','observation_mode':'sensors',
            'perception':{'min_mask_score':.7},'max_skill_steps':450},
            worker=SimpleNamespace(request=AsyncMock()),stop=AsyncMock(return_value=True))
        return RMBenchTools(backend,Path(path),SimpleNamespace(write=lambda *a,**kw:None))

    async def test_model_chooses_free_query_and_finish_without_task_predicates(self):
        log = SimpleNamespace(write=lambda *a,**kw:None)
        tools = SimpleNamespace(descriptions={'perceive':'query'},schema={},
            observe=AsyncMock(return_value={'images':{},'facts':{}}), invoke=AsyncMock(return_value={'detections':[]}))
        client = SimpleNamespace(complete=AsyncMock(side_effect=[
            {'call':{'name':'perceive','arguments':{'query':'dial. latch.'}},'reason':'inspect','working_memory':'uncertain'},
            {'call':{'name':'finish','arguments':{'evidence':'visible change'}},'reason':'claim','working_memory':'claim'}]))
        report = await ToolAgent(client,tools,'generic',log).run('user task')
        tools.invoke.assert_awaited_once_with('perceive',{'query':'dial. latch.'})
        self.assertTrue(report['agent_declared_complete'])
        second = client.complete.call_args_list[1].args[2]
        self.assertEqual(second['working_memory'],'uncertain')
        self.assertNotIn('candidates',second)
        self.assertNotIn('visual:complete',str(second))

    async def test_ik_uses_fresh_detection_and_invalidates_after_motion(self):
        with tempfile.TemporaryDirectory() as path:
            tools = self.make_tools(path)
            tools.detections={7:{'id':7,'surface_world':[.1,.2,.8],'mask_score':.9}}
            tools.backend.worker.request.return_value={'moved':True}
            args={'targets':[{'arm':'left','detection_id':7,'clearance':.06}]}
            await tools.invoke('move_to',args)
            tools.backend.worker.request.assert_awaited_once_with('move_to',targets=[
                {'arm':'left','point':[.1,.2,.8],'clearance':.06,'detection_id':7}])
            result=await tools.invoke('move_to',args)
            self.assertIn('fresh',result['error'])
            self.assertEqual(tools.backend.worker.request.await_count,1)

    async def test_execute_preserves_model_instruction_and_limits_chunk_then_stops(self):
        with tempfile.TemporaryDirectory() as path:
            tools=self.make_tools(path)
            result={'executed':3,'subtask_end':False,'exhausted':False,'skill_steps':3}
            tools.backend.worker.request.side_effect=[{'images':{},'proprioception':{'remaining_action_steps':100}},
                {'started':True},{'actions':[[0]*14]*15},result]
            actual=await tools.invoke('execute',{'instruction':'Rotate the visible item gently.','max_actions':3})
            calls=tools.backend.worker.request.call_args_list
            self.assertEqual(calls[1].kwargs['prompt'],'Rotate the visible item gently.')
            self.assertEqual(calls[1].kwargs['position'],'')
            self.assertEqual(len(calls[3].kwargs['actions']),3)
            self.assertEqual(actual['physical_outcome'],'unverified')
            tools.backend.stop.assert_awaited_once()
            self.assertFalse(tools.locked)

    async def test_unacknowledged_stop_locks_control(self):
        with tempfile.TemporaryDirectory() as path:
            tools=self.make_tools(path)
            tools.backend.worker.request.side_effect=[{'images':{},'proprioception':{'remaining_action_steps':100}},
                {'started':True},{'actions':[[0]*14]}, {'executed':1,'subtask_end':True,'exhausted':False}]
            tools.backend.stop.return_value=False
            with self.assertRaisesRegex(RuntimeError,'Stop not acknowledged'):
                await tools.invoke('execute',{'instruction':'Move object','max_actions':1})
            with self.assertRaisesRegex(RuntimeError,'locked'):
                await tools.invoke('observe',{})

    def test_schema_has_no_battery_directions_or_task_skill_candidates(self):
        with tempfile.TemporaryDirectory() as path:
            tools=self.make_tools(path)
            schema=output_schema('tool_plan',{'tool_schema':tools.schema})
            self.assertEqual(len(schema['properties']['call']['anyOf']),6)
            for word in ['battery','positive','negative','INSTALL_PAIR','visual:complete']:
                self.assertNotIn(word,str(schema)+str(tools.descriptions))

    def test_cover_sequence_bookkeeping_stays_inside_simulator(self):
        from unittest.mock import Mock
        from primitive_vla.simulation.rmbench_sensors import advance_sensor_state
        task=SimpleNamespace(fail_flag=False,update_state_transition=Mock(),check_success=Mock())
        self.assertIsNone(advance_sensor_state(task,'cover_blocks'))
        task.update_state_transition.assert_called_once()
        task.check_success.assert_not_called()
        task.fail_flag=True
        advance_sensor_state(task,'cover_blocks')
        task.update_state_transition.assert_called_once()

    def test_shared_capability_documents_have_no_task_content(self):
        from primitive_vla.plugins.generic_tools import capability_descriptions
        docs=capability_descriptions()
        self.assertEqual(set(docs),{'perceive','execute','move_to'})
        for word in ['battery','cover','positive','negative']:
            self.assertNotIn(word,' '.join(docs.values()).lower())
