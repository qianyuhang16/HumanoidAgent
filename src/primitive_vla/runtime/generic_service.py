"""One interactive simulation at a time, with persisted prompts and run artifacts."""
import asyncio
import json
from pathlib import Path
from time import time
from uuid import uuid4

from .generic_episode import episode
from .service import TaskConflict

SCENES = {
    'cover': ('盖积木', 'cover_raster.json', 'cover-generic/stepwise-v1/vla-300102/media/initial.png'),
    'battery': ('安装电池', 'config.json', 'battery-generic/test-v0/vla-300102/media/initial.png'),
}


class GenericService:
    def __init__(self, root, workspace, runner=episode):
        self.root, self.workspace = Path(root).resolve(), Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.runner, self.worker, self.active_id = runner, None, None
        self.cancelling = False

    @property
    def busy(self):
        return self.worker is not None and not self.worker.done()

    def scenes(self):
        result = []
        for key, (label, file, _) in SCENES.items():
            config = self.config(key)
            result.append({'id':key, 'label':label, 'instruction':config['instruction'],
                           'model':config['vlm']['model'], 'executor':config['task_name']})
        return result

    def config(self, scene):
        if scene not in SCENES:
            raise ValueError('Unknown scene')
        config = json.loads((self.root/'benchmarks/mem0/generic_tools'/SCENES[scene][1]).read_text())
        for key in ('python','repo','checkpoint','backbone','norm_stats','planner_prompt'):
            config[key] = str(self.root/config[key])  # Do not resolve the venv interpreter symlink.
        for key in ('detector','segmenter'):
            config['perception'][key] = str(self.root/config['perception'][key])
        config.update(camera_shader='default', mode='vla')
        return config

    def directory(self, run_id):
        if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):
            raise KeyError(run_id)
        path=self.workspace/run_id
        if not (path/'request.json').exists():
            raise KeyError(run_id)
        return path

    def get(self, run_id):
        path=self.directory(run_id)
        result=json.loads((path/'request.json').read_text())
        summary=path/'episode/summary.json'
        if self.busy and self.active_id==run_id:
            status='cancelling' if self.cancelling else 'running'
        elif summary.exists():
            status=json.loads(summary.read_text())['status']
        else:
            status='interrupted'
        return result | {'id':run_id,'status':status,
                         'summary':json.loads(summary.read_text()) if summary.exists() else None}

    def list(self):
        paths=sorted(self.workspace.glob('*/request.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        return [self.get(p.parent.name) for p in paths[:30]]

    async def start(self, scene, instruction, seed, planner_prompt=None):
        if self.busy:
            raise TaskConflict('已有任务运行中，请等待结束或先停止。')
        if not instruction.strip():
            raise ValueError('请输入任务目标')
        config=self.config(scene)
        prompt=planner_prompt if planner_prompt is not None else Path(config['planner_prompt']).read_text()
        if not prompt.strip():
            raise ValueError('规划提示不能为空')
        run_id=uuid4().hex
        path=self.workspace/run_id
        path.mkdir()
        (path/'planner.md').write_text(prompt)
        request={'scene':scene,'instruction':instruction.strip(),'seed':seed,'created_at':time(),
                 'planner_prompt':prompt}
        (path/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n')
        config.update(seed=seed,instruction=instruction.strip(),planner_prompt=str(path/'planner.md'))
        self.active_id, self.cancelling = run_id, False

        async def work():
            try:
                await self.runner(config,path/'episode')
            except Exception as error:
                episode_path=path/'episode'
                episode_path.mkdir(exist_ok=True)
                (episode_path/'summary.json').write_text(json.dumps({'status':'failed',
                    'agent_declared_complete':False,'physical_success':None,'error':str(error)}))

        self.worker=asyncio.create_task(work())
        await asyncio.sleep(0)  # Enter episode cleanup scope before cancellation is accepted.
        return self.get(run_id)

    async def cancel(self, run_id):
        self.directory(run_id)
        if self.active_id != run_id or not self.busy:
            raise TaskConflict('该任务已经结束')
        if not self.cancelling:
            self.cancelling=True
            self.worker.cancel()
        return {'status':'cancelling'}

    async def close(self):
        if self.busy:
            if not self.cancelling:
                self.cancelling=True
                self.worker.cancel()
            await asyncio.gather(self.worker,return_exceptions=True)
