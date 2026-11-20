"""Thin integration of Chen's scoped recovery policy and Li's received calculation.

Only the native TaskAwareAgent/WorkingMemoryLedger owns execution completion.
Received originals and remaining real-input obligations are documented in the intake review.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json,time
from pathlib import Path
from urllib.parse import urlparse,unquote
from primitive_vla.contracts import Domain,ResourceRef
from primitive_vla.tools.contracts import ToolResult,Verification
from .task_effect import judge_case
from .obstacle_policy import propose_recovery
from .working_memory import GoalState

def _scoped_record(agent,session_id,record_id):
    s=agent.sessions[session_id];r=agent.memory.evidence(record_id)
    if r.session_id!=s.id or r.task_id!=s.task_id or r.expires_at is not None and r.expires_at<=time.time():
        raise ValueError('foreign or stale task evidence')
    return r

class LiEffectProvider:
    """Read a same-task ObservationSink case, never model-supplied inline evidence."""
    def __init__(self,agent,*,allow_synthetic=False):
        if type(allow_synthetic) is not bool:raise ValueError('explicit fixture gate required')
        self.agent,self.allow_synthetic=agent,allow_synthetic

    async def execute(self,arguments,context):
        record=_scoped_record(self.agent,context.session_id,arguments['case_record_id'])
        if record.domain!=Domain.STATE or record.event_type!='observation' or not record.resource_refs:
            raise ValueError('Li input must be a source-bearing observation')
        case=record.payload['li_effect_case']
        json.dumps(case,allow_nan=False)  # retain raw nested data; no coercion of nonfinite values
        ctx=case['task_context'];action=ctx['action'];ledger=self.agent.ledgers[context.session_id]
        goal=next((g for g in ledger.active_goals() if g.item_id==arguments['item_id']),None)
        if (goal is None or arguments['receptacle_id']!=goal.receptacle_id
                or ctx['task_id']!=context.task_id or ctx['session_id']!=context.session_id
                or ctx['scene_version']!=ledger._current.scene_version
                or ctx['map_version']!=(ledger._current.base_map_version or ledger._current.scene_version)
                or record.payload.get('scene_version')!=ledger._current.scene_version
                or ctx['goal']!=ledger._current.instruction or ctx['box_object_id']!=goal.receptacle_id
                or action['object_id']!=goal.item_id or action['call_id']!=goal.execution_call_id
                or action['action']!='place'):
            raise ValueError('Li case differs from actual task/action/item/receptacle/scene binding')
        refs={r.id:asdict(r) for r in record.resource_refs}
        if refs!={r['id']:r for r in case['resources']}:
            raise ValueError('Li resource references differ from original observation')
        if case['origin']=='synthetic':
            if not self.allow_synthetic:raise ValueError('synthetic evidence requires explicit local fixture gate')
        else:
            for ref in case['resources']:
                parsed=urlparse(ref['uri'])
                if parsed.scheme=='blob':raw=self.agent.memory.read_resource(ref['id'])
                elif parsed.scheme=='file' and not parsed.netloc:
                    name=unquote(parsed.path)
                    if len(name)>2 and name[0]=='/' and name[2]==':':name=name[1:]
                    path=Path(name)
                    if not path.is_absolute() or str(path).startswith('\\\\'):raise ValueError('local evidence only')
                    raw=path.read_bytes()
                else:raise ValueError('real evidence requires locally readable bytes; no remote fetching')
                if sha256(raw).hexdigest()!=ref.get('metadata',{}).get('sha256'):
                    raise ValueError('real evidence bytes/SHA mismatch')
        verdict=judge_case(case)
        if verdict['status']!='ok':raise ValueError('Li evidence binding rejected: '+verdict['reason'])
        evidence=tuple(ResourceRef(**r) for r in verdict['evidence'])
        # Explicit subject mapping after identity checks; retain Li's proposition verbatim.
        return ToolResult({'item_id':goal.item_id,'receptacle_id':goal.receptacle_id,
            'scene_version':ctx['scene_version'],'li_subject':verdict['subject'],
            'li_verdict':verdict,'case_record_id':record.id,'origin':case['origin'],
            # Li certifies this proposition only; no pose/holding/other effects.
            'effect':{'predicate':'inside','subject_id':goal.item_id,
                'value':{'success':True,'failure':False,'uncertain':None}[verdict['conclusion']],
                'receptacle_id':goal.receptacle_id,'observation_record_ids':[record.id]}},evidence,
            Verification(goal.item_id,verdict['conclusion'],verdict['reason'],evidence,
                verdict['related_call_id'],verdict['failure_type']))

    async def cancel(self,context):return True  # calculation has no physical control

def insert_chen_obstacle(agent,session_id,scene,*,record_id,permission_record_id,movable_ids,
                         observation_source,post_observation_source,verification_tool,
                         source_task_id=None):
    """Consume Chen's pure policy; source facts never override the root protection set."""
    ledger=agent.ledgers[session_id];s=agent.sessions[session_id]
    contract=agent.execution_contracts[session_id].get('move_obstacle')
    if contract is None or contract.action!='push' or contract.prerequisite_only is not True:
        raise ValueError('move_obstacle requires a prerequisite-only push contract')
    r=_scoped_record(agent,session_id,record_id)
    if r.domain!=Domain.STATE or r.source!=observation_source or not r.resource_refs:
        raise ValueError('approved source observation required')
    recovery=deepcopy(r.payload['recovery']);obstacle=recovery['obstacle_id']
    region=recovery['proposed_target_region_id']
    if obstacle in ledger._current.protected_object_ids or region in ledger._current.protected_object_ids:
        raise ValueError('protected root object cannot be displaced regardless of provider claim/permission')
    if obstacle not in set(movable_ids):raise ValueError('obstacle outside explicit movable allowlist')
    if (recovery['task_id']!=(source_task_id or s.task_id)
            or recovery['scene_version']!=ledger._current.scene_version
            or r.payload.get('scene_version')!=ledger._current.scene_version):
        raise ValueError('recovery source task/scene differs')
    ids={ref.id for ref in r.resource_refs}
    for fact in recovery.get('facts',{}).values():
        if not fact.get('evidence_refs') or not set(fact['evidence_refs'])<=ids:
            raise ValueError('recovery fact lacks original observation evidence')
    permission=_scoped_record(agent,session_id,permission_record_id)
    if (permission.event_type!='user_input'
            or permission.payload.get('go2_movement_trigger_record_id')!=r.id):
        raise ValueError('same-trigger native intent reply required')
    recovery['user_authorization']=deepcopy(permission.payload['go2_movement_permission'])
    recovery['trusted_sources']=[observation_source]
    names={tool.name for tool in agent.registry.catalog()}
    recovery['providers']={'navigate':'navigate' in names,'move_obstacle':'move_obstacle' in names}
    proposal=propose_recovery(recovery,{'original_task':s.goal,'constraints':list(ledger._current.constraints),
        'protected_object_ids':list(ledger._current.protected_object_ids),'remaining':ledger.view()['current_goal_item_id']})
    if proposal['decision']!='propose_temporary_movement_goal':
        raise ValueError('movement not admitted by Chen policy: '+proposal['decision'])
    agent.insert_prerequisite(session_id,scene,subtask_id='chen-obstacle/'+r.id,
        instruction='移开获准轻物，独立核验并复查通道后恢复原任务',trigger_record_id=r.id,
        goals=(GoalState(item_id=obstacle,receptacle_id=region,required_action='push',
                        verification_tool=verification_tool),),
        post_observation_source=post_observation_source)
    return proposal

def prompt_chen_reply(app,*,command_stream,output,automated=False):
    """Actual Chen ContinuationApp.reply caller; no paid standalone run command."""
    if type(automated) is not bool or not automated and not command_stream.isatty():
        raise ValueError('non-TTY input must be explicitly declared automated')
    if automated and not app.ledger.task['synthetic']:
        raise ValueError('automated reply fixture cannot be passed as a production user')
    query=app.query();question=query['question']
    if question is None:raise ValueError('no pending Chen question')
    print(json.dumps(query,ensure_ascii=False),file=output);output.flush()
    raw=command_stream.readline()
    if not raw.strip():raise ValueError('no answer; no automatic resume')
    document={key:question[key] for key in ('task_id','scene_version','visit_id','round','question_id')}
    document['answer']=raw
    if question['kind']=='selection':
        try:selected=json.loads(raw.strip())
        except ValueError:selected=raw.strip()
        document['selected_object_id']=selected  # original caller validates full typed scope
    else:
        if raw.strip().lower() not in ('yes','no'):raise ValueError('permission requires explicit yes or no')
        document['granted']=raw.strip().lower()=='yes'
    return app.reply(document)
