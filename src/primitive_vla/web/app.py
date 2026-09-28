"""Browser application, HTTP routes and static assets."""

import base64
import binascii
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from primitive_vla.config.loader import save_config
from primitive_vla.config.schema import AppConfig, VLMConfig
from primitive_vla.runtime.service import TaskConflict, TaskService
from primitive_vla.providers.factory import create_client
from .schemas import TaskRequest

STATIC = Path(__file__).parent / "static"


def create_app(workspace: Path = Path("workspace"), config_path: Path | None = None,
               *, index_path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = TaskService(workspace, config_path)
        yield
        await app.state.service.close()

    app = FastAPI(title="Primitive Tool Agent", version="0.3.0", lifespan=lifespan)

    @app.exception_handler(TaskConflict)
    async def task_conflict(request: Request, error: TaskConflict):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin mutation is not allowed"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    async def index():
        return FileResponse(index_path or STATIC / "tools.html")

    @app.get("/api/config")
    async def get_config():
        service = app.state.service
        return {"config": service.config.model_dump(mode="json"),
                "key_configured": bool(service.api_key or os.environ.get(str(service.config.model.options.get("api_key_env", "VLM_API_KEY")))),
                "control_locked": service.store.control_locked}

    @app.put("/api/config")
    async def put_config(config: AppConfig):
        service = app.state.service
        save_config(config, service.config_path)
        service.config = config
        return {"saved": True, "applies_to": "next task"}

    @app.get("/api/tools")
    async def tools():
        service = app.state.service
        return {"tools": [t.model_dump() for t in service.runtime.tools.definitions()] if service.runtime else [],
                "providers": [p.adapter for p in service.config.tool_providers]}

    @app.get("/api/config/schema")
    async def config_schema():
        return AppConfig.model_json_schema()

    @app.post("/api/vlm/key")
    async def set_key(request: Request):
        body = await request.json()
        key = body.get("api_key", "")
        if not isinstance(key, str):
            raise HTTPException(422, "api_key 必须是字符串")
        app.state.service.api_key = key or None
        return {"configured": bool(key)}

    @app.post("/api/vlm/test")
    async def test_vlm():
        service = app.state.service
        if service.config.model.adapter != "primitive_vla.agent.model:JSONModel":
            raise HTTPException(422, "连接测试仅适用于内置 JSONModel；自定义模型使用其提供的检查方式")
        options = VLMConfig.model_validate(service.config.model.options)
        client = None
        try:
            client = create_client(options, service.workspace / "media", api_key=service.api_key)
            result = await client.complete("connection_test", 'Reply with {"ok":true}.', {"request": "connection test"},
                                           {})
            if result.get("ok") is not True:
                raise ValueError("服务连接成功，但没有返回约定的 JSON")
            return {"ok": True, "model": options.model}
        except (RuntimeError, ValueError, OSError) as error:
            raise HTTPException(502, str(error)) from None
        finally:
            if client is not None:
                await client.close()

    @app.get("/api/tasks")
    async def list_tasks():
        service = app.state.service
        return {"tasks": service.store.list(), "active_id": service.active_id if service.busy else None,
                "control_locked": service.store.control_locked}

    @app.post("/api/tasks", status_code=202)
    async def start_task(request: TaskRequest):
        try:
            return await app.state.service.start(request.instruction, request.task)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        try:
            return app.state.service.get_task(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.get("/api/tasks/{task_id}/events")
    async def get_events(task_id: str, after: int = 0):
        return {"events": app.state.service.store.events(task_id, after)}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        return await app.state.service.cancel(task_id)

    @app.post("/api/images", status_code=201)
    async def upload_image(request: Request):
        service = app.state.service
        body = await request.json()
        try:
            header, encoded = body["data_url"].split(",", 1)
            formats = {"data:image/png;base64": "png", "data:image/jpeg;base64": "jpg", "data:image/webp;base64": "webp"}
            extension = formats[header]
            if len(encoded) > 8_388_608 * 4 / 3 + 4:
                raise ValueError("图像超过大小限制")
            content = base64.b64decode(encoded, validate=True)
            valid = ((extension == "png" and content.startswith(b"\x89PNG\r\n\x1a\n")) or
                     (extension == "jpg" and content.startswith(b"\xff\xd8\xff")) or
                     (extension == "webp" and content.startswith(b"RIFF") and content[8:12] == b"WEBP"))
            if not valid:
                raise ValueError("文件内容不是所声明的图像格式")
        except (KeyError, ValueError, TypeError, binascii.Error):
            raise HTTPException(422, "需要有效的 PNG/JPEG/WebP data URL，且不超过配置大小限制") from None
        name = uuid4().hex + "." + extension
        (service.workspace / "media" / name).write_bytes(content)
        return {"reference": name, "url": "/api/media/" + name}

    @app.get("/api/media/{name}")
    async def media(name: str):
        root = app.state.service.workspace / "media"
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix not in (".png", ".jpg", ".webp", ".gif", ".mp4"):
            raise HTTPException(404)
        return FileResponse(path)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
