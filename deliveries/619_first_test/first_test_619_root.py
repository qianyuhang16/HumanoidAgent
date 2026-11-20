"""Offline 619 root-policy exercise. All actions and measurements are synthetic.

This exercises the real project consumer, ledger and Li calculation. It does not
call Chen's platform, a model, an SDK or hardware. The companion runner owns the
separate exercise of the received Chen host. No import-path changes occur here.
"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import time

from primitive_vla.contracts import Domain, Record, ResourceRef, ToolError
from primitive_vla.interfaces import TaskService
from primitive_vla.memory import Memory
from primitive_vla.planning import Decision
from primitive_vla.runtime.session import Limits
from primitive_vla.skills import SkillStore
from primitive_vla.tools.contracts import ToolResult, ToolSpec
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from scene_memory import Fact, SceneMemory
from scene_memory.chen_t05 import ChenT05Consumer
from scene_memory.host_bridge import register_li_effect
from scene_memory.working_memory import ExecutionContract, TaskAwareAgent


class SyntheticPlace:
    """Explicit execution test substitute; its completed result proves no effect."""
    def __init__(self):
        self.calls = []

    async def execute(self, arguments, context):
        self.calls.append({'arguments': deepcopy(arguments), 'call_id': context.call_id})
        return ToolResult({'synthetic_fixture': True, 'physical_execution': False})

    async def cancel(self, context):
        return True


def _scene(task):
    mapping = {o['object_id']: 'synthetic619/frame/' + o['object_id'] for o in task['objects']}
    snapshot = {
        'schema_version': 'stage1.v1', 'dataset_id': '619-authored-engineering-fixture',
        'session_id': 'synthetic619', 'map_version': task['scene_version'],
        'region_id': 'synthetic-table', 'observation_id': 'frame', 'observed_at_ns': 0,
        'coordinate_frame': 'world', 'length_unit': 'm', 'source_kind': 'synthetic',
        'identity_scope': 'frame_local', 'status': 'ok',
        'calibration_id': 'authored-synthetic-only', 'pose_source': 'authored-synthetic-only',
        'objects': [{'object_id': mapping[o['object_id']], 'label': o['label'],
                     'bbox_center': [0.7, 0.25, 0.08], 'bbox_extent': [0.04, 0.04, 0.04],
                     'evidence_refs': ['synthetic:object/' + o['object_id']]}
                    for o in task['objects']],
    }
    facts = [Fact('protected/' + i, mapping[i], 'protected', 'true',
                  ('synthetic:object/' + i,), kind='protection')
             for i in task['protected_object_ids']]
    return SceneMemory.from_snapshot(snapshot, relations=[], facts=facts), mapping


def _case(agent, session, consumer, goal_id, execution):
    """Construct new, labelled synthetic measurements after this native call.

    Geometry matches the public Li positive engineering fixture; no old source
    resource, sensor frame or historical test result is presented as a new input.
    """
    scene = agent.scenes[session.id]
    goal = consumer.goals[goal_id]
    captured = time.time()
    if captured <= execution.timestamp:
        raise ValueError('clock did not advance after native execution; preserve failure')
    ctx = {
        'schema_version': 't05.task_context.v1', 'session_id': session.id,
        'task_id': session.task_id, 'scene_version': scene.snapshot.map_version,
        'map_version': consumer.binding['map_version'],
        'coordinate_frame': scene.snapshot.coordinate_frame, 'length_unit': 'm',
        'identity_scope': scene.snapshot.identity_scope, 'goal': session.goal,
        'box_object_id': goal['container_id'],
        'items': {role: consumer.goals[g]['object_id']
                  for role, g in consumer.binding['item_roles'].items()},
        'action': {'call_id': execution.call_id, 'action': 'place',
                   'item': next(role for role, g in consumer.binding['item_roles'].items()
                                if g == goal_id),
                   'object_id': goal['object_id'], 'tool_status': 'completed',
                   'visit': consumer.binding['li_visit'], 'attempt': 1,
                   'end_time': execution.timestamp},
        'tolerance': deepcopy(consumer.binding['tolerance']),
    }
    box = goal['container_id']
    payloads = [
        ('cavity_sensing', {'kind': 'cavity', 'box_object_id': box,
            'cavity_measurement': 'direct', 'method': 'depth_interior_scan',
            'footprint': {'min': [0.1, 0.1], 'max': [0.4, 0.4]},
            'z_range': [0.02, 0.15],
            'full_bbox': {'min': [0., 0., 0.], 'max': [0.5, 0.5, 0.18]},
            'accuracy_m': 0.005}),
        ('instance_geometry', {'kind': 'object_pose', 'object_id': goal['object_id'],
            'center': [0.25, 0.25, 0.08], 'half_extent': [0.02, 0.02, 0.02],
            'accuracy_m': 0.004}),
        ('image_observation', {'kind': 'coverage', 'box_object_id': box,
            'region': 'box_cavity', 'covered_fraction': 1.0, 'accuracy_m': 0.01}),
    ]
    resources, observations = [], []
    for index, (source, payload) in enumerate(payloads):
        ref = ResourceRef('synthetic://619/' + execution.call_id + '/' + str(index),
                          'application/json', description='Authored synthetic ' + payload['kind'],
                          metadata={'origin': 'synthetic', 'fixture': 'first_test_619_root'})
        resources.append(asdict(ref))
        observations.append({**{k: ctx[k] for k in ('session_id', 'task_id', 'scene_version',
            'map_version', 'coordinate_frame', 'length_unit', 'identity_scope')},
            'observation_id': 'obs-' + ref.id, 'source_kind': source, 'captured_at': captured,
            'related_call_id': execution.call_id, 'visit': ctx['action']['visit'],
            'attempt': 1, 'payload': payload, 'resource_refs': [ref.id]})
    return {'schema_version': 't05.effect_case.v1', 'origin': 'synthetic',
            'task_context': ctx, 'resources': resources, 'observations': observations}


async def run_root(out: Path, task: dict) -> dict:
    """Exercise real root contracts using an explicitly mapped Chen task fixture.

    `out` is a new output directory; `task` is Chen's validate_task contract.
    Returns evidence IDs and summaries and writes root_summary.json plus Memory.
    Direct dispatch is declared: this does not claim autonomous model planning.
    """
    from examples.chen_taskaware.legacy.ledger import validate_task
    task = validate_task(task)
    if len(task['objects']) != 4 or set(o['object_id'] for o in task['objects']) != {'a', 'b', 'box', 'protected'}:
        raise ValueError('the offline fixture requires exactly a/b/box/protected')
    if task['protected_object_ids'] != ['protected']:
        raise ValueError('fixture protection contract differs')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    scene, mapping = _scene(task)
    root_instruction = task['instruction']
    memory = Memory(out / 'memory')
    agent = TaskAwareAgent(None, ToolRegistry(), memory, SkillStore(), ResourceManager(),
                           Limits(context_chars=100000))
    service = TaskService(agent)
    session = agent.create_session(root_instruction, 'offline-no-robot', 'synthetic619')
    place = SyntheticPlace()
    schema = {'type': 'object', 'properties': {k: {'type': 'string', 'minLength': 1}
        for k in ('item_id', 'receptacle_id', 'scene_version')},
        'required': ['item_id', 'receptacle_id', 'scene_version'], 'additionalProperties': False}
    agent.registry.register(ToolSpec('fixture.place', Domain.EXECUTION,
        'Synthetic placement substitute; no hardware or effect claim', schema,
        {'type': 'object'}, '619.synthetic'), place)
    register_li_effect(service, allow_synthetic=True)
    items = tuple(mapping[g['object_id']] for g in task['goals'])
    box = mapping[task['goals'][0]['container_id']]
    agent.bind_task(session.id, scene, item_ids=items, receptacle_id=box,
        protected_object_ids=(mapping['protected'],), constraints=tuple(task['constraints']),
        execution_contracts=(ExecutionContract('fixture.place', 'place'),),
        verification_tools=('li.verify_effect',), terminal_predicate='inside')
    native_task = {'external_task_id': task['task_id'], 'instruction': root_instruction,
        'constraints': task['constraints'], 'scene_version': task['scene_version'],
        'synthetic': True, 'visit_id': 'root-fixture-visit', 'round_id': 'root-fixture-round',
        'goals': [{'goal_id': g['goal_id'], 'object_id': mapping[g['object_id']],
                   'container_id': mapping[g['container_id']], 'predicate': 'in',
                   'subject': f"in({mapping[g['object_id']]},{mapping[g['container_id']]})"}
                  for g in task['goals']]}
    binding = {'visit_id': native_task['visit_id'], 'round_id': native_task['round_id'],
        'li_visit': 1, 'map_version': task['scene_version'], 'coordinate_frame': 'world',
        'length_unit': 'm', 'identity_scope': 'frame_local',
        'item_roles': {g['li_item']: g['goal_id'] for g in task['goals']},
        'tolerance': {'boundary_band_m': 0.02, 'inside_margin_m': 0.005,
                      'coverage_required': 1.0, 'basis': 'Authored engineering fixture; not robot tolerance'}}
    consumer = ChenT05Consumer(service, session.id, native_task, binding,
        observation_sources=('fixture.chen_envelope',), allow_synthetic=True)
    rejections, receipts = [], []

    async def dispatch(decision):
        return await agent._dispatch(session, decision, 0, 20)

    async def denied(label, decision):
        before = len(place.calls)
        try:
            await dispatch(decision)
        except ToolError as error:
            if len(place.calls) != before:
                raise AssertionError('denied action reached execution provider')
            rejections.append({'case': label, 'error': error.to_dict()})
        else:
            raise AssertionError('expected policy rejection: ' + label)

    def place_decision(item, revision):
        return Decision('call_tool', {'name': 'fixture.place', 'arguments': {
            'item_id': item, 'receptacle_id': box, 'scene_version': revision}})

    try:
        query = scene.query_context({'target_id': items[0]}, scene.snapshot.region_id,
            100000, context_object_ids=tuple(mapping.values()))
        if query.selected_object_id != items[0] or set(o.object_id for o in query.objects) != set(mapping.values()):
            raise AssertionError('source-bound role query lost the declared context')
        context = agent.context.build(session, [])
        if context.data['working_memory_ledger']['protected_object_ids'] != [mapping['protected']]:
            raise AssertionError('mandatory root protection context missing')
        initial = scene.snapshot.map_version
        await denied('premature_finish', Decision('finish', {'outcome': 'success', 'message': 'unverified'}))
        await denied('protected_object', place_decision(mapping['protected'], initial))
        await denied('old_scene', place_decision(items[0], 'outdated-scene'))
        original_snapshot = scene.snapshot.model_dump(mode='json')
        for g in task['goals']:
            revision = agent.scenes[session.id].snapshot.map_version
            result = await dispatch(place_decision(mapping[g['object_id']], revision))
            if result['status'] != 'completed':
                raise AssertionError(result)
            if agent.ledgers[session.id].complete():
                raise AssertionError('execution completed incorrectly established task success')
            execution = memory.search(filters={'call_id': result['call_id'],
                'event_type': 'tool_finished', 'domain': Domain.EXECUTION}, limit=1)[0]
            case = _case(agent, session, consumer, g['goal_id'], execution)
            record = memory.write(Record(session_id=session.id, task_id=session.task_id,
                source='fixture.chen_envelope', domain=Domain.STATE,
                event_type='tool_finished', status='completed',
                payload={'result': {'payload': {'raw': {'li_effect_case': case}}}},
                resource_refs=tuple(ResourceRef(**r) for r in case['resources'])))
            raw = deepcopy(record.payload)
            receipt = await consumer.consume(g['goal_id'], record.id, expected_revision=revision,
                retain_effect_ids=tuple(r['verification_record_id'] for r in receipts))
            if receipt['conclusion'] != 'success' or receipt['scene_after'] == revision:
                raise AssertionError(receipt)
            if memory.evidence(record.id).payload != raw:
                raise AssertionError('original envelope was changed')
            receipts.append(receipt)
        if not agent.ledgers[session.id].complete() or scene.snapshot.model_dump(mode='json') != original_snapshot:
            raise AssertionError('task completion or immutable parent check failed')
        finish = await dispatch(Decision('finish', {'outcome': 'success', 'message': 'synthetic only',
            'evidence_ids': [r['verification_record_id'] for r in receipts]}))
        summary = {'engineering_only': True, 'physical_result': False, 'real_model_calls': 0,
            'dispatch_origin': 'explicit offline harness via TaskAwareAgent._dispatch',
            'chen_host_called_by_this_function': False, 'original_chen_task': task,
            'root_task': native_task, 'root_object_id_map': mapping,
            'visit_mapping': 'Chen per-goal visit 1/2 is not reused; root fixture has one explicit li_visit=1',
            'binding': binding, 'rejections': rejections, 'receipts': receipts,
            'synthetic_provider_calls': place.calls, 'final_ledger': agent.ledgers[session.id].view(),
            'final_status': session.status, 'finish_result': finish,
            'scene_relations': [asdict(r) for r in agent.scenes[session.id].relations],
            'selected_context': json.loads(query.context_json())}
        (out / 'root_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        return summary
    finally:
        await service.close()
