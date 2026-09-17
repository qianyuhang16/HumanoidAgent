"""Agent-callable perception plugin; retains estimated facts separately from control state."""
import json
from pathlib import Path

from primitive_vla.domain.models import ExecutionStatus, Fact, Truth
from primitive_vla.runtime.process import JsonProcess


def bind_targets(detections, config, selected_ids=None):
    """Bind exactly two independently segmented targets using calibrated world-x order."""
    if selected_ids is not None:
        detections = [d for d in detections if d['id'] in set(selected_ids)]
    objects = [d for d in detections if config.get('target_label', 'battery') in d['label']
               and d['surface_world'] is not None and d['mask_score'] >= config.get('min_mask_score', .7)]
    if len(objects) != 2:
        return {}
    objects.sort(key=lambda d: d['surface_world'][0])
    if objects[1]['surface_world'][0] - objects[0]['surface_world'][0] < config.get('min_separation', .025):
        return {}
    return {side: obj['surface_world'] for side, obj in zip(('left', 'right'), objects)}


def bind_partial_targets(detections, assignments, config):
    """Accept independently grounded roles; missing objects stay unknown."""
    by_id = {d['id']: d for d in detections}
    result, used = {}, set()
    for assignment in assignments:
        side, identifier = assignment['side'], assignment['detection_id']
        if side in result or identifier in used:
            return {}  # Conflicting identities cannot be resolved by a geometric guess.
        detection = by_id.get(identifier)
        if (detection is None or assignment['confidence'] < config.get('min_identity_confidence', .7)
                or config.get('target_label', 'battery') not in detection['label']
                or detection['surface_world'] is None
                or detection['mask_score'] < config.get('min_mask_score', .7)):
            continue
        result[side] = detection['surface_world']
        used.add(identifier)
    if len(result) == 2 and result['right'][0] - result['left'][0] < config.get('min_separation', .025):
        return {}
    return result


class PerceptionBackend:
    model_version = 'grounding-dino-tiny+sam-vit-base+visual-assessor'

    def __init__(self, config, observer, client, directory, log):
        self.config, self.observer, self.client, self.log = config, observer, client, log
        self.directory = Path(directory)
        path = self.directory/'perception-config.json'
        path.write_text(json.dumps(config, indent=2))
        self.worker = JsonProcess(observer.config['python'], 'primitive_vla.plugins.grounded_sam.worker',
            [path], self.directory/'perception-worker.log', startup_timeout=180, timeout=120)
        self.sequence = 0

    async def observe(self):
        return await self.observer.observe()

    async def warmup(self):
        await self.worker.start()

    async def run(self, call, candidate, lease, authority, feedback):
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        operation = candidate.spec.program[0]['op']
        if operation == 'declare_complete':
            self.observer.protocol['agent:complete'] = Truth.TRUE
            self.log.write('agent_completion_claim', call_id=call.call_id, source='agent_visual_claim')
            return ExecutionStatus.COMPLETED
        if operation != 'inspect':
            raise ValueError('Unsupported perception skill operation')
        self.sequence += 1
        self.observer.invalidate()
        snapshot = await self.observer.worker.request('sense')
        output = self.directory/'media'/f'inspection-{self.sequence:02d}'
        evidence = await self.worker.request('inspect', rgb=snapshot['rgb'], sensor=snapshot['sensor'], output=str(output), initial_rgb=str(self.directory/'media'/'initial.png'))
        self.log.write('perception_grounded', call_id=call.call_id, frame=snapshot['frame'], **evidence)
        criteria = self.config['visual_criteria']
        images = {'rgb': Path(snapshot['rgb']).name, 'initial_reference': 'initial.png', 'grounded_masks': str(Path(evidence['overlay']).relative_to(self.directory/'media'))}
        images['object_details'] = str(Path(evidence['details']).relative_to(self.directory/'media'))
        judgement = await self.client.complete('visual_inspection', candidate.spec.instructions + '\n' + self.config['assessment_prompt'],
            {'criteria': criteria, 'detections': evidence['detections'], 'frame': snapshot['frame'],
             'allow_partial_targets': self.config.get('allow_partial_targets', False)}, images)
        (output/'result.json').write_text(json.dumps({'grounding': evidence, 'assessment': judgement}, ensure_ascii=False, indent=2))
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        estimates = {key: Fact(Truth.UNKNOWN, 'vlm:visual_inspection', ttl_s=600) for key in criteria}
        for item in judgement['predicates']:
            if item['predicate'] in criteria:
                estimates[item['predicate']] = Fact(Truth(item['value']), 'vlm:visual_inspection',
                                                     confidence=item['confidence'], ttl_s=600)
        if self.config.get('allow_partial_targets'):
            self.observer.targets = bind_partial_targets(evidence['detections'], judgement['target_assignments'], self.config)
        else:
            self.observer.targets = bind_targets(evidence['detections'], self.config, judgement['target_detection_ids'])
        for side in ('left', 'right'):
            estimates[f'target:{side}:available'] = Fact(
                Truth.TRUE if side in self.observer.targets else Truth.FALSE, 'grounding_dino+sam+rgbd', ttl_s=600)
        self.observer.perception_evidence = dict(frame=snapshot['frame'],
            detections=evidence['detections'], assessment=judgement, overlay=images['grounded_masks'])
        self.observer.estimates = estimates
        self.observer.protocol['inspection:needed'] = Truth.FALSE
        self.log.write('perception_assessed', call_id=call.call_id, frame=snapshot['frame'],
                       judgement=judgement, targets=self.observer.targets)
        return ExecutionStatus.COMPLETED

    async def stop(self, call_id, reason):
        # No actuator or mutable remote state. Executive revokes authority before cancellation;
        # a late inference response cannot publish estimates to the observation cache.
        return True

    async def close(self):
        await self.worker.close()
