"""A subprocess fixture, never installed or exposed as a production platform."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import time

from primitive_vla.runtime.connections.server import PlatformRuntime, serve
from primitive_vla.contracts import Domain, Failure, ToolError
from primitive_vla.memory import Memory
from primitive_vla.tools.assembly import PlatformPackage
from primitive_vla.tools.contracts import ToolResult, Verification
from primitive_vla.tools.state.views import ViewFrame, ViewSpec


@asynccontextmanager
async def factory(params):
    package = PlatformPackage(Path.cwd())
    profile, skills, options = package.assemble(params['profile_id'], params['options'])
    os.environ.update(package.environment.get('variables', {}))
    memory = Memory(Path(params['workdir']) / 'fixture')
    active = set()
    ended = set()
    Path(params['workdir'], 'opened').write_text(str(os.getpid()))

    class Handler:
        def __init__(self, domain):
            self.domain = domain

        async def execute(self, arguments, context):
            if self.domain == Domain.EXECUTION:
                active.add(context.call_id)
                await context.progress({'started': True})
                if arguments.get('crash'):
                    os._exit(9)
                await asyncio.sleep(0.15)
                active.remove(context.call_id)
                ended.add(context.call_id)
            if arguments.get('error'):
                raise ToolError(Failure.PRECONDITION, 'need a new observation', {'cause': 'hidden'})
            resource = memory.put_resource(b'raw image bytes', 'image/png')
            verification = Verification('fixture', 'failure', 'effect absent', (resource,)) if self.domain == Domain.VERIFICATION else None
            return ToolResult({'mode': options['mode'], 'image': resource.id, 'cwd': str(Path.cwd()),
                               'env': os.environ['FIXTURE_OPTION']}, (resource,), verification)

        async def cancel(self, context):
            return context.call_id in ended

    class Views:
        def list_views(self):
            return (ViewSpec('front', 'Front', True),)

        async def subscribe_view(self, view_id):
            try:
                index = 0
                while True:
                    yield ViewFrame(str(index), time.time(), 'image/jpeg', b'jpeg', 1, 1)
                    index += 1
                    await asyncio.sleep(0.01)
            finally:
                Path(params['workdir'], 'unsubscribed').touch()

    try:
        yield PlatformRuntime('fixture-1', 'fixture', 'Fixture', {'base'}, memory.read_resource,
                              skills, package.catalog(), profile.id, views=Views(), context={'handler': Handler})
    finally:
        if active:
            raise RuntimeError('physical stop unconfirmed')
        Path(params['workdir'], 'closed').touch()
        memory.close()


def bind(context, spec, options):
    return context['handler'](spec.domain)


if __name__ == '__main__':
    asyncio.run(serve(factory))
