"""Sensor-only manipulation; learned end votes hand control back to the Agent."""
from primitive_vla.domain.models import ExecutionStatus, Fact, SkillFeedback, Truth
from .rmbench import RMBenchBackend


class VisualRMBenchBackend(RMBenchBackend):
    def __init__(self, config, directory, log):
        if config.get('observation_mode') != 'sensors':
            raise ValueError('Visual backend requires the sensor-only world')
        super().__init__(config, directory, log)
        self.estimates = {}
        self.targets = {}
        self.perception_evidence = None
        self.protocol = {'inspection:needed': Truth.TRUE, 'agent:complete': Truth.FALSE,
                         'command:installation_attempted': Truth.FALSE}

    async def observe(self):
        observation = await super().observe()
        observation.facts['control:available'] = Fact(
            Truth.TRUE if observation.proprioception['remaining_action_steps'] > 0 else Truth.FALSE,
            'controller:action_budget', ttl_s=600)
        if self.perception_evidence is not None:
            observation.images['grounded_masks'] = self.perception_evidence['overlay']
        observation.facts.update(self.estimates)
        observation.facts.update({k: Fact(v, 'agent:protocol', ttl_s=600) for k, v in self.protocol.items()})
        return observation

    def invalidate(self):
        self.protocol['inspection:needed'] = Truth.TRUE
        self.targets = {}
        self.perception_evidence = None
        # Explicit unknowns replace old evidence in State, which otherwise retains omitted facts.
        self.estimates = {key: Fact(Truth.UNKNOWN, 'perception:invalidated', ttl_s=600)
                          for key in self.estimates}

    async def run(self, call, candidate, lease, authority, feedback):
        await self.warmup()
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        prompt = candidate.spec.policy_prompt.format(**call.bindings)
        entry = await self.observe()
        review_image = entry.images["simulation"]
        reviews = 0
        next_review = self.config.get("review_interval_steps", 160)
        targets = dict(self.targets)
        self.invalidate()
        self.log.write('controller_selected', call_id=call.call_id, prompt=prompt,
                       controller=self.config['mode'], skill=call.skill, bindings=call.bindings,
                       target_source='grounding_dino+sam+rgbd', targets=targets)
        await self.worker.request('begin', call_id=call.call_id, generation=lease.generation,
            prompt=prompt, position=call.bindings['position'], deadline=call.deadline, targets=targets)
        if call.bindings['position'] == 'pair':
            self.protocol['command:installation_attempted'] = Truth.TRUE
        while authority.accepts(call.call_id, lease.generation):
            prediction = await self.worker.request('infer')
            self.log.write('vla_response', call_id=call.call_id, **prediction)
            if not authority.accepts(call.call_id, lease.generation):
                return ExecutionStatus.CANCELLED
            result = await self.worker.request('chunk', call_id=call.call_id, generation=lease.generation,
                                               actions=prediction['actions'])
            self.log.write('vla_chunk_executed', call_id=call.call_id, **result)
            feedback(SkillFeedback(call.call_id, lease.generation, 'mem0_execution',
                evidence=f"{result['skill_steps']} actions; learned end votes={result['end_signals']}"))
            if result['subtask_end'] or result['exhausted']:
                await self.stop(call.call_id, 'learned boundary' if result['subtask_end'] else 'action budget')
                # Completion means control returned, never that the physical effect succeeded.
                return ExecutionStatus.COMPLETED
            if (self.supervisor is not None and result['skill_steps'] >= next_review
                    and reviews < self.config.get('max_reviews_per_skill', 3)):
                observation = await self.observe()
                context = {'skill': call.skill, 'bindings': call.bindings, 'instruction': prompt,
                    'execution': result, 'actions': ['continue', 'yield'], 'review': reviews + 1,
                    'remaining_reviews': self.config.get('max_reviews_per_skill', 3) - reviews - 1}
                decision = await self.supervisor(context, {'current': observation.images['simulation'],
                    'entry': entry.images['simulation'], 'previous_review': review_image})
                self.log.write('handoff_review', call_id=call.call_id, context=context, decision=decision)
                if not authority.accepts(call.call_id, lease.generation):
                    return ExecutionStatus.CANCELLED
                if decision.get('action') not in context['actions']:
                    raise ValueError('Visual review must continue or yield to the Agent')
                if decision['action'] == 'yield':
                    await self.stop(call.call_id, 'Agent requested visual reassessment')
                    return ExecutionStatus.COMPLETED
                reviews += 1
                review_image = observation.images['simulation']
                next_review = result['skill_steps'] + self.config.get('review_interval_steps', 160)
        return ExecutionStatus.CANCELLED
