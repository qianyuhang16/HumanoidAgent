"""Persistent follow-up turns retain evidence without replaying actions."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from primitive_vla.config.loader import default_config
from primitive_vla.runtime.service import TaskService, TaskConflict
from primitive_vla.storage.tasks import TaskStore


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_continue_after_restart_and_new_conversation_is_isolated(self):
        contexts = []
        class Model:
            async def step(self, context, tools):
                contexts.append(context)
                if context.task == '继续' and not any(e.get('tool') == 'session.read' for e in context.history):
                    return {'calls': [{'name': 'session.read', 'arguments': {'index': 0}}]}
                return {'final': {'status': 'completed', 'reason': '记住了绿色碗。'}, 'memory': '绿色碗'}
        cfg = default_config()
        cfg.tool_providers = []; cfg.tools = {}; cfg.finish_check = None; cfg.instructions = []
        with tempfile.TemporaryDirectory() as folder, patch('primitive_vla.runtime.session.load_adapter', return_value=lambda *_: Model()):
            service = TaskService(Path(folder))
            first = await service.start('记住绿色碗', config=cfg)
            await service.worker
            await service.close()
            service = TaskService(Path(folder))
            try:
                second = await service.start('继续', config=cfg, parent_id=first['id'])
                await service.worker
                self.assertEqual(second['conversation_id'], first['id'])
                previous = contexts[1]
                self.assertEqual(previous.working_memory, '绿色碗')
                self.assertTrue(any(e.get('instruction') == '记住绿色碗' for e in previous.history))
                self.assertTrue(any(e.get('final', {}).get('reason') == '记住了绿色碗。' for e in previous.history))
                self.assertEqual(previous.images, {})
                report = service.get_task(second['id'])['report']
                self.assertEqual([r['tool'] for r in report['results']], ['session.read'])
                self.assertIn('记住绿色碗', report['results'][0]['data']['content'])
                with self.assertRaises(TaskConflict):
                    await service.start('outdated', config=cfg, parent_id=first['id'])
                third = await service.start('继续', config=cfg, parent_id=second['id'])
                await service.worker
                self.assertEqual(service.get_task(third['id'])['report']['results'], [])
                fresh = await service.start('新话题', config=cfg)
                await service.worker
                self.assertEqual(fresh['conversation_id'], fresh['id'])
                self.assertEqual(contexts[-1].working_memory, '')
                self.assertEqual([e['kind'] for e in contexts[-1].history], ['user'])
                self.assertEqual(len(service.store.conversation(first['id'])), 3)
            finally:
                await service.close()

    async def test_cancelled_turn_can_be_continued_and_busy_turn_cannot(self):
        entered = asyncio.Event()
        class Model:
            async def step(self, context, tools):
                if context.task == 'wait':
                    entered.set(); await asyncio.Event().wait()
                self.context = context
                return {'final': {'status': 'completed', 'reason': '继续回答'}}
        model = Model(); cfg = default_config()
        cfg.tool_providers=[];cfg.tools={};cfg.instructions=[];cfg.finish_check=None
        with tempfile.TemporaryDirectory() as folder, patch('primitive_vla.runtime.session.load_adapter', return_value=lambda *_: model):
            service=TaskService(Path(folder))
            try:
                first=await service.start('wait',config=cfg);await entered.wait()
                with self.assertRaises(TaskConflict):
                    await service.start('continue',config=cfg,parent_id=first['id'])
                await service.cancel(first['id']);await service.worker
                await service.start('continue',config=cfg,parent_id=first['id']);await service.worker
                self.assertTrue(any(e.get('status')=='cancelled' for e in model.context.history))
            finally:
                await service.close()

    def test_legacy_database_migration_keeps_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tasks.db'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,created REAL,updated REAL,status TEXT,instruction TEXT,config TEXT,report TEXT,state TEXT)')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)',('old',1.,1.,'completed','hello','{}',json.dumps({'reason':'hi'}),'{}'))
            db.commit();db.close()
            store=TaskStore(path)
            try:
                self.assertEqual(store.get('old')['conversation_id'],'old')
                store.create('next','again',{},conversation_id='old',parent_id='old')
                self.assertEqual(len(store.conversation('old')),2)
            finally:
                store.close()
