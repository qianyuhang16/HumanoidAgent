"""Request/reply transport for task-owned Python workers."""

import asyncio
import json
import os
from pathlib import Path


class JsonProcess:
    def __init__(self, executable, module, args, log_path, timeout=60):
        self.executable, self.module, self.args = executable, module, args
        self.log_path, self.timeout = Path(log_path), timeout
        self.process = self.reader = self.log = None
        self.pending, self.sequence = {}, 0
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            if self.process:
                return
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log = self.log_path.open('w')
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])+os.pathsep+env.get('PYTHONPATH', '')
            env.setdefault('MUJOCO_GL', 'egl')
            env.setdefault('PYOPENGL_PLATFORM', 'egl')
            self.pending[0] = asyncio.get_running_loop().create_future()
            try:
                self.process = await asyncio.create_subprocess_exec(self.executable, '-u', '-m', self.module,
                    *map(str, self.args), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=self.log, env=env, limit=4*1024*1024)
                self.reader = asyncio.create_task(self.read())
                self.metadata = await asyncio.wait_for(self.pending[0], self.timeout)
            except BaseException:
                await self.close()
                raise
            finally:
                self.pending.pop(0, None)

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                future = self.pending.get(message.get('id'))
                if future is not None and not future.done():
                    if 'error' in message:
                        future.set_exception(RuntimeError(message['error']))
                    else:
                        future.set_result(message['result'])
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(f'Worker exited; see {self.log_path}'))

    async def request(self, op, **data):
        await self.start()
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.process.stdin.write((json.dumps({'id': request_id, 'op': op, **data})+'\n').encode())
            await self.process.stdin.drain()
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self.pending.pop(request_id, None)

    async def close(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.stdin.write(b'{"id":-1,"op":"close"}\n')
                await self.process.stdin.drain()
                await asyncio.wait_for(self.process.wait(), 5)
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                self.process.kill()
                await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.log:
            self.log.close()
