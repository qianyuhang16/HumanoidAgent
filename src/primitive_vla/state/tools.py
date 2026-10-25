"""Small Agent-facing views; full evidence is retrieved only when requested."""

import json

from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from primitive_vla.media import image_refs


def state_tools(state, capabilities):
    async def query(args, context):
        return ToolResult(data={"state": state.query(args.get("subject"), args.get("property"),
                                                    limit=args.get("limit", 20))})

    async def read(args, context):
        try:
            record = state.read(args["id"])
        except ValueError as error:
            return ToolResult(status="rejected", text=str(error))
        body = json.dumps(record, ensure_ascii=False)
        offset, length = args.get("offset", 0), args.get("length", 8000)
        end = offset + length
        artifacts = record["payload"].get("artifacts", {})
        return ToolResult(data={"content": body[offset:end], "total_chars": len(body),
                                "next_offset": end if end < len(body) else None},
                          artifacts=artifacts, images=image_refs(artifacts))

    async def estimate(args, context):
        try:
            result, attempts = await capabilities.estimate(args["backend"], args["subject"], args["property"],
                                                          args["evidence"], context)
        except ValueError as error:
            return ToolResult(status="rejected", text=str(error))
        context.check_active()
        producer = f"state:{args['backend']}:{attempts[-1]['implementation']}" if attempts else args["backend"]
        references = state.record_claims(result.claims, producer, execution_id=context.call_id)
        return ToolResult(status="error" if result.status == "error" else "ok", text=result.reason,
                          data={"capability_status": result.status, "attempts": attempts,
                                "artifacts": result.artifacts, "backend": args["backend"], "state_refs": references})

    string = {"type": "string", "minLength": 1}
    tools = [FunctionTool(ToolDefinition(name="state.query", description=(
        "Query current environment judgments with source, evidence and validity. Conflicts and stale records are explicit."),
        parameters={"type": "object", "properties": {"subject": string, "property": string,
        "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}), query),
        FunctionTool(ToolDefinition(name="state.read", description="Read a full evidence record by ID; paginate large records.",
        parameters={"type": "object", "properties": {"id": string,
        "offset": {"type": "integer", "minimum": 0},
        "length": {"type": "integer", "minimum": 1, "maximum": 16000}},
        "required": ["id"], "additionalProperties": False}), read)]
    if capabilities.bindings:
        tools.append(FunctionTool(ToolDefinition(name="state.estimate", description=(
            "Ask a state backend to estimate a subject property using evidence IDs. Supported contracts: "
            + json.dumps(capabilities.descriptions())), parameters={"type": "object", "properties": {
            "backend": {"type": "string", "enum": list(capabilities.bindings)}, "subject": string, "property": string,
            "evidence": {"type": "array", "items": string, "minItems": 1, "maxItems": 16}},
            "required": ["backend", "subject", "property", "evidence"], "additionalProperties": False}), estimate))
    return tools
