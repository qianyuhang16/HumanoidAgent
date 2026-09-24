"""The sole Agent loop: model -> tools -> feedback -> model."""

import asyncio
from time import monotonic

from .contracts import AgentContext, AgentReport, ModelReply, ModelResponseError


class Agent:
    def __init__(self, max_turns=40, max_calls=80, timeout_s=180.0):
        self.max_turns, self.max_calls, self.timeout_s = max_turns, max_calls, timeout_s

    async def run(self, instruction: str, context: AgentContext) -> AgentReport:
        session, runtime = context.session, context.tools
        session.task = instruction
        deadline = monotonic() + self.timeout_s
        turns, calls = 0, 0
        status, reason, evidence = "stopped", "Turn budget exhausted", []
        context.log.write("task_started", instruction=instruction)

        def model_context():
            return session.context(remaining_turns=self.max_turns - turns,
                                   remaining_calls=self.max_calls - calls,
                                   remaining_seconds=max(0, deadline - monotonic()))

        try:
            while turns < self.max_turns:
                if runtime.control_locked:
                    status, reason = "blocked", "Control stop is unconfirmed"
                    break
                if monotonic() >= deadline:
                    reason = "Task time budget exhausted"
                    break
                turns += 1
                try:
                    reply = await asyncio.wait_for(
                        context.model.step(model_context(), runtime.definitions()),
                        timeout=max(.001, deadline - monotonic()))
                except ModelResponseError as error:
                    session.add("feedback", text=f"Invalid model response: {error}. Return a complete JSON object; no tools were executed for this response.")
                    continue
                try:
                    reply = ModelReply.model_validate(reply)
                except ValueError as error:
                    session.add("feedback", text=f"Invalid model response: {error}")
                    continue
                session.record_reply(reply)
                context.log.write("decision", **reply.model_dump())
                if reply.final:
                    unknown = set(reply.final.evidence) - {r["call_id"] for r in session.results}
                    if unknown:
                        session.add("feedback", text="Final evidence must reference actual tool call IDs")
                        continue
                    if context.finish_check:
                        rejection = await asyncio.wait_for(
                            context.finish_check.check(reply.final, model_context()),
                            timeout=max(.001, deadline - monotonic()))
                        if rejection:
                            session.add("feedback", text=rejection)
                            continue
                    status, reason, evidence = reply.final.status, reply.final.reason, reply.final.evidence
                    break
                for call in reply.calls:
                    if calls >= self.max_calls or monotonic() >= deadline or runtime.control_locked:
                        from primitive_vla.tools import ToolResult
                        result = ToolResult(status="rejected", text="Execution budget exhausted or control locked")
                    else:
                        calls += 1
                        result = await runtime.invoke(call, deadline)
                    session.record_result(call, result)
                if calls >= self.max_calls:
                    # One final model response may explain the result; no further actions.
                    session.add("feedback", text="Tool budget exhausted. Return a final status using existing evidence.")
            if runtime.control_locked:
                status, reason = "blocked", "Control stop is unconfirmed"
        except asyncio.TimeoutError:
            reason = "Task time budget exhausted"
        except asyncio.CancelledError:
            context.log.write("task_cancelled", steps=calls)
            raise
        except Exception as error:
            status, reason = "failed", f"{type(error).__name__}: {error}"
        report = AgentReport(status=status, reason=reason, turns=turns, steps=calls,
                             results=session.results, evidence=evidence,
                             control_locked=runtime.control_locked)
        session.finished = True
        context.log.write("task_finished", **report.model_dump())
        return report
