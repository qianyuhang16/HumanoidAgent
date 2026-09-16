"""Async adapter for an isolated SAPIEN worker in an existing RoboTwin environment."""

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
from uuid import uuid4

from primitive_vla.config.schema import SimulationConfig
from primitive_vla.domain.models import ExecutionStatus, Fact, Observation, SkillFeedback, Truth
from primitive_vla.domain.skills import check_conditions
from primitive_vla.vla.client import PolicyClient


class SapienBackend:
    model_version = 'sapien3-robotwin-panda-ik-v1'

    def __init__(self, config: SimulationConfig, workspace: Path, vla=None, log=None):
        self.config, self.workspace = config, workspace
        self.session = 'sapien-' + uuid4().hex[:12]
        self.process = self.reader = None
        self.pending = {}
        self.feedback = None
        self.start_lock = asyncio.Lock()
        self.sequence = 0
        self.log_file = None
        self.vla_config, self.log = vla, log
        self.policy = PolicyClient(vla, workspace, self.session, log) if vla else None
        if vla:
            self.model_version = 'pi05-base-franka-experimental'

    async def warmup(self):
        if self.policy:
            await self.policy.start()

    async def start(self):
        async with self.start_lock:
            if self.process is not None:
                return
            media = self.workspace / 'media'
            media.mkdir(parents=True, exist_ok=True)
            run_dir = self.workspace / 'runs' / self.session
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / 'config.json').write_text(self.config.model_dump_json(indent=2))
            self.log_file = (run_dir / 'simulator.log').open('w')
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get('PYTHONPATH', '')
            env['PYTHONWARNINGS'] = 'ignore::DeprecationWarning,ignore::UserWarning'
            try:
                self.process = await asyncio.create_subprocess_exec(
                    self.config.python_executable, '-m', 'primitive_vla.simulation.worker', str(run_dir / 'evidence.json'),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.log_file, env=env,
                    limit=4 * 1024 * 1024)
                self.reader = asyncio.create_task(self.read())
                await asyncio.wait_for(self.request('init', config=self.config.model_dump(mode='json'), media=str(media), session=self.session),
                                       self.config.startup_timeout_s)
            except BaseException:
                await self.close()
                raise

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if message.get('event') == 'feedback':
                    if self.feedback:
                        self.feedback(SkillFeedback(message['call_id'], message['generation'], message['phase']))
                    continue
                future = self.pending.get(message.get('id'))
                if future is not None and not future.done():
                    if 'error' in message:
                        future.set_exception(RuntimeError(message['error']))
                    else:
                        future.set_result(message['result'])
        except Exception as error:
            reason = f'Simulation protocol failed: {error}'
        else:
            reason = f'Simulation worker exited; see workspace/runs/{self.session}/simulator.log'
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(locals().get('reason', 'Simulation worker closed')))

    async def request(self, op, **payload):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.process.stdin.write((json.dumps({'id': request_id, 'op': op, **payload}) + '\n').encode())
            await self.process.stdin.drain()
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def observe(self):
        await self.start()
        data = await self.request('observe')
        facts = {key: Fact(value=Truth(fact['value']), source=fact['source'], timestamp=fact['timestamp'],
                           confidence=fact['confidence'], ttl_s=fact['ttl_s']) for key, fact in data['facts'].items()}
        return Observation(facts, timestamp=data['timestamp'], images=data['images'],
                           proprioception=data['proprioception'], frames=data['frames'], sensors=tuple(data['sensors']))

    async def prepare(self, call, candidate, lease, authority):
        raise NotImplementedError('SAPIEN supports complete atomic skills')

    async def run(self, call, candidate, lease, authority, feedback):
        await self.start()
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        self.feedback = feedback
        try:
            if candidate.spec.policy_prompt:
                return await self.run_policy(call, candidate, lease, authority)
            if self.policy and any(step.get('kind') not in (None, 'approach') or step.get('op') not in ('point', 'move', 'phase') for step in candidate.spec.program):
                raise ValueError('VLA mode permits only approach motion through the scripted/IK path')
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
        if self.process is None:
            return True
        if self.process.returncode is not None:
            return True  # Process exit destroys this simulated controller and world.
        result = await self.request('stop', call_id=call_id, reason=reason)
        return result.get('stopped') is True and result.get('call_id') == call_id

    async def close(self):
        if self.policy:
            await self.policy.close()
        if self.process and self.process.returncode is None:
            try:
                self.process.stdin.write(b'{"id":0,"op":"close"}\n')
                await self.process.stdin.drain()
                await asyncio.wait_for(self.process.wait(), 8)
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                self.process.kill()
                await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.log_file:
            self.log_file.close()
