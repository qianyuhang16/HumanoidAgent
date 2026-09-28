"""Strict wire schemas for Agent decisions, assessments and connection checks."""

import json


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


def choice(values):
    return {"type": "string", "enum": list(values)}


def output_schema(purpose, context):
    string = {"type": "string"}
    if purpose == "outcome_assessment":
        return obj({"goal": {"type": ["string", "null"]}, "verdict": choice(["satisfied", "unsatisfied", "unknown"]),
                    "reason": string, "evidence": array(string)})
    if purpose == "tool_step":
        # Tool arguments may use arbitrary JSON Schema (including optional
        # properties). Keep them in a JSON string on this strict-output wire;
        # the tool runtime validates the decoded value with the original schema.
        names = [tool["name"] for tool in context["tools"]]
        return obj({"calls": array(obj({"name": choice(names) if names else string, "arguments_json": string})),
                    "final": {"anyOf": [obj({"status": choice(["completed", "blocked", "failed"]),
                                             "reason": string, "evidence": array(string)}), {"type": "null"}]},
                    "memory": {"type": ["string", "null"]},
                    "assessments": array(obj({"call_id": string,
                        "goal": {"type": ["string", "null"]},
                        "verdict": choice(["satisfied", "unsatisfied", "unknown"]),
                        "reason": string, "evidence": array(string)}))})
    if purpose == "connection_test":
        return obj({"ok": {"type": "boolean"}})
    raise ValueError(f"Unsupported Codex purpose: {purpose}")


def normalize(purpose, result):
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    if purpose == "tool_step":
        for call in result.get("calls", []):
            call["arguments"] = json.loads(call.pop("arguments_json"))
            if not isinstance(call["arguments"], dict):
                raise ValueError("Tool arguments must be an object")
    return result
