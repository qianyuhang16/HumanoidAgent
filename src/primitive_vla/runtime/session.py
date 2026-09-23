"""Assemble and run one task against an immutable configuration snapshot."""

import asyncio
from dataclasses import asdict, replace
from pathlib import Path
from time import monotonic

from primitive_vla.agent.task_parser import TaskParser
from primitive_vla.agent.contracts import AgentContext, CompileContext, VerificationRequest
from primitive_vla.agent.memory import InMemory
from primitive_vla.config.schema import AppConfig
from primitive_vla.runtime.lifecycle import close_all
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.state import State
from primitive_vla.execution.executive import Executive
from primitive_vla.runtime.composition import create_vlm_client, instantiate, load_adapter, validate_adapter
from primitive_vla.storage.events import EventLog


class Runtime:
    def __init__(self, config: AppConfig, workspace: Path, log: EventLog, api_key: str | None = None, transport=None):
        self.config, self.log = config, log
        validate_adapter(config.adapters.agent, ("run",))
        validate_adapter(config.adapters.compiler, ("compile",))
        validate_adapter(config.adapters.decision_model, ("decide",))
        validate_adapter(config.adapters.executor, ("execute", "stop", "close"))
        validate_adapter(config.adapters.verifier, ("verify",))
        if config.adapters.observer != "configured":
            validate_adapter(config.adapters.observer, ("observe", "close"))
        loader = load_adapter(config.adapters.skill_loader)
        if not callable(loader):
            raise TypeError("skill_loader adapter must be callable")
        self.state = State({o.id: o for o in config.scene.objects}, config.state.confidence_threshold)
        if len(config.backends) != 1:
            raise ValueError("The canonical Agent runtime requires exactly one scene adapter")
        self.backends = {}
        for name, options in config.backends.items():
            adapter = config.adapters.backends.get(options.type)
            if not adapter:
                raise ValueError(f"No backend adapter configured for {options.type}")
            factory = load_adapter(adapter)
            backend = factory(options, workspace, config.vla, log, config)
            self.backends[name] = backend
        backend = self.backends[config.observer_backend]
        self.observer = (backend if config.adapters.observer == "configured" else
                          instantiate(config.adapters.observer, backend=backend))
        self.catalog = Registry(config.skills)
        self.executive = Executive(self.catalog, self.state, backend, log,
                                   config.execution.poll_s, config.execution.stop_timeout_s,
                                   observer=self.observer)
        self.vlm = create_vlm_client(config.vlm, workspace / "media", adapter=config.adapters.vlm,
                                     api_key=api_key, log=log, transport=transport)
        self.compiler = instantiate(config.adapters.compiler,
            parser=TaskParser(config.parser, config.task_defaults, self.vlm),
            config=config.parser, defaults=config.task_defaults, client=self.vlm)
        self.decision_model = instantiate(config.adapters.decision_model,
            client=self.vlm, prompt=config.planner.prompt, history_limit=config.planner.history_limit,
            max_nodes=config.planner.max_nodes)
        self.executor = instantiate(config.adapters.executor, executive=self.executive)
        self.verifier = instantiate(config.adapters.verifier, config=config.verification, client=self.vlm)
        self.agent = instantiate(config.adapters.agent, max_steps=config.task_defaults.max_steps,
                                 timeout_s=config.task_defaults.timeout_s,
                                 max_observations=config.task_defaults.max_observations)
        self.memory = InMemory()
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
            self.observed(self.compiler.compile(instruction, CompileContext(self.state, tuple(cfg.skills), task_data))), timeout=remaining())
        task = replace(task, timeout_s=min(task.timeout_s, remaining()), max_steps=min(task.max_steps, cfg.task_defaults.max_steps),
                       max_observations=min(task.max_observations, cfg.task_defaults.max_observations))
        self.task = task
        context = AgentContext(state=self.state, observer=self.observer, catalog=self.catalog,
                               decision_model=self.decision_model, executor=self.executor,
                               verifier=self.verifier, memory=self.memory, log=self.log)
        report = asdict(await self.agent.run(task, context))
        self.log.write("evidence_after", state=self.state.snapshot())
        final = await asyncio.wait_for(self.observed(self.verifier.verify(
            VerificationRequest(task, self.state, report=self.memory.read().values))), timeout=remaining())
        report["verification"] = asdict(final)
        if cfg.verification.mode == "enforce" and final.status != "success":
            report["status"] = "stopped"
            report["reason"] = "Task verification did not confirm all goals"
        self.log.write("task_verdict", verification=asdict(final), status=report["status"])
        return report

    async def close(self):
        await close_all(self.vlm, self.observer)
