"""Importable host assembly using the existing Agent and the real stdio platform protocol."""
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from primitive_vla.contracts import Domain, Failure, ToolError
from primitive_vla.interfaces import TaskService
from primitive_vla.memory import Memory
from primitive_vla.memory.files import write_json
from primitive_vla.planning import ModelPlanner, PlanningContext
from primitive_vla.runtime.agent import Agent
from primitive_vla.runtime.connections.stdio import open_platform
from primitive_vla.runtime.session import Limits

from .legacy.evidence import digest, stable
from .legacy.ledger import Ledger, OBSERVE, PLACE, VERIFY, validate_task


class CallGuard:
    def __init__(self, ledger):
        self.ledger = ledger

    async def __call__(self, stage, record):
        if stage != "before_call":
            return
        ledger = self.ledger
        view = ledger.view(exclude_call=record.call_id)
        args = record.payload["arguments"]
        goal = next((g for g in view["goals"] if g["goal_id"] == args["goal_id"]), None)
        if ((record.session_id, record.task_id)
                != (ledger.session.id, ledger.session.task_id) or goal is None):
            raise ToolError(Failure.PERMISSION, "call belongs to another task/goal")
        expected = {"external_task_id": ledger.task["task_id"],
                    "scene_revision": view["scene"]["revision"],
                    "goal_id": goal["goal_id"], "visit": goal["visit"], "attempt": goal["attempt"]}
        if any(args[k] != value for k, value in expected.items()):
            raise ToolError(Failure.PRECONDITION, "call identity/scene/visit/attempt is stale")
        if goal["status"] == "verified":
            raise ToolError(Failure.PRECONDITION, "verified goal must not be replayed")
        if record.source == PLACE and goal["placement_call_id"] is not None:
            raise ToolError(Failure.PRECONDITION, "completed placement requires verification")
        if record.source in (OBSERVE, VERIFY):
            if (not goal["placement_call_id"]
                    or args["related_call_id"] != goal["placement_call_id"]):
                raise ToolError(Failure.PRECONDITION, "latest completed placement required")
        if record.source == VERIFY and (
                not goal["observation_call_id"]
                or args["observation_call_id"] != goal["observation_call_id"]):
            raise ToolError(Failure.PRECONDITION, "current same-call observation required")
        if record.source == VERIFY and goal["verification_call_id"] is not None:
            # Re-judging identical evidence cannot change Li's answer; a new placement or
            # observation is required, otherwise the planner must stop.
            raise ToolError(Failure.PRECONDITION,
                            "this observation already has a terminal Verification: "
                            + str(goal["verification_conclusion"]))


class RevisionContext:
    def __init__(self, base, ledger, agent):
        self.base, self.ledger, self.agent = base, ledger, agent

    def build(self, session, active_calls):
        self.ledger.publish_pending(active_calls, self.agent.router.active(session.id))
        block = self.ledger.view()
        budget = self.base.max_chars
        # Reserve the entire business block; never truncate task identity or constraints.
        self.base.max_chars = budget - len(json.dumps({"task_scene": block}, ensure_ascii=False))
        try:
            context = self.base.build(session, active_calls)
        finally:
            self.base.max_chars = budget
        data = {**context.data, "task_scene": block}
        if len(json.dumps(data, ensure_ascii=False)) + len(
                json.dumps(context.action_schema, ensure_ascii=False)) > budget:
            raise ValueError("complete task context exceeds the host budget")
        return PlanningContext(data, context.action_schema)


class RecordingPlanner:
    def __init__(self, inner, ledger):
        self.inner, self.ledger = inner, ledger

    async def decide(self, context):
        resource = self.ledger.memory.put_resource(
            stable({"data": context.data, "action_schema": context.action_schema}).encode(),
            "application/json", "Exact PlanningContext; synthetic platform evidence",
        )
        self.ledger.append("planning_revision_binding", {
            "revision": context.data["task_scene"]["scene"]["revision"],
            "remaining": context.data["task_scene"]["remaining"], "context_resource": resource.id,
            "sha256": resource.uri.removeprefix("blob:"),
        })
        decision = await self.inner.decide(context)
        if (decision.action == "finish" and decision.arguments.get("outcome") == "success"
                and self.ledger.view()["remaining"]):
            raise ValueError("task success requires independently verified current effects")
        return decision


class App:
    def __init__(self, agent, service, ledger, root, platform):
        self.agent, self.service, self.ledger = agent, service, ledger
        self.root, self.platform = root, platform
        self.session = ledger.session

    def query(self):
        return {"session_id": self.session.id, "framework_task_id": self.session.task_id,
                "status": self.session.status, "question": self.session.question,
                "task_scene": self.ledger.view(), "platform": deepcopy(self.platform)}

    async def run(self):
        return await self.service.resume(self.session.id)

    async def pause(self):
        await self.service.pause(self.session.id)

    async def reply(self, answer):
        # The original answer is journalled by the native Agent before same-task resume.
        return await self.service.respond(self.session.id, answer)

    def report(self, error=None):
        """Engineering summary; re-checks the whole chain from original native evidence.

        This is the historical/report boundary (freshness is not re-applied). A failed
        re-check is reported as unconfirmed integrity instead of preventing the summary.
        """
        integrity = {"verify_chain": "passed", "freshness_reapplied": False}
        try:
            chain = self.ledger.verify_chain()
            view = self.ledger.view()
            if chain != view["scene"]:
                raise ValueError("projected scene differs from the re-verified chain")
        except (ValueError, KeyError, TypeError, OSError) as failure:
            view = None
            integrity = {"verify_chain": "failed", "freshness_reapplied": False,
                         "error_type": type(failure).__name__, "detail": str(failure)}
        if view is None:
            status = "unconfirmed_integrity_check_failed"
        else:
            status = "verified_synthetic" if not view["remaining"] else "incomplete"
        document = {
            "artifact_kind": "synthetic_scene_retention_summary",
            "plan": None, "plan_artifact_completeness": "absent",
            "task_effect_status": status, "integrity": integrity,
            "identity": {"external_task_id": self.ledger.task["task_id"],
                         "session_id": self.session.id, "framework_task_id": self.session.task_id},
            "task_scene": view, "synthetic": True, "physical_execution_verified": False,
            "error": None if error is None else
            {"type": type(error).__name__, "detail": str(error)},
        }
        target = self.root / ("engineering_summary_" + uuid4().hex + ".json")
        write_json(target, document)
        return target


@asynccontextmanager
async def build(*, task, vlm, connection, run_dir, acknowledge_synthetic,
                resume=False, profile="scene-revision-synthetic", limits=None,
                synthetic_scenario="positive"):
    """Build one genuine Agent; caller supplies a VLM, never a default fake model.

    connection is the framework's command/args/cwd/env object. Only the child platform
    reads platform.json, profiles and skill.md; this host consumes its protocol report.
    synthetic_scenario selects a declared synthetic observation set (see runtime.synthetic).
    """
    task = validate_task(task)
    if acknowledge_synthetic is not True or vlm is None:
        raise ValueError("explicit synthetic acknowledgement and caller VLM are required")
    root = Path(run_dir).resolve()
    if not resume:
        root.mkdir(parents=True, exist_ok=False)
    elif not root.is_dir():
        raise ValueError("resume directory is missing")
    owner = root / "owner.lock"
    with owner.open("x", encoding="utf-8") as stream:
        stream.write("single host owner; crash recovery requires manual stop investigation\n")
    stack = AsyncExitStack()
    released = False
    try:
        try:
            memory = Memory(root / "memory")
            stack.callback(memory.close)
            binding, skills = await stack.enter_async_context(open_platform(
                connection, profile, memory=memory, workdir=root / "native",
                options={"acknowledge_synthetic": True, "task": task,
                         "synthetic_scenario": synthetic_scenario},
            ))
            agent = Agent(ModelPlanner(vlm, read_resource=memory.read_resource),
                          binding.registry, memory, skills, binding.resources,
                          limits or Limits(context_chars=100000))
            service = TaskService(agent)
            stack.push_async_callback(service.close)
            identity_path = root / "identity.json"
            if resume:
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
                if identity["task_sha256"] != digest(task):
                    raise ValueError("resume task differs from original")
                session = agent.load_session(identity["session_id"])
            else:
                session = agent.create_session(task["instruction"], binding.robot_id,
                                               binding.robot_type)
                write_json(identity_path, {"session_id": session.id,
                                           "task_sha256": digest(task)})
            ledger = Ledger(memory, task, session)
            agent.context = RevisionContext(agent.context, ledger, agent)
            agent.planner = RecordingPlanner(agent.planner, ledger)
            guard = CallGuard(ledger)
            for domain in (Domain.STATE, Domain.EXECUTION, Domain.VERIFICATION):
                agent.hooks.add(domain, guard)
            app = App(agent, service, ledger, root, binding.platform)
            try:
                yield app
            except Exception as error:
                app.report(error)
                raise
            else:
                app.report()
        finally:
            # Agent, Router and platform shutdown refuse to return while a stop is
            # unconfirmed; only a normal return of every close step releases ownership.
            await stack.aclose()
            released = True
    except Exception as error:
        write_json(root / ("host_failure_" + uuid4().hex + ".json"), {
            "error_type": type(error).__name__, "detail": str(error),
            "plan": None, "usable_as_plan": False, "synthetic": True,
            "host_resources_released": released,
        })
        raise
    finally:
        # A business error with a confirmed close releases the lock; a failed close keeps it.
        if released:
            owner.unlink()
