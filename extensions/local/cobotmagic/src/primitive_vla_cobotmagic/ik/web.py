"""Read-only web adapter for the shared PixelIK workflow; no motion endpoint."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from .pixel import PixelIK as IKWorkspace, PixelTarget as Selection, DEFAULT_CONFIG
# Preserve geometry imports used by existing callers.
from .pixel import ray_plane, compensated_target, rpy_from_matrix, project_path

def create_app(config_path, *, bridge=None):
    workspace = IKWorkspace(config_path, bridge)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await workspace.bridge.close()

    app = FastAPI(lifespan=lifespan)
    app.state.workspace = workspace

    @app.middleware("http")
    async def origin_check(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (Path(__file__).parent / "static/ik.html").read_text()

    @app.get("/api/settings")
    async def settings():
        return workspace.public_settings()

    @app.post("/api/capture")
    async def capture():
        async with workspace.lock:
            try:
                return await workspace.capture()
            except (ValueError, OSError, RuntimeError, KeyError, asyncio.TimeoutError) as error:
                raise HTTPException(409, f"取图失败：{error}") from error

    @app.post("/api/calculate")
    async def calculate(body: Selection):
        async with workspace.lock:
            try:
                return await asyncio.to_thread(workspace.calculate, body)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError, cv2.error) as error:
                raise HTTPException(409, str(error)) from error

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18085)
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(args.config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
