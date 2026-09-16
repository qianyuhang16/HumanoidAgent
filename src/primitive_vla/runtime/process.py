"""Task-owned JSON-lines transport, including startup, events and bounded cleanup."""

import asyncio
import json
import os
from pathlib import Path


class JsonProcess:
    def __init__(self, executable, module, args, log_path, timeout=60, *,
                 startup_timeout=None, init_payload=None, env=None, on_event=None,
                 close_timeout=5):
        self.executable, self.module, self.args = executable, module, args
        self.log_path, self.timeout = Path(log_path), timeout
        self.startup_timeout = startup_timeout if startup_timeout is not None else (timeout or 60)
        self.init_payload, self.env, self.on_event = init_payload, env or {}, on_event
        self.close_timeout = close_timeout
        self.process = self.reader = self.log = None
        self.metadata = {}
        self.pending, self.sequence = {}, 0
        self.lock = asyncio.Lock()
        self.closed = False
        self.failure = None

    async def start(self):
        async with self.lock:
            self._check_open()
            if self.process is not None:
                return
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log = self.log_path.open('w')
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])+os.pathsep+env.get('PYTHONPATH', '')
            env.update(self.env)
            ready = asyncio.get_running_loop().create_future()
            self.pending[0] = ready
            try:
                self.process = await asyncio.create_subprocess_exec(self.executable, '-u', '-m', self.module,
                    *map(str, self.args), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=self.log, env=env, limit=4*1024*1024)
                self.reader = asyncio.create_task(self._read())
                async def handshake():
                    if self.init_payload is not None:
                        await self._send({'id': 0, 'op': 'init', **self.init_payload})
                    return await ready
                self.metadata = await asyncio.wait_for(handshake(), self.startup_timeout)
            except BaseException:
                ready.cancel()
                await self._shutdown()
                raise
            finally:
                self.pending.pop(0, None)

    def _check_open(self):
        if self.closed:
            raise RuntimeError('Worker transport is closed')
        if self.failure:
            raise RuntimeError(self.failure)

    def _fail_pending(self, reason):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError(reason))

    async def _read(self):
        reason = f'Worker exited; see {self.log_path}'
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if 'event' in message:
                    if self.on_event:
                        self.on_event(message)
                    continue
                future = self.pending.get(message.get('id'))
                if future is not None and not future.done():
                    if 'error' in message:
                        future.set_exception(RuntimeError(message['error']))
                    else:
                        future.set_result(message['result'])
        except Exception as error:
            reason = f'Worker protocol failed: {error}; see {self.log_path}'
        finally:
            self.failure = reason
            self._fail_pending(reason)

    async def _send(self, message):
        self.process.stdin.write((json.dumps(message)+'\n').encode())
        await self.process.stdin.drain()

    async def request(self, op, *, timeout=None, **data):
        await self.start()
        self._check_open()
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        async def exchange():
            await self._send({'id': request_id, 'op': op, **data})
            return await future
        try:
            return await asyncio.wait_for(exchange(), self.timeout if timeout is None else timeout)
        finally:
            self.pending.pop(request_id, None)
            # Cancelled/timed-out requests leave no waiter for a late reply.
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # A pipe failure can race with the reader failing this future.
                future.exception()

    async def _shutdown(self):
        cleanup = asyncio.create_task(self._reap())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _reap(self):
        self.closed = True
        self._fail_pending('Worker transport is closed')
        try:
            if self.process and self.process.returncode is None:
                async def graceful_close():
                    await self._send({'id': -1, 'op': 'close'})
                    await self.process.wait()
                try:
                    await asyncio.wait_for(graceful_close(), self.close_timeout)
                except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                    if self.process.returncode is None:
                        self.process.kill()
                    await self.process.wait()
        finally:
            if self.reader:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
            if self.log:
                self.log.close()

    async def close(self):
        async with self.lock:
            if not self.closed:
                await self._shutdown()
