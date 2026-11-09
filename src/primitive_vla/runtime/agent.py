from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import time
from typing import Any

from jsonschema import ValidationError

from primitive_vla.contracts import Domain, Failure, Record, ToolError
from primitive_vla.events import EventBus
from primitive_vla.memory import Memory
from primitive_vla.planning import Decision, Planner
from primitive_vla.planning.actions import ACTION_VALIDATOR
from primitive_vla.planning.context import ContextBuilder
from primitive_vla.runtime.session import Limits, Session
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.hooks import Hooks
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.tools.router import ToolRouter
from primitive_vla.tools.state import ObservationSink

FINAL_STATES = {"completed", "failed", "cancelled", "budget_exhausted"}
INTERRUPTED = object()


async def interruptible(operation, control: asyncio.Event, timeout: float):
    """Interrupt planning or a runtime wait without treating coroutine cancellation as robot stop."""
    task = asyncio.create_task(operation)
    signal = asyncio.create_task(control.wait())
    try:
        ready, _ = await asyncio.wait({task, signal}, timeout=max(0, timeout),
                                     return_when=asyncio.FIRST_COMPLETED)
        if signal in ready:
            return INTERRUPTED
        if task not in ready:
            raise TimeoutError("operation time budget exhausted")
        return task.result()
    finally:
        task.cancel()
        signal.cancel()
        await asyncio.gather(task, signal, return_exceptions=True)


class Agent:
    """A decision loop over events and capabilities, with no task-specific transition graph."""

    def __init__(self, planner: Planner, registry: ToolRegistry, memory: Memory,
                 skills: SkillStore, resources: ResourceManager, limits: Limits = Limits()):
        self.planner, self.registry, self.memory, self.skills = planner, registry, memory, skills
        self.limits = limits
        self.events = EventBus()
        self.hooks = Hooks(memory, self.events)
        self.router = ToolRouter(registry, resources, self.hooks)
        self.router.restore()
        self.observations = ObservationSink(self.hooks)
        self.context = ContextBuilder(memory, registry, skills, limits.context_chars)
        self.sessions: dict[str, Session] = {}
        self._running: set[str] = set()
        self._controls: dict[str, asyncio.Event] = {}

    def create_session(self, goal: str, robot_id: str, robot_type: str,
                       allowed_tools: tuple[str, ...] | None = None) -> Session:
        if not goal.strip() or not robot_id or not robot_type:
            raise ValueError("goal, robot_id, and robot_type are required")
        session = Session(goal, robot_id, robot_type, allowed_tools)
        self.sessions[session.id] = session
        self._controls[session.id] = asyncio.Event()
        self.memory.save_session(session.to_dict())
        self.memory.write(Record(session.id, session.task_id, "planning", Domain.PLANNING,
                                 "task_created", "ready", {"goal": goal}))
        return session

    def load_session(self, session_id: str) -> Session:
        if session_id in self.sessions:
            return self.sessions[session_id]
        session = Session.from_dict(self.memory.load_session(session_id))
        if session.status in {"running", "waiting", "ready"}:
            session.status = "paused"
        self.sessions[session.id] = session
        self._controls[session.id] = asyncio.Event()
        self.events.subscribe(session.id, session.subscriptions)
        self.memory.save_session(session.to_dict())
        return session

    def pause(self, session_id: str) -> None:
        session = self.sessions[session_id]
        if session.status in {"ready", "running", "waiting"}:
            session.status = "paused"
            self._controls[session_id].set()
            self.events.wake(session_id)
            self.memory.save_session(session.to_dict())

    def respond(self, session_id: str, answer: str, payload: dict[str, Any] | None = None) -> None:
        session = self.sessions[session_id]
        if session.status != "waiting_user" or not answer.strip():
            raise ValueError("respond requires a pending question and nonempty answer")
        record = self.memory.write(Record(session.id, session.task_id, "user", Domain.PLANNING,
                                         "user_input", "received", {"answer": answer, **(payload or {})}))
        session.last_result = record.to_dict()
        session.last_result_record_id = record.id
        session.question = None
        session.status = "ready"
        self.memory.save_session(session.to_dict())
        self.events.wake(session_id)

    async def cancel(self, session_id: str) -> Session:
        session = self.sessions[session_id]
        if session.status in FINAL_STATES:
            return session
        self._controls[session_id].set()
        self.events.wake(session_id)
        await self._stop(session, "cancelled")
        return session

    async def _stop(self, session: Session, final_status: str) -> None:
        session.pending_status = final_status
        session.status = "stopping"
        self.memory.save_session(session.to_dict())
        await self.router.cancel_session(session.id)
        if not self.router.active(session.id):
            session.status = final_status
            session.pending_status = None
        self.memory.save_session(session.to_dict())

    async def reconcile(self, session_id: str) -> Session:
        """Ask Providers again to confirm stop, including restored calls; never replay them."""
        session = self.sessions[session_id]
        await self.router.cancel_session(session.id)
        if session.pending_status and not self.router.active(session.id):
            session.status = session.pending_status
            session.pending_status = None
        self.memory.save_session(session.to_dict())
        return session

    async def run(self, session_id: str) -> Session:
        session = self.sessions[session_id]
        if session_id in self._running or session.status not in {"ready", "paused"}:
            raise ValueError(f"session cannot run: {session.status}")
        self._running.add(session_id)
        control = self._controls[session_id]
        control.clear()
        session.status = "running"
        started = time.monotonic()
        previous_seconds = session.active_seconds
        try:
            while session.status == "running":
                session.active_seconds = previous_seconds + time.monotonic() - started
                remaining = self.limits.max_active_seconds - session.active_seconds
                if (session.decisions >= self.limits.max_decisions or
                    session.consecutive_errors >= self.limits.max_consecutive_errors or remaining <= 0):
                    session.last_result = {"error": "reasoning, retry, or time budget exhausted"}
                    await self._stop(session, "budget_exhausted")
                    break
                cursor = self.events.version(session_id)
                active = [call.snapshot() for call in self.router.active(session_id)]
                context = self.context.build(session, active)
                session.decisions += 1
                self.memory.save_session(session.to_dict())
                try:
                    decision = await interruptible(self.planner.decide(context), control,
                                                   min(remaining, self.limits.decision_timeout))
                    if decision is INTERRUPTED:
                        break
                    ACTION_VALIDATOR.validate(asdict(decision))
                    self.memory.write(Record(session.id, session.task_id, "planning", Domain.PLANNING,
                                             "decision", "selected", asdict(decision)))
                except (ValidationError, TypeError, TimeoutError) as error:
                    self._feedback(session, {"error": str(error), "kind": "interface_error"}, failed=True)
                    continue
                except Exception as error:
                    # Model adapter boundary; errors become evidence for the next decision.
                    self._feedback(session, {"error": str(error), "kind": "planner_error"}, failed=True)
                    continue
                if session.status != "running":
                    break
                remaining = self.limits.max_active_seconds - (previous_seconds + time.monotonic() - started)
                try:
                    result = await interruptible(self._dispatch(session, decision, cursor, remaining),
                                                 control, remaining)
                    if result is INTERRUPTED:
                        break
                    self._feedback(session, result, failed="error" in result and result["error"] is not None)
                except TimeoutError:
                    self._feedback(session, {"error": "active time budget exhausted"}, failed=True)
                    await self._stop(session, "budget_exhausted")
                except (ToolError, KeyError, ValueError) as error:
                    self._feedback(session, error.to_dict() if isinstance(error, ToolError) else
                                   {"error": str(error), "kind": "interface_error"}, failed=True)
                self.memory.save_session(session.to_dict())
        except asyncio.CancelledError:
            # Cancelling the host coroutine pauses planning; physical stop has an explicit API.
            if session.status == "running":
                session.status = "paused"
            raise
        except Exception:
            # An internal/storage failure is not recoverable by inventing model feedback.
            # Pause the runner and propagate it to the host; control leases remain tracked.
            if session.status in {"running", "waiting"}:
                session.status = "paused"
            raise
        finally:
            session.active_seconds = previous_seconds + time.monotonic() - started
            self.memory.save_session(session.to_dict())
            self._running.remove(session_id)
        return session

    def _feedback(self, session: Session, result: dict, *, failed: bool = False) -> None:
        session.last_result = result
        session.consecutive_errors = session.consecutive_errors + 1 if failed else 0
        record = self.memory.write(Record(session.id, session.task_id, "planning", Domain.PLANNING,
                                         "action_result", "failed" if failed else "completed", result))
        session.last_result_record_id = record.id

    async def _dispatch(self, session: Session, decision: Decision, cursor: int, remaining: float) -> dict:
        action, args = decision.action, decision.arguments
        if action == "discover_tools":
            return {"tools": self.registry.discover(session.robot_type, session.allowed_tools, **args)}
        if action == "inspect_tool":
            return self.registry.get(args["name"]).spec.to_dict()
        if action == "list_skills":
            offset = args.get("offset", 0)
            return {"skills": self.skills.catalog()[offset:offset + args.get("limit", 20)]}
        if action == "read_skill":
            return self.skills.read(args["id"], args.get("offset", 0), args.get("limit", 8000))
        if action == "call_tool":
            if session.tool_calls >= self.limits.max_tool_calls:
                await self._stop(session, "budget_exhausted")
                return {"error": "tool call budget exhausted"}
            session.tool_calls += 1
            self.memory.save_session(session.to_dict())
            call = await self.router.invoke(**args, session_id=session.id, task_id=session.task_id,
                                            robot_id=session.robot_id, robot_type=session.robot_type,
                                            allowed_tools=session.allowed_tools)
            return call.snapshot()
        if action in {"tool_status", "cancel_tool"}:
            call = self.router.calls[args["call_id"]]
            if call.context.session_id != session.id:
                raise ToolError(Failure.PERMISSION, "call belongs to another session")
            if action == "cancel_tool":
                await self.router.cancel(call.id)
            return call.snapshot()
        if action == "memory_search":
            return {"records": [record.to_dict() for record in self.memory.search(**args)]}
        if action == "memory_get":
            record = self.memory.get(args["id"])
            body = json.dumps(record.to_dict(), ensure_ascii=False)
            offset, limit = args.get("offset", 0), args.get("limit", 8000)
            return {"id": record.id, "content": body[offset:offset + limit],
                    "offset": offset, "total_chars": len(body)}
        if action == "memory_write":
            record = self.memory.write(Record(session.id, session.task_id, "planning", Domain.PLANNING,
                                              "note", "recorded", args["payload"],
                                              lifetime=args.get("lifetime", "short")))
            return {"id": record.id}
        if action == "memory_update":
            record = self.memory.get(args["id"])
            if record.domain != Domain.PLANNING or record.source != "planning" or record.event_type != "note":
                raise ToolError(Failure.PERMISSION, "Planning may update notes, not Tool evidence")
            return self.memory.update(record.id, args["changes"]).to_dict()
        if action == "get_resource":
            return asdict(self.memory.get_resource(args["id"]))
        if action == "subscribe":
            session.subscriptions = args["sources"]
            self.events.subscribe(session.id, session.subscriptions)
            return {"sources": session.subscriptions}
        if action == "wait":
            session.status = "waiting"
            self.memory.save_session(session.to_dict())
            try:
                await self.events.wait(session.id, cursor, max(0.001, min(args.get("seconds", remaining), remaining)))
            except asyncio.TimeoutError:
                pass  # Timer wake is an intentional event, not an execution failure.
            if session.status == "waiting":
                session.status = "running"
            return {"event_version": self.events.version(session.id)}
        if action == "ask_user":
            refs = [asdict(self.memory.get_resource(ref_id)) for ref_id in args.get("resource_ids", [])]
            session.question = args["question"]
            session.status = "waiting_user"
            return {"question": session.question, "resources": refs}
        if action == "finish":
            if self.router.active(session.id):
                raise ToolError(Failure.RESOURCE, "cannot finish with active or unconfirmed control tasks")
            evidence = [self.memory.get(record_id) for record_id in args.get("evidence_ids", [])]
            session.response = args["message"]
            session.status = "completed" if args["outcome"] == "success" else "failed"
            self.memory.write(Record(session.id, session.task_id, "planning", Domain.PLANNING,
                                     "task_finished", session.status,
                                     {"message": session.response, "evidence_ids": [r.id for r in evidence]},
                                     lifetime="long"))
            return {"status": session.status, "message": session.response}
        raise ValueError(f"unsupported action: {action}")

    async def close(self) -> None:
        if self._running:
            raise RuntimeError("pause and await running sessions before closing")
        await self.router.close()
        self.memory.close()
