from pathlib import Path
import os
import yaml
from pydantic import BaseModel, ConfigDict, Field
from primitive_vla.config.loader import load_config
from primitive_vla.config.schema import AdapterSpec, AgentLimits, AgentVerificationConfig
from ..vla.config import VLASettings, ResetSettings

DEFAULT_CONFIG = Path(__file__).resolve().parents[3]/'configs/agent.yaml'


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    model_source: str = 'cameras.yaml'
    ik_config: str = 'ik.yaml'
    python_executable: str
    models_dir: str
    device: str = 'cuda'
    box_threshold: float = Field(default=.25,gt=0,le=1)
    inference_timeout_s: float = Field(default=120,gt=0,le=300)
    task_timeout_s: float = Field(default=600,gt=0)
    arm_preference_distance_margin_m: float = Field(default=.05,ge=0)
    reset: ResetSettings = Field(default_factory=ResetSettings)
    vla: VLASettings | None = None
    skills: list[str] = Field(default_factory=list)
    verification: AgentVerificationConfig = Field(default_factory=AgentVerificationConfig)
    system_prompt: str = ('Help the user accomplish their goal using the available tools when useful. '
                          'Choose your own approach and revise it based on evidence. '
                          'Distinguish observed facts, predictions and completed actions. '
                          'Report uncertainty or missing capabilities honestly. Answer in the user’s language.')


def settings(path=DEFAULT_CONFIG):
    path=Path(path).resolve()
    values=Options.model_validate(yaml.safe_load(path.read_text())).model_dump()
    for key in ('model_source','ik_config','python_executable','models_dir'):
        candidate=path.parent/Path(values[key]).expanduser()
        # Resolving a venv's Python symlink would silently select its base environment.
        values[key]=os.path.abspath(candidate) if key=='python_executable' else str(candidate.resolve())
    for key in ('python_executable','ik_config','model_source'):
        if not Path(values[key]).is_file():
            raise ValueError(f'Missing local {key}: {values[key]}')
    values['skills']=[str((path.parent/Path(reference).expanduser()).resolve()) for reference in values['skills']]
    for reference in values['skills']:
        if not Path(reference).is_file():raise ValueError(f'Missing Agent skill: {reference}')
    for name in ('grounding-dino','sam','depth-anything'):
        folder=Path(values['models_dir'])/name
        if not (folder/'config.json').is_file() or not list(folder.glob('*.safetensors')):
            raise ValueError(f'Missing existing weights: {folder}; automatic download is disabled')
    if values['vla']:
        vla=values['vla']
        for key in ('python_executable','source'):
            vla[key]=os.path.abspath(path.parent/Path(vla[key]).expanduser())
        if not Path(vla['python_executable']).is_file() or not (Path(vla['source'])/'lerobot').is_dir():
            raise ValueError('Existing LeRobot environment/source not found')
        for policy in vla['policies'].values():
            policy['checkpoint']=str((path.parent/Path(policy['checkpoint']).expanduser()).resolve())
            for name in ('config.json','policy_preprocessor.json','policy_postprocessor.json'):
                if not (Path(policy['checkpoint'])/name).is_file():raise ValueError(f'Missing policy file: {name}')
    return values


def agent_config(path, arm='auto'):
    if arm not in ('auto','left','right'):
        raise ValueError('arm must be auto/left/right')
    values=settings(path)
    config=load_config(Path(values['model_source']))
    config.name='Cobot Magic Agent'
    config.tool_providers=[AdapterSpec(adapter='primitive_vla_cobotmagic.agent.provider:CobotTools',
                                      options={**values,'arm':arm})]
    config.tools={}
    config.finish_check=None
    config.verification=AgentVerificationConfig.model_validate(values['verification'])
    config.instructions=list(dict.fromkeys([
        str(Path(__file__).resolve().parents[3]/'skills/cobotmagic-manipulation/SKILL.md'),
        *values['skills']]))
    config.limits=AgentLimits(max_turns=30,max_calls=40,timeout_s=values['task_timeout_s'],stop_timeout_s=25)
    config.system_prompt=values['system_prompt']
    return config
