"""Expose existing Mock/SAPIEN skills as tools; no task parser or symbolic planner."""

import asyncio
from dataclasses import asdict

from primitive_vla.config.loader import legacy_config
from primitive_vla.domain.models import SkillCall
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.state import State
from primitive_vla.execution.executive import Executive
from primitive_vla.runtime.composition import load_adapter

from .contracts import FunctionTool, ToolDefinition, ToolResult


class SceneTools:
    def __init__(self, options, context):
        if set(options) - {"config"}:
            raise ValueError("SceneTools options accepts only config")
        config = legacy_config(options.get("config", {}), context.config_dir)
        if len(config.backends) != 1:
            raise ValueError("SceneTools requires one shared scene")
        backend_options = config.backends[config.observer_backend]
        factory = load_adapter(config.adapters.backends[backend_options.type])
        self.backend = factory(backend_options, context.workspace, config.vla, context.log, config)
        self.state = State({obj.id: obj for obj in config.scene.objects}, config.state.confidence_threshold)
        self.registry = Registry(config.skills)
        self.executive = Executive(self.registry, self.state, self.backend, context.log,
                                   config.execution.poll_s, config.execution.stop_timeout_s)
        self.tools = [FunctionTool(ToolDefinition(name="scene.observe", description="Observe current scene images, objects, robot state and measured facts. Call this to check physical outcomes."), self.observe)]
        for spec in config.skills:
            properties = {param: {"type": "string", "enum": kinds if param == "hand" else
                          [obj.id for obj in config.scene.objects if obj.kind in kinds]}
                          for param, kinds in spec.parameters.items()}
            async def execute(args, call, spec=spec):
                # Refresh state at the tool boundary; never require the model to fabricate a version.
                self.state.update(await self.backend.observe())
                call.check_active()
                from time import monotonic, time
                command = SkillCall(spec.name, args, self.state.version, spec.version, call.call_id,
                                    time() + max(0, call.deadline - monotonic()))
                result = await self.executive.execute(command)
                status = "ok" if result.execution.value == "completed" else "rejected" if result.execution.value == "rejected" else "error"
                return ToolResult(status=status, data=asdict(result), text=result.reason,
                                  control_released=self.executive.control.lease is None)

            self.tools.append(FunctionTool(ToolDefinition(name=f"skill.{spec.name}",
                description=spec.description + "\n" + spec.instructions +
                    f"\nRequired conditions: {spec.preconditions}. Expected effects (not guaranteed): {spec.effects}.",
                parameters={"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
                read_only=False, resources=["legacy_scene"], timeout_s=spec.timeout_s + config.execution.stop_timeout_s + 1), execute, self.stop))

    async def start(self):
        await self.backend.warmup()

    async def observe(self, args, call):
        observation = await self.backend.observe()
        self.state.update(observation)
        return ToolResult(data={"objects": [asdict(o) for o in self.state.objects.values()],
                                "facts": self.state.snapshot()["facts"],
                                "values": self.state.values(), "proprioception": observation.proprioception,
                                "timestamp": observation.timestamp}, images=observation.images)

    async def stop(self, call_id, reason):
        self.executive.cancel(call_id)
        entry = self.executive.requests.get(call_id)
        if entry:
            await asyncio.shield(entry[1])
        return self.executive.control.lease is None

    async def close(self):
        await self.backend.close()
