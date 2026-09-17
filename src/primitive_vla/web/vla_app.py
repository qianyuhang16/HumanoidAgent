"""Direct VLA playground; preload once and accept human subtask instructions."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field

from primitive_vla.runtime.vla_lab import VLALab
from primitive_vla.runtime.service import TaskConflict

STATIC=Path(__file__).parent/'static'


class Command(BaseModel):
    prompt:str=Field(min_length=1,max_length=8000)
    max_actions:int=Field(default=450,ge=1,le=450)
    stop_on_end:bool=False


class Reset(BaseModel):
    seed:int=Field(default=300100,ge=0,le=2147483647)


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        lab=VLALab(Path.cwd(),Path('workspace/vla-lab'))
        app.state.lab=lab
        lab.job=asyncio.create_task(lab.load())
        yield
        await lab.close()
    app=FastAPI(title='VLA Prompt Lab',lifespan=lifespan)

    @app.exception_handler(TaskConflict)
    async def conflict(request,error):
        return JSONResponse({'detail':str(error)},status_code=409)

    @app.middleware('http')
    async def origin(request:Request,call_next):
        value=request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and value and urlsplit(value).netloc!=request.headers.get('host'):
            return JSONResponse({'detail':'Cross-origin mutation is not allowed'},status_code=403)
        return await call_next(request)

    @app.get('/')
    async def index():return FileResponse(STATIC/'vla.html')

    @app.get('/api/status')
    async def status():return app.state.lab.status()

    @app.post('/api/execute',status_code=202)
    async def execute(body:Command):
        try:return await app.state.lab.execute(body.prompt,body.max_actions,body.stop_on_end)
        except ValueError as error:raise HTTPException(422,str(error)) from None

    @app.post('/api/stop')
    async def stop():return await app.state.lab.stop()

    @app.post('/api/reset',status_code=202)
    async def reset(body:Reset):return await app.state.lab.reset(body.seed)

    @app.get('/api/frame')
    async def frame():
        path=app.state.lab.scene_directory/'media/latest.png'
        if not path.exists():raise HTTPException(404,'场景正在加载')
        return FileResponse(path,headers={'Cache-Control':'no-store'})

    @app.get('/api/commands')
    async def commands():return app.state.lab.commands

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app
