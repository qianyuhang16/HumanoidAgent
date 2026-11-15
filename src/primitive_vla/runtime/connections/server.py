"""Platform-side stdio host. SDK-specific code supplies an async context manager."""
import asyncio
import base64
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Callable

from primitive_vla.contracts import Domain, Failure, ToolError
from primitive_vla.skills import SkillStore
from primitive_vla.tools.contracts import CallContext
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.tools.registry.loading import register_tools
from primitive_vla.tools.state.views import RobotViewSource


@dataclass
class PlatformRuntime:
    robot_id: str
    robot_type: str
    label: str
    resources: set[str]
    read_resource: Callable[[str], bytes]
    skills: SkillStore
    platform: dict
    profile_id: str
    aliases: dict[str, set[str]] = field(default_factory=dict)
    views: RobotViewSource | None = None
    context: object = None


class StdioServer:
    def __init__(self, factory, reader, writer):
        self.factory, self.reader, self.writer = factory, reader, writer
        self.stack = AsyncExitStack()
        self.runtime = None
        self.registry = ToolRegistry()
        self.calls = {}
        self.tasks = set()
        self.streams = {}
        self.stopping = False
        self.closing = None
        self.running_calls = set()
        self.unconfirmed = set()

    async def send(self, message):
        self.writer.write((json.dumps(message, ensure_ascii=False, allow_nan=False)+'\n').encode())
        await self.writer.drain()

    def export(self, refs):
        for ref in refs:
            data = self.runtime.read_resource(ref.id)
            digest = sha256(data).hexdigest()
            (self.transfer / digest).write_bytes(data)
            yield {**asdict(ref), 'uri': f'blob:{digest}'}

    async def handle(self, message):
        request_id, method, params = message['id'], message['method'], message['params']
        try:
            if method == 'initialize':
                if self.runtime is not None:
                    raise ValueError('platform is already initialized')
                self.transfer = Path(params['workdir']) / 'transfer'
                self.transfer.mkdir(parents=True, exist_ok=True)
                self.runtime = await self.stack.enter_async_context(self.factory(params))
                runtime = self.runtime
                register_tools(self.registry, runtime.skills.tool_definitions(), runtime.context)
                result = {'protocol': 2, 'robot': {'id': runtime.robot_id, 'type': runtime.robot_type,
                          'label': runtime.label}, 'resources': sorted(runtime.resources),
                          'aliases': {key: sorted(value) for key, value in runtime.aliases.items()},
                          'tools': [spec.to_dict() for spec in self.registry.catalog()],
                          'skills': runtime.skills.documents(), 'platform': runtime.platform,
                          'profile_id': runtime.profile_id,
                          'views': [asdict(view) for view in runtime.views.list_views()] if runtime.views else []}
            elif method == 'execute':
                async def progress(payload, refs=()):
                    await self.send({'event': 'progress', 'id': request_id, 'payload': payload,
                                     'resource_refs': list(self.export(refs))})
                context = CallContext(**params['context'], cancel_requested=asyncio.Event(), progress=progress)
                entry = self.registry.get(params['name'])
                handler = entry.provider
                if entry.spec.domain == Domain.EXECUTION:
                    self.unconfirmed.add(context.call_id)
                self.calls[context.call_id] = (handler, context)
                outcome = await handler.execute(params['arguments'], context)
                self.unconfirmed.discard(context.call_id)
                result = asdict(outcome)
                result['resource_refs'] = list(self.export(outcome.resource_refs))
                if outcome.verification:
                    result['verification']['evidence'] = list(self.export(outcome.verification.evidence))
            elif method == 'cancel':
                call = self.calls.get(params['call_id'])
                if call is None:
                    result = False  # A missing/recovered handle cannot prove physical stop.
                else:
                    handler, context = call
                    context.cancel_requested.set()
                    result = await handler.cancel(context)
                    if result is True:
                        self.unconfirmed.discard(context.call_id)
            elif method == 'view_open':
                view_id, subscription = params['view_id'], params['subscription']
                if view_id not in {v.id for v in self.runtime.views.list_views()}:
                    raise ValueError('unknown view')
                self.streams[subscription] = asyncio.create_task(self.stream(view_id, subscription))
                result = True
            elif method == 'view_close':
                task = self.streams.pop(params['subscription'])
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                result = True
            elif method == 'shutdown':
                if self.running_calls or self.unconfirmed:
                    raise RuntimeError('platform calls have not drained or physical stop is unconfirmed')
                # A failed SDK shutdown must never become a successful second acknowledgement.
                if self.closing is None:
                    self.closing = asyncio.create_task(self.stack.aclose())
                await asyncio.shield(self.closing)
                for task in self.streams.values():
                    task.cancel()
                await asyncio.gather(*self.streams.values(), return_exceptions=True)
                self.streams.clear()
                result = True
                self.stopping = True
            else:
                raise ValueError(f'unknown protocol method: {method}')
            await self.send({'id': request_id, 'result': result})
        except Exception as error:
            # One protocol boundary: preserve native failure classifications and evidence.
            failure = error if isinstance(error, ToolError) else ToolError(Failure.EXECUTION, str(error))
            await self.send({'id': request_id, 'error': failure.to_dict()})
        finally:
            if method == 'execute':
                self.running_calls.discard(request_id)

    async def stream(self, view_id, subscription):
        source = self.runtime.views.subscribe_view(view_id)
        try:
            async for frame in source:
                await self.send({'event': 'view', 'id': subscription,
                                 'frame': {**asdict(frame), 'data': base64.b64encode(frame.data).decode('ascii')}})
        except Exception as error:
            await self.send({'event': 'view_error', 'id': subscription, 'message': str(error)})
        finally:
            await source.aclose()

    async def run(self):
        async with self.stack:
            while not self.stopping:
                line = await self.reader.readline()
                if not line:
                    break
                message = json.loads(line)
                # Initialize/shutdown serialize lifecycle; calls run concurrently so cancel works.
                if message['method'] in {'initialize', 'shutdown'}:
                    await self.handle(message)
                else:
                    if message['method'] == 'execute':
                        self.running_calls.add(message['id'])
                    task = asyncio.create_task(self.handle(message))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
            await asyncio.gather(*self.tasks)


async def serve(factory):
    """Reserve stdout for protocol frames; native/Python diagnostics go to stderr."""
    protocol_output = os.fdopen(os.dup(sys.stdout.fileno()), 'wb', buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, protocol_output)
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    try:
        await StdioServer(factory, reader, writer).run()
    finally:
        writer.close()
