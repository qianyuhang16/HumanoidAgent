import asyncio
from typing import Any

from primitive_vla.runtime.agent import Agent
from primitive_vla.runtime.session import Session


class TaskService:
    """Embedding API for a terminal, map UI, or server. No transport or robot SDK dependency."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self.tasks: dict[str, asyncio.Task] = {}

    async def _run_ready(self, session_id: str) -> Session:
        session = self.agent.sessions[session_id]
        # A user may pause/cancel between scheduling the runner and its first event-loop turn.
        if session.status == "ready":
            return await self.agent.run(session_id)
        return session

    def start(self, goal: str, robot_id: str, robot_type: str,
              allowed_tools: tuple[str, ...] | None = None) -> Session:
        session = self.agent.create_session(goal, robot_id, robot_type, allowed_tools)
        self.tasks[session.id] = asyncio.create_task(self._run_ready(session.id))
        return session

    def resume(self, session_id: str) -> asyncio.Task:
        previous = self.tasks.get(session_id)
        if previous is not None and not previous.done():
            raise ValueError("session runner is still active")
        session = self.agent.load_session(session_id)
        if session.status not in {"ready", "paused"}:
            raise ValueError(f"session cannot resume: {session.status}")
        session.status = "ready"
        self.agent.memory.save_session(session.to_dict())
        task = asyncio.create_task(self._run_ready(session_id))
        self.tasks[session_id] = task
        return task

    async def pause(self, session_id: str) -> None:
        self.agent.pause(session_id)
        await self.tasks[session_id]

    def respond(self, session_id: str, answer: str, payload: dict[str, Any] | None = None) -> asyncio.Task:
        self.agent.respond(session_id, answer, payload)
        return self.resume(session_id)

    async def cancel(self, session_id: str) -> Session:
        session = await self.agent.cancel(session_id)
        task = self.tasks.get(session_id)
        if task is not None:
            await task
        return session

    async def close(self) -> None:
        for session_id, task in self.tasks.items():
            if not task.done():
                self.agent.pause(session_id)
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.agent.close()
