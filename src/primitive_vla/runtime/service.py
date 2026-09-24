"""Workspace ownership, task lifecycle and persisted task views for all frontends."""

import asyncio
import fcntl
from pathlib import Path
from time import time
from uuid import uuid4

from primitive_vla.config.loader import default_config, load_config, save_config
from primitive_vla.storage.events import EventLog
from primitive_vla.storage.tasks import TaskStore
from .session import Runtime


class TaskConflict(RuntimeError):
    """A request conflicts with task or controller lifecycle."""


class TaskService:
    def __init__(self, workspace: Path, config_path: Path | None = None):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.workspace_lock = (self.workspace / ".lock").open("a")
        try:
            fcntl.flock(self.workspace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.workspace_lock.close()
            raise RuntimeError("该工作区已有服务运行，请使用现有网页或另选 --workspace") from None
        (self.workspace / "media").mkdir(exist_ok=True)
        self.config_path = config_path or self.workspace / "config.yaml"
        try:
            self.config = load_config(self.config_path) if config_path or self.config_path.exists() else default_config()
            if not self.config_path.exists():
                save_config(self.config, self.config_path)
            self.store = TaskStore(self.workspace / "tasks.sqlite3")
        except BaseException:
            self.workspace_lock.close()
            raise
        self.api_key: str | None = None
        self.worker: asyncio.Task | None = None
        self.runtime: Runtime | None = None
        self.active_id: str | None = None

    @property
    def busy(self):
        return self.worker is not None and not self.worker.done()

    async def start(self, instruction: str, task_data: dict | None = None, *, config=None):
        if self.busy:
            raise TaskConflict("已有任务执行中，请等待完成或取消")
        if self.store.control_locked:
            raise TaskConflict("控制停止尚未确认；请先在机器人侧核对并处理工作区控制锁")
        if not instruction.strip() and task_data is None:
            raise ValueError("请输入任务指令或结构化任务")
        # A frontend may select a task-specific capability configuration without
        # changing defaults or racing another task's settings.
        cfg = (config if config is not None else self.config).model_copy(deep=True)
        task_id = uuid4().hex
        instruction = instruction or task_data.get("instruction", "Structured task")
        self.store.create(task_id, instruction, cfg.model_dump(mode="json"))
        log = EventLog(sink=lambda event: self.store.event(task_id, event))
        self.runtime = Runtime(cfg, self.workspace, log, self.api_key, config_dir=self.config_path.resolve().parent)
        self.active_id = task_id

        async def work():
            runtime = self.runtime
            try:
                report = await runtime.run(instruction, task_data)
            except asyncio.CancelledError:
                report = {"status": "cancelled", "reason": "用户取消任务；工具运行时已处理停止请求"}
            except Exception as error:
                report = {"status": "failed", "reason": str(error) or type(error).__name__}
                log.write("error", reason=report["reason"])
            finally:
                if runtime.control_locked:
                    self.store.lock_control()
                cleanup = asyncio.gather(runtime.close(), return_exceptions=True)
                try:
                    cleanup_results = await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cleanup_results = await cleanup  # A late cancel must not skip the terminal record.
                error = cleanup_results[0]
                if isinstance(error, BaseException):
                    log.write("cleanup_error", reason=str(error))
                    report = {"status": "failed", "reason": f"Cleanup failed: {error}"}
                if runtime.control_locked:
                    self.store.lock_control()
            report["control_locked"] = self.store.control_locked
            if report["control_locked"]:
                report["status"] = "blocked"
                report["reason"] = "停止尚未确认，控制保持锁定；需要在机器人侧核对实际状态"
            results = list(runtime.session.results)
            recorded = {r["call_id"] for r in results}
            for event in log.events:
                if event["event"] == "tool_finished" and event["call"]["id"] not in recorded:
                    results.append({"call_id": event["call"]["id"], "tool": event["call"]["name"],
                                    "arguments": event["call"]["arguments"], **event["result"]})
            report.setdefault("results", results)
            report.setdefault("steps", len(report["results"]))
            self.store.finish(task_id, report, runtime.snapshot())

        self.worker = asyncio.create_task(work())
        await asyncio.sleep(0)  # Enter cleanup scope before accepting cancellation.
        return {"id": task_id, "status": "running"}

    async def cancel(self, task_id):
        if task_id != self.active_id or not self.busy:
            raise TaskConflict("该任务已结束")
        if self.store.get(task_id)["status"] != "cancelling":
            self.store.cancelling(task_id)
            self.runtime.log.write("cancel_requested", time_requested=time())
            self.worker.cancel()
        return {"status": "cancelling"}

    async def close(self):
        if self.busy:
            if self.store.get(self.active_id)["status"] != "cancelling":
                self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        self.store.close()
        self.workspace_lock.close()

    def get_task(self, task_id: str) -> dict:
        task = self.store.get(task_id)
        if task_id == self.active_id and self.runtime:
            task["state"] = self.runtime.snapshot()
        return task
