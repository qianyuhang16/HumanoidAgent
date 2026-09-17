"""Agent backend for an external RMBench world and official Mem-0 executor."""

import json
from pathlib import Path

from primitive_vla.domain.models import ExecutionStatus, Fact, Observation, SkillFeedback, Truth
from primitive_vla.runtime.process import JsonProcess


class RMBenchBackend:
    model_version = 'mem0-cover-blocks-step30000'

    def __init__(self, config, directory, log, supervisor=None):
        self.config, self.log = config, log
        self.supervisor = supervisor
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
        reviews = 0
        next_review = self.config.get("review_interval_steps", 160)
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
            observation = await self.observe()
            achieved = all(observation.facts.get(key) and observation.facts[key].value == value
                           for key, value in candidate.effects.items())
            if result['success'] or (result['subtask_end'] and achieved):
                await self.stop(call.call_id, 'verified classifier boundary')
                return ExecutionStatus.COMPLETED
            if result['exhausted']:
                return ExecutionStatus.TIMED_OUT
            review_due = result['subtask_end'] or (result['skill_steps'] >= next_review and not achieved)
            if review_due and self.supervisor is not None:
                if reviews >= self.config.get('max_reviews_per_skill', 3):
                    return ExecutionStatus.FAILED
                context = {'skill': call.skill, 'bindings': call.bindings, 'prompt': prompt,
                           'expected_effects': candidate.effects,
                           'facts': {k: v.value for k, v in observation.facts.items()},
                           'execution': result, 'review': reviews + 1,
                           'remaining_reviews': self.config.get('max_reviews_per_skill', 3) - reviews,
                           'actions': (['continue', 'reapproach', 'stop'] if self.config['mode'] == 'ik_approach'
                                       else ['continue', 'stop']),
                           'instructions': candidate.spec.instructions}
                decision = await self.supervisor(context, observation.images)
                action = decision.get('action')
                if action not in context['actions']:
                    raise ValueError('Supervisor must choose an allowed recovery action')
                self.log.write('handoff_review', call_id=call.call_id, context=context, decision=decision)
                if not authority.accepts(call.call_id, lease.generation):
                    return ExecutionStatus.CANCELLED
                if action == 'stop':
                    return ExecutionStatus.FAILED
                await self.worker.request('resume', call_id=call.call_id, generation=lease.generation,
                                          action=action, position=call.bindings['position'])
                reviews += 1
                next_review = result['skill_steps'] + self.config.get('review_interval_steps', 160)
            elif result['subtask_end']:
                # Baseline preserves classifier termination and independent verification.
                await self.stop(call.call_id, 'classifier boundary')
                return ExecutionStatus.COMPLETED
        return ExecutionStatus.CANCELLED

    async def stop(self, call_id, reason):
        result = await self.worker.request('stop', call_id=call_id)
        return result.get('stopped') is True and result.get('call_id') == call_id

    async def close(self):
        await self.worker.close()
