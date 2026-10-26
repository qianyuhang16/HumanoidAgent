"""Small model-facing interface for asynchronous device actions."""

from uuid import NAMESPACE_URL, uuid5

from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult


def execution_tools(queue):
    names = [d.name for d in queue.runtime.definitions() if not d.read_only and d.device_id]
    if not names:
        return []

    async def submit(args, context):
        context.check_active()
        call = ToolCall(name=args["tool"], arguments=args["arguments"],
                        id=uuid5(NAMESPACE_URL, "execution:" + context.call_id).hex)
        try:
            task = queue.submit(call)
        except ValueError as error:
            return ToolResult(status="rejected", text=str(error))
        return ToolResult(data={"task": task, "revision": queue.revision},
                          text="Task accepted; this receipt is not physical completion. Use execution.query/wait.")

    async def query(args, context):
        if "task_id" in args:
            try:
                job = queue.get(args["task_id"])
            except ValueError as error:
                return ToolResult(status="rejected", text=str(error))
            rows = [{**queue.view(job), "arguments": job.call.arguments}]
        else:
            rows = queue.snapshot(args.get("device_id"), details=True)
        return ToolResult(data={"revision": queue.revision, "tasks": rows})

    async def wait(args, context):
        try:
            return ToolResult(data=await queue.wait(args["after"], args.get("timeout_s", 5.0)))
        except ValueError as error:
            return ToolResult(status="rejected", text=str(error))

    async def cancel(args, context):
        try:
            return ToolResult(data={"task": await queue.cancel(args["task_id"]), "revision": queue.revision})
        except ValueError as error:
            return ToolResult(status="rejected", text=str(error))

    string = {"type": "string", "minLength": 1}
    def definition(name, description, properties, required=(), **extra):
        return ToolDefinition(name="execution." + name, description=description,
            parameters={"type": "object", "properties": properties, "required": list(required),
                        "additionalProperties": False}, **extra)

    # These tools manage scheduling, never claim physical control themselves. The
    # queued action is validated, logged and controlled by ExecutionManager.
    return [
        FunctionTool(definition("submit", "Submit one device action asynchronously. Arguments must match the target tool schema. Same-device tasks queue; independent devices can run concurrently. Returns task_id, not action success.",
            {"tool": {"type": "string", "enum": names}, "arguments": {"type": "object"}},
            ("tool", "arguments")), submit),
        FunctionTool(definition("query", "List device tasks and results. task_id selects one task; device_id filters the list. Read full completed results via state.read(result_ref).",
            {"task_id": string, "device_id": string}), query),
        FunctionTool(definition("wait", "Wait for queue changes after a revision, or a bounded timeout. Waiting does not cancel actions. Use the returned revision for the next wait.",
            {"after": {"type": "integer", "minimum": 0},
             "timeout_s": {"type": "number", "minimum": 0, "maximum": 10}}, ("after",), timeout_s=12), wait),
        FunctionTool(definition("cancel", "Cancel a queued or running task. Running cancellation waits for stop confirmation; unknown control is not a successful stop.",
            {"task_id": string}, ("task_id",)), cancel),
    ]
