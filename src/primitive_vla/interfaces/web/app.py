from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import json
import hmac
import os
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StringConstraints

from primitive_vla.contracts import Domain, Record, ToolError
from primitive_vla.interfaces.service import TaskService
from primitive_vla.memory import Memory
from primitive_vla.memory.organizer import MemoryOrganizer
from primitive_vla.runtime.agent import Agent
from primitive_vla.runtime.session import Session

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]


@dataclass(frozen=True)
class RobotProfile:
    id: str
    type: str
    label: str


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: Text
    robot_id: str = ""
    start: bool = False


class ReplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: Text
    payload: dict[str, Any] = {}


class Console:
    """HTTP-facing projection of one Runtime; never supplies an agent decision policy."""

    def __init__(self, memory: Memory, agent: Agent | None, robots: tuple[RobotProfile, ...]):
        if len({robot.id for robot in robots}) != len(robots):
            raise ValueError("robot profile IDs must be unique")
        self.memory, self.agent, self.robots = memory, agent, robots
        self.service = TaskService(agent) if agent else None
        self.failures: dict[str, str] = {}
        self.maintenance: asyncio.Task | None = None
        self.organizer: MemoryOrganizer | None = None

    def session(self, session_id: str) -> Session:
        if self.agent:
            return self.agent.load_session(session_id)
        return Session.from_dict(self.memory.load_session(session_id))

    def require_runtime(self) -> TaskService:
        if self.service is None:
            raise HTTPException(409, "Planning 尚未连接。任务已保存，连接 Runtime 后可以继续。")
        return self.service

    def status(self) -> dict:
        maintenance = {"status": "idle", "mode": "model" if self.organizer else "index-and-ttl"}
        if self.maintenance is not None:
            maintenance["status"] = "running" if not self.maintenance.done() else "stopped"
            if self.maintenance.done() and not self.maintenance.cancelled():
                error = self.maintenance.exception()
                if error is not None:
                    maintenance.update(status="failed", error=str(error))
        if self.organizer:
            maintenance["last_result"] = self.organizer.last_result
        return {
            "name": "Primitive", "connected": self.agent is not None,
            "workspace": str(self.memory.directory.resolve()),
            "planner": type(self.agent.planner).__name__ if self.agent else None,
            "model": getattr(getattr(self.agent.planner, "vlm", None), "model", None) if self.agent else None,
            "robots": [asdict(robot) for robot in self.robots],
            "tools": len(self.agent.registry.catalog()) if self.agent else 0,
            "skills": len(self.agent.skills.catalog()) if self.agent else 0,
            "limits": asdict(self.agent.limits) if self.agent else None,
            "memory_maintenance": maintenance,
        }

    def watch(self, session_id: str, task: asyncio.Task) -> None:
        self.failures.pop(session_id, None)

        def finished(completed: asyncio.Task) -> None:
            if not completed.cancelled() and (error := completed.exception()) is not None:
                self.failures[session_id] = str(error)

        task.add_done_callback(finished)

    def create(self, body: TaskInput) -> Session:
        robot = next((robot for robot in self.robots if robot.id == body.robot_id), None)
        if body.start:
            service = self.require_runtime()
            if robot is None:
                raise HTTPException(422, "请选择已连接的机器人。")
            session = service.start(body.goal, robot.id, robot.type)
            self.watch(session.id, service.tasks[session.id])
            return session
        session = Session(body.goal, body.robot_id, robot.type if robot else "", status="draft")
        self.memory.save_session(session.to_dict())
        self.memory.write(Record(session.id, session.task_id, "planning", Domain.PLANNING,
                                 "task_created", "draft", {"goal": session.goal}))
        return session

    def snapshot(self, session_id: str) -> dict:
        session = self.session(session_id)
        records = self.memory.history(session_id, limit=60)
        active = [call.snapshot() for call in self.agent.router.active(session_id)] if self.agent else []
        occupied = self.agent.router.resources.occupied(session.robot_id) if self.agent else {}
        return {"session": session.to_dict(), "records": [r.to_dict() for r in reversed(records)],
                "active_calls": active, "occupied": occupied,
                "runner_error": self.failures.get(session_id), "has_more": len(records) == 60}

    async def control(self, session_id: str, action: str, robot_id: str) -> dict:
        service = self.require_runtime()
        session = self.session(session_id)
        if action == "resume":
            if session.status == "draft":
                robot = next((robot for robot in self.robots if robot.id == (robot_id or session.robot_id)), None)
                if robot is None:
                    raise HTTPException(422, "请选择已连接的机器人。")
                session.robot_id, session.robot_type, session.status = robot.id, robot.type, "ready"
                self.memory.save_session(session.to_dict())
            self.watch(session_id, service.resume(session_id))
        elif action == "pause":
            self.agent.pause(session_id)
            if session_id in service.tasks:
                await service.tasks[session_id]
        elif action == "cancel":
            await service.cancel(session_id)
        else:
            # Reconciliation requests physical stop, so expose it only for stopping sessions.
            if session.status != "stopping":
                raise HTTPException(409, "只有等待停止确认的任务可以重新确认。")
            await self.agent.reconcile(session_id)
        self.memory.write(Record(session.id, session.task_id, "user", Domain.PLANNING,
                                 "session_control", session.status, {"action": action}))
        return self.snapshot(session_id)


def create_app(agent: Agent | None = None, *, workspace: str | Path = "workspace/agent",
               robots: tuple[RobotProfile, ...] = (), organizer: MemoryOrganizer | None = None) -> FastAPI:
    """Attach an existing Agent, or open a real persisted workspace in draft/inspection mode."""
    memory = agent.memory if agent else Memory(workspace)
    console = Console(memory, agent, robots)
    console.organizer = organizer

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        console.maintenance = asyncio.create_task(organizer.run(stop) if organizer else memory.maintain(stop))
        try:
            yield
        finally:
            stop.set()
            console.maintenance.cancel()
            await asyncio.gather(console.maintenance, return_exceptions=True)
            if console.service:
                await console.service.close()
            else:
                memory.close()

    app = FastAPI(title="Primitive Agent Workbench", lifespan=lifespan)
    app.state.console = console
    desktop_token = os.environ.get("PRIMITIVE_DESKTOP_TOKEN")

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if desktop_token and request.url.path.startswith("/api/") and not hmac.compare_digest(
            request.headers.get("x-primitive-token", ""), desktop_token
        ):
            return JSONResponse({"detail": "桌面连接令牌无效。"}, status_code=401)
        # This local control surface must not accept commands from unrelated browser sites.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "跨站控制请求已拒绝。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
            "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "记录或资源不存在。"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ToolError)
    async def tool_error(request, error):
        return JSONResponse({"detail": str(error), "error": error.to_dict()}, status_code=409)

    @app.get("/api/status")
    async def status():
        return console.status()

    @app.get("/api/desktop/activity")
    async def activity():
        if not agent:
            return {"running": 0, "active_calls": 0}
        return {"running": sum(not task.done() for task in console.service.tasks.values()),
                "active_calls": sum(call.status not in {"completed", "failed", "cancelled"}
                                    for call in agent.router.calls.values())}

    @app.post("/api/desktop/prepare-close")
    async def prepare_close():
        if agent:
            service = console.service
            for session_id, task in service.tasks.items():
                if not task.done():
                    agent.pause(session_id)
            await asyncio.gather(*service.tasks.values(), return_exceptions=True)
            sessions = {call.context.session_id for call in agent.router.calls.values()
                        if call.status not in {"completed", "failed", "cancelled"}}
            for session_id in sessions:
                agent.load_session(session_id)
                await agent.cancel(session_id)
            unresolved = [call.id for call in agent.router.calls.values()
                          if call.status not in {"completed", "failed", "cancelled"}]
            if unresolved:
                raise HTTPException(409, "部分动作尚未确认停止。窗口将保持打开，请处理停止确认后再退出。")
        return {"ready": True}

    @app.get("/api/sessions")
    async def sessions(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        entries = memory.list_sessions(limit, offset)
        return {"sessions": [console.session(entry["id"]).to_dict() for entry in entries]}

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: TaskInput):
        return console.create(body).to_dict()

    @app.get("/api/sessions/{session_id}")
    async def snapshot(session_id: str):
        return console.snapshot(session_id)

    @app.get("/api/sessions/{session_id}/history")
    async def history(session_id: str, offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=100)):
        console.session(session_id)
        return {"records": [record.to_dict() for record in memory.history(session_id, limit, offset)]}

    @app.post("/api/sessions/{session_id}/{action}")
    async def control(session_id: str, action: Literal["resume", "pause", "cancel", "reconcile"],
                      robot_id: str = ""):
        return await console.control(session_id, action, robot_id)

    @app.post("/api/replies/{session_id}")
    async def reply(session_id: str, body: ReplyInput):
        service = console.require_runtime()
        console.session(session_id)
        console.watch(session_id, service.respond(session_id, body.answer, body.payload))
        return console.snapshot(session_id)

    @app.get("/api/sessions/{session_id}/stream")
    async def stream(session_id: str, request: Request):
        console.session(session_id)

        async def updates():
            previous = None
            ticks = 0
            while not await request.is_disconnected():
                state = json.dumps(console.snapshot(session_id), ensure_ascii=False)
                if state != previous:
                    yield f"event: snapshot\ndata: {state}\n\n"
                    previous = state
                elif ticks % 15 == 0:
                    yield ": heartbeat\n\n"
                ticks += 1
                await asyncio.sleep(1)

        return StreamingResponse(updates(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/memory")
    async def records(query: str = "", session_id: str = "", domain: str = "", lifetime: str = "",
                      offset: int = Query(0, ge=0), limit: int = Query(40, ge=1, le=100)):
        filters = {key: value for key, value in {"session_id": session_id, "domain": domain,
                                               "lifetime": lifetime}.items() if value}
        return {"records": [r.to_dict() for r in memory.search(query, filters, limit, offset)]}

    @app.get("/api/records/{record_id}")
    async def record(record_id: str):
        return memory.get(record_id).to_dict()

    @app.get("/api/evidence/{record_id}")
    async def evidence(record_id: str):
        return memory.evidence(record_id).to_dict()

    @app.get("/api/tools")
    async def tools():
        return {"tools": [spec.to_dict() for spec in agent.registry.catalog()] if agent else []}

    @app.get("/api/skills")
    async def skills():
        return {"skills": agent.skills.catalog() if agent else []}

    @app.get("/api/skills/{skill_id:path}")
    async def skill(skill_id: str, offset: int = Query(0, ge=0), limit: int = Query(8000, ge=1, le=16000)):
        console.require_runtime()
        return agent.skills.read(skill_id, offset, limit)

    @app.get("/api/resources/{resource_id}")
    async def resource(resource_id: str):
        return asdict(memory.get_resource(resource_id))

    @app.get("/api/resources/{resource_id}/content")
    async def content(resource_id: str):
        ref = memory.get_resource(resource_id)
        # Only indexed local blobs are served; never proxy arbitrary Provider URLs or paths.
        if not ref.uri.startswith("blob:"):
            raise HTTPException(409, "外部资源需要由对应 Provider 提供读取能力。")
        previewable = ref.media_type in {"image/png", "image/jpeg", "image/webp", "image/gif"}
        return Response(memory.read_resource(resource_id), media_type=ref.media_type,
                        headers={"Content-Disposition": f'{"inline" if previewable else "attachment"}; filename="{resource_id}"'})

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    return app
