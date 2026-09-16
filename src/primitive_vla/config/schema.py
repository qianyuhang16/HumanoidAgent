"""One validated configuration shared by CLI, API and dashboard."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from primitive_vla.domain.models import SceneObject, Truth
from primitive_vla.domain.skills import Registry, SkillSpec


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexConfig(Section):
    executable: str = "codex"
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"


class VLMConfig(Section):
    provider: Literal["http", "codex"] = "http"
    codex: CodexConfig = Field(default_factory=CodexConfig)
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = ""
    api_key_env: str = "VLM_API_KEY"
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=2048, gt=0)
    timeout_s: float = Field(default=45, gt=0)
    json_mode: bool = True
    max_images: int = Field(default=4, ge=0, le=16)
    max_image_bytes: int = Field(default=8_388_608, gt=0)
    image_detail: Literal["auto", "low", "high"] = "auto"


class PlannerConfig(Section):
    type: Literal["rule", "vlm"] = "rule"
    max_nodes: int = Field(default=12000, gt=0)
    history_limit: int = Field(default=4, ge=0)
    prompt: str = "选择推进剩余目标的一个合法候选技能；关注双手占用、依赖与最近失败。证据不足时重新观察。"


class ParserConfig(Section):
    templates: list[dict] | None = None
    type: Literal["template", "vlm"] = "template"
    prompt: str = "把用户指令解析为最终目标和必要的依赖，不生成固定动作序列，不添加用户未要求的目标。"


class VerificationConfig(Section):
    mode: Literal["rules", "audit", "enforce"] = "rules"
    prompt: str = "逐项评估用户目标；执行完成不代表物理效果成功，图像无法证明的条件返回 unknown。"


class ExecutionConfig(Section):
    observation_poll_s: float = Field(default=0, ge=0)
    poll_s: float = Field(default=0.05, gt=0)
    stop_timeout_s: float = Field(default=2, gt=0)


class TaskDefaults(Section):
    max_steps: int = Field(default=24, gt=0)
    timeout_s: float = Field(default=180, gt=0)
    max_observations: int = Field(default=2, ge=0)


class StateConfig(Section):
    confidence_threshold: float = Field(default=0.5, ge=0, le=1)
    mock_fact_ttl_s: float = Field(default=5, gt=0)


class VLAConfig(Section):
    python_executable: str = "python"
    checkpoint: str
    norm_stats: str
    device: str = "cuda"
    dtype: Literal["bfloat16", "float32"] = "bfloat16"
    startup_timeout_s: float = Field(default=240, gt=0)
    inference_timeout_s: float = Field(default=60, gt=0)
    num_inference_steps: int = Field(default=10, ge=1)
    execute_steps: int = Field(default=5, ge=1, le=50)
    max_chunks: int = Field(default=12, ge=1)
    action_hz: int = Field(default=20, ge=1, le=60)
    max_joint_step: float = Field(default=0.06, gt=0, le=0.25)
    max_gripper_step: float = Field(default=0.01, gt=0, le=0.04)
    action_mode: Literal["joint_delta", "joint_position"] = "joint_delta"
    image_key: str = "observation.images.base_0_rgb"
    image_source: str = "simulation"
    adapter: Literal["franka_experimental"] = "franka_experimental"


class SimulationObject(Section):
    id: str
    position: tuple[float, float]
    color: tuple[float, float, float, float]


class SimulationConfig(Section):
    python_executable: str = "python"
    robotwin_root: str = ""
    physics_hz: int = Field(default=240, ge=60)
    control_hz: int = Field(default=60, ge=10)
    realtime_factor: float = Field(default=2, gt=0, le=10)
    startup_timeout_s: float = Field(default=45, gt=0)
    bowl_position: tuple[float, float, float] = (.5, .17, 0)
    bowl_radius: float = Field(default=.1, gt=.04)
    bowl_height: float = Field(default=.07, gt=.02)
    object_size: float = Field(default=.032, gt=.01, lt=.07)
    objects: list[SimulationObject] = Field(default_factory=lambda: [
        SimulationObject(id="red", position=(.33, -.2), color=(.9, .18, .12, 1)),
        SimulationObject(id="green", position=(.48, -.22), color=(.2, .7, .25, 1)),
        SimulationObject(id="yellow", position=(.63, -.12), color=(.95, .7, .12, 1)),
    ])
    tool_position: tuple[float, float, float] = (.3, .13, .073)
    tool_home_radius: float = Field(default=.09, gt=.02)
    tcp_offset: float = Field(default=.1, gt=0)
    arm_stiffness: float = Field(default=1000, gt=0)
    arm_damping: float = Field(default=100, gt=0)
    arm_force_limit: float = Field(default=100, gt=0)
    gripper_stiffness: float = Field(default=300, gt=0)
    gripper_damping: float = Field(default=20, gt=0)
    gripper_force_limit: float = Field(default=20, gt=0)
    static_friction: float = Field(default=1.5, gt=0)
    dynamic_friction: float = Field(default=1.2, gt=0)
    speed_m_s: float = Field(default=.2, gt=0, le=.5)
    travel_height: float = Field(default=.28, gt=.1)
    stir_turns: int = Field(default=2, ge=1, le=10)
    stir_radius: float = Field(default=.045, gt=0)
    stir_depth: float = Field(default=.03, gt=0)
    stir_period_s: float = Field(default=4, gt=0)
    min_stir_contact_steps: int = Field(default=2, ge=1)
    width: int = Field(default=800, ge=160, le=1920)
    height: int = Field(default=600, ge=120, le=1080)
    camera_position: tuple[float, float, float] = (1.05, -.9, .85)
    camera_target: tuple[float, float, float] = (.35, .02, .12)
    render_fps: int = Field(default=12, ge=1, le=30)
    record_video: bool = True
    approach_clearance: float = Field(default=.10, gt=.02)
    near_tolerance: float = Field(default=.04, gt=0)

    @model_validator(mode="after")
    def validate_scene(self):
        if self.physics_hz % self.control_hz:
            raise ValueError("physics_hz must be divisible by control_hz")
        if not self.objects or len({o.id for o in self.objects}) != len(self.objects):
            raise ValueError("Simulation needs objects with unique IDs")
        if {o.id for o in self.objects} & {"stirrer", "table", "bowl", "tool_home"}:
            raise ValueError("Ingredient IDs must not replace scene fixtures")
        if not 0 < self.stir_radius < self.bowl_radius - .025 or self.stir_depth >= self.bowl_height:
            raise ValueError("Stir path must fit inside the bowl")
        return self


class LiberoPolicyConfig(Section):
    model_python: str
    checkpoint: str
    norm_stats: str
    tokenizer: str
    model_repo: str = "lerobot/pi05_libero_base"
    device: str = "cuda"
    dtype: Literal["bfloat16", "float32"] = "bfloat16"
    num_inference_steps: int = Field(default=10, ge=1)
    action_horizon: int = Field(default=10, ge=1)
    discrete_state_input: bool = False
    image_keys: list[str] = ["observation.images.image", "observation.images.image2"]
    seed: int = 7


class LiberoConfig(Section):
    python_executable: str
    libero_root: str
    suite: str = "libero_10"
    task_id: int = Field(default=7, ge=0)
    initial_state: int = Field(default=0, ge=0)
    seed: int = 7
    render_resolution: int = Field(default=256, ge=64)
    image_resolution: int = Field(default=224, ge=64)
    control_hz: int = Field(default=20, ge=1)
    settle_steps: int = Field(default=10, ge=0)
    replan_steps: int = Field(default=5, ge=1)
    max_skill_steps: int = Field(default=300, ge=1)
    success_hold_steps: int = Field(default=5, ge=1)
    startup_timeout_s: float = Field(default=300, gt=0)
    objects: dict[str, str]
    predicates: dict[str, list[str]]
    policy: LiberoPolicyConfig

    @model_validator(mode="after")
    def validate_horizon(self):
        if self.replan_steps > self.policy.action_horizon:
            raise ValueError("LIBERO replan_steps must fit the model action horizon")
        return self


class BackendConfig(Section):
    type: Literal["mock", "http", "sapien", "libero"] = "mock"
    delay_s: float = Field(default=0.45, ge=0)
    fault: Literal["none", "missed_grasp", "uncertain_pour", "timeout"] = "none"
    base_url: str = "http://127.0.0.1:9000"
    api_key_env: str = "ROBOT_API_KEY"
    request_timeout_s: float = Field(default=5, gt=0)
    poll_s: float = Field(default=0.1, gt=0)
    model_version: str = "remote-unreported"
    simulation: SimulationConfig | None = None
    libero: LiberoConfig | None = None


class WebConfig(Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    refresh_ms: int = Field(default=700, ge=100)


class SceneConfig(Section):
    objects: list[SceneObject]
    facts: dict[str, Truth]
    images: dict[str, str] = Field(default_factory=dict)


class AppConfig(Section):
    name: str = "G1 · Kitchen Lab"
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    parser: ParserConfig = Field(default_factory=ParserConfig)
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    vla: VLAConfig | None = None
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    task_defaults: TaskDefaults = Field(default_factory=TaskDefaults)
    state: StateConfig = Field(default_factory=StateConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    backends: dict[str, BackendConfig] = Field(default_factory=lambda: {"mock": BackendConfig()})
    observer_backend: str = "mock"
    scene: SceneConfig
    skill_sources: list[str] = Field(default_factory=list)
    skills: list[SkillSpec]

    @model_validator(mode="after")
    def validate_bindings(self):
        Registry(self.skills)
        if not self.scene.objects or len({o.id for o in self.scene.objects}) != len(self.scene.objects):
            raise ValueError("Scene needs unique object IDs")
        if self.observer_backend not in self.backends or any(s.backend not in self.backends for s in self.skills):
            raise ValueError("Observer and skill backends must be registered")
        simulations = [name for name, backend in self.backends.items() if backend.type == "sapien"]
        if self.vla is not None and not simulations:
            raise ValueError("The current VLA adapter supports only the shared SAPIEN backend")
        if any(s.policy_prompt and self.backends[s.backend].type != "libero" for s in self.skills) and self.vla is None:
            raise ValueError("Policy skills require vla configuration")
        liberos = [name for name, backend in self.backends.items() if backend.type == "libero"]
        if liberos:
            if len(liberos) != 1 or self.observer_backend != liberos[0] or any(s.backend != liberos[0] for s in self.skills):
                raise ValueError("LIBERO tasks require one shared world for observation and all skills")
            if self.backends[liberos[0]].libero is None:
                raise ValueError("LIBERO requires simulator and policy configuration")
        if any(s.policy_prompt and s.program for s in self.skills):
            raise ValueError("A skill must select policy_prompt or program, not both")
        if simulations:
            if len(simulations) != 1 or self.observer_backend != simulations[0] or any(s.backend != simulations[0] for s in self.skills):
                raise ValueError("A SAPIEN task must use one shared simulation for observation and all skills")
            simulation = self.backends[simulations[0]].simulation
            if simulation is None or not simulation.robotwin_root:
                raise ValueError("SAPIEN requires simulation configuration and robotwin_root")
            if self.vla is not None and self.vla.action_hz > simulation.control_hz:
                raise ValueError("VLA action_hz cannot exceed simulation control_hz")
            expected = {o.id for o in simulation.objects} | {"stirrer", "table", "tool_home", "bowl"}
            if {o.id for o in self.scene.objects} != expected:
                raise ValueError("Simulation and symbolic scene object IDs must match")
        for skill in self.skills:
            if skill.timeout_s <= 0 or skill.max_attempts < 1 or skill.boundary not in ("atomic", "local"):
                raise ValueError(f"Invalid execution contract: {skill.name}")
            for value in [*skill.preconditions.values(), *skill.invariants.values(), *skill.effects.values()]:
                Truth(value)
            fields = [*skill.preconditions, *skill.invariants, *skill.effects, *skill.resources]
            for template in fields:
                try:
                    template.format(**{k: k for k in skill.parameters})
                except (KeyError, ValueError) as error:
                    raise ValueError(f"Invalid template in {skill.name}: {template}") from error
        for url in [self.vlm.base_url, *(b.base_url for b in self.backends.values() if b.type == "http")]:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.query:
                raise ValueError("Endpoints must be HTTP(S) URLs without embedded credentials/query strings")
        return self
