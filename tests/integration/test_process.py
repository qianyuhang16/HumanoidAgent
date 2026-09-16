"""Shared transport regression tests against real, lightweight Python workers."""

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from primitive_vla.runtime.process import JsonProcess


class JsonProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def worker(self, mode='ready', **options):
        options.setdefault('close_timeout', .1)
        worker = JsonProcess(sys.executable, 'tests.fixtures.json_worker', [mode],
                             Path(self.directory.name) / (mode+'.log'), timeout=2, **options)
        self.addAsyncCleanup(worker.close)
        return worker

    async def test_both_handshakes_concurrent_replies_and_events(self):
        for mode in ('ready', 'init'):
            with self.subTest(mode=mode):
                events = []
                worker = self.worker(mode, init_payload={'config': {'test': True}} if mode == 'init' else None,
                                     on_event=events.append)
                replies = await asyncio.gather(worker.request('echo', value='slow', delay=.05),
                                               worker.request('echo', value='fast'))
                self.assertEqual(replies, ['slow', 'fast'])
                self.assertTrue(worker.metadata['ready'])
                if mode == 'init':
                    self.assertEqual(worker.metadata['config'], {'test': True})
                self.assertEqual(await worker.request('event'), 'done')
                self.assertEqual(events, [{'event': 'feedback', 'phase': 'running'}])
                with self.assertRaisesRegex(RuntimeError, 'requested failure'):
                    await worker.request('error')
                self.assertEqual(await worker.request('echo', value='still ready'), 'still ready')

    async def test_timeout_and_cancellation_ignore_late_replies(self):
        worker = self.worker()
        await worker.start()
        with self.assertRaises(asyncio.TimeoutError):
            await worker.request('echo', value='expired', delay=.08, timeout=.01)
        pending = asyncio.create_task(worker.request('echo', value='cancelled', delay=.08))
        await asyncio.sleep(.01)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(await worker.request('echo', value='current', delay=.12), 'current')
        self.assertEqual(worker.pending, {})

    async def test_protocol_failure_and_exit_fail_all_waiters_and_future_requests(self):
        for operation, reason in [('malformed', 'protocol failed'), ('exit', 'Worker exited')]:
            with self.subTest(operation=operation):
                worker = self.worker()
                await worker.start()
                results = await asyncio.gather(worker.request('echo', delay=.5),
                                               worker.request(operation), return_exceptions=True)
                for result in results:
                    self.assertIsInstance(result, RuntimeError)
                    self.assertIn(reason, str(result))
                with self.assertRaisesRegex(RuntimeError, reason):
                    await worker.request('echo')

    async def test_startup_timeout_and_cancel_reap_worker(self):
        worker = self.worker('init', startup_timeout=.1, init_payload={'hang': True})
        with self.assertRaises(asyncio.TimeoutError):
            await worker.start()
        self.assertIsNotNone(worker.process.returncode)
        self.assertTrue(worker.log.closed)
        cancelled = self.worker('init', init_payload={'hang': True})
        starting = asyncio.create_task(cancelled.start())
        while cancelled.process is None:
            await asyncio.sleep(.01)
        starting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertIsNotNone(cancelled.process.returncode)
        self.assertTrue(cancelled.log.closed)

    async def test_close_reaps_uncooperative_worker_and_fails_pending(self):
        worker = self.worker('ignore-close')
        await worker.start()
        pending = asyncio.create_task(worker.request('echo', delay=10))
        await asyncio.sleep(.01)
        await worker.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            await pending
        await worker.close()
        self.assertIsNotNone(worker.process.returncode)
        self.assertTrue(worker.reader.done())
        self.assertTrue(worker.log.closed)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            await worker.request('echo')

    async def test_cancel_during_close_still_reaps_worker(self):
        worker = self.worker('ignore-close')
        await worker.start()
        closing = asyncio.create_task(worker.close())
        while not worker.closed:
            await asyncio.sleep(.01)
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertIsNotNone(worker.process.returncode)
        self.assertTrue(worker.log.closed)
