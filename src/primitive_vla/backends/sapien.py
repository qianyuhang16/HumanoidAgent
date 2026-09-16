"""Async adapter for an isolated SAPIEN worker in an existing RoboTwin environment."""

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from primitive_vla.config.schema import SimulationConfig
from primitive_vla.runtime.lifecycle import close_all
from primitive_vla.domain.models import ExecutionStatus, Fact, Observation, SkillFeedback, Truth
from primitive_vla.domain.skills import check_conditions
from primitive_vla.vla.client import PolicyClient
from primitive_vla.runtime.process import JsonProcess


class SapienBackend:
    model_version = 'sapien3-robotwin-panda-ik-v1'

    def __init__(self, config: SimulationConfig, workspace: Path, vla=None, log=None):
        self.config, self.workspace = config, workspace
        self.session = 'sapien-' + uuid4().hex[:12]
        self.feedback = None
        media = workspace / 'media'
        media.mkdir(parents=True, exist_ok=True)
        run_dir = workspace / 'runs' / self.session
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / 'config.json').write_text(config.model_dump_json(indent=2))
        self.worker = JsonProcess(config.python_executable, 'primitive_vla.simulation.worker',
            [run_dir / 'evidence.json'], run_dir / 'simulator.log', timeout=None,
            startup_timeout=config.startup_timeout_s,
            init_payload={'config': config.model_dump(mode='json'), 'media': str(media), 'session': self.session},
            env={'PYTHONWARNINGS': 'ignore::DeprecationWarning,ignore::UserWarning'}, on_event=self._on_event)
        self.vla_config, self.log = vla, log
        self.policy = PolicyClient(vla, workspace, self.session, log) if vla else None
        if vla:
            self.model_version = 'pi05-base-franka-experimental'

    async def warmup(self):
        if self.policy:
            await self.policy.start()

    def _on_event(self, message):
        if message.get('event') == 'feedback' and self.feedback:
            self.feedback(SkillFeedback(message['call_id'], message['generation'], message['phase']))

    async def request(self, op, **payload):
        return await self.worker.request(op, **payload)

    async def observe(self):
        data = await self.request('observe')
        facts = {key: Fact(value=Truth(fact['value']), source=fact['source'], timestamp=fact['timestamp'],
                           confidence=fact['confidence'], ttl_s=fact['ttl_s']) for key, fact in data['facts'].items()}
        return Observation(facts, timestamp=data['timestamp'], images=data['images'],
                           proprioception=data['proprioception'], frames=data['frames'], sensors=tuple(data['sensors']))

    async def run(self, call, candidate, lease, authority, feedback):
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        self.feedback = feedback
        try:
            if candidate.spec.policy_prompt:
                return await self.run_policy(call, candidate, lease, authority)
            if self.log:
                self.log.write('controller_selected', call_id=call.call_id, controller='ik', skill=call.skill)
            result = await self.request('run', call=asdict(call), generation=lease.generation,
                                        program=candidate.spec.program)
            if result.get('reason'):
                feedback(SkillFeedback(call.call_id, lease.generation, result['status'], evidence=result['reason']))
                if result['status'] == 'failed':
                    raise RuntimeError(result['reason'])
            return ExecutionStatus(result['status'])
        finally:
            self.feedback = None

    async def run_policy(self, call, candidate, lease, authority):
        if not self.policy:
            raise ValueError('This skill requires a configured VLA policy')
        await self.policy.start()
        cfg = self.vla_config
        prompt = candidate.spec.policy_prompt.format(**call.bindings)
        if self.log:
            self.log.write('controller_selected', call_id=call.call_id, controller='vla', skill=call.skill,
                           prompt=prompt, checkpoint=cfg.checkpoint, adapter=cfg.adapter)
        await self.request('policy_begin', call=asdict(call), generation=lease.generation,
                           track_stir=any(key.startswith('event:stirred:') for key in candidate.effects))
        for index in range(1, cfg.max_chunks + 1):
            observation = await self.observe()
            if not authority.accepts(call.call_id, lease.generation):
                return ExecutionStatus.CANCELLED
            if self.log:
                self.log.write('vla_requested', call_id=call.call_id, chunk=index, prompt=prompt,
                               images=observation.images, proprioception=observation.proprioception)
            result = await self.policy.infer(observation, prompt)
            if self.log:
                self.log.write('vla_response', call_id=call.call_id, chunk=index, **result)
            if not authority.accepts(call.call_id, lease.generation):
                return ExecutionStatus.CANCELLED
            applied = await self.request('policy_chunk', call_id=call.call_id, generation=lease.generation,
                chunk=index, actions=result['actions'][:cfg.execute_steps], action_hz=cfg.action_hz,
                max_joint_step=cfg.max_joint_step, max_gripper_step=cfg.max_gripper_step)
            if self.log:
                self.log.write('vla_chunk_executed', call_id=call.call_id, chunk=index, **applied)
            if applied['status'] != 'completed':
                raise RuntimeError(applied.get('reason', 'Policy chunk failed'))
            observed = await self.observe()
            values = {key: fact.current() for key, fact in observed.facts.items()}
            if check_conditions(candidate.effects, values).verdict == Truth.TRUE:
                await self.request('policy_end', call_id=call.call_id, generation=lease.generation)
                return ExecutionStatus.COMPLETED
        # Exhaustion is a failure, never an IK fallback or a fabricated success.
        raise RuntimeError(f'VLA did not establish skill effects within {cfg.max_chunks} action chunks')

    async def stop(self, call_id, reason):
        if self.worker.process is None:
            return True
        if self.worker.process.returncode is not None:
            return True  # Process exit destroys this simulated controller and world.
        result = await self.request('stop', call_id=call_id, reason=reason)
        return result.get('stopped') is True and result.get('call_id') == call_id

    async def close(self):
        await close_all(self.worker, *([self.policy] if self.policy else []))
