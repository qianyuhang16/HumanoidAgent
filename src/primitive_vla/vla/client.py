"""Policy-specific requests over the shared task-owned worker transport."""

import asyncio

from primitive_vla.runtime.process import JsonProcess


class PolicyClient:
    def __init__(self, config, workspace, session, log):
        self.config, self.workspace, self.log = config, workspace, log
        self.worker = JsonProcess(config.python_executable, 'primitive_vla.vla.worker', [],
            workspace / 'runs' / session / 'policy.log', config.inference_timeout_s,
            startup_timeout=config.startup_timeout_s, init_payload={'config': config.model_dump()},
            env={'HF_HUB_OFFLINE': '1'})
        self.lock = asyncio.Lock()
        self.ready = False

    async def start(self):
        async with self.lock:
            if self.ready:
                return
            if self.log:
                self.log.write('vla_loading', checkpoint=self.config.checkpoint, adapter=self.config.adapter)
            await self.worker.start()
            self.ready = True
            if self.log:
                self.log.write('vla_ready', **self.worker.metadata, checkpoint=self.config.checkpoint)

    async def infer(self, observation, prompt):
        await self.start()
        ref = observation.images[self.config.image_source]
        media = (self.workspace / 'media').resolve()
        image = (media / ref).resolve()
        if not image.is_relative_to(media):
            raise ValueError('Policy image must be in task media')
        return await self.worker.request('infer', image=str(image),
            qpos=observation.proprioception['qpos'], prompt=prompt)

    async def close(self):
        await self.worker.close()
