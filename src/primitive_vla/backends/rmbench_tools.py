"""Generic sensor-grounded tools over the existing RMBench worker and model plugin."""
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from primitive_vla.runtime.process import JsonProcess
from primitive_vla.plugins.generic_tools import capability_descriptions
from primitive_vla.providers.codex_schema import obj, array, choice


class RMBenchTools:
    descriptions = capability_descriptions() | {
        'observe': 'Read current camera RGB and robot feedback; does not run detection or move anything. Initial image is a reference, not current state.',
        'finish': 'Submit your task-completion claim and visible evidence. No predefined visual conditions gate this claim. Ends the episode permanently; independent grading happens afterwards.',
        'stop': 'End without claiming success and explain the limitation.'}

    def __init__(self, backend, directory, log):
        self.backend, self.log = backend, log
        self.directory = Path(directory)
        self.config = backend.config
        if self.config['mode'] != 'vla' or self.config['observation_mode'] != 'sensors':
            raise ValueError('Generic tools require sensor-only world with explicit (not automatic) IK')
        config_path = self.directory/'perception-config.json'
        config_path.write_text(json.dumps(self.config['perception'], indent=2))
        self.perception = JsonProcess(self.config['python'], 'primitive_vla.plugins.grounded_sam.worker',
            [config_path], self.directory/'perception-worker.log', startup_timeout=180, timeout=120)
        self.detections, self.images = {}, {}
        self.sequence, self.generation = 0, 0
        self.active = None
        self.locked = False
        string = {'type': 'string', 'minLength': 1}
        arms = obj({'arm': choice(['left','right']), 'detection_id': {'type':'integer'},
                    'clearance': {'type':'number', 'minimum':.02, 'maximum':.25}})
        arguments = {
            'perceive': obj({'query': string}),
            'move_to': obj({'targets': dict(array(arms), minItems=1, maxItems=2)}),
            'execute': obj({'instruction': string, 'max_actions': {'type':'integer', 'minimum':1,
                                                      'maximum':self.config['max_skill_steps']}}),
            'observe': obj({}), 'finish': obj({'evidence':string}), 'stop':obj({'evidence':string})}
        self.schema = {'anyOf':[obj({'name':choice([name]), 'arguments':args}) for name,args in arguments.items()]}

    async def observe(self):
        observation = await self.backend.worker.request('observe')
        observation['images'].update(self.images)
        observation['detections'] = list(self.detections.values())
        return observation

    def invalidate(self):
        self.detections, self.images = {}, {}

    async def invoke(self, name, arguments):
        if self.locked:
            raise RuntimeError('Control locked after unacknowledged stop')
        # Model argument errors are feedback, transport/control failures end the episode.
        try:
            if name == 'observe':
                return await self.observe()
            if name == 'perceive':
                self.sequence += 1
                snapshot = await self.backend.worker.request('sense')
                output = self.directory/'media'/f'inspection-{self.sequence:02d}'
                result = await self.perception.request('inspect', rgb=snapshot['rgb'],
                    sensor=snapshot['sensor'], output=str(output), query=arguments['query'],
                    initial_rgb=str(self.directory/'media'/'initial.png'))
                self.detections = {d['id']: d for d in result['detections']}
                self.images = {key: str(Path(result[field]).relative_to(self.directory/'media'))
                               for key,field in [('grounded_masks','overlay'),('object_details','details')]}
                self.log.write('perception_grounded', frame=snapshot['frame'], **result)
                return result
            if name == 'move_to':
                targets, used = [], set()
                for target in arguments['targets']:
                    arm, identifier = target['arm'], target['detection_id']
                    if arm not in ('left','right') or arm in used:
                        raise ValueError('Choose each arm at most once')
                    used.add(arm)
                    detection = self.detections.get(identifier)
                    if detection is None or detection['surface_world'] is None:
                        raise ValueError('Target ID must have a fresh measured depth point; call perceive')
                    if detection['mask_score'] < self.config['perception']['min_mask_score']:
                        raise ValueError('Target mask confidence below configured threshold')
                    clearance = target['clearance']
                    if not .02 <= clearance <= .25:
                        raise ValueError('Clearance must be between .02 and .25 meters')
                    targets.append(dict(arm=arm, point=detection['surface_world'], clearance=clearance,
                                        detection_id=identifier))
                if not targets:
                    raise ValueError('At least one approach target is required')
                self.invalidate()
                self.log.write('tool_motion', tool=name, targets=targets)
                return await self.backend.worker.request('move_to', targets=targets)
            if name == 'execute':
                budget = arguments['max_actions']
                if type(budget) is not int or not 1 <= budget <= self.config['max_skill_steps']:
                    raise ValueError('Invalid action budget')
                if not arguments['instruction'].strip():
                    raise ValueError('A nonempty VLA instruction is required')
                return await self.execute(arguments['instruction'], budget)
            raise ValueError('Unknown generic tool')
        except (ValueError, KeyError) as error:
            return {'error': str(error), 'executed': False}

    async def execute(self, instruction, budget, stop_requested=None, *, stop_on_end=True):
        observation = await self.observe()
        if observation['proprioception']['remaining_action_steps'] <= 0:
            return {'error':'Episode action budget exhausted', 'executed':False}
        self.invalidate()
        self.generation += 1
        call_id = self.active = uuid4().hex
        executed = 0
        result = {'subtask_end':False, 'exhausted':False}
        self.log.write('controller_selected', call_id=call_id, controller='vla', prompt=instruction)
        try:
            await self.backend.worker.request('begin', call_id=call_id, generation=self.generation,
                prompt=instruction, position='', deadline=time.time()+600)
            while executed < budget and not (stop_requested and stop_requested()):
                prediction = await self.backend.worker.request('infer')
                if stop_requested and stop_requested():
                    break
                result = await self.backend.worker.request('chunk', call_id=call_id,
                    generation=self.generation, actions=prediction['actions'][:budget-executed],
                    stop_on_end=stop_on_end)
                executed += result['executed']
                self.log.write('vla_chunk_executed', call_id=call_id, **result)
                if (stop_on_end and result['subtask_end']) or result['exhausted'] or result['executed']==0:
                    break
            return {'actions':executed, 'learned_end':result['subtask_end'],
                    'budget_exhausted':result['exhausted'] or executed>=budget,
                    'physical_outcome':'unverified'}
        finally:
            self.locked = True
            acknowledged = await asyncio.shield(self.backend.stop(call_id, 'generic tool returned'))
            if not acknowledged:
                raise RuntimeError('Stop not acknowledged; no further commands allowed')
            self.active, self.locked = None, False

    async def close(self):
        await self.perception.close()
