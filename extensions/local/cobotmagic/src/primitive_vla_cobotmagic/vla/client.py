"""Workspace-owned worker cache; turn-owned execution and cancellation handles."""
import asyncio
import json
import os
from pathlib import Path
from time import monotonic,time
from uuid import uuid4
from primitive_vla.runtime.process import JsonProcess

_pools={}

def client_for(options,workspace,connection):
    key=str(Path(workspace).resolve())
    signature=json.dumps([options,connection],sort_keys=True)
    existing=_pools.get(key)
    if existing is not None:
        if existing.signature!=signature:raise ValueError('VLA settings changed; restart the web service')
        return existing
    client=Client(options,Path(workspace),connection)
    client.signature=signature;_pools[key]=client
    return client

async def close_workspace(workspace):
    client=_pools.pop(str(Path(workspace).resolve()),None)
    if client:await client.process.close()

class Client:
    def __init__(self,options,workspace,connection):
        self.options=options;self.workspace=workspace
        paths=[str(Path(__file__).resolve().parents[2]),options['source']]
        self.process=JsonProcess(options['python_executable'],'primitive_vla_cobotmagic.vla.worker',[],
            workspace/'runs'/f'vla-worker-{uuid4().hex}.log',startup_timeout=20,close_timeout=10,
            init_payload={'options':options,'connection':connection,'media':str(workspace/'media')},
            env={'PYTHONPATH':os.pathsep.join(paths),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
                 'TOKENIZERS_PARALLELISM':'false'})
        self.runs={}

    async def run(self,policy_id,duration_s,prepared_joints,context):
        context.check_active()
        # Register before any dispatch so a cancellation always has an owned handle.
        task=asyncio.create_task(self.process.request('run',policy_id=policy_id,duration_s=duration_s,
            prepared_joints=prepared_joints,call_id=context.call_id,
            deadline=time()+max(.001,context.deadline-monotonic()),
            timeout=max(.001,context.deadline-monotonic())+9))
        self.runs[context.call_id]=task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(self.stop(context.call_id))
            raise

    async def stop(self,call_id):
        task=self.runs.get(call_id)
        if task is None:return True
        try:
            if not task.done():
                await self.process.request('cancel',call_id=call_id,timeout=2)
            result=await asyncio.wait_for(asyncio.shield(task),8)
            return result.get('control_released') is True
        except (Exception,asyncio.CancelledError):
            # Reap the worker to fence future sends even if it never acknowledges.
            await asyncio.shield(self.process.close())
            return False

    async def finish_turn(self,call_ids):
        for call_id in call_ids:
            task=self.runs.get(call_id)
            if task is not None and not task.done():await self.stop(call_id)
            if task is not None and task.done():
                if not task.cancelled():task.exception()
                self.runs.pop(call_id,None)
