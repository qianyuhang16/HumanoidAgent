from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Any

from primitive_vla.contracts import Domain, Failure, Record, ToolError, new_id
from primitive_vla.memory import Memory
from primitive_vla.tools.contracts import CallContext, ToolResult, ToolSpec, Verification
from .backend import MotionCancelled, NativeScene
from .views import RoboCasaViews

ROBOT_TYPE = "robocasa.PandaOmron"
PROVIDER = "rpent.robocasa"
OBJECT = {"type": "object"}


@dataclass(frozen=True)
class RoboCasaConfig:
    task: str = "OpenDrawer"
    split: str = "target"
    seed: int = 1
    robot_id: str = "robocasa-1"
    image_size: int = 256
    gpu: int = 0
    assets_path: Path | None = None
    workdir: Path = Path("workspace/robocasa/native")
    action_timeout: float = 120.0

    def __post_init__(self):
        if (not self.task.strip() or self.split not in {"target", "pretrain", "all"}
                or type(self.seed) is not int or self.seed < 0 or not self.robot_id
                or type(self.image_size) is not int or not 32 <= self.image_size <= 1024
                or type(self.gpu) is not int or self.gpu < 0
                or self.action_timeout <= 0):
            raise ValueError("invalid RoboCasa configuration")


@dataclass
class _Work:
    future: asyncio.Future | None
    context: CallContext
    execution: bool
    cancelled: threading.Event


class RoboCasa:
    """One in-process episode, one native worker, and independently registered tools.

    No model, task policy, reset tool, background simulation stepping, or automatic replay.
    Create one instance per environment; close the Router before closing this instance.
    """

    def __init__(self, config: RoboCasaConfig, memory: Memory):
        self.config, self.memory = config, memory
        self.episode_id = new_id()
        self._snapshot: dict[str, Any] = {}
        self._works: dict[str, _Work] = {}
        self._sessions: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="robocasa-egl")
        self._closed = False
        self.views = RoboCasaViews(self)

    @classmethod
    async def open(cls, config: RoboCasaConfig, memory: Memory, *, new_episode: bool = False):
        # An in-process simulation cannot reconstruct its old physics state after a restart.
        # Check before constructing RPent objects, whose constructors initialize the scene.
        unfinished = [record.call_id for record in memory.unfinished_calls()
                      if record.payload["robot_id"] == config.robot_id]
        if unfinished:
            raise RuntimeError(f"unresolved calls prevent creating a new simulation: {unfinished}")
        history = memory.search(filters={"source": PROVIDER, "event_type": "episode_started",
                                         "session_id": f"environment:{config.robot_id}"}, limit=1)
        if not new_episode and history:
            raise RuntimeError("RoboCasa cannot restore an old episode; explicitly create a new "
                               "episode/workspace instead of resuming it")
        self = cls(config, memory)
        loop = asyncio.get_running_loop()
        opening = loop.run_in_executor(self._executor, NativeScene, config, self._publish,
                                       self.views.render_due)
        try:
            self._scene = await asyncio.shield(opening)
            self._declarations = self._scene.declarations()
        except asyncio.CancelledError:
            await asyncio.wait({opening})
            if opening.exception() is None:
                await loop.run_in_executor(self._executor, opening.result().close)
            self._executor.shutdown(wait=True)
            raise
        except Exception:
            self._executor.shutdown(wait=True)
            raise
        memory.write(Record(
            session_id=f"environment:{config.robot_id}", task_id=config.task,
            source=PROVIDER, domain=Domain.STATE, event_type="episode_started", status="ready",
            lifetime="long", payload={"episode_id": self.episode_id, "robot_id": config.robot_id,
                                      "task": config.task, "split": config.split, "seed": config.seed},
        ))
        return self

    def _publish(self, snapshot):
        # Worker replaces the immutable-by-convention value; readers never touch MuJoCo.
        self._snapshot = {**snapshot, "episode_id": self.episode_id,
                          "task": self.config.task, "split": self.config.split,
                          "seed": self.config.seed}

    def register(self, registry, resources, skills):
        resources.define_robot(self.config.robot_id, {"simulation"})
        skills.load(Path(__file__).with_name("skills"))
        no_arguments = {"type": "object", "properties": {}, "additionalProperties": False}
        state_tools = [
            ("observe", "读取最近已完成仿真步的机器人状态，不推进仿真；附观测时间和步号。", no_arguments),
            ("capture_image", "采集当前相机 RGB，可同时保存米制深度；在 EGL 线程上串行采集。",
             {"type": "object", "properties": {
                 "camera": {"type": "string", "enum": ["agentview", "agentview_right", "wrist", "navview"]},
                 "depth": {"type": "boolean"}}, "additionalProperties": False}),
            ("localize_pixel", "将当前相机的图像像素按深度回投到世界坐标；使用采集结果的步号，过期观测会被拒绝。不是目标检测或可达性判断。",
             {"type": "object", "properties": {
                 "camera": {"type": "string", "enum": ["agentview", "agentview_right", "wrist", "navview"]},
                 "row": {"type": "integer", "minimum": 0, "maximum": self.config.image_size - 1},
                 "column": {"type": "integer", "minimum": 0, "maximum": self.config.image_size - 1},
                 "episode_id": {"const": self.episode_id},
                 "simulation_steps": {"type": "integer", "minimum": 0}},
                 "required": ["row", "column", "episode_id", "simulation_steps"], "additionalProperties": False}),
        ]
        for operation, description, schema in state_tools:
            registry.register(ToolSpec(
                name=f"state.robocasa.{operation}", domain=Domain.STATE, description=description,
                input_schema=schema, output_schema=OBJECT, provider=PROVIDER,
                supported_robots=(ROBOT_TYPE,), timeout=self.config.action_timeout,
                skill_ref="robocasa.operation",
            ), _Provider(self, operation, Domain.STATE))
        for operation, description, schema in self._declarations:
            registry.register(ToolSpec(
                name=f"execution.robocasa.{operation}", domain=Domain.EXECUTION,
                description=description, input_schema=schema, output_schema=OBJECT,
                provider=PROVIDER, supported_robots=(ROBOT_TYPE,),
                required_resources=("simulation",), execution_mode="async",
                timeout=self.config.action_timeout, skill_ref="robocasa.operation",
            ), _Provider(self, operation, Domain.EXECUTION))
        registry.register(ToolSpec(
            name="verification.robocasa.task", domain=Domain.VERIFICATION,
            description="检查当前 episode 的原生任务成功谓词与进度；这是仿真真值验证。",
            input_schema={"type": "object", "properties": {
                "related_call_id": {"type": "string", "minLength": 1}},
                "additionalProperties": False},
            output_schema=OBJECT, provider=PROVIDER, supported_robots=(ROBOT_TYPE,),
            timeout=self.config.action_timeout, skill_ref="robocasa.operation",
        ), _Provider(self, "verify_task", Domain.VERIFICATION))

    async def close(self):
        if self._closed:
            return
        active = [work for work in self._works.values()
                  if work.future is not None and not work.future.done()]
        if active:
            raise RuntimeError("RoboCasa has active calls; stop and drain the Router before close")
        await self.views.close()
        await asyncio.get_running_loop().run_in_executor(self._executor, self._scene.close)
        self._executor.shutdown(wait=True)
        self._closed = True


class _Provider:
    def __init__(self, owner: RoboCasa, operation: str, domain: Domain):
        self.owner, self.operation, self.domain = owner, operation, domain

    async def execute(self, arguments: dict, context: CallContext) -> ToolResult:
        owner = self.owner
        work = _Work(None, context, self.domain == Domain.EXECUTION, threading.Event())
        owner._works[context.call_id] = work
        if context.robot_id != owner.config.robot_id:
            raise ToolError(Failure.PERMISSION, "tool is bound to a different simulator instance")
        if owner._closed:
            raise ToolError(Failure.EXECUTION, "RoboCasa is closed")
        if context.session_id not in owner._sessions:
            binding = owner.memory.search(filters={"session_id": context.session_id,
                                                   "source": PROVIDER,
                                                   "event_type": "episode_bound"}, limit=1)
            if binding and binding[0].payload["episode_id"] != owner.episode_id:
                raise ToolError(Failure.PRECONDITION, "this session belongs to an earlier physics "
                                "episode; create a new session instead of replaying it")
            owner.memory.write(Record(
                context.session_id, context.task_id, PROVIDER, Domain.STATE,
                "episode_bound", "ready", {"episode_id": owner.episode_id}, lifetime="long",
            ))
            owner._sessions.add(context.session_id)
        if self.operation == "observe":
            return ToolResult(owner._snapshot)
        related = arguments.get("related_call_id") if self.domain == Domain.VERIFICATION else None
        if related is not None:
            action = owner._works.get(related)
            if (action is None or not action.execution
                    or action.context.session_id != context.session_id
                    or action.context.task_id != context.task_id
                    or action.future is None or not action.future.done()):
                raise ToolError(Failure.INTERFACE, "verification requires an ended execution "
                                "from this episode, session and task")
        if context.cancel_requested.is_set():
            work.cancelled.set()
        future = asyncio.get_running_loop().run_in_executor(
            owner._executor, owner._scene.invoke, self.operation, arguments, work.cancelled,
        )
        work.future = future
        last_step = owner._snapshot["simulation_steps"]
        while not future.done():
            await asyncio.wait({future}, timeout=0.1)
            snapshot = owner._snapshot
            if self.domain == Domain.EXECUTION and snapshot["simulation_steps"] != last_step:
                last_step = snapshot["simulation_steps"]
                await context.progress(snapshot, ())
        try:
            payload, blobs = await asyncio.shield(future)
        except MotionCancelled:
            return ToolResult({"interrupted": True, **owner._snapshot})
        payload = {**payload, "episode_id": owner.episode_id, "task": owner.config.task,
                   "split": owner.config.split, "seed": owner.config.seed}
        refs = tuple(owner.memory.put_resource(data, media_type,
                                              f"RoboCasa {payload.get('camera', '')} {kind}")
                     for kind, data, media_type in blobs)
        if blobs:
            payload["resources"] = {blob[0]: ref.id for blob, ref in zip(blobs, refs)}
            # Completed futures remain available for stop/verification correlation.
            # Keep their metadata, but release image/depth bytes after resource storage.
            blobs.clear()
        if self.domain == Domain.VERIFICATION:
            evidence = owner.memory.put_resource(
                json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
                "application/json", "RoboCasa task predicate evidence",
            )
            conclusion = "success" if payload["predicate"] else "failure"
            return ToolResult(payload, refs, Verification(
                subject=owner.config.task, conclusion=conclusion,
                reason=f"RoboCasa native _check_success() = {payload['predicate']}; simulator ground truth",
                evidence=(evidence,), related_call_id=related,
                failure_type=None if payload["predicate"] else Failure.EFFECT.value,
            ))
        if self.domain == Domain.EXECUTION:
            result = payload["native_result"]
            if result.get("error") or result.get("ok") is False:
                raise ToolError(Failure.EXECUTION, "RoboCasa primitive did not complete its motion", payload)
        return ToolResult(payload, refs)

    async def cancel(self, context: CallContext) -> bool:
        work = self.owner._works.get(context.call_id)
        if work is None:
            return False  # In particular, a restored call is not a known stopped native worker.
        work.cancelled.set()
        if work.future is None:
            return True  # This live request was rejected before any native work was submitted.
        if not work.future.done():
            return False  # The running execute will report completion after its current step drains.
        error = work.future.exception()
        if error is not None and not isinstance(error, MotionCancelled):
            await context.progress({"native_error": str(error), "native_worker_stopped": True}, ())
        return True
