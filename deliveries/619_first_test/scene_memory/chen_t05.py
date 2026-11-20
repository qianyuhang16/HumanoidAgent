"""Port of Chen's finite T05 caller checks onto this project's native consumer.

The received t05_li/host_contracts slice supplies the visit/attempt/role/context
checks below. It does not replace Chen's Planning or create another executor.
Original tool records and proposition subjects remain separately traceable.
The v4 source/current revision separation reuses the native verified scene chain;
it does not import Chen's second ledger or infer a scene from serialized state.
"""
from copy import deepcopy
from dataclasses import asdict
import json,time

from primitive_vla.contracts import Domain, Record
from primitive_vla.planning import Decision
from .host_bridge import publish_scene_evolution
from .task_effect.binding import bind_case


def check_context(actual,expected):
    """Chen v3 host_contracts.check_context: exact type and value, not coercion."""
    action=actual.get('action') if isinstance(actual,dict) else None
    if not isinstance(action,dict):raise ValueError('Li case lacks task_context.action')
    for key,value in expected.items():
        if key=='action':
            for field,wanted in value.items():
                got=action.get(field)
                if type(got) is not type(wanted) or got!=wanted:
                    raise ValueError('Li action differs from actual call: '+field)
        elif type(actual.get(key)) is not type(value) or actual.get(key)!=value:
            raise ValueError('Li context differs from host binding: '+key)


def _source_scene(agent,session_id,state):
    """Resolve original input through actual same-task scene proofs, never an ID alone."""
    from .task_effect.evolution import validate_child
    session=agent.sessions[session_id]
    scene=agent.scenes[session_id]
    if scene.snapshot.map_version!=state.scene_version:
        raise ValueError('current scene head differs from ledger')
    for entry in reversed(state.scene_transitions):
        transition=validate_child(scene,session_id=session.id,task_id=session.task_id,
            revision=scene.snapshot.map_version)
        expected=json.loads(json.dumps(asdict(transition)))
        if ({k:v for k,v in entry.items() if k!='retained_effect_ids'}!=expected
                or scene._native_memory.directory.resolve()!=agent.memory.directory.resolve()):
            raise ValueError('scene chain differs from same-store ledger history')
        scene=scene._parent_scene
    if scene.snapshot.schema_version=='task-scene.v1':
        raise ValueError('ledger lacks the complete native scene ancestry')
    return scene


class ChenT05Consumer:
    """Consume a received T05 observation using one TaskAwareAgent/scene head.

    Host configuration is explicit. The caller never chooses a goal or action,
    changes an observation's fields, calls a model, or operates a platform.
    """
    def __init__(self,service,session_id,task,binding,*,observation_sources,
                 verification_tool='li.verify_effect',allow_synthetic=False):
        self.service,self.agent,self.session_id=service,service.agent,session_id
        self.task,self.binding=deepcopy(task),deepcopy(binding)
        self.verification_tool=verification_tool
        if type(allow_synthetic) is not bool:raise ValueError('explicit synthetic gate required')
        self.allow_synthetic=allow_synthetic
        self.observation_sources=tuple(observation_sources)
        if not self.observation_sources or any(type(v) is not str or not v.strip() for v in self.observation_sources):
            raise ValueError('host observation sources required')
        state=self.agent.ledgers[session_id]._current
        source=_source_scene(self.agent,session_id,state)
        if task['instruction']!=state.instruction or task['scene_version']!=source.snapshot.map_version:
            raise ValueError('original task instruction/scene differs')
        if tuple(task['constraints'])!=state.constraints:
            raise ValueError('original task constraints differ')
        if type(task.get('synthetic')) is not bool or task['synthetic']!=allow_synthetic:
            raise ValueError('explicit task provenance differs')
        keys={'visit_id','round_id','li_visit','map_version','coordinate_frame','length_unit','identity_scope','item_roles','tolerance'}
        if set(binding)!=keys or binding['visit_id']!=task['visit_id'] or binding['round_id']!=task['round_id']:
            raise ValueError('Chen visit/round binding differs')
        if type(binding['li_visit']) is not int or binding['li_visit']<1:raise ValueError('host-declared integer visit required')
        goals={g['goal_id']:g for g in task['goals']}
        if (len(task['goals'])!=2 or len(goals)!=2 or set(binding['item_roles'])!={'甲','乙'}
                or sorted(binding['item_roles'].values())!=sorted(goals)):
            raise ValueError('two host-declared T05 roles required')
        if any(g['predicate']!='in' or g['subject']!=f"in({g['object_id']},{g['container_id']})" for g in goals.values()):
            raise ValueError('Chen T05 proposition differs from mapped objects')
        actual={(g.item_id,g.receptacle_id) for g in state.goals}
        if {(g['object_id'],g['container_id']) for g in goals.values()}!=actual:
            raise ValueError('Chen role mapping differs from the actual task objects')
        if any(g.terminal_predicate!='inside' for g in state.goals):
            raise ValueError('T05 must declare its inside terminal predicate')
        scene=self.agent.scenes[session_id]
        if binding['map_version']!=(state.base_map_version or state.scene_version):raise ValueError('map basis differs')
        if any(binding[k]!=getattr(scene.snapshot,k) for k in ('coordinate_frame','length_unit','identity_scope')):
            raise ValueError('coordinate/unit/identity basis differs')
        self.goals=goals

    async def consume(self,goal_id,observation_record_id,*,expected_revision,provider=None,retain_effect_ids=()):
        agent=self.agent;session=agent.sessions[self.session_id];ledger=agent.ledgers[self.session_id]
        scene=agent.scenes[self.session_id];state=ledger._current
        if expected_revision!=state.scene_version or scene.snapshot.map_version!=expected_revision:
            raise ValueError('stale caller scene revision')
        host_goal=self.goals[goal_id]
        goal=next((g for g in ledger.active_goals() if g.item_id==host_goal['object_id']),None)
        if goal is None or goal.receptacle_id!=host_goal['container_id'] or goal.execution_action!='place':
            raise ValueError('latest active T05 placement required')
        executions=agent.memory.search(filters={'session_id':session.id,'task_id':session.task_id,
            'call_id':goal.execution_call_id,'domain':Domain.EXECUTION,'event_type':'tool_finished','status':'completed'},limit=1)
        if not executions:raise ValueError('original completed placement missing')
        execution=executions[0]
        if execution.source not in agent.execution_contracts[session.id]:raise ValueError('unbound placement source')
        observation=agent.memory.evidence(observation_record_id);now=time.time()
        if ((observation.session_id,observation.task_id)!=(session.id,session.task_id)
                or observation.domain!=Domain.STATE or observation.source not in self.observation_sources
                or not observation.resource_refs or not execution.timestamp<observation.timestamp<=now
                or now-observation.timestamp>300 or observation.expires_at is not None and observation.expires_at<=now):
            raise ValueError('current same-task Chen observation required')
        wrapped=observation.event_type=='tool_finished'
        if wrapped:
            if observation.status!='completed':raise ValueError('observation tool not completed')
            raw=observation.payload['result']['payload']['raw'];case=raw['li_effect_case']
        elif observation.event_type=='observation':case=observation.payload['li_effect_case']
        else:raise ValueError('unsupported observation envelope')
        attempts=list(dict.fromkeys(e.call_id for e in state.executions if e.item_id==goal.item_id
            and e.action=='place' and e.call_id is not None))
        expected={'task_id':session.task_id,'session_id':session.id,'scene_version':expected_revision,
            'map_version':self.binding['map_version'],'coordinate_frame':self.binding['coordinate_frame'],
            'length_unit':self.binding['length_unit'],'identity_scope':self.binding['identity_scope'],
            'goal':session.goal,'box_object_id':goal.receptacle_id,
            'items':{role:self.goals[g]['object_id'] for role,g in self.binding['item_roles'].items()},
            'action':{'call_id':execution.call_id,'visit':self.binding['li_visit'],
                'attempt':attempts.index(execution.call_id)+1,'action':'place',
                'item':next(role for role,g in self.binding['item_roles'].items() if g==goal_id),
                'object_id':goal.item_id,'end_time':execution.timestamp,'tool_status':'completed'},
            'tolerance':self.binding['tolerance']}
        check_context(case['task_context'],expected)
        bound=bind_case(case)
        if bound.origin!=('synthetic' if self.allow_synthetic else 'real'):
            raise ValueError('case provenance differs from host')
        if bound.unpaired or bound.dropped_preview:raise ValueError('unpaired/preview data cannot be consumed')
        if {r.id:asdict(r) for r in observation.resource_refs}!=bound.resources:
            raise ValueError('Chen case resources differ from original observation')
        if any(not execution.timestamp<o.captured_at<=observation.timestamp or now-o.captured_at>300 for o in bound.observations):
            raise ValueError('capture must follow actual placement and precede original receipt')
        case_record=observation
        if wrapped:
            # Preserve raw case fields. Relocation of the envelope is a new,
            # source-linked native record, never an in-place evidence rewrite.
            remaining=min(60.,300-(now-min(o.captured_at for o in bound.observations)))
            if observation.expires_at is not None:remaining=min(remaining,observation.expires_at-now)
            if remaining<=0:raise ValueError('original observation expired')
            case_record=await agent.observations.publish(session_id=session.id,task_id=session.task_id,
                source='chen.t05.normalized_observation',payload={'scene_version':expected_revision,
                    'related_call_id':execution.call_id,'li_effect_case':deepcopy(case),
                    'chen_source_record_id':observation.id},resource_refs=observation.resource_refs,ttl=remaining)
        result=await agent._dispatch(session,Decision('call_tool',{'name':self.verification_tool,
            'arguments':{'item_id':goal.item_id,'receptacle_id':goal.receptacle_id,'case_record_id':case_record.id}}),0,20)
        receipt={'session_id':session.id,'task_id':session.task_id,'original_task':deepcopy(self.task),
            'instruction':state.instruction,'constraints':list(state.constraints),
            'protected_object_ids':list(state.protected_object_ids),
            'visit_id':self.binding['visit_id'],'round_id':self.binding['round_id'],
            'goal_id':goal_id,'chen_subject':host_goal['subject'],'original_observation_id':observation.id,
            'case_record_id':case_record.id,'execution_record_id':execution.id,'native_status':result['status'],
            'scene_before':expected_revision,'scene_after':expected_revision,'synthetic':self.allow_synthetic,
            'physical_result':False}
        if result['status']!='completed':return self._save_receipt(receipt)
        verification=agent.memory.search(filters={'session_id':session.id,'task_id':session.task_id,
            'call_id':result['call_id'],'domain':Domain.VERIFICATION,'event_type':'verification','status':'completed'},limit=1)[0]
        native=agent.router.calls[result['call_id']].result
        receipt.update(verification_record_id=verification.id,li_subject=native.payload['li_subject'],
            object_subject=native.verification.subject,conclusion=native.verification.conclusion)
        if native.verification.conclusion=='success':
            child=scene.evolve_verified(agent.memory,session_id=session.id,task_id=session.task_id,
                event_id='chen-t05-'+verification.id,related_call_id=execution.call_id,
                execution_record_id=execution.id,observation_record_id=case_record.id,verification_record_id=verification.id,
                expected_parent=expected_revision,observation_sources=(case_record.source,),
                verification_sources=(self.verification_tool,),execution_sources=(execution.source,),allow_synthetic=self.allow_synthetic)
            publish_scene_evolution(self.service,session.id,scene,child,provider=provider,retain_effect_ids=retain_effect_ids)
            receipt['scene_after']=child.snapshot.map_version
        return self._save_receipt(receipt)

    def _save_receipt(self,receipt):
        record=self.agent.memory.write(Record(session_id=self.session_id,task_id=receipt['task_id'],
            source='chen.t05.consumer',domain=Domain.PLANNING,event_type='consumer_receipt',
            status=receipt['native_status'],payload=receipt))
        return dict(receipt,receipt_record_id=record.id)
