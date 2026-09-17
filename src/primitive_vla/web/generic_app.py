"""Interactive entry point for the same generic Agent used in simulation evaluations."""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from primitive_vla.runtime.generic_service import GenericService, SCENES
from primitive_vla.runtime.service import TaskConflict

STATIC=Path(__file__).parent/'static'


class RunRequest(BaseModel):
    scene: Literal['cover','battery']
    instruction: str = Field(min_length=1,max_length=8000)
    seed: int = Field(default=300102,ge=0,le=2147483647)
    planner_prompt: str | None = Field(default=None,max_length=24000)


def create_app(root=None,workspace=None,runner=None):
    root=Path(root or os.environ.get('PRIMITIVE_PROJECT_ROOT',Path.cwd())).resolve()
    workspace=Path(workspace or root/'workspace/interactive')

    @asynccontextmanager
    async def lifespan(app):
        app.state.service=GenericService(root,workspace,**({'runner':runner} if runner else {}))
        yield
        await app.state.service.close()

    app=FastAPI(title='Robot Agent',lifespan=lifespan)

    @app.exception_handler(TaskConflict)
    async def conflict(request,error):
        return JSONResponse({'detail':str(error)},status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request,error):
        return JSONResponse({'detail':'任务不存在'},status_code=404)

    @app.middleware('http')
    async def same_origin(request:Request,call_next):
        origin=request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin and urlsplit(origin).netloc!=request.headers.get('host'):
            return JSONResponse({'detail':'Cross-origin mutation is not allowed'},status_code=403)
        return await call_next(request)

    @app.get('/')
    async def index():
        return FileResponse(STATIC/'generic.html')

    @app.get('/api/scenes')
    async def scenes():
        service=app.state.service
        return {'scenes':service.scenes(),'planner_prompt':Path(service.config('cover')['planner_prompt']).read_text()}

    @app.get('/api/scenes/{scene}/preview')
    async def preview(scene:str):
        if scene not in SCENES:
            raise HTTPException(404)
        path=root/'workspace/mem0'/SCENES[scene][2]
        if not path.exists():
            raise HTTPException(404,'暂无场景预览，请启动任务')
        return FileResponse(path)

    @app.get('/api/runs')
    async def runs():
        s=app.state.service
        return {'runs':s.list(),'active_id':s.active_id if s.busy else None}

    @app.post('/api/runs',status_code=202)
    async def start(body:RunRequest):
        try:
            return await app.state.service.start(**body.model_dump())
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @app.get('/api/runs/{run_id}')
    async def run(run_id:str):
        return app.state.service.get(run_id)

    @app.post('/api/runs/{run_id}/cancel')
    async def cancel(run_id:str):
        return await app.state.service.cancel(run_id)

    @app.get('/api/runs/{run_id}/events')
    async def events(run_id:str,after:int=0):
        path=app.state.service.directory(run_id)/'episode/events.jsonl'
        rows=path.read_text().splitlines() if path.exists() else []
        result=[]
        for line in rows[max(0,after):]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                break  # A concurrently written final line will be retried next poll.
        return {'events':result,'cursor':max(0,after)+len(result)}

    @app.get('/api/runs/{run_id}/files/{name:path}')
    async def artifact(run_id:str,name:str):
        directory=app.state.service.directory(run_id)/'episode'
        path=(directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or path.suffix not in ('.png','.jpg','.mp4','.json','.jsonl'):
            raise HTTPException(404)
        return FileResponse(path)

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app
