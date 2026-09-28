"""Interactive camera questions using the existing camera and VLM interfaces."""

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from time import monotonic, time
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from primitive_vla.config.loader import load_config
from primitive_vla.config.schema import VLMConfig
from primitive_vla.providers.factory import create_client
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import CallContext, PluginContext
from .provider import CameraTools

STATIC = Path(__file__).parent / "static"
PROMPT = """You answer questions about a real robot's camera images.
Answer in the user's language. The attached images were captured for the CURRENT question.
Past conversation is textual context, not evidence of the current scene. Distinguish visible facts
from uncertainty and occlusion. RGB views have no metric depth/calibration and are not synchronized.
You can observe only; never claim to have moved the robot. Return JSON: {"answer": "your answer"}.
"""


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cameras: list[Literal["front", "left", "right"]] = Field(default_factory=lambda: ["front", "left", "right"], min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.cameras)) != len(self.cameras):
            raise ValueError("相机不能重复")
        return self


class Question(Selection):
    question: str = Field(min_length=1, max_length=8000)


class KeyInput(BaseModel):
    api_key: str = Field(max_length=4096)


class Chat:
    def __init__(self, config, config_dir, workspace, transport=None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.media = self.workspace / "media"
        self.log = EventLog(self.workspace / "events.jsonl")
        spec = next(s for s in config.tool_providers if s.adapter == "primitive_vla_cobotmagic.provider:CameraTools")
        self.camera = CameraTools(spec.options, PluginContext(self.workspace, config_dir, self.log))
        self.model_config = VLMConfig.model_validate(config.model.options)
        self.transport, self.api_key = transport, None
        self.capture_lock = asyncio.Lock()
        self.task = None
        self.path = self.workspace / "conversation.json"
        self.turns = json.loads(self.path.read_text()) if self.path.exists() else []
        for turn in self.turns:
            if turn["status"] in ("capturing", "answering"):
                turn.update(status="failed", error="服务已重启，请重新提问。")
        self.save()

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.turns, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def validate_cameras(self, cameras):
        if not set(cameras) <= set(self.camera.config.cameras):
            raise HTTPException(422, "所选相机未配置")

    async def capture(self, cameras):
        self.validate_cameras(cameras)
        async with self.capture_lock:
            context = CallContext(uuid4().hex, monotonic() + self.camera.config.request_timeout_s + 1)
            return await asyncio.wait_for(self.camera.observe({"cameras": cameras}, context),
                                          self.camera.config.request_timeout_s + 1)

    async def preview(self, cameras):
        result = await self.capture(cameras)
        try:
            return {"data": result.data, "images": {name: "data:image/jpeg;base64," +
                base64.b64encode((self.media / ref).read_bytes()).decode() for name, ref in result.images.items()}}
        finally:
            # Live previews are ephemeral; question evidence remains on disk.
            for ref in result.images.values():
                (self.media / ref).unlink(missing_ok=True)

    def ask(self, question, cameras):
        if self.busy:
            raise HTTPException(409, "正在生成回答，请等待或先停止。")
        if not question.strip():
            raise HTTPException(422, "请输入问题")
        self.validate_cameras(cameras)
        if len(cameras) > self.model_config.max_images:
            raise HTTPException(422, f"当前模型配置最多接收 {self.model_config.max_images} 路图像")
        turn = {"id": uuid4().hex, "question": question.strip(), "cameras": cameras,
                "created_at": time(), "status": "capturing", "answer": "", "error": "", "images": {}}
        self.turns.append(turn)
        self.save()
        self.task = asyncio.create_task(self.answer(turn))
        return turn

    async def answer(self, turn):
        client = None
        try:
            client = create_client(self.model_config, self.media, self.api_key, self.log, self.transport)
            observation = await self.capture(turn["cameras"])
            turn.update(images=observation.images, observation=observation.data, status="answering")
            self.save()
            history = [{"question": t["question"], "answer": t["answer"], "created_at": t["created_at"]}
                       for t in self.turns if t["status"] == "completed"][-12:]
            reply = await client.complete("camera_question", PROMPT,
                {"question": turn["question"], "previous_turns": history,
                 "current_observation": observation.data}, observation.images)
            if not isinstance(reply.get("answer"), str) or not reply["answer"].strip():
                raise ValueError("模型没有返回有效回答，请重新提问。")
            turn.update(answer=reply["answer"].strip(), status="completed")
        except asyncio.CancelledError:
            turn.update(status="cancelled", error="已停止生成回答。")
        except Exception as error:
            turn.update(status="failed", error=str(error) or type(error).__name__)
        finally:
            try:
                if client:
                    await client.close()
            finally:
                self.save()

    async def cancel(self):
        if self.busy:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        # Cancellation may arrive before the coroutine's first instruction.
        for turn in self.turns:
            if turn["status"] in ("capturing", "answering"):
                turn.update(status="cancelled", error="已停止生成回答。")
        self.save()

    async def close(self):
        await self.cancel()
        await self.camera.close()


def create_app(config_path, workspace, *, transport=None):
    config_path = Path(config_path).resolve()
    @asynccontextmanager
    async def lifespan(app):
        app.state.chat = Chat(load_config(config_path), config_path.parent, workspace, transport)
        try:
            yield
        finally:
            await app.state.chat.close()

    app = FastAPI(title="Cobot Magic · 视觉问答", lifespan=lifespan)

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin mutation is not allowed"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    async def state():
        chat = app.state.chat
        return {"model": chat.model_config.model, "cameras": chat.camera.config.cameras,
                "key_configured": bool(chat.api_key or os.environ.get(chat.model_config.api_key_env)),
                "busy": chat.busy, "turns": chat.turns}

    @app.post("/api/preview")
    async def preview(selection: Selection):
        try:
            return await app.state.chat.preview(selection.cameras)
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(502, str(error) or type(error).__name__) from None

    @app.post("/api/chat", status_code=202)
    async def ask(question: Question):
        return app.state.chat.ask(question.question, question.cameras)

    @app.post("/api/cancel")
    async def cancel():
        await app.state.chat.cancel()
        return {"ok": True}

    @app.post("/api/clear")
    async def clear():
        chat = app.state.chat
        if chat.busy:
            raise HTTPException(409, "请先停止当前回答。")
        chat.turns.clear()
        chat.save()
        return {"ok": True}

    @app.post("/api/key")
    async def key(body: KeyInput):
        app.state.chat.api_key = body.api_key.strip() or None
        return {"ok": True}

    @app.get("/api/media/{name}")
    async def media(name: str):
        root = app.state.chat.media.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix != ".jpg":
            raise HTTPException(404)
        return FileResponse(path)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path("workspace/cobotmagic-chat"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    config = load_config(args.config)
    import uvicorn
    uvicorn.run(create_app(args.config, args.workspace), host=args.host or config.web.host,
                port=args.port or config.web.port)


if __name__ == "__main__":
    main()
