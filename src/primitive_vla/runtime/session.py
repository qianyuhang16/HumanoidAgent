"""Compose plugins and own resources for one model-directed tool session."""

import asyncio
from copy import deepcopy
from pathlib import Path
from time import monotonic

from primitive_vla.agent.contracts import AgentContext
from primitive_vla.agent.core import Agent
from primitive_vla.agent.memory import Session
from primitive_vla.verification.runtime import Verification
from primitive_vla.tools import FunctionTool, PluginContext
from primitive_vla.execution.runtime import ExecutionManager
from .composition import load_adapter
from .lifecycle import close_all


class Runtime:
    def __init__(self, config, workspace: Path, log, api_key=None, transport=None, config_dir=None):
        self.config = config.model_copy(deep=True)
        self.log = log
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "media").mkdir(exist_ok=True)
        self.plugin_context = PluginContext(workspace.resolve(), Path(config_dir or Path.cwd()).resolve(),
                                            log, api_key, transport)
        limits = config.limits
        self.session = Session(history_limit=limits.history_limit, result_chars=limits.result_chars,
                               memory_chars=limits.memory_chars)
        self.tools = ExecutionManager(self.session.tools(), log=log, denied=limits.denied_tools,
                                 stop_timeout_s=limits.stop_timeout_s, state=self.session.state)
        self.resources = []
        self.model = self.finish_check = None
        self.verification = None
        self.started = self.closed = False
        self.run_started = False
        self._close_task = None

    def build(self, spec):
        plugin = load_adapter(spec.adapter)(deepcopy(spec.options), self.plugin_context)
        self.resources.append(plugin)
        return plugin

    async def start(self):
        if self.closed:
            raise RuntimeError("Runtime is closed")
        if self.started:
            return
        try:
            instructions = [self.config.system_prompt] if self.config.system_prompt else []
            for reference in self.config.instructions:
                path = Path(reference).expanduser()
                if not path.is_absolute():
                    path = self.plugin_context.config_dir / path
                instructions.append(path.read_text(encoding="utf-8"))
            self.session.instructions = tuple(instructions)
            self.model = self.build(self.config.model)
            if not callable(getattr(self.model, "step", None)):
                raise TypeError("Model Adapter must implement step(context, tools)")
            for spec in self.config.tool_providers:
                provider = self.build(spec)
                for tool in provider.tools:
                    self.tools.register(tool)
            for name, spec in self.config.tools.items():
                tool = self.build(spec)
                self.tools.register(FunctionTool(tool.definition.model_copy(update={"name": name}),
                                                 tool.call, getattr(tool, "stop", None)))
            if self.config.finish_check:
                self.finish_check = self.build(self.config.finish_check)
                if not callable(getattr(self.finish_check, "check", None)):
                    raise TypeError("Finish check must implement check(answer, context)")
            reviewer = None
            if self.config.verification.mode == "independent":
                reviewer = self.build(self.config.verification.reviewer)
                if not callable(getattr(reviewer, "assess", None)):
                    raise TypeError("Outcome reviewer must implement assess(context)")
            self.verification = Verification(self.session, self.tools.definitions(), self.log, reviewer, self.config.verification.policy)
            self.tools.precondition = self.verification.policy.before_call
            for tool in self.verification.tools():
                self.tools.register(tool)
            for resource in self.resources:
                if callable(getattr(resource, "start", None)):
                    await resource.start()
            self.started = True
            self.log.write("tools_registered", tools=[t.model_dump() for t in self.tools.definitions()])
        except BaseException:
            await asyncio.shield(self.close())
            raise

    @property
    def control_locked(self):
        return self.tools.control_locked

    async def run(self, instruction, task_data=None):
        if self.run_started:
            raise RuntimeError("A Runtime owns exactly one task; construct another for a new task")
        self.run_started = True
        started = monotonic()
        await asyncio.wait_for(self.start(), timeout=self.config.limits.timeout_s)
        self.session.metadata = deepcopy(task_data or {})
        limits = self.config.limits
        agent = Agent(limits.max_turns, limits.max_calls, max(.001, limits.timeout_s - (monotonic() - started)),
                      max_model_retries=limits.max_model_retries)
        context = AgentContext(self.model, self.tools, self.session, self.log, self.finish_check, self.verification)
        return (await agent.run(instruction, context)).model_dump()

    def snapshot(self):
        return self.session.snapshot()

    async def close(self):
        if self._close_task is None:
            self.closed = True
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def _close(self):
        try:
            await self.tools.close()
        finally:
            unique = {id(r): r for r in self.resources if callable(getattr(r, "close", None))}
            await close_all(*reversed(list(unique.values())))
