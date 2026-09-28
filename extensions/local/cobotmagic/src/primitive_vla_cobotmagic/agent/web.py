"""Generic task UI; physical actions run through the Agent tool runtime."""
from contextlib import asynccontextmanager
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from primitive_vla.runtime.service import TaskService, TaskConflict
from .config import DEFAULT_CONFIG, agent_config, settings
from ..vla.client import close_workspace
from .recovery import recover_control

STATIC=Path(__file__).parent/'static'
RESET_PLAN={'camera.observe','perception.segment','robot.plan_hover','robot.execute','robot.reset_arm','vla.run'}
DETAIL_KEYS={'arm','found','score','box_xyxy','pixel_uv','pixel_method','observation_id',
             'source','unit','measured','metric_accuracy_verified','used_for_ik','percentiles_2_50_98',
             'solved','execution_ready','target_xyz_m','duration_s','target_downward_angle_deg',
             'compensation','blockers','reason','depth_prediction_used_for_ik','executed',
             'arrival_verified','nominal_duration_s','position_error_m','measured_tcp_xyz_m',
             'preparation_id','reset_completed','needs_reset','used_arms','actions_sent','elapsed_s',
             'stop_reason','task_success','policy_id','arm_context','target_gripper_m','gripper_error_m',
             'goal','verdict','evidence','reviewed_call_id','revision'}


class TaskRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    instruction: str=Field(min_length=1,max_length=4000)
    parent_id: str | None=Field(default=None,min_length=1,max_length=64)


class RecoveryRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed_stopped: bool=Field(strict=True)


def task_view(service, task_id):
    record=service.store.get(task_id)
    events=service.store.events(task_id)
    steps={}
    reviews={}
    images={}
    plan=None
    for event in events:
        kind=event['event']
        if kind=='outcome_review_started':
            key='review:'+str(event['seq'])
            reviews[event['call_id']]=key
            steps[key]={'id':key,'name':'outcome.assess','status':'running',
                        'arguments':{'call_id':event['call_id']},'data':{'source':event.get('source','independent')}}
            continue
        if kind=='outcome_review_finished':
            key=reviews.pop(event['call_id'],'review:'+str(event['seq']))
            data={k:event[k] for k in ('goal','verdict','reason','evidence','source','revision') if k in event}
            data.setdefault('source','independent')
            steps[key]={'id':key,'name':'outcome.assess','status':'ok',
                        'arguments':{'call_id':event['call_id']},'data':data,'text':event['reason']}
            continue
        if kind not in ('tool_started','tool_finished'):
            continue
        call=event['call']
        name=call['name']
        if kind=='tool_started':
            if name in RESET_PLAN:
                plan=None
            if name=='camera.observe':
                images={}
            elif name=='perception.segment':
                images.pop('segmentation',None)
            elif name=='perception.depth':
                images.pop('depth_prediction',None)
            steps[call['id']]={'id':call['id'],'name':name,'status':'running','arguments':call['arguments']}
        else:
            result=event['result']
            data=result.get('data',{})
            steps[call['id']]={'id':call['id'],'name':name,'status':result['status'],
                'arguments':call['arguments'],'text':result.get('text',''),
                'data':{k:v for k,v in data.items() if k in DETAIL_KEYS}}
            details=json.dumps({k:v for k,v in data.items() if k not in ('command','preview_command','path_pixels')},ensure_ascii=False,indent=2)
            steps[call['id']]['detail_json']=details[:12000]+('\n…结果已截短，完整记录保存在工作区。' if len(details)>12000 else '')
            images.update(result.get('images',{}))
            if result.get('before_images'):
                images={k:v for k,v in images.items() if not k.startswith(('before:','after:'))}
                images.update({'before:'+k:v for k,v in result['before_images'].items()})
                images.update({'after:'+k:v for k,v in result.get('images',{}).items()})
            if name=='robot.plan_hover':
                plan=data if result['status']=='ok' else None
    command=None
    if record['status']=='completed' and plan and plan.get('execution_ready'):
        command=plan.get('command')
    turns=[]
    for row in service.store.conversation(record['conversation_id']):
        report=json.loads(row['report'])
        turn={'id':row['id'],'instruction':row['instruction'],'status':row['status'],
              'reason':report.get('reason','')}
        if row['status']=='stopped':
            snapshot=(record if row['id']==task_id else service.store.get(row['id']))['state']
            turn['progress']=snapshot.get('working_memory','')
            history=snapshot.get('history',[])
            start=max((i for i,e in enumerate(history) if e['kind']=='user'),default=0)
            turn['model_errors']=report.get('model_errors',sum(
                e['kind']=='feedback' and e.get('text','').startswith('Invalid model response:')
                for e in history[start:]))
        turns.append(turn)
    return {'id':task_id,'conversation_id':record['conversation_id'],'turns':turns,
            'instruction':record['instruction'],'created':record['created'],
            'status':record['status'],'reason':record['report'].get('reason',''),
            'steps':list(steps.values()),'images':images,
            'plan':{k:v for k,v in (plan or {}).items() if k in DETAIL_KEYS},'command':command}


def create_app(workspace=Path('workspace/cobotmagic-agent/web'),config_path=DEFAULT_CONFIG):
    workspace,config_path=Path(workspace).resolve(),Path(config_path).resolve()
    recovery_lock=asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        values=settings(config_path)
        app.state.ik_config=values['ik_config']
        # TaskService owns the journal and cleanup; no cameras or models start here.
        service=TaskService(workspace,Path(values['model_source']))
        app.state.service=service
        try:
            yield
        finally:
            try:await service.close()
            finally:await close_workspace(workspace)

    app=FastAPI(title='Cobot Magic Agent',lifespan=lifespan)

    @app.middleware('http')
    async def origin_check(request:Request,call_next):
        origin=request.headers.get('origin')
        if request.method=='POST' and origin and urlsplit(origin).netloc!=request.headers.get('host'):
            return JSONResponse({'detail':'拒绝跨站请求'},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(TaskConflict)
    async def conflict(request,error):
        return JSONResponse({'detail':str(error)},status_code=409)

    @app.get('/')
    async def index():
        return FileResponse(STATIC/'index.html')

    @app.get('/api/status')
    async def status():
        service=app.state.service
        return {'active_id':service.active_id if service.busy else None,
                'tasks':service.store.list(),'model':service.config.model.options.get('model',''),
                'tools':[{'name':t.name,'description':t.description} for t in service.runtime.tools.definitions()] if service.runtime else [],
                'workspace':str(workspace),'control_locked':service.store.control_locked}

    @app.post('/api/tasks',status_code=202)
    async def start(body:TaskRequest):
        if recovery_lock.locked():raise TaskConflict('正在核对恢复状态，请稍候')
        try:
            return await app.state.service.start(body.instruction,config=agent_config(config_path),parent_id=body.parent_id)
        except KeyError:
            raise HTTPException(404,'会话不存在') from None
        except (ValueError,OSError) as error:
            raise HTTPException(422,str(error)) from error

    @app.post('/api/control/recover')
    async def recover(body:RecoveryRequest):
        if recovery_lock.locked():raise TaskConflict('正在核对恢复状态，请稍候')
        async with recovery_lock:
            try:
                return await recover_control(app.state.service,app.state.ik_config,
                                             confirmed_stopped=body.confirmed_stopped)
            except (ValueError,OSError,RuntimeError,asyncio.TimeoutError) as error:
                if isinstance(error,TaskConflict):raise
                reason=str(error) or 'ROS 反馈请求超时，请检查连接后重试'
                raise HTTPException(409,f'未解除控制锁：{reason}') from None

    @app.get('/api/tasks/{task_id}')
    async def task(task_id:str):
        try:
            return task_view(app.state.service,task_id)
        except KeyError:
            raise HTTPException(404,'任务不存在') from None

    @app.post('/api/tasks/{task_id}/cancel')
    async def cancel(task_id:str):
        return await app.state.service.cancel(task_id)

    @app.get('/api/media/{name}')
    async def media(name:str):
        root=(workspace/'media').resolve()
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
            raise HTTPException(404)
        return FileResponse(path)

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app
