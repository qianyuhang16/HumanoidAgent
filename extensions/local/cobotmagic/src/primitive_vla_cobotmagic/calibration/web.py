"""Keyboard capture UI. The only robot client is the read-only CameraBridge."""

import asyncio
from contextlib import asynccontextmanager, suppress
import fcntl
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split: Literal["train", "validation"]


def create_app(collector):
    @asynccontextmanager
    async def lifespan(app):
        lock = (collector.dataset.folder / ".capture.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("Another capture server already uses this session")
        task = asyncio.create_task(collector.poll())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await collector.bridge.close()
            lock.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def origin_check(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin capture rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (Path(__file__).parent / "static/calibration.html").read_text()

    @app.get("/api/state")
    async def state():
        return collector.state()

    @app.post("/api/record")
    async def record(body: Record):
        try:
            return {"id": collector.record(body.split), "split": body.split}
        except (ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    return app


class WorkspaceAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str


class Selection(WorkspaceAction):
    step: Literal['intrinsics', 'left', 'right', 'tcp_left', 'tcp_right']
    run: str | None = None
    new: bool = False


class WorkspaceRecord(WorkspaceAction):
    split: Literal['train', 'validation']


def create_workspace_app(workspace):
    import cv2

    @asynccontextmanager
    async def lifespan(app):
        try:
            workspace.acquire()
            workspace.import_legacy()
            workspace.running = True
            yield
        finally:
            workspace.running = False
            await workspace.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware('http')
    async def origin_check(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Cross-origin capture rejected'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/', response_class=HTMLResponse)
    async def index():
        return (Path(__file__).parent / 'static/calibration_workspace.html').read_text()

    @app.get('/api/state')
    async def state():
        async with workspace.lock:
            return workspace.state()

    async def mutate(body, operation):
        async with workspace.lock:
            try:
                workspace.check_revision(body.revision)
                return await operation()
            except (ValueError, KeyError, OSError, cv2.error) as error:
                raise HTTPException(409, str(error)) from error

    @app.post('/api/select')
    async def select(body: Selection):
        async def operation():
            await workspace.select(body.step, body.run, body.new)
            return workspace.state()
        return await mutate(body, operation)

    @app.post('/api/record')
    async def record(body: WorkspaceRecord):
        async def operation():
            if not workspace.collector:
                raise ValueError('请先选择标定批次')
            return {'id': workspace.collector.record(body.split)}
        return await mutate(body, operation)

    @app.post('/api/solve')
    async def solve(body: WorkspaceAction):
        return await mutate(body, workspace.solve)

    return app
