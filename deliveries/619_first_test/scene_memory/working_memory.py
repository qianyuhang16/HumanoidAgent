"""Persistent task view and independent completion policy on the read-only host."""
from dataclasses import asdict, dataclass, replace
import json
import time
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse,unquote
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from primitive_vla.contracts import Domain, Failure, Record, ToolError
from primitive_vla.planning.base import PlanningContext
from primitive_vla.runtime.agent import Agent

SOURCE='go2.working_memory'
ExecutionAction=Literal['pick','place','recover','pour','wipe','push','navigate']
EXECUTION_ACTIONS=frozenset(('pick','place','recover','pour','wipe','push','navigate'))
EffectPredicate=Literal['inside','placed','poured','clean','holding','gripper_empty','passage_available']
_ACTION_TERMINALS={'pour':'poured','wipe':'clean'}

class GoalState(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    item_id:str=Field(min_length=1)
    receptacle_id:str|None=Field(min_length=1)
    status:Literal['pending','in_progress','failed_needs_recovery','verified_done']='pending'
    execution_call_id:str|None=None
    verification_record_id:str|None=None
    required_action:ExecutionAction|None=None
    execution_action:ExecutionAction|None=None
    verification_tool:str|None=None
    terminal_predicate:EffectPredicate|None=None

    @model_validator(mode='after')
    def optional_wipe_region(self):
        if self.receptacle_id is None and (self.required_action!='wipe' or self.terminal_predicate!='clean'):
            raise ValueError('only an explicit wipe/clean goal may omit its target region')
        return self

class ExecutionState(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    item_id:str
    call_id:str|None
    action:ExecutionAction|None
    status:Literal['started','running','completed','failed','cancelled','stop_unconfirmed']
    scene_version:str
    scope:str='root'

class EffectState(BaseModel):
    """A scoped proposition admitted by the host, never an action recommendation."""
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,allow_inf_nan=False)
    predicate:EffectPredicate
    subject_id:str=Field(min_length=1)
    receptacle_id:str|None=None
    value:bool|None
    related_call_id:str=Field(min_length=1)
    verification_record_id:str=Field(min_length=1)
    observation_record_ids:tuple[str,...]=()
    scene_version:str=Field(min_length=1)
    source_refs:tuple[str,...]=Field(min_length=1)
    observed_at:float|None=None
    validity:Literal['valid','stale','conflict','unknown']='valid'
    scope:str='root'
    admission:Literal['predicate','legacy-terminal']='predicate'
    source_scene_version:str|None=None

class _EffectClaim(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,allow_inf_nan=False)
    predicate:EffectPredicate
    subject_id:str=Field(min_length=1)
    receptacle_id:str|None=None
    value:bool|None
    observation_record_ids:tuple[str,...]=Field(min_length=1)

class PrerequisiteState(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    subtask_id:str=Field(min_length=1)
    instruction:str=Field(min_length=1)
    trigger_record_id:str=Field(min_length=1)
    source_refs:tuple[str,...]
    goals:tuple[GoalState,...]
    status:Literal['active','awaiting_observation','verified_done']='active'
    post_observation_source:str|None=None
    completion_observation_record_id:str|None=None

class LedgerState(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    version:Literal['task-working-memory.v1','task-working-memory.v2','task-working-memory.v3']='task-working-memory.v3'
    session_id:str
    task_id:str
    instruction:str
    constraints:tuple[str,...]
    protected_object_ids:tuple[str,...]
    scene_version:str
    source_refs:tuple[str,...]
    goals:tuple[GoalState,...]
    subtasks:tuple[PrerequisiteState,...]=()
    active_subtask_id:str|None=None
    suspended_goal_ids:tuple[str,...]=()
    current_goal_item_id:str|None=None
    executions:tuple[ExecutionState,...]=()
    effects:tuple[EffectState,...]=()
    base_map_version:str|None=None
    scene_transitions:tuple[dict,...]=()

def _state(data):
    return LedgerState.model_validate_json(json.dumps(data,ensure_ascii=False,allow_nan=False))

def _check_resource(memory,ref,*,synthetic):
    json.dumps(ref,allow_nan=False)
    if asdict(memory.get_resource(ref['id']))!=ref:raise ValueError('registered resource differs from original proof')
    uri=ref['uri'];parsed=urlparse(uri)
    if parsed.scheme in ('fixture','synthetic'):
        if not synthetic:raise ValueError('fixture provenance must be explicit')
    elif parsed.scheme=='blob':
        raw=memory.read_resource(ref['id'])
        if sha256(raw).hexdigest()!=uri.removeprefix('blob:'):raise ValueError('effect resource bytes changed')
        digest=ref.get('metadata',{}).get('sha256')
        if digest is not None and sha256(raw).hexdigest()!=digest:raise ValueError('effect resource SHA differs')
    elif parsed.scheme=='file' and not parsed.netloc:
        name=unquote(parsed.path)
        if len(name)>2 and name[0]=='/' and name[2]==':':name=name[1:]
        path=Path(name)
        if not path.is_absolute() or str(path).startswith('\\\\'):raise ValueError('local resource required')
        if sha256(path.read_bytes()).hexdigest()!=ref.get('metadata',{}).get('sha256'):
            raise ValueError('effect resource bytes changed')
    else:raise ValueError('no unresolved external effect resources')

@dataclass(frozen=True)
class ExecutionContract:
    tool_name:str
    action:ExecutionAction
    prerequisite_only:bool=False

class WorkingMemoryLedger:
    """Persist through the host Memory journal; no second executor or service."""
    def __init__(self,memory,expected:LedgerState):
        self.memory=memory
        previous=memory.search(filters={'session_id':expected.session_id,'task_id':expected.task_id,
                                       'source':SOURCE},limit=1)
        self._current=_state(previous[0].payload) if previous else expected
        identity=lambda s:(s.session_id,s.task_id,s.instruction,s.constraints,s.protected_object_ids,
                           s.scene_version,s.source_refs,tuple((g.item_id,g.receptacle_id,
                               g.terminal_predicate or 'placed',g.required_action or 'place') for g in s.goals))
        if identity(self._current)!=identity(expected):raise ValueError('working memory task/source binding changed')
        if not previous:self._save(expected)

    def _save(self,state):
        goals=next(s.goals for s in state.subtasks if s.subtask_id==state.active_subtask_id) if state.active_subtask_id else state.goals
        state=state.model_copy(update={'version':'task-working-memory.v3',
            'current_goal_item_id':next((g.item_id for g in goals if g.status!='verified_done'),None)})
        checked=_state(state.model_dump(mode='json'))
        self.memory.write(Record(checked.session_id,checked.task_id,SOURCE,Domain.PLANNING,
            'task_working_memory','recorded',checked.model_dump(mode='json'),lifetime='long'))
        self._current=checked

    def view(self):return self._current.model_dump(mode='json')

    def context_view(self):
        """Keep goals/unknowns/provenance, omit repeated historical entries only."""
        value=self.view();latest={}
        for entry in value['executions']:
            latest[entry['item_id']]=entry
        value['executions']=list(latest.values())
        current={}
        for effect in value['effects']:
            # Native record anchors resolve full resource/lineage references via
            # evidence_get; keep those bytes in the journal, not repeated here.
            effect['source_refs']=[effect['verification_record_id']]
            for key in ('observed_at','source_scene_version'):
                if effect[key] is None:effect.pop(key)
            if effect['admission']=='predicate':effect.pop('admission')
            current[(effect['predicate'],effect['subject_id'],effect['receptacle_id'])]=effect
        value['effects']=list(current.values())
        # Complete immutable event history remains in the ledger's native journal.
        if value['scene_transitions']:value['scene_transitions']=value['scene_transitions'][-1:]
        return value

    def active_goals(self):
        return (next(s.goals for s in self._current.subtasks if s.subtask_id==self._current.active_subtask_id)
                if self._current.active_subtask_id else self._current.goals)

    def insert_prerequisite(self,subtask):
        if (self._current.active_subtask_id or len(self._current.subtasks)>=8
                or any(s.subtask_id==subtask.subtask_id or s.trigger_record_id==subtask.trigger_record_id
                       for s in self._current.subtasks)):
            raise ValueError('one active prerequisite; duplicate/reused trigger or eight-subtask budget exhausted')
        if self.complete():raise ValueError('original task already complete')
        self._save(self._current.model_copy(update={'version':'task-working-memory.v3',
            'subtasks':self._current.subtasks+(subtask,),'active_subtask_id':subtask.subtask_id,
            'suspended_goal_ids':tuple(g.item_id for g in self._current.goals if g.status!='verified_done')}))

    def _update_active_goals(self,goals,*,state=None):
        current=state or self._current
        if current.active_subtask_id:
            done=all(g.status=='verified_done' and g.verification_record_id for g in goals)
            active=next(s for s in current.subtasks if s.subtask_id==current.active_subtask_id)
            waiting=done and active.post_observation_source is not None
            subtasks=tuple(s.model_copy(update={'goals':goals,
                'status':'awaiting_observation' if waiting else 'verified_done' if done else 'active'})
                if s.subtask_id==current.active_subtask_id else s for s in current.subtasks)
            current=current.model_copy(update={'subtasks':subtasks,
                'active_subtask_id':None if done and not waiting else current.active_subtask_id,
                'suspended_goal_ids':() if done and not waiting else current.suspended_goal_ids})
        else:current=current.model_copy(update={'goals':goals})
        self._save(current)

    def execution(self,item_id,call_id,failed,action=None,*,status=None):
        if action is not None and action not in EXECUTION_ACTIONS:raise ValueError('unknown execution action')
        scope=self._current.active_subtask_id or 'root'
        entry=ExecutionState(item_id=item_id,call_id=call_id,action=action,
            status=status or ('started' if call_id is None else 'failed' if failed else 'completed'),
            scene_version=self._current.scene_version,scope=scope)
        effects=tuple(e.model_copy(update={'validity':'stale'})
            if e.validity in ('valid','unknown','conflict') and (e.subject_id==item_id or e.predicate in ('holding','gripper_empty'))
            else e for e in self._current.effects)
        state=self._current.model_copy(update={'executions':self._current.executions+(entry,),'effects':effects})
        goals=tuple(g.model_copy(update={'status':'failed_needs_recovery' if failed else 'in_progress',
            'execution_call_id':call_id,'execution_action':action,'verification_record_id':None}) if g.item_id==item_id else g
            for g in self.active_goals())
        self._update_active_goals(goals,state=state)

    def verification(self,record,item_id):
        """Legacy terminal proof only; an unrelated last action cannot complete it."""
        goal=next((g for g in self.active_goals() if g.item_id==item_id),None)
        if (goal and goal.terminal_predicate=='placed' and goal.execution_action==(goal.required_action or 'place')):
            return self.record_effect(EffectState(predicate='placed',subject_id=item_id,receptacle_id=goal.receptacle_id,
                value=True,related_call_id=goal.execution_call_id,verification_record_id=record.id,
                scene_version=self._current.scene_version,source_refs=(record.id,),
                scope=self._current.active_subtask_id or 'root',admission='legacy-terminal'))
        goals=tuple(g.model_copy(update={'status':'verified_done','verification_record_id':record.id})
                    if (g.item_id==item_id and g.terminal_predicate is None
                        and g.execution_action==(g.required_action or 'place')) else g for g in self.active_goals())
        self._update_active_goals(goals)

    def record_effect(self,effect:EffectState):
        """Consume a host-admitted proposition, not arbitrary Tool.completed."""
        checked=EffectState.model_validate(effect.model_dump(mode='python'))
        if checked.scene_version!=self._current.scene_version:raise ValueError('effect scene differs')
        key=lambda e:(e.predicate,e.subject_id,e.receptacle_id)
        existing=self._current.effects
        if any(e.verification_record_id==checked.verification_record_id and key(e)==key(checked) for e in existing):
            return False
        conflict=any(key(e)==key(checked) and e.validity in ('valid','conflict')
            and e.related_call_id==checked.related_call_id and e.value is not None and checked.value is not None
            and e.value!=checked.value for e in existing)
        if checked.value is None:checked=checked.model_copy(update={'validity':'unknown'})
        if conflict:checked=checked.model_copy(update={'validity':'conflict'})
        effects=tuple(e.model_copy(update={'validity':'conflict' if conflict else 'stale'})
            if key(e)==key(checked) and e.validity in ('valid','conflict') and (conflict or checked.value is None)
            else e for e in existing)+(checked,)
        state=self._current.model_copy(update={'effects':effects})
        goals=[]
        for g in self.active_goals():
            matches=(checked.subject_id==g.item_id and checked.receptacle_id==g.receptacle_id
                and checked.predicate==(g.terminal_predicate or 'placed')
                and checked.related_call_id==g.execution_call_id
                and g.execution_action==(g.required_action or 'place'))
            # Legacy place contracts can accept a declared placement proposition.
            matches=matches or (g.terminal_predicate is None and checked.predicate=='placed'
                and checked.subject_id==g.item_id and checked.receptacle_id==g.receptacle_id
                and checked.related_call_id==g.execution_call_id and g.execution_action==(g.required_action or 'place'))
            if matches:
                done=checked.validity=='valid' and checked.value is True
                g=g.model_copy(update={'status':'verified_done' if done else 'in_progress',
                    'verification_record_id':checked.verification_record_id if done else None})
            goals.append(g)
        self._update_active_goals(tuple(goals),state=state)
        return True

    def complete(self):
        return (not self._current.active_subtask_id and bool(self._current.goals)
                and all(s.status=='verified_done' for s in self._current.subtasks)
                and all(g.status=='verified_done' and g.verification_record_id
                    and g.execution_action==(g.required_action or 'place')
                    and (g.required_action not in _ACTION_TERMINALS
                        or g.terminal_predicate==_ACTION_TERMINALS[g.required_action])
                    and (g.terminal_predicate is None or any(e.predicate==g.terminal_predicate
                        and e.subject_id==g.item_id and e.receptacle_id==g.receptacle_id and e.value is True
                        and e.validity=='valid' and e.scene_version==self._current.scene_version
                        and e.verification_record_id==g.verification_record_id for e in self._current.effects))
                    for g in self._current.goals))

    def prepare_scene_advance(self,child,*,expected_parent,retain_effect_ids=()):
        """Validate one real B-owned child, producing a detached pending ledger state."""
        from .task_effect.evolution import validate_child
        from scene_memory import SceneMemory
        state=self._current
        if not isinstance(child,SceneMemory):raise ValueError('genuine derived SceneMemory required, not a version string')
        if expected_parent!=state.scene_version:raise ValueError('ledger parent revision changed')
        if state.active_subtask_id:raise ValueError('pending prerequisite authorization must be resolved before scene publication')
        transition=validate_child(child,session_id=state.session_id,task_id=state.task_id,revision=child.snapshot.map_version)
        if (transition.parent_version!=expected_parent or child._parent_scene.snapshot.map_version!=expected_parent
                or transition.base_map_version!=(state.base_map_version or expected_parent)
                or child._native_memory.directory.resolve()!=self.memory.directory.resolve()):
            raise ValueError('ledger requires a direct same-store/base-map parent transition')
        if any(t['event_id']==transition.event_id for t in state.scene_transitions):raise ValueError('scene event already applied')
        latest=[e for e in state.executions if e.item_id in transition.updated_object_ids and e.call_id is not None]
        if (not latest or latest[-1].call_id!=transition.related_call_id or latest[-1].status!='completed'
                or latest[-1].scene_version!=expected_parent or latest[-1].action!='place'):
            raise ValueError('scene transition is not the ledger current place execution')
        all_goals=state.goals+tuple(g for subtask in state.subtasks for g in subtask.goals)
        if any(i not in child.objects for g in all_goals for i in (g.item_id,g.receptacle_id)):
            raise ValueError('transition lost original role objects')
        if any(i not in child.objects for i in state.protected_object_ids):raise ValueError('transition lost protection')
        for identifier in (transition.execution_record_id,transition.observation_record_id,transition.verification_record_id):
            r=self.memory.evidence(identifier)
            if (r.session_id,r.task_id)!=(state.session_id,state.task_id):raise ValueError('foreign transition source')
        if type(retain_effect_ids) is not tuple or len(set(retain_effect_ids))!=len(retain_effect_ids):
            raise ValueError('explicit unique retained Verification IDs required')
        requested=set(retain_effect_ids);retained=set();effects=[]
        for effect in state.effects:
            covered=(effect.predicate=='inside' and effect.subject_id in transition.updated_object_ids
                and effect.verification_record_id==transition.verification_record_id
                and effect.related_call_id==transition.related_call_id and effect.admission=='predicate')
            carry=(effect.verification_record_id in requested and effect.predicate in ('inside','placed')
                and effect.subject_id not in transition.updated_object_ids and effect.receptacle_id not in transition.updated_object_ids
                and effect.admission=='predicate')
            keep=(covered or carry) and effect.validity=='valid' and effect.scene_version==expected_parent
            if keep:self._revalidate_effect(effect)
            if carry and keep:retained.add(effect.verification_record_id)
            effects.append(effect.model_copy(update={'scene_version':transition.scene_version,
                'source_scene_version':effect.source_scene_version or effect.scene_version}) if keep else
                effect.model_copy(update={'validity':'stale'}) if effect.validity in ('valid','unknown','conflict') else effect)
        if requested!=retained:raise ValueError('retention not covered by unaffected predicate evidence')
        def revise(goal):
            if goal.status!='verified_done':return goal
            valid=any(e.verification_record_id==goal.verification_record_id and e.validity=='valid'
                and e.scene_version==transition.scene_version for e in effects)
            return goal if valid else goal.model_copy(update={'status':'in_progress','verification_record_id':None})
        entry={**asdict(transition),'retained_effect_ids':list(retain_effect_ids)}
        refs=tuple(sorted(set(state.source_refs)|{ref for g in state.goals for i in (g.item_id,g.receptacle_id)
            for ref in child.describe(i).evidence_refs}|{ref for i in state.protected_object_ids for ref in child.describe(i).evidence_refs}))
        return _state(state.model_copy(update={'scene_version':transition.scene_version,'source_refs':refs,
            'base_map_version':transition.base_map_version,'effects':tuple(effects),
            'goals':tuple(revise(g) for g in state.goals),
            # Closed prerequisite goals remain historical achievements; their
            # effects above can still become stale and grant no new authorization.
            'subtasks':state.subtasks,
            'scene_transitions':state.scene_transitions+(entry,)}).model_dump(mode='json'))

    def _revalidate_effect(self,effect):
        """An explicit retention request never extends expired or changed proof."""
        state=self._current;now=time.time()
        record=self.memory.evidence(effect.verification_record_id)
        records=[record]+[self.memory.evidence(i) for i in effect.observation_record_ids]
        if not effect.observation_record_ids:raise ValueError('effect retention requires original observations')
        for original in records:
            if ((original.session_id,original.task_id)!=(state.session_id,state.task_id)
                    or original.expires_at is not None and original.expires_at<=now
                    or not 0<=now-original.timestamp<=300):
                raise ValueError('retained effect proof expired or belongs to another task')
        payload=record.payload['result']['payload'];verification=record.payload['result']['verification']
        claim=_EffectClaim.model_validate_json(json.dumps(payload['effect'],allow_nan=False))
        origin=effect.source_scene_version or effect.scene_version
        if (record.domain!=Domain.VERIFICATION or record.status!='completed'
                or verification['subject']!=effect.subject_id or verification['related_call_id']!=effect.related_call_id
                or verification['conclusion']!='success' or payload.get('scene_version')!=origin
                or (claim.predicate,claim.subject_id,claim.receptacle_id,claim.value,claim.observation_record_ids)!=
                   (effect.predicate,effect.subject_id,effect.receptacle_id,effect.value,effect.observation_record_ids)):
            raise ValueError('retained proposition differs from original Verification')
        proof={ref['id']:ref for ref in verification['evidence']}
        native={ref.id:asdict(ref) for ref in record.resource_refs}
        if not proof or any(native.get(k)!=v for k,v in proof.items()):raise ValueError('retained Verification evidence differs')
        for ref in proof.values():_check_resource(self.memory,ref,synthetic=payload.get('synthetic_fixture') is True or payload.get('origin')=='synthetic')
        executions=self.memory.search(filters={'session_id':state.session_id,'task_id':state.task_id,
            'call_id':effect.related_call_id,'domain':Domain.EXECUTION},limit=100)
        execution=next((r for r in executions if r.event_type=='tool_finished' and r.status=='completed'),None)
        if execution is None or execution.timestamp>record.timestamp:raise ValueError('retained execution proof missing')
        for obs in records[1:]:
            related=obs.payload.get('related_call_id')
            if 'li_effect_case' in obs.payload:
                from .task_effect import judge_case
                case=obs.payload['li_effect_case'];ctx=case['task_context'];related=ctx['action']['call_id']
                if (ctx['session_id']!=state.session_id or ctx['task_id']!=state.task_id
                        or ctx['scene_version']!=origin or payload.get('li_verdict')!=judge_case(case)
                        or ctx['map_version']!=(state.base_map_version or origin)
                        or effect.predicate!='inside' or effect.subject_id!=ctx['action']['object_id']
                        or effect.receptacle_id!=ctx['box_object_id']):
                    raise ValueError('retained Li observation differs')
            if (obs.domain!=Domain.STATE or obs.event_type!='observation' or obs.payload.get('scene_version')!=origin or related!=effect.related_call_id
                    or not execution.timestamp<obs.timestamp<=record.timestamp
                    or not {ref.id for ref in obs.resource_refs}&proof.keys()):
                raise ValueError('retained observation/call/source differs')
            if any(proof[ref.id]!=asdict(ref) for ref in obs.resource_refs if ref.id in proof):
                raise ValueError('retained observation resources changed')

    def advance_scene(self,child,*,expected_parent,retain_effect_ids=()):
        candidate=self.prepare_scene_advance(child,expected_parent=expected_parent,retain_effect_ids=retain_effect_ids)
        self._save(candidate)
        return child.transition

def attach_context_block(context: PlanningContext, key: str, value: dict) -> PlanningContext:
    """Keep native turns and images, and expose the task block in their transport."""
    data = {**context.data, key: value}
    messages = getattr(context, 'messages', ())
    if not messages:
        return replace(context, data=data)
    from primitive_vla.vlm import Message

    text = json.dumps({key: value}, ensure_ascii=False, allow_nan=False)
    return replace(context, data=data, messages=messages + ((Message('user', text), ()),))


class LedgerContextBuilder:
    def __init__(self,base,ledgers,max_chars=6400):
        self.base,self.ledgers,self.max_chars=base,ledgers,max_chars

    def build(self,session,active_calls):
        ledger=self.ledgers.get(session.id)
        if ledger is None:return self.base.build(session,active_calls)
        view=ledger.context_view() if hasattr(ledger,'context_view') else ledger.view()
        size=len(json.dumps(view,ensure_ascii=False))
        if size>self.max_chars:raise ValueError('working memory exceeds explicit character budget')
        # Reserve before building so mandatory ledger cannot silently evict goal/schema.
        from primitive_vla.planning.context import ContextBuilder
        overhead = 160 if 'messages' in PlanningContext.__dataclass_fields__ else 64
        base=ContextBuilder(self.base.memory,self.base.registry,self.base.skills,self.base.max_chars-size-overhead)
        context=base.build(session,active_calls)
        context=attach_context_block(context,'working_memory_ledger',view)
        if len(json.dumps(context.data,ensure_ascii=False))+len(json.dumps(context.action_schema,ensure_ascii=False))>self.base.max_chars:
            raise ValueError('context budget cannot retain working memory')
        return context

class TaskAwareAgent(Agent):
    """Project policy adapter; delegates scheduling, cancellation and tools upstream."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.ledgers={};self.execution_contracts={};self.verification_tools={};self.scenes={}
        self.context=LedgerContextBuilder(self.context,self.ledgers)

    def bind_prepared_request(self,session_id,request,scene,*,execution_contracts=(),verification_tools=()):
        """Consume the existing validated PlannerRequest and its source-bound intent roles."""
        from scene_memory.planner_bridge import PlannerRequest
        checked=PlannerRequest(request.instruction,request.context_json,request.available_skills)
        context=json.loads(checked.context_json);roles=context.get('operation_bindings')
        if not roles or not roles.get('destination_id'):raise ValueError('prepared operation and destination roles required')
        if checked.instruction!=self.sessions[session_id].goal:raise ValueError('original task instruction differs')
        if context['snapshot_metadata']['map_version']!=scene.snapshot.map_version:raise ValueError('scene version differs')
        for obj in context['objects']:
            if obj['object_id'] not in scene.objects or json.loads(json.dumps(asdict(scene.describe(obj['object_id']))))!=obj:
                raise ValueError('prepared object does not match current source Memory')
        protected=set(roles.get('protected_ids',()))
        protected.update(f['subject_id'] for obj in context['objects'] for f in obj['facts'] if f['kind']=='protection')
        constraints=tuple(dict.fromkeys([c['text'] for c in context.get('protection_constraints',())]
            +list(context.get('clarification',{}).get('constraints',()) if context.get('clarification') else ())))
        self.bind_task(session_id,scene,item_ids=roles['object_ids'],receptacle_id=roles['destination_id'],
            protected_object_ids=tuple(sorted(protected)),constraints=constraints,
            execution_contracts=execution_contracts,verification_tools=verification_tools)

    def bind_task(self,session_id,scene,*,item_ids,receptacle_id=None,protected_object_ids=(),constraints=(),
                  execution_contracts=(),verification_tools=(),terminal_predicate:EffectPredicate|None='placed',
                  required_action:Literal['place','pour','wipe']|None=None):
        session=self.sessions[session_id]
        inferred={'poured':'pour','clean':'wipe'}.get(terminal_predicate,'place')
        required_action=inferred if required_action is None else required_action
        if (required_action not in ('place','pour','wipe')
                or terminal_predicate in ('poured','clean') and required_action!=inferred
                or required_action in _ACTION_TERMINALS and terminal_predicate!=_ACTION_TERMINALS[required_action]
                or receptacle_id is None and required_action!='wipe'):
            raise ValueError('task action, terminal predicate and destination requirements differ')
        ids=tuple(item_ids);protected=tuple(protected_object_ids);constraints=tuple(constraints)
        if (not ids or len(set(ids))!=len(ids) or receptacle_id in ids or set(ids)&set(protected)
            or receptacle_id in protected or len(set(protected))!=len(protected)):
            raise ValueError('distinct operation, receptacle and protected roles required')
        all_ids=ids+((receptacle_id,) if receptacle_id is not None else ())+protected
        if any(i not in scene.objects for i in all_ids):raise ValueError('role outside source scene')
        if scene.snapshot.schema_version=='task-scene.v1':
            from .task_effect.evolution import validate_child
            validate_child(scene,session_id=session_id,task_id=session.task_id,revision=scene.snapshot.map_version)
        if any(type(c) is not str or not c.strip() or c not in session.goal for c in constraints):
            raise ValueError('constraints must retain original instruction text')
        refs=tuple(sorted({ref for i in all_ids for ref in scene.describe(i).evidence_refs}))
        expected=LedgerState(session_id=session.id,task_id=session.task_id,instruction=session.goal,
            constraints=constraints,protected_object_ids=protected,scene_version=scene.snapshot.map_version,
            base_map_version=getattr(scene.snapshot,'base_map_version',scene.snapshot.map_version),
            source_refs=refs,goals=tuple(GoalState(item_id=i,receptacle_id=receptacle_id,
                required_action=required_action,terminal_predicate=terminal_predicate) for i in ids))
        contracts={c.tool_name:c for c in execution_contracts}
        if len(contracts)!=len(execution_contracts):raise ValueError('duplicate execution contract')
        for name,c in contracts.items():
            if (c.action not in EXECUTION_ACTIONS or type(c.prerequisite_only) is not bool
                    or self.registry.get(name).spec.domain!=Domain.EXECUTION):
                raise ValueError('execution contract domain/action mismatch')
        for name in verification_tools:
            if self.registry.get(name).spec.domain!=Domain.VERIFICATION:raise ValueError('independent verification domain required')
        if required_action in _ACTION_TERMINALS and not any(c.action==required_action and not c.prerequisite_only for c in contracts.values()):
            raise ValueError('task terminal action requires a bound root execution contract')
        self.ledgers[session_id]=WorkingMemoryLedger(self.memory,expected)
        self.execution_contracts[session_id]=contracts
        self.verification_tools[session_id]=frozenset(verification_tools)
        self.scenes[session_id]=scene

    def insert_prerequisite(self,session_id,scene,*,subtask_id,instruction,trigger_record_id,goals,
                            post_observation_source=None):
        """A caller-selected policy consumes source-bound observation; never starts a tool."""
        ledger=self.ledgers[session_id];state=ledger._current;session=self.sessions[session_id]
        if session.status in ('completed','failed','stopped') or self.router.active(session_id):
            raise ValueError('cannot interrupt a terminal task or unconfirmed active control')
        trigger=self.memory.evidence(trigger_record_id)
        if (trigger.session_id!=session_id or trigger.task_id!=session.task_id or trigger.domain!=Domain.STATE
                or trigger.status not in ('observed','completed') or not trigger.resource_refs
                or trigger.payload.get('scene_version')!=state.scene_version
                or scene.snapshot.map_version!=state.scene_version
                or trigger.expires_at is not None and trigger.expires_at<=time.time()):
            raise ValueError('prerequisite requires current same-task scene observation and evidence')
        goals=tuple(GoalState.model_validate(g.model_dump()) for g in goals)
        item_ids=tuple(g.item_id for g in goals)
        actions={c.action for c in self.execution_contracts[session_id].values()}
        if (not goals or len(set(item_ids))!=len(item_ids) or any(g.status!='pending' or g.execution_call_id
                or g.verification_record_id or g.execution_action or g.required_action not in actions
                or g.required_action in _ACTION_TERMINALS and g.terminal_predicate!=_ACTION_TERMINALS[g.required_action]
                or g.verification_tool not in self.verification_tools[session_id] for g in goals)):
            raise ValueError('fresh unique prerequisite goals require bound action and verifier')
        all_ids=tuple(i for g in goals for i in (g.item_id,g.receptacle_id) if i is not None)
        if (any(i not in scene.objects or i in state.protected_object_ids for i in all_ids)
                or any(g.receptacle_id in item_ids for g in goals)):
            raise ValueError('prerequisite roles outside source, protected, or conflicting')
        refs=tuple(sorted({ref for i in all_ids for ref in scene.describe(i).evidence_refs}))
        ledger.insert_prerequisite(PrerequisiteState(subtask_id=subtask_id,instruction=instruction,
            trigger_record_id=trigger_record_id,source_refs=refs,goals=goals,
            post_observation_source=post_observation_source))

    def confirm_prerequisite_observation(self,session_id,record_id):
        """Chen's new post-movement passage observation, after independent Verification."""
        ledger=self.ledgers[session_id];state=ledger._current
        active=next((s for s in state.subtasks if s.subtask_id==state.active_subtask_id),None)
        if active is None or active.status!='awaiting_observation':raise ValueError('no verified prerequisite awaiting observation')
        r=self.memory.evidence(record_id);s=self.sessions[session_id]
        last=max((self.memory.evidence(g.verification_record_id) for g in active.goals),key=lambda record:record.timestamp)
        if (r.session_id!=s.id or r.task_id!=s.task_id or r.domain!=Domain.STATE
                or r.source!=active.post_observation_source or not r.resource_refs
                or r.timestamp<=last.timestamp or r.expires_at is not None and r.expires_at<=time.time()
                or r.payload.get('scene_version')!=state.scene_version
                or r.payload.get('related_call_id') not in {g.execution_call_id for g in active.goals}
                or r.payload.get('passage_available') is not True):
            raise ValueError('new same-task/source/call passage evidence required')
        ledger._save(state.model_copy(update={'subtasks':tuple(child.model_copy(update={
            'status':'verified_done','completion_observation_record_id':r.id})
            if child.subtask_id==active.subtask_id else child for child in state.subtasks),
            'active_subtask_id':None,'suspended_goal_ids':()}))

    def record_effect(self,session_id,verification_record_id):
        """Admit one explicit predicate from native immutable Verification evidence.

        Resolve observation IDs here; model-inline statements and Tool.completed
        are not a source of facts. Legacy terminal verifiers use a separate path.
        """
        ledger=self.ledgers[session_id];state=ledger._current;session=self.sessions[session_id]
        record=self.memory.evidence(verification_record_id)
        if (record.session_id!=session_id or record.task_id!=session.task_id
                or record.domain!=Domain.VERIFICATION or record.status!='completed'
                or record.event_type!='verification' or record.source not in self.verification_tools[session_id]
                or record.expires_at is not None and record.expires_at<=time.time()):
            raise ValueError('current same-task native Verification record required')
        result=record.payload['result'];payload=result['payload'];verification=result['verification']
        claim=_EffectClaim.model_validate_json(json.dumps(payload['effect'],allow_nan=False))
        if (payload.get('scene_version')!=state.scene_version or verification['subject']!=claim.subject_id
                or not verification['evidence'] or verification['conclusion'] not in ('success','uncertain','failure')
                or verification['conclusion']=='uncertain' and claim.value is not None
                or verification['conclusion']=='failure' and claim.value is True):
            raise ValueError('effect predicate/scene/Verification differs')
        goals=state.goals+tuple(g for child in state.subtasks for g in child.goals)
        if claim.predicate=='gripper_empty':
            if claim.subject_id!='gripper' or claim.receptacle_id is not None:raise ValueError('explicit gripper subject required')
        elif claim.subject_id not in {g.item_id for g in goals}:
            raise ValueError('effect subject is not a task object')
        matching=next((g for g in goals if g.item_id==claim.subject_id),None)
        if claim.predicate in ('inside','placed','poured') and (matching is None or claim.receptacle_id!=matching.receptacle_id):
            raise ValueError('effect destination differs')
        if (claim.predicate=='clean' and matching is not None and matching.required_action=='wipe'
                and claim.receptacle_id!=matching.receptacle_id):
            raise ValueError('wipe effect target region differs')
        global_gripper=claim.predicate in ('holding','gripper_empty')
        executions=[e for e in state.executions if e.call_id is not None and (global_gripper or e.item_id==claim.subject_id)]
        latest=executions[-1] if executions else None
        if (latest is None or latest.status!='completed' or latest.scene_version!=state.scene_version
                or latest.call_id!=verification['related_call_id']):
            raise ValueError('effect is not bound to the latest current execution')
        actions={'inside':('place',),'placed':('place',),'poured':('pour',),'clean':('wipe','place')}
        if claim.predicate in actions and latest.action not in actions[claim.predicate]:
            raise ValueError('effect is bound to the wrong action')
        calls=self.memory.search(filters={'session_id':session_id,'task_id':session.task_id,
            'call_id':latest.call_id,'domain':Domain.EXECUTION},limit=100)
        execution=next((r for r in calls if r.event_type=='tool_finished' and r.status=='completed'),None)
        if (execution is None or execution.source not in self.execution_contracts[session_id]
                or execution.timestamp>record.timestamp):raise ValueError('original completed execution evidence missing')
        proof={ref['id']:ref for ref in verification['evidence']}
        native={ref.id:asdict(ref) for ref in record.resource_refs}
        if any(native.get(k)!=v for k,v in proof.items()):raise ValueError('Verification resources changed')
        for ref in proof.values():_check_resource(self.memory,ref,synthetic=payload.get('synthetic_fixture') is True or payload.get('origin')=='synthetic')
        observations=[]
        for identifier in claim.observation_record_ids:
            obs=self.memory.evidence(identifier)
            related=obs.payload.get('related_call_id')
            if 'li_effect_case' in obs.payload:
                from .task_effect import judge_case
                case=obs.payload['li_effect_case'];ctx=case['task_context']
                verdict=judge_case(case)
                if (payload.get('case_record_id')!=obs.id or payload.get('li_verdict')!=verdict or verdict['status']!='ok'
                        or ctx['session_id']!=session_id or ctx['task_id']!=session.task_id
                        or ctx['scene_version']!=state.scene_version or claim.predicate!='inside'
                        or ctx['map_version']!=(state.base_map_version or state.scene_version)
                        or claim.subject_id!=ctx['action']['object_id'] or claim.receptacle_id!=ctx['box_object_id']
                        or claim.value!={'success':True,'failure':False,'uncertain':None}.get(verdict['conclusion'])):
                    raise ValueError('Li effect source/case/predicate differs')
                scene=self.scenes[session_id]
                if any(ctx[key]!=getattr(scene.snapshot,key) for key in ('coordinate_frame','length_unit','identity_scope')):
                    raise ValueError('effect changed frame/unit/identity basis')
                related=ctx['action']['call_id']
            if (obs.session_id!=session_id or obs.task_id!=session.task_id or obs.domain!=Domain.STATE or obs.event_type!='observation'
                    or not obs.resource_refs or obs.payload.get('scene_version')!=state.scene_version
                    or related!=latest.call_id
                    or not execution.timestamp<obs.timestamp<=record.timestamp
                    or obs.expires_at is not None and obs.expires_at<=time.time()
                    or not {r.id for r in obs.resource_refs}&proof.keys()):
                raise ValueError('current post-execution observation evidence required')
            if any(proof[r.id]!=asdict(r) for r in obs.resource_refs if r.id in proof):raise ValueError('observation resources differ')
            observations.append(obs)
        effect=EffectState(predicate=claim.predicate,subject_id=claim.subject_id,receptacle_id=claim.receptacle_id,
            value=claim.value,related_call_id=latest.call_id,verification_record_id=record.id,
            observation_record_ids=claim.observation_record_ids,scene_version=state.scene_version,
            source_refs=(execution.id,record.id)+claim.observation_record_ids+tuple(proof),
            observed_at=max(obs.timestamp for obs in observations),scope=latest.scope)
        return ledger.record_effect(effect)

    async def _dispatch(self,session,decision,cursor,remaining):
        ledger=self.ledgers.get(session.id)
        if decision.action=='finish' and decision.arguments.get('outcome')=='success':
            if ledger is None or not ledger.complete():
                raise ToolError(Failure.PRECONDITION,'independent verification required for every task goal')
        if decision.action!='call_tool' or ledger is None:
            return await super()._dispatch(session,decision,cursor,remaining)
        name=decision.arguments['name'];arguments=decision.arguments['arguments']
        entry=self.registry.get(name);contract=self.execution_contracts[session.id].get(name)
        goal=None
        if entry.spec.domain==Domain.EXECUTION:
            if contract is None:raise ToolError(Failure.PERMISSION,'unbound execution tool')
            if arguments.get('scene_version',ledger._current.scene_version)!=ledger._current.scene_version:
                raise ToolError(Failure.PRECONDITION,'execution authorization belongs to an old scene')
            active=next((s for s in ledger._current.subtasks if s.subtask_id==ledger._current.active_subtask_id),None)
            if contract.prerequisite_only and active is None or active and active.status=='awaiting_observation':
                raise ToolError(Failure.PRECONDITION,'prerequisite-only action or post-observation gate')
            goal=next((g for g in ledger.active_goals() if g.item_id==arguments.get('item_id')),None)
            provided_destination=('receptacle_id' in arguments
                and not (contract.action=='wipe' and arguments['receptacle_id'] is None))
            if (goal is None or contract.action in ('place','pour') and
                    (arguments.get('receptacle_id') is None or arguments['receptacle_id']!=goal.receptacle_id)
                    or goal and provided_destination and arguments['receptacle_id']!=goal.receptacle_id):
                raise ToolError(Failure.PRECONDITION,'operation role binding mismatch for active task')
            # Invalidate BEFORE any execution starts, including async/unconfirmed failure.
            ledger.execution(goal.item_id,None,False,contract.action)
        result=await super()._dispatch(session,decision,cursor,remaining)
        if goal is not None:
            status=result.get('status')
            ledger.execution(goal.item_id,result.get('call_id'),status not in ('completed','running'),contract.action,
                status=status if status in ('completed','running','failed','cancelled','stop_unconfirmed') else None)
        elif name in self.verification_tools[session.id]:
            call=self.router.calls[result['call_id']]
            verification=call.result.verification if call.result else None
            payload=call.result.payload if call.result else {}
            if 'effect' in payload:
                records=self.memory.search(filters={'session_id':session.id,'task_id':session.task_id,
                    'call_id':call.id,'domain':Domain.VERIFICATION},limit=100)
                terminal=next((r for r in records if r.event_type=='verification' and r.status=='completed'),None)
                try:
                    if terminal:self.record_effect(session.id,terminal.id)
                except (ValueError,KeyError,TypeError,OSError) as error:
                    self.memory.write(Record(session.id,session.task_id,SOURCE+'.admission',Domain.PLANNING,
                        'effect_rejected','rejected',{'verification_call_id':call.id,'reason':str(error)}))
                return result
            g=next((g for g in ledger.active_goals() if g.item_id==arguments.get('item_id')),None)
            if (g and g.execution_call_id and call.status=='completed' and verification
                and verification.conclusion=='success' and verification.evidence
                and verification.related_call_id==g.execution_call_id and verification.subject==g.item_id
                and g.execution_action==(g.required_action or 'place') and g.terminal_predicate in (None,'placed')
                and g.required_action not in _ACTION_TERMINALS
                and (g.verification_tool is None or name==g.verification_tool)
                and payload.get('item_id')==g.item_id and payload.get('receptacle_id')==g.receptacle_id
                and payload.get('scene_version')==ledger._current.scene_version):
                records=self.memory.search(filters={'session_id':session.id,'task_id':session.task_id,
                                                   'call_id':call.id,'domain':Domain.VERIFICATION},limit=100)
                terminal=next((r for r in records if r.event_type=='verification' and r.status=='completed'),None)
                if terminal:ledger.verification(terminal,g.item_id)
        return result
