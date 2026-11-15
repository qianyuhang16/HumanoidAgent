"""RoboCasa command entry. This file belongs to the workspace, not the Agent core."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from primitive_vla.runtime.connections.server import PlatformRuntime, serve
from primitive_vla.memory import Memory
from runtime.environment import RoboCasa, RoboCasaConfig, ROBOT_TYPE


@asynccontextmanager
async def connect(params):
    options = dict(params['options'])
    new_episode = options.pop('new_episode', False)
    if type(new_episode) is not bool:
        raise ValueError('new_episode must be boolean')
    options['assets_path'] = Path(options['assets_path']).resolve()
    workdir = Path(params['workdir'])
    config = RoboCasaConfig(**options, workdir=workdir / 'native')
    unfinished = [r['call_id'] for r in params['unfinished_calls'] if r['payload']['robot_id'] == config.robot_id]
    if unfinished:
        raise RuntimeError(f'unresolved calls prevent creating a new simulation: {unfinished}')
    # Private episode ledger and artifacts; the Agent remains the sole writer of its Memory.
    memory = Memory(workdir / 'platform-state')
    try:
        environment = await RoboCasa.open(config, memory, new_episode=new_episode)
        try:
            yield PlatformRuntime(config.robot_id, ROBOT_TYPE, f'RoboCasa · {config.task}（仿真）',
                                  {'simulation'}, memory.read_resource, views=environment.views, context=environment)
        finally:
            await environment.close()
    finally:
        memory.close()


if __name__ == '__main__':
    asyncio.run(serve(connect))
