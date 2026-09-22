"""Persistent direct-VLA session. No planner client, task skills or automatic IK."""
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from primitive_vla.backends.rmbench import RMBenchBackend
from primitive_vla.backends.rmbench_tools import RMBenchTools
from primitive_vla.storage.events import EventLog
from .generic_service import GenericService
from .service import TaskConflict


class VLALab:
    def __init__(self, root, workspace):
        self.directory=Path(workspace).resolve()/time.strftime('%Y%m%d-%H%M%S')
        self.directory.mkdir(parents=True)
        service = GenericService(root,Path(workspace)/'settings')
        self.config=service.external_config('vla-lab.json')
        self.config.update(instruction=self.config['default_prompt'], persistent_vla_lab=True,live_frame=True)
        self.stream=(self.directory/'events.jsonl').open('w')
        self.log=EventLog(sink=self.record)
        self.backend=RMBenchBackend(self.config,self.directory/'scene-000',self.log)
        self.tools=RMBenchTools(self.backend,self.directory/'scene-000',self.log)
        self.scene_directory=self.directory/'scene-000'
        self.state='loading';self.error=None;self.stop_requested=False
        self.commands=[];self.info={};self.command=None;self.observation={}
        self.job=None

    def record(self,event):
        self.stream.write(json.dumps(event,ensure_ascii=False)+'\n');self.stream.flush()
        if event['event']=='vla_chunk_executed' and self.command:
            self.command['actions']+=event['executed']
            self.command['frame']=event.get('frame')

    async def load(self):
        try:
            await self.backend.warmup()
            self.info=await self.backend.worker.request('session_info')
            self.observation=self.info['observation']
            self.state='ready'
        except Exception as error:
            self.error=str(error);self.state='error'

    def require_ready(self):
        if self.state!='ready':
            raise TaskConflict('模型尚未就绪或正在执行，请先等待或停止当前指令')

    def status(self):
        return {'state':self.state,'error':self.error,'session':self.directory.name,
                'model':self.config['model_label'],'model_identity':self.info.get('model_identity'),
                'worker_pid':self.backend.worker.process.pid if self.backend.worker.process else None,
                'scene_index':self.info.get('scene_index',0),'seed':self.info.get('seed',self.config['seed']),
                'observation':self.observation,'commands':self.commands,
                'official_prompt':self.config['default_prompt'],
                'example_prompts':self.config['example_prompts'],
                'has_end_classifier':self.config['has_end_classifier']}

    async def execute(self,prompt,budget,stop_on_end=False):
        self.require_ready()
        if not prompt.strip():
            raise ValueError('请输入 VLA 指令')
        self.stop_requested=False;self.state='running'
        self.command={'id':uuid4().hex,'prompt':prompt,'max_actions':budget,'stop_on_end':stop_on_end,'actions':0,
                      'scene_index':self.info['scene_index'],'seed':self.info['seed'],
                      'started_at':time.time(),'status':'running'}
        self.commands.append(self.command)
        async def run():
            try:
                result=await self.tools.execute(prompt,budget,lambda:self.stop_requested,stop_on_end=stop_on_end)
                self.command.update(status='stopped' if self.stop_requested else 'finished',result=result)
                self.observation=await self.backend.worker.request('observe')
                self.command['diagnostics']=await self.backend.worker.request('diagnostics')
                self.state='ready'
            except Exception as error:
                self.command.update(status='error',error=str(error));self.error=str(error);self.state='error'
            finally:
                self.command['ended_at']=time.time()
                (self.directory/'commands.json').write_text(json.dumps(self.commands,ensure_ascii=False,indent=2)+'\n')
        self.job=asyncio.create_task(run())
        return self.command

    async def reset(self,seed):
        self.require_ready();self.state='resetting'
        async def run():
            try:
                self.info=await self.backend.worker.request('reset_scene',seed=seed,timeout=180)
                self.scene_directory=Path(self.info['directory'])
                self.observation=self.info['observation']
                self.tools.generation=0
                self.tools.invalidate()
                self.state='ready'
            except Exception as error:
                self.state='error';self.error=str(error)
        self.job=asyncio.create_task(run())
        return {'state':'resetting'}

    async def stop(self):
        if self.state=='running':
            self.stop_requested=True
            return {'state':'stopping','reason':'finish the current in-flight chunk, then stop'}
        return {'state':self.state}

    async def close(self):
        self.stop_requested=True
        if self.job:
            await asyncio.gather(self.job,return_exceptions=True)
        await asyncio.gather(self.tools.close(),self.backend.close(),return_exceptions=True)
        self.stream.close()
