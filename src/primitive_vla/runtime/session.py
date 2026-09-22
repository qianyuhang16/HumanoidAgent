"""Assemble and run one task against an immutable configuration snapshot."""

import asyncio
from dataclasses import replace
from pathlib import Path
from time import monotonic

from primitive_vla.agent.planner import GoalPlanner, StructuredPlanner
from primitive_vla.agent.task_parser import TaskParser
from primitive_vla.backends.base import Backend
from primitive_vla.config.schema import AppConfig
from primitive_vla.runtime.lifecycle import close_all
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.state import State
from primitive_vla.execution.executive import Executive
from primitive_vla.runtime.composition import create_vlm_client, load_adapter
from primitive_vla.storage.events import EventLog
from primitive_vla.verification.task import TaskVerifier


class Runtime:
    def __init__(self, config: AppConfig, workspace: Path, log: EventLog, api_key: str | None = None, transport=None):
        self.config, self.log = config, log
        self.state = State({o.id: o for o in config.scene.objects}, config.state.confidence_threshold)
        self.backends: dict[str, Backend] = {}
        mock_world = None
        for name, options in config.backends.items():
            adapter = config.adapters.backends.get(options.type)
            if not adapter:
                raise ValueError(f"No backend adapter configured for {options.type}")
            factory = load_adapter(adapter)
            backend = factory(options, workspace, config.vla, log, config)
            if options.type == "mock":
                if mock_world is None:
                    mock_world = backend.world
                else:
                    backend.world = mock_world
            self.backends[name] = backend
        self.observer = self.backends[config.observer_backend]
        self.executive = Executive(Registry(config.skills), self.state, self.backends, log,
                                   config.execution.poll_s, config.execution.stop_timeout_s)
        self.vlm = create_vlm_client(config.vlm, workspace / "media", adapter=config.adapters.vlm,
                                     api_key=api_key, log=log, transport=transport)
        self.parser = TaskParser(config.parser, config.task_defaults, self.vlm)
        self.verifier = TaskVerifier(config.verification, self.vlm)
        self.task = None

    async def observed(self, operation):
        """Keep camera and facts fresh during model latency; fail if observation fails."""
        interval = self.config.execution.observation_poll_s
        if not interval:
            return await operation
        pending = asyncio.create_task(operation)
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=interval)
                self.state.update(await self.observer.observe())
            return await pending
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def run(self, instruction: str, task_data: dict | None = None) -> dict:
        cfg = self.config
        deadline = monotonic() + cfg.task_defaults.timeout_s
        remaining = lambda: max(0.001, deadline - monotonic())
        self.state.update(await asyncio.wait_for(self.observer.observe(), timeout=remaining()))
        self.log.write("evidence_before", state=self.state.snapshot())
        await asyncio.wait_for(self.observed(self.observer.warmup()), timeout=remaining())
        task = await asyncio.wait_for(
            self.observed(self.parser.parse(instruction, task_data, self.state, cfg.skills)), timeout=remaining())
        task = replace(task, timeout_s=min(task.timeout_s, remaining()), max_steps=min(task.max_steps, cfg.task_defaults.max_steps),
                       max_observations=min(task.max_observations, cfg.task_defaults.max_observations))
        self.task = task
        if cfg.planner.type == "vlm":
            async def choose(context):
                return await self.observed(self.vlm.complete("plan", cfg.planner.prompt +
                    '\nReturn {"action":"skill","index":integer,"reason":"brief decision basis"}, '
                    'or {"action":"observe"} or {"action":"stop","reason":"..."}. Select only from candidates.',
                    context, self.state.observation.images))
            planner = StructuredPlanner(choose, cfg.planner.history_limit)
        else:
            planner = GoalPlanner(cfg.planner.max_nodes)
        agent = load_adapter(cfg.adapters.agent)(planner, self.executive, self.observer)
        report = await agent.run(task)
        self.log.write("evidence_after", state=self.state.snapshot())
        try:
            report["verification"] = await asyncio.wait_for(self.observed(self.verifier.verify(task, report, self.state)), timeout=remaining())
        except asyncio.TimeoutError:
            report["verification"] = {"mode": cfg.verification.mode, "status": "unknown", "criteria": [], "reason": "Task verification deadline exceeded"}
        if cfg.verification.mode == "enforce" and report["verification"]["status"] != "success":
            report["status"] = "stopped"
            report["reason"] = "Task-level VLM verification did not confirm all goals"
        self.log.write("task_verdict", verification=report["verification"], status=report["status"])
        return report

    async def close(self):
        await close_all(self.vlm, *self.backends.values())
