"""The sole Agent loop: model -> tools -> feedback -> model."""

import asyncio
from time import monotonic

from primitive_vla.verification.runtime import Verification
from .contracts import AgentContext, AgentReport, ModelReply, ModelResponseError


class Agent:
    def __init__(self, max_turns=40, max_calls=80, timeout_s=180.0, *, max_model_retries=2):
        self.max_turns, self.max_calls, self.timeout_s = max_turns, max_calls, timeout_s
        self.max_model_retries = max_model_retries

    async def run(self, instruction: str, context: AgentContext) -> AgentReport:
        session, runtime = context.session, context.tools
        verification = context.verification or Verification(session, runtime.definitions(), context.log)
        session.task = instruction
        first_result = len(session.results)
        session.add("user", instruction=instruction)
        deadline = monotonic() + self.timeout_s
        scheduler = runtime.scheduler
        if scheduler is not None:
            scheduler.deadline = deadline
        turns, calls = 0, 0
        model_requests, model_errors, consecutive_errors = 0, 0, 0
        status, reason, evidence = "stopped", "Turn budget exhausted", []
        context.log.write("task_started", instruction=instruction)

        def model_context():
            return session.context(remaining_turns=self.max_turns - turns,
                                   remaining_calls=self.max_calls - calls,
                                   remaining_seconds=max(0, deadline - monotonic()))

        async def review_completed():
            if scheduler is not None:
                while scheduler.completed:
                    call, result = scheduler.completed.pop(0)
                    await verification.after_action(call, result, deadline)
                if scheduler.error is not None:
                    raise RuntimeError(f"Execution queue failed: {scheduler.error}")

        try:
            while turns < self.max_turns:
                await review_completed()
                if runtime.control_locked:
                    status, reason = "blocked", "Control stop is unconfirmed"
                    break
                if monotonic() >= deadline:
                    reason = "Task time budget exhausted"
                    break
                model_requests += 1
                try:
                    reply = await asyncio.wait_for(
                        context.model.step(model_context(), runtime.definitions()),
                        timeout=max(.001, deadline - monotonic()))
                    await review_completed()
                    try:
                        reply = ModelReply.model_validate(reply)
                        verification.validate(reply.assessments)
                        session.state.validate_claims(reply.claims)
                    except ValueError as error:
                        raise ModelResponseError(str(error)) from None
                except ModelResponseError as error:
                    model_errors += 1
                    consecutive_errors += 1
                    context.log.write("model_response_invalid", reason=str(error),
                                      attempt=consecutive_errors, model_requests=model_requests)
                    session.add("feedback", text=f"Invalid model response: {error}. Return a complete JSON object; no tools were executed for this response. Continue from existing results; do not repeat completed physical actions to repair formatting.")
                    if consecutive_errors > self.max_model_retries:
                        reason = f"Model response recovery exhausted after {consecutive_errors} invalid responses: {error}"
                        break
                    continue
                consecutive_errors = 0
                turns += 1
                session.record_reply(reply)
                context.log.write("decision", **reply.model_dump())
                verification.accept(reply.assessments)
                session.state.record_claims(reply.claims, "main_agent")
                if reply.final:
                    if reply.final.status == "completed" and scheduler is not None and scheduler.pending:
                        session.add("feedback", text="Execution tasks remain queued or running. Wait for results or cancel them before completing the session.")
                        continue
                    unknown = set(reply.final.evidence) - set(session.state.records)
                    if unknown:
                        session.add("feedback", text="Final evidence must reference recorded calls, observations or judgments")
                        continue
                    rejection = verification.policy.finish(reply.final)
                    if rejection:
                        session.add("feedback", text=rejection)
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
                    await verification.after_action(call, result, deadline)
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
        finally:
            if scheduler is not None:
                await scheduler.close()
        if runtime.control_locked:
            status, reason = "blocked", "Control stop is unconfirmed"
        elif scheduler is not None and scheduler.error is not None:
            status, reason = "failed", f"Execution queue failed: {scheduler.error}"
        report = AgentReport(status=status, reason=reason, turns=turns, steps=calls,
                             model_requests=model_requests, model_errors=model_errors,
                             results=session.results[first_result:], evidence=evidence,
                             control_locked=runtime.control_locked)
        session.finished = True
        context.log.write("task_finished", **report.model_dump())
        return report
