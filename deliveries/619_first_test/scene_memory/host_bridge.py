"""Normal-package assembly of the received host and the scene adapters."""
from pathlib import Path
from primitive_vla.interfaces.service import TaskService
from primitive_vla.memory import Memory
from primitive_vla.runtime.agent import Agent
from primitive_vla.planning.model import ModelPlanner
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from scene_memory import state_provider

ROOT=Path(__file__).resolve().parents[1]

def register_li_effect(service,*,allow_synthetic=False,name='li.verify_effect'):
    """Register Li's actual calculation on the native Verification path."""
    from primitive_vla.contracts import Domain
    from primitive_vla.tools.contracts import ToolSpec
    from .team_bridge import LiEffectProvider
    schema={'type':'object','properties':{k:{'type':'string','minLength':1}
        for k in ('item_id','receptacle_id','case_record_id')},
        'required':['item_id','receptacle_id','case_record_id'],'additionalProperties':False}
    provider=LiEffectProvider(service.agent,allow_synthetic=allow_synthetic)
    service.agent.registry.register(ToolSpec(name,Domain.VERIFICATION,'Li T05 sourced containment check',
        schema,{'type':'object'},'li.task_effect'),provider)
    return provider

def build_task_service(planner,directory,*,allow_model_planner=False):
    """Construct the project task adapter; bind_task before the runner starts."""
    if type(allow_model_planner) is not bool:raise ValueError('allow_model_planner must be bool')
    if isinstance(planner,ModelPlanner) and not allow_model_planner:
        raise ValueError('ModelPlanner requires explicit allow_model_planner=True')
    from scene_memory.working_memory import TaskAwareAgent
    return TaskService(TaskAwareAgent(planner,ToolRegistry(),Memory(directory),SkillStore(),ResourceManager()))


def publish_scene_evolution(service,session_id,parent,child,*,provider=None,retain_effect_ids=()):
    """Publish a prevalidated direct scene/ledger edge under one synchronous owner.

    No await occurs during publication. Provider/source/ledger validation precedes
    durable ledger write; the remaining assignments cannot invoke external IO.
    A newly returned provider must be explicitly installed by its host caller.
    """
    agent=service.agent;ledger=agent.ledgers[session_id]
    from scene_memory import SceneMemory
    if not isinstance(child,SceneMemory):raise ValueError('verified child SceneMemory required')
    if agent.scenes.get(session_id) is not parent or agent.router.active(session_id):
        raise ValueError('current bound parent and confirmed idle controls required')
    if child._parent_scene is not parent:raise ValueError('only direct child of bound parent can be published')
    expected_parent=parent.snapshot.map_version
    if provider is not None:
        if type(provider) is not state_provider.SceneQueryProvider:raise ValueError('native scene provider required')
        provider.validate_switch(child,expected_parent=expected_parent)
        ready_provider=provider
    else:
        ready_provider=state_provider.SceneQueryProvider.from_derived(child,session_id=session_id,
            task_id=agent.sessions[session_id].task_id,revision=child.snapshot.map_version)
    candidate=ledger.prepare_scene_advance(child,expected_parent=expected_parent,retain_effect_ids=retain_effect_ids)
    ledger._save(candidate)
    ready_provider.memory=child
    ready_provider._derived=True
    agent.scenes[session_id]=child
    return ready_provider

def start_archive_control(planner, directory, project_binding, *, allow_model_planner=False):
    """Start one bound archive service inside a running asyncio event loop.

    Enabling a model adapter is configuration, not authorization for API use.
    """
    if type(allow_model_planner) is not bool:
        raise ValueError('allow_model_planner must be bool')
    if isinstance(planner,ModelPlanner) and not allow_model_planner:
        raise ValueError('ModelPlanner requires explicit allow_model_planner=True')
    directory=Path(directory).resolve()
    if directory.exists():raise ValueError('Fresh caller-owned output directory required')
    memory=Memory(directory)
    registry=ToolRegistry()
    agent=Agent(planner,registry,memory,SkillStore(),ResourceManager())
    service=TaskService(agent)
    session=service.start(project_binding['instruction'],'offline-no-robot','archive',
                          allowed_tools=(state_provider.TOOL_NAME,))
    provider=state_provider.SceneQueryProvider(ROOT/state_provider.SOURCE_PATH,project_binding,session.id,session.task_id)
    registry.register(provider.spec(),provider)
    return service,session,provider
