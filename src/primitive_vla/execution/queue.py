"""Session-owned action queues. Planning and physical control stay outside this module."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic

from primitive_vla.tools import ToolCall, ToolResult


@dataclass
class Job:
    call: ToolCall
    device_id: str
    resources: set[str]
    deadline: float
    status: str = "queued"
    runner: asyncio.Task | None = None
    result: ToolResult | None = None


class ActionQueue:
    def __init__(self, runtime, *, publish=None, log=None, observe=None):
        self.runtime, self.publish, self.log = runtime, publish, log
        self.observe = observe
        self.completed = []
        self.jobs = {}
        self.revision = 0
        self.changed = asyncio.Event()
        self.deadline = float("inf")
        self.closed = False
        self.error = None
        self._closing = None
        runtime.scheduler = self

    @property
    def pending(self):
        return any(job.result is None for job in self.jobs.values())

    def view(self, job):
        result = job.result
        return dict(task_id=job.call.id, device_id=job.device_id, tool=job.call.name,
                    status=job.status, result_ref=job.call.id if result is not None else None,
                    execution_phase=result.execution.phase if result and result.execution else None,
                    control=(result.execution.control if result.execution else
                             "released" if result.control_released else "unknown") if result else None)

    def snapshot(self, device_id=None, *, details=False):
        return deepcopy([{**self.view(job), **({"arguments": job.call.arguments} if details else {})}
                         for job in self.jobs.values() if device_id is None or job.device_id == device_id])

    def _change(self, job):
        self.revision += 1
        self.changed.set()
        if self.log:
            self.log.write("execution_task", **self.view(job), revision=self.revision)
        if self.observe:
            self.observe(self.snapshot(), self.revision)

    def submit(self, call):
        call = ToolCall.model_validate(call).model_copy(deep=True)
        if call.id in self.jobs:
            if self.jobs[call.id].call != call:
                raise ValueError("Task ID reused with different arguments")
            return self.view(self.jobs[call.id])
        reason = self.runtime.validate_call(call)
        if self.closed or self.error or self.runtime.control_locked:
            raise ValueError("Action queue is closed, failed or control is locked")
        if reason:
            raise ValueError(reason)
        definition = self.runtime.tools[call.name][1]
        if definition.read_only or not definition.device_id:
            raise ValueError("Async submission requires an action with adapter-declared device_id")
        if call.id in self.runtime.calls:
            raise ValueError("Task ID already belongs to a direct tool call")
        if monotonic() >= self.deadline:
            raise ValueError("Session deadline expired")
        job = Job(call, definition.device_id, self.runtime.resources(definition), self.deadline)
        # Persist admission before scheduling anything capable of physical dispatch.
        self.jobs[call.id] = job
        try:
            self._change(job)
        except Exception:
            del self.jobs[call.id]
            raise
        self.wake()
        return self.view(job)

    def admit(self, call, definition):
        job = self.jobs.get(call.id)
        if job is not None and job.call == call and job.status == "running":
            return None
        resources = self.runtime.resources(definition)
        if any(j.result is None and resources & j.resources for j in self.jobs.values()):
            return "Resources have queued actions; use execution.submit or wait for the queue"
        return None

    def wake(self):
        if self.closed or self.error:
            return
        # Reserve resources in submission order. Independent devices pass blocked
        # queues; overlapping jobs cannot overtake one another or hold partial leases.
        reserved = set(self.runtime.leases)
        for job in self.jobs.values():
            if job.result is not None:
                continue
            occupied = bool(reserved & job.resources)
            reserved.update(job.resources)
            if job.status != "queued" or occupied:
                continue
            job.status = "running"
            try:
                self._change(job)
            except Exception as error:
                self.error = error
                return
            job.runner = asyncio.create_task(self.runtime.invoke(job.call, job.deadline))
            job.runner.add_done_callback(lambda task, job=job: self._done(job, task))

    def _done(self, job, task):
        if task.cancelled():
            result = self.runtime.receipts.get(job.call.id, ToolResult(status="cancelled",
                text="Cancelled before dispatch", control_released=True))
        else:
            try:
                result = task.result()
            except Exception as error:
                self.error = error
                result = self.runtime.receipts.get(job.call.id, ToolResult(status="error", text=str(error)))
        self._finish(job, result)
        self.wake()

    def _finish(self, job, result):
        if job.result is not None:
            return
        job.result = result.model_copy(deep=True)
        job.status = "unknown" if not result.control_released else (
            "finished" if result.status == "ok" else result.status)
        try:
            if self.publish:
                self.publish(job.call, job.result)
            self.completed.append((job.call, job.result))
            self._change(job)
        except Exception as error:
            self.error = error
            self.changed.set()

    def get(self, task_id):
        if task_id not in self.jobs:
            raise ValueError(f"Unknown execution task: {task_id}")
        return self.jobs[task_id]

    async def wait(self, after, timeout_s):
        if after > self.revision:
            raise ValueError("Revision is ahead of the queue")
        if after == self.revision:
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout_s)
            except asyncio.TimeoutError:
                pass
        return dict(revision=self.revision, tasks=self.snapshot())

    async def cancel(self, task_id):
        job = self.get(task_id)
        if job.result is None:
            if job.runner is None:
                self._finish(job, ToolResult(status="cancelled", text="Cancelled before dispatch"))
                self.wake()
            else:
                if job.status != "cancelling":
                    job.status = "cancelling"
                    job.runner.cancel()
                    try:
                        self._change(job)
                    except Exception as error:
                        # Recording failure must not skip the stop handshake.
                        self.error = error
                await asyncio.shield(asyncio.gather(job.runner, return_exceptions=True))
        return self.view(job)

    async def close(self):
        if self._closing is None:
            self.closed = True
            self._closing = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._closing)
        except asyncio.CancelledError:
            await asyncio.shield(self._closing)
            raise

    async def _close(self):
        # Cancel all waiting work before stopping any running action, so releasing
        # a device during shutdown can never start its next queued action.
        for job in self.jobs.values():
            if job.result is None and job.runner is None:
                self._finish(job, ToolResult(status="cancelled", text="Session ended before dispatch"))
        await asyncio.gather(*(self.cancel(j.call.id) for j in self.jobs.values() if j.result is None))
