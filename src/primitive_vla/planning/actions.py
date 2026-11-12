from jsonschema import Draft202012Validator


def object_schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


STRING = {"type": "string", "minLength": 1}
PAGE = {"offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100}}

# These are runtime operations, not robot/model tools. Tool schemas remain Provider-owned.
ARGUMENT_SCHEMAS = {
    "discover_tools": object_schema({"query": {"type": "string"}, **PAGE}),
    "inspect_tool": object_schema({"name": STRING}, ("name",)),
    "call_tool": object_schema({"name": STRING, "arguments": {"type": "object"}}, ("name", "arguments")),
    "tool_status": object_schema({"call_id": STRING}, ("call_id",)),
    "cancel_tool": object_schema({"call_id": STRING}, ("call_id",)),
    "list_skills": object_schema(PAGE),
    "read_skill": object_schema({"id": STRING, "offset": {"type": "integer", "minimum": 0},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}, ("id",)),
    "memory_search": object_schema({"query": {"type": "string"}, "filters": {"type": "object"}, **PAGE}),
    "memory_get": object_schema({"id": STRING, "offset": {"type": "integer", "minimum": 0},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}, ("id",)),
    "memory_index": object_schema({"offset": {"type": "integer", "minimum": 0},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}),
    "memory_write": object_schema({"title": STRING, "content": STRING,
                                   "source_ids": {"type": "array", "items": STRING},
                                   "tags": {"type": "array", "items": STRING},
                                   "lifetime": {"enum": ["short", "long"]}}, ("title", "content")),
    "memory_update": object_schema({"id": STRING, "changes": {"type": "object"}}, ("id", "changes")),
    "memory_delete": object_schema({"id": STRING}, ("id",)),
    "evidence_get": object_schema({"id": STRING, "version": STRING,
                                   "offset": {"type": "integer", "minimum": 0},
                                   "limit": {"type": "integer", "minimum": 1, "maximum": 16000}}, ("id",)),
    "get_resource": object_schema({"id": STRING}, ("id",)),
    "subscribe": object_schema({"sources": {"type": "array", "items": STRING}}, ("sources",)),
    "wait": object_schema({"seconds": {"type": "number", "exclusiveMinimum": 0}}),
    "ask_user": object_schema({"question": STRING, "resource_ids": {"type": "array", "items": STRING}}, ("question",)),
    "finish": object_schema({"message": STRING, "outcome": {"enum": ["success", "failed"]},
                             "evidence_ids": {"type": "array", "items": STRING}}, ("message", "outcome")),
}
ACTION_SCHEMA = {"oneOf": [object_schema({"action": {"const": action}, "arguments": schema},
                                         ("action", "arguments"))
                          for action, schema in ARGUMENT_SCHEMAS.items()]}
ACTION_VALIDATOR = Draft202012Validator(ACTION_SCHEMA)
