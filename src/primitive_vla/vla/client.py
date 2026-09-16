"""Async client for a task-owned, separately installed PI05 process."""

import asyncio
import json
import os
from pathlib import Path


class PolicyClient:
    def __init__(self, config, workspace, session, log):
        self.config, self.workspace, self.session, self.log = config, workspace, session, log
        self.process = self.reader = self.log_file = None
        self.pending = {}
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            if self.process:
                return
            directory = self.workspace / 'runs' / self.session
            directory.mkdir(parents=True, exist_ok=True)
            self.log_file = (directory / 'policy.log').open('w')
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get('PYTHONPATH', '')
            env['HF_HUB_OFFLINE'] = '1'
            if self.log:
                self.log.write('vla_loading', checkpoint=self.config.checkpoint, adapter=self.config.adapter)
            try:
                self.process = await asyncio.create_subprocess_exec(self.config.python_executable,
                    '-m', 'primitive_vla.vla.worker', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=self.log_file, env=env, limit=4 * 1024 * 1024)
                self.reader = asyncio.create_task(self.read())
                result = await asyncio.wait_for(self.request('init', config=self.config.model_dump()), self.config.startup_timeout_s)
                if self.log:
                    self.log.write('vla_ready', **result, checkpoint=self.config.checkpoint)
            except BaseException:
                await self.close()
                raise

    async def read(self):
        reason = 'PI05 worker exited; see policy.log'
        try:
            async for line in self.process.stdout:
                message = json.loads(line)
                future = self.pending.get(message['id'])
                if future is not None and not future.done():
                    if 'error' in message:
                        future.set_exception(RuntimeError(message['error']))
                    else:
                        future.set_result(message['result'])
        except Exception as error:
            reason = str(error)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(reason))

    async def request(self, op, **data):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.process.stdin.write((json.dumps({'id': request_id, 'op': op, **data}) + '\n').encode())
            await self.process.stdin.drain()
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def infer(self, observation, prompt):
        ref = observation.images[self.config.image_source]
        media = (self.workspace / 'media').resolve()
        image = (media / ref).resolve()
        if not image.is_relative_to(media):
            raise ValueError('Policy image must be in task media')
        return await asyncio.wait_for(self.request('infer', image=str(image),
            qpos=observation.proprioception['qpos'], prompt=prompt), self.config.inference_timeout_s)

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.log_file:
            self.log_file.close()
        self.process = self.reader = self.log_file = None
