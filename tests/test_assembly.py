import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import yaml

from primitive_vla.runtime.connections.stdio import open_platform
from primitive_vla.tools.assembly import AssemblyCatalog
from primitive_vla.contracts import Domain, Failure, ToolError
from primitive_vla.events import EventBus
from primitive_vla.memory import Memory
from primitive_vla.tools.hooks import Hooks
from primitive_vla.tools.router import ToolRouter
from tests.platform_support import workspace


class AssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.platform_dir = workspace(self.root)
        self.memory = Memory(self.root / 'memory')
        self.catalog = AssemblyCatalog(self.root)
        self.workdir = self.root / 'process'

    async def asyncTearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def open(self, name='coffee'):
        platform, profile = self.catalog.select('external', name)
        return open_platform(platform, profile, memory=self.memory, workdir=self.workdir, options={})

    async def invoke(self, router, name, args=None):
        return await router.invoke(name, args or {}, session_id='s', task_id='t', robot_id='fixture-1', robot_type='fixture')

    async def test_file_discovery_profile_skills_command_environment_and_artifacts(self):
        self.assertFalse(self.workdir.exists())
        self.assertEqual(len(self.catalog.catalog()), 1)
        for name in ('coffee', 'desk'):
            async with self.open(name) as (binding, skills):
                self.assertEqual({s['id'] for s in skills.catalog()},
                                 {name, 'state.fixture', 'execution.fixture', 'verification.fixture'})
                router = ToolRouter(binding.registry, binding.resources, Hooks(self.memory, EventBus()))
                result = await self.invoke(router, 'state.fixture')
                self.assertEqual(result.result.payload['mode'], name)
                self.assertEqual(result.result.payload['env'], 'connected')
                self.assertEqual(result.result.payload['cwd'], str(self.platform_dir))
                self.assertEqual(self.memory.read_resource(result.result.payload['image']), b'raw image bytes')
                await router.close()
            self.assertTrue((self.workdir / 'closed').exists())

    async def test_progress_conflict_stop_confirmation_and_verification(self):
        async with self.open() as (binding, _):
            router = ToolRouter(binding.registry, binding.resources, Hooks(self.memory, EventBus()))
            action = await self.invoke(router, 'execution.fixture')
            conflict = await self.invoke(router, 'execution.fixture')
            self.assertEqual(conflict.error.kind, Failure.RESOURCE)
            await asyncio.sleep(0.03)
            await router.cancel(action.id)
            self.assertEqual(action.status, 'stop_unconfirmed')
            self.assertTrue(binding.resources.occupied('fixture-1'))
            await asyncio.wait_for(action.done.wait(), 2)
            self.assertEqual(action.status, 'cancelled')
            self.assertFalse(binding.resources.occupied('fixture-1'))
            verification = await self.invoke(router, 'verification.fixture')
            self.assertEqual(verification.result.verification.conclusion, 'failure')
            self.assertEqual(self.memory.read_resource(verification.result.verification.evidence[0].id), b'raw image bytes')
            progress = self.memory.search(filters={'call_id': action.id, 'event_type': 'tool_progress'})
            self.assertTrue(progress)
            failed = await self.invoke(router, 'state.fixture', {'error': True})
            self.assertEqual(failed.error.kind, Failure.PRECONDITION)
            self.assertEqual(failed.error.evidence, {'cause': 'hidden'})
            await router.close()

    async def test_views_do_not_write_memory_and_unsubscribe(self):
        async with self.open() as (binding, _):
            before = self.memory.search()
            stream = binding.views.subscribe_view('front')
            first = await asyncio.wait_for(anext(stream), 2)
            self.assertEqual(first.data, b'jpeg')
            await asyncio.sleep(0.05)
            latest = await anext(stream)
            self.assertGreater(int(latest.id), int(first.id))
            await stream.aclose()
            self.assertTrue((self.workdir / 'unsubscribed').exists())
            self.assertEqual(self.memory.search(), before)

    async def test_disconnection_never_releases_execution_ownership(self):
        async with self.open() as (binding, _):
            router = ToolRouter(binding.registry, binding.resources, Hooks(self.memory, EventBus()))
            call = await self.invoke(router, 'execution.fixture', {'crash': True})
            await asyncio.wait_for(call.supervisor, 2)
            self.assertEqual(call.status, 'stop_unconfirmed')
            self.assertEqual(binding.resources.occupied('fixture-1'), {'base': call.id})
            with self.assertRaisesRegex(RuntimeError, 'stop unconfirmed'):
                await router.close()
            self.assertTrue(self.memory.unfinished_calls())

    async def test_missing_skill_tool_and_unknown_selection_never_start_platform(self):
        path = self.platform_dir / 'skills/coffee/skill.md'
        path.write_text(path.read_text().replace('state.fixture', 'state.missing'))
        with self.assertRaisesRegex(ValueError, 'unavailable tools'):
            async with self.open():
                self.fail('invalid assembly accepted')
        self.assertFalse(self.workdir.exists())
        with self.assertRaises(KeyError):
            self.catalog.select('external', 'missing')

    async def test_add_profile_and_skill_by_files_only(self):
        (self.platform_dir / 'skills/new.md').write_text('---\nid: new\nversion: "1"\ndescription: New\nrequired_tools: []\n---\n# New')
        (self.platform_dir / 'profiles/new.json').write_text(json.dumps({'id': 'new', 'label': 'New',
            'environment': {'mode': 'new'}, 'skills': ['skills/new.md']}))
        self.catalog = AssemblyCatalog(self.root)
        async with self.open('new') as (_, skills):
            self.assertEqual(skills.catalog()[0]['id'], 'new')

    async def test_shutdown_refused_during_motion_can_close_after_drain(self):
        async with self.open() as (binding, _):
            started = asyncio.Event()
            async def observe(stage, record):
                if stage == 'on_progress' and record.payload.get('started'):
                    started.set()
            hooks = Hooks(self.memory, EventBus())
            hooks.add(Domain.EXECUTION, observe)
            router = ToolRouter(binding.registry, binding.resources, hooks)
            action = await self.invoke(router, 'execution.fixture')
            await asyncio.wait_for(started.wait(), 2)
            connection = binding.registry.get('execution.fixture').provider.connection
            with self.assertRaisesRegex(ToolError, 'not drained'):
                await connection.close()
            self.assertIsNone(connection.process.returncode)
            self.assertTrue(binding.resources.occupied('fixture-1'))
            await asyncio.wait_for(action.done.wait(), 2)
            await router.close()
        self.assertEqual(connection.process.returncode, 0)

    async def test_external_platform_added_by_files_and_configuration_error_precedes_launch(self):
        import shutil
        other = self.root / 'second'
        shutil.copytree(self.platform_dir, other)
        path = other / 'platform.json'
        data = json.loads(path.read_text())
        data['id'] = 'second'
        path.write_text(json.dumps(data))
        catalog = AssemblyCatalog(self.root)
        self.assertEqual(len(catalog.catalog()), 2)
        platform, profile = catalog.select('second', 'desk')
        async with open_platform(platform, profile, memory=self.memory, workdir=self.workdir, options={}) as (_, skills):
            self.assertIn('desk', {s['id'] for s in skills.catalog()})
        with self.assertRaisesRegex(ValueError, 'override'):
            async with open_platform(platform, profile, memory=self.memory, workdir=self.root/'invalid', options={'mode': 'coffee'}):
                self.fail('profile override accepted')
        self.assertFalse((self.root/'invalid').exists())

    async def test_tool_name_schema_and_presence_are_controlled_by_skill_files(self):
        # Select the tool directory alone, so removal is not blocked by an unrelated knowledge dependency.
        profile_path = self.platform_dir / 'profiles/coffee.json'
        profile = json.loads(profile_path.read_text())
        profile['skills'] = ['skills/tools']
        profile_path.write_text(json.dumps(profile))
        self.catalog = AssemblyCatalog(self.root)
        path = self.platform_dir / 'skills/tools/state/skill.md'
        text = path.read_text()
        metadata, body = text[4:].split('\n---\n', 1)
        declaration = yaml.safe_load(metadata)
        declaration['id'] = 'custom.observe'
        declaration['tool']['input_schema'] = {
            'type': 'object', 'properties': {'value': {'type': 'integer'}},
            'required': ['value'], 'additionalProperties': False,
        }
        path.write_text('---\n'+yaml.safe_dump(declaration)+'---\n'+body)
        async with self.open() as (binding, skills):
            self.assertEqual({s.name for s in binding.registry.catalog()}, {s['id'] for s in skills.catalog()})
            router = ToolRouter(binding.registry, binding.resources, Hooks(self.memory, EventBus()))
            old = await self.invoke(router, 'state.fixture')
            self.assertEqual(old.error.kind, Failure.INTERFACE)
            invalid = await self.invoke(router, 'custom.observe')
            self.assertEqual(invalid.error.kind, Failure.INTERFACE)
            actual = await self.invoke(router, 'custom.observe', {'value': 3})
            self.assertEqual(actual.status, 'completed')
            self.assertEqual(actual.result.payload['mode'], 'coffee')
            await router.close()

        path.unlink()
        async with self.open() as (binding, skills):
            self.assertNotIn('custom.observe', {s.name for s in binding.registry.catalog()})
            self.assertEqual(len(skills.catalog()), 2)

        extra = self.platform_dir / 'skills/tools/extra/skill.md'
        extra.parent.mkdir()
        declaration['id'] = 'state.added_from_file'
        extra.write_text('---\n'+yaml.safe_dump(declaration)+'---\n'+body)
        async with self.open() as (binding, _):
            router = ToolRouter(binding.registry, binding.resources, Hooks(self.memory, EventBus()))
            call = await self.invoke(router, 'state.added_from_file', {'value': 7})
            self.assertEqual(call.status, 'completed')
            await router.close()

    async def test_missing_implementation_entrypoint_closes_the_platform(self):
        path = self.platform_dir / 'skills/tools/state/skill.md'
        path.write_text(path.read_text().replace('robot_process:bind', 'robot_process:missing'))
        with self.assertRaises(ToolError):
            async with self.open():
                self.fail('missing implementation accepted')
        self.assertTrue((self.workdir / 'closed').exists())
