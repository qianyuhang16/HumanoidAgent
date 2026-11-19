from jsonschema import Draft202012Validator


def object_schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


STRING = {"type": "string", "minLength": 1}
RECORD_ID = {**STRING, "description": "Journal record ID, not a resource/call ID; see records[].id or session.last_result_record_id."}
RESOURCE_ID = {**STRING, "description": "Resource ID from resource_refs[].id or Tool resource outputs, not a journal record ID."}
PAGE = {"offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100}}

# These are runtime operations, not robot/model tools. Tool schemas remain Provider-owned.
ARGUMENT_SCHEMAS = {
    "discover_tools": object_schema({"query": {"type": "string"}, **PAGE}),
    "inspect_tool": object_schema({"name": STRING}, ("name",)),
    "call_tool": object_schema({"name": STRING, "arguments": {
        "type": "object", "description": "Use this Tool's input_schema from inspect_tool/read_skill; retrieve it before choosing unknown parameters."}}, ("name", "arguments")),
    "tool_status": object_schema({"call_id": STRING}, ("call_id",)),
    "cancel_tool": object_schema({"call_id": STRING}, ("call_id",)),
    "list_skills": object_schema(PAGE),
    "read_skill": object_schema({"id": STRING, "offset": {"type": "integer", "minimum": 0},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}, ("id",)),
    "memory_search": object_schema({"query": {"type": "string", "description":
        "Literal, case-insensitive AND of whitespace-separated terms. Use a short keyword; empty query lists filtered records."},
        "filters": {"type": "object"}, **PAGE}),
    "memory_get": object_schema({"id": STRING, "offset": {"type": "integer", "minimum": 0},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}, ("id",)),
    "memory_index": object_schema({"offset": {"type": "integer", "minimum": 0},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}),
    "memory_write": object_schema({"title": STRING, "content": STRING,
                                   "source_ids": {"type": "array", "items": RECORD_ID},
                                   "tags": {"type": "array", "items": STRING},
                                   "lifetime": {"enum": ["short", "long"]}}, ("title", "content")),
    "memory_update": object_schema({"id": STRING, "changes": {"type": "object"}}, ("id", "changes")),
    "memory_delete": object_schema({"id": STRING}, ("id",)),
    "evidence_get": object_schema({"id": RECORD_ID, "version": STRING,
                                   "offset": {"type": "integer", "minimum": 0},
                                   "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}, ("id",)),
    "get_resource": object_schema({"id": RESOURCE_ID}, ("id",)),
    "subscribe": object_schema({"sources": {"type": "array", "items": STRING}}, ("sources",)),
    "wait": object_schema({"seconds": {"type": "number", "exclusiveMinimum": 0}}),
    "ask_user": object_schema({"question": STRING, "resource_ids": {"type": "array", "items": RESOURCE_ID}}, ("question",)),
    "finish": object_schema({"message": STRING, "outcome": {"enum": ["success", "failed"]},
                             "evidence_ids": {"type": "array", "items": RECORD_ID}}, ("message", "outcome")),
}
ACTION_SCHEMA = {"oneOf": [object_schema({"action": {"const": action}, "arguments": schema},
                                         ("action", "arguments"))
                          for action, schema in ARGUMENT_SCHEMAS.items()]}
ACTION_VALIDATOR = Draft202012Validator(ACTION_SCHEMA)
