"""Agent backend for an external RMBench world and official Mem-0 executor."""

import json
from pathlib import Path

from primitive_vla.domain.models import ExecutionStatus, Fact, Observation, SkillFeedback, Truth
from primitive_vla.runtime.process import JsonProcess


class RMBenchBackend:
    model_version = 'mem0-cover-blocks-step30000'

    def __init__(self, config, directory, log):
        self.config, self.log = config, log
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        (directory/'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
        self.worker = JsonProcess(config['python'], 'primitive_vla.simulation.rmbench_worker',
            [directory/'config.json', directory], directory/'worker.log', timeout=180,
            startup_timeout=300, close_timeout=10)
        self.ready = False

    async def observe(self):
        data = await self.worker.request('observe')
        facts = {key: Fact(Truth(f['value']), f['source'], f['timestamp'], f['confidence'], f['ttl_s'])
                 for key, f in data['facts'].items()}
        return Observation(facts, timestamp=data['timestamp'], images=data['images'],
                           proprioception=data['proprioception'], frames=data['frames'], sensors=tuple(data['sensors']))

    async def warmup(self):
        if not self.ready:
            self.log.write('vla_loading', checkpoint=self.config['checkpoint'])
            metadata = await self.worker.request('warmup', timeout=300)
            self.log.write('vla_ready', **metadata)
            self.ready = True

    async def run(self, call, candidate, lease, authority, feedback):
        await self.warmup()
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        prompt = candidate.spec.policy_prompt.format(**call.bindings)
        self.log.write('controller_selected', call_id=call.call_id, prompt=prompt,
                       controller=self.config['mode'], skill=call.skill, bindings=call.bindings)
        await self.worker.request('begin', call_id=call.call_id, generation=lease.generation,
            prompt=prompt, position=call.bindings['position'], deadline=call.deadline)
        while authority.accepts(call.call_id, lease.generation):
            prediction = await self.worker.request('infer')
            self.log.write('vla_response', call_id=call.call_id, **prediction)
            if not authority.accepts(call.call_id, lease.generation):
                return ExecutionStatus.CANCELLED
            result = await self.worker.request('chunk', call_id=call.call_id, generation=lease.generation,
                                               actions=prediction['actions'])
            self.log.write('vla_chunk_executed', call_id=call.call_id, **result)
            feedback(SkillFeedback(call.call_id, lease.generation, 'mem0_execution',
                                  evidence=f"{result['skill_steps']} steps; end signals={result['end_signals']}"))
            if result['fail_flag']:
                return ExecutionStatus.FAILED
            if result['subtask_end'] or result['success']:
                await self.stop(call.call_id, 'classifier boundary')
                return ExecutionStatus.COMPLETED
            if result['exhausted']:
                return ExecutionStatus.TIMED_OUT
        return ExecutionStatus.CANCELLED

    async def stop(self, call_id, reason):
        result = await self.worker.request('stop', call_id=call_id)
        return result.get('stopped') is True and result.get('call_id') == call_id

    async def close(self):
        await self.worker.close()
