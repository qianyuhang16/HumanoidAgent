"""Host-side process connection. Transport failures never confirm physical stop."""
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path

from primitive_vla.contracts import Domain, Failure, ResourceRef, ToolError, new_id
from primitive_vla.skills import SkillStore
from primitive_vla.tools.contracts import ToolResult, ToolSpec, Verification
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.tools.state.views import RobotViewSource, ViewFrame, ViewSpec


@dataclass
class PlatformBinding:
    robot_id: str
    robot_type: str
    label: str
    registry: ToolRegistry
    resources: ResourceManager
    views: RobotViewSource | None
    platform: dict
    profile_id: str


class ProcessConnection:
    def __init__(self, process, memory, workdir):
        self.process, self.memory = process, memory
        self.transfer = workdir / 'transfer'
        self.pending, self.progress, self.subscriptions = {}, {}, {}
        self.view_specs = ()
        self.reader_task = asyncio.create_task(self.read())

    def import_refs(self, values):
        refs = []
        for value in values:
            ref = ResourceRef(**value)
            digest = ref.uri.removeprefix('blob:')
            if not ref.uri.startswith('blob:') or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('platform resources must reference transferred blobs')
            data = (self.transfer / digest).read_bytes()
            stored = self.memory.put_resource(data, ref.media_type, ref.description, resource_id=ref.id)
            if stored.uri != ref.uri:
                raise ValueError('platform resource digest mismatch')
            self.memory.register_resource(ref)
            refs.append(ref)
        return tuple(refs)

    async def request(self, method, params, *, progress=None):
        if self.reader_task.done():
            raise ConnectionError('platform process is disconnected')
        request_id = new_id()
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        if progress:
            self.progress[request_id] = progress
        try:
            self.process.stdin.write((json.dumps({'id': request_id, 'method': method, 'params': params},
                                                ensure_ascii=False, allow_nan=False)+'\n').encode())
            await self.process.stdin.drain()
            return await future
        finally:
            self.pending.pop(request_id, None)
            self.progress.pop(request_id, None)

    async def read(self):
        error = ConnectionError('platform process disconnected; physical stop is unconfirmed')
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                request_id = message['id']
                event = message.get('event')
                if event == 'progress':
                    callback = self.progress.get(request_id)
                    if callback:
                        await callback(message['payload'], self.import_refs(message['resource_refs']))
                elif event in {'view', 'view_error'}:
                    queue = self.subscriptions.get(request_id)
                    if queue is not None:
                        if event == 'view':
                            frame = message['frame']
                            item = ViewFrame(**{**frame, 'data': base64.b64decode(frame['data'], validate=True)})
                        else:
                            item = RuntimeError(message['message'])
                        if queue.full():
                            queue.get_nowait()
                        queue.put_nowait(item)
                else:
                    future = self.pending.get(request_id)
                    if future is not None and not future.done():
                        if 'error' in message:
                            detail = message['error']
                            future.set_exception(ToolError(Failure(detail['kind']), detail['message'], detail['evidence']))
                        else:
                            future.set_result(message['result'])
        except Exception as failure:
            error = failure
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            for queue in self.subscriptions.values():
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(error)

    def list_views(self):
        return self.view_specs

    async def subscribe_view(self, view_id):
        subscription = new_id()
        queue = asyncio.Queue(maxsize=1)
        self.subscriptions[subscription] = queue
        opening = asyncio.create_task(self.request('view_open', {'view_id': view_id, 'subscription': subscription}))
        try:
            await asyncio.shield(opening)
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.subscriptions.pop(subscription)
            await opening
            if not self.reader_task.done():
                await self.request('view_close', {'subscription': subscription})

    async def close(self):
        if self.reader_task.done():
            self.process.stdin.close()
            await asyncio.wait_for(self.process.wait(), 5)
            return
        await self.request('shutdown', {})
        self.process.stdin.close()
        await self.process.wait()
        await self.reader_task


class RemoteTool:
    def __init__(self, connection, name):
        self.connection, self.name = connection, name

    async def execute(self, arguments, context):
        data = await self.connection.request('execute', {'name': self.name, 'arguments': arguments,
            'context': {name: getattr(context, name) for name in ('call_id', 'session_id', 'task_id', 'robot_id')}},
            progress=context.progress)
        refs = self.connection.import_refs(data['resource_refs'])
        verification = data['verification']
        if verification is not None:
            verification = Verification(**{**verification,
                'evidence': self.connection.import_refs(verification['evidence'])})
        return ToolResult(data['payload'], refs, verification)

    async def cancel(self, context):
        return await self.connection.request('cancel', {'call_id': context.call_id})


@asynccontextmanager
async def open_platform(connection, profile_id, *, memory, workdir, options):
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    cwd = Path(connection['cwd']).resolve()
    command = connection['command']
    if '/' in command:
        command = os.path.abspath(cwd / command)  # Preserve virtualenv interpreter symlinks.
    environment = {**os.environ, **connection.get('env', {})}
    with (workdir / 'platform.log').open('ab', buffering=0) as log:
        process = await asyncio.create_subprocess_exec(command, *connection.get('args', []), cwd=cwd,
            env=environment, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
            limit=16 * 1024 * 1024)
        client = ProcessConnection(process, memory, workdir)
        opening = asyncio.create_task(client.request('initialize', {'options': options, 'workdir': str(workdir),
                                     'unfinished_calls': [r.to_dict() for r in memory.unfinished_calls()],
                                     'profile_id': profile_id}))
        try:
            description = await asyncio.shield(opening)
            if description['protocol'] != 2:
                raise ValueError('unsupported platform protocol')
            robot = description['robot']
            registry, resources = ToolRegistry(), ResourceManager()
            resources.define_robot(robot['id'], set(description['resources']),
                                   {key: set(value) for key, value in description['aliases'].items()})
            for spec in description['tools']:
                spec = ToolSpec(**{**spec, 'domain': Domain(spec['domain']),
                                  'required_resources': tuple(spec['required_resources']),
                                  'supported_robots': tuple(spec['supported_robots'])})
                registry.register(spec, RemoteTool(client, spec.name))
            client.view_specs = tuple(ViewSpec(**view) for view in description['views'])
            skills = SkillStore()
            for document in description['skills']:
                skills.add(document)
            yield PlatformBinding(robot['id'], robot['type'], robot['label'], registry, resources,
                                  client if client.view_specs else None,
                                  description['platform'], description['profile_id']), skills
        finally:
            # Initialization cancellation must drain creation before closing the platform.
            await asyncio.gather(opening, return_exceptions=True)
            await client.close()
