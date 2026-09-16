"""Agent skill execution through π0.5 in one shared LIBERO world."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from primitive_vla.runtime.lifecycle import close_all
from primitive_vla.domain.models import ExecutionStatus, Fact, Observation, SkillFeedback, Truth
from primitive_vla.runtime.process import JsonProcess


class LiberoBackend:
    model_version = 'pi05-libero-lerobot-osc-pose'

    def __init__(self, config, workspace, log):
        self.config, self.log = config, log
        self.session = 'libero-'+uuid4().hex[:12]
        run_dir = Path(workspace)/'runs'/self.session
        media = Path(workspace)/'media'
        run_dir.mkdir(parents=True, exist_ok=True)
        media.mkdir(parents=True, exist_ok=True)
        (run_dir/'simulator.json').write_text(config.model_dump_json(indent=2))
        (run_dir/'policy.json').write_text(config.policy.model_dump_json(indent=2))
        graphics_env = {key: os.environ.get(key, 'egl') for key in ('MUJOCO_GL', 'PYOPENGL_PLATFORM')}
        self.world = JsonProcess(config.python_executable, 'primitive_vla.simulation.libero_worker',
            [run_dir/'simulator.json', media, run_dir, self.session], run_dir/'simulator.log', config.startup_timeout_s,
            env=graphics_env)
        self.policy = JsonProcess(config.policy.model_python, 'primitive_vla.vla.libero_worker',
            [run_dir/'policy.json'], run_dir/'policy.log', config.startup_timeout_s, env=graphics_env)
        self.warmup_lock = asyncio.Lock()
        self.ready = False

    async def warmup(self):
        async with self.warmup_lock:
            if self.ready:
                return
            self.log.write('vla_loading', checkpoint=self.config.policy.checkpoint, adapter='libero_osc_pose')
            await self.policy.start()
            self.ready = True
            self.log.write('vla_ready', checkpoint=self.config.policy.checkpoint, **self.policy.metadata)

    async def observe(self):
        data = await self.world.request('observe')
        facts = {key: Fact(Truth(f['value']), f['source'], f['timestamp'], f['confidence'], f['ttl_s'])
                 for key, f in data['facts'].items()}
        return Observation(facts, timestamp=data['timestamp'], images=data['images'], frames=data['frames'],
                           proprioception=data['proprioception'], sensors=tuple(data['sensors']))

    async def run(self, call, candidate, lease, authority, feedback):
        if not candidate.spec.policy_prompt or candidate.spec.program:
            raise ValueError('LIBERO requires a policy skill from SKILL.md')
        await self.warmup()
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        prompt = candidate.spec.policy_prompt.format(**call.bindings).replace('_', ' ')
        self.log.write('controller_selected', call_id=call.call_id, controller='vla', skill=call.skill,
                       prompt=prompt, checkpoint=self.config.policy.checkpoint, adapter='libero_osc_pose',
                       session=self.session)
        await self.world.request('begin', call_id=call.call_id, generation=lease.generation,
                                 deadline=call.deadline, effects={k:v.value for k,v in candidate.effects.items()})
        chunk = 0
        while authority.accepts(call.call_id, lease.generation):
            chunk += 1
            observation = await self.world.request('model_observation')
            self.log.write('vla_requested', call_id=call.call_id, chunk=chunk, prompt=prompt,
                           observation=observation, state_used_by_model=False)
            result = await self.policy.request('infer', observation=observation['path'], prompt=prompt)
            self.log.write('vla_response', call_id=call.call_id, chunk=chunk, adapter='libero_osc_pose', **result)
            # A cancelled/expired lease must never submit the late model response.
            if not authority.accepts(call.call_id, lease.generation):
                return ExecutionStatus.CANCELLED
            applied = await self.world.request('chunk', call_id=call.call_id, generation=lease.generation,
                chunk=chunk, actions=result['actions'][:self.config.replan_steps])
            self.log.write('vla_chunk_executed', call_id=call.call_id, chunk=chunk, **applied)
            feedback(SkillFeedback(call.call_id, lease.generation, 'policy_execution',
                                  evidence=f"{applied['skill_steps']} LIBERO control steps"))
            if applied['skill_complete']:
                await self.world.request('stop', call_id=call.call_id)
                return ExecutionStatus.COMPLETED
            if applied['exhausted']:
                raise RuntimeError(f"VLA did not establish skill effects within {self.config.max_skill_steps} steps")
        return ExecutionStatus.CANCELLED

    async def stop(self, call_id, reason):
        if not self.world.process or self.world.process.returncode is not None:
            return True
        result = await self.world.request('stop', call_id=call_id)
        return result.get('stopped') is True and result.get('call_id') == call_id

    async def close(self):
        await close_all(self.world, self.policy)
