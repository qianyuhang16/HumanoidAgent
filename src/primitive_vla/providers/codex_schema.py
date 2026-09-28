"""Strict Codex wire schemas; normalize predicate lists to domain condition maps."""

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
    if purpose == "parse":
        return obj({"goals": array(obj({
            "id": string,
            "conditions": array(obj({"predicate": choice(context["facts"]), "value": choice(["true", "false"])})),
            "after": array(string),
        })), "forbidden_skills": array(string)})
    if purpose == "tool_plan":
        return obj({"call": context["tool_schema"], "working_memory": string, "reason": string})
    if purpose == "plan":
        properties = {"action": choice(["skill", "observe", "stop"]), "index": {"type": ["integer", "null"]}, "reason": string}
        if "working_memory" in context:
            properties["working_memory"] = obj({"hypothesis": string,
                "trials": array(obj({"configuration": string, "evidence": string,
                                     "outcome": choice(["supported", "rejected", "unknown"])})),
                "uncertainties": array(string)})
        return obj(properties)
    if purpose == "handoff":
        return obj({"action": choice(context["actions"]), "reason": string})
    if purpose == "verify":
        return obj({"criteria": array(obj({"id": choice(g["id"] for g in context["goals"]),
            "status": choice(["success", "failure", "unknown"]), "reason": string,
            "evidence_refs": array(choice(context["evidence_ids"]))}))})
    if purpose == "visual_inspection":
        properties = {"target_detection_ids": array({"type": "integer"}), "predicates": array(obj({
            "predicate": choice(context["criteria"]), "value": choice(["true", "false", "unknown"]),
            "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "evidence": string,
        }))}
        if context.get("allow_partial_targets"):
            properties.pop("target_detection_ids")
            properties["target_assignments"] = array(obj({"side": choice(["left", "right"]),
                "detection_id": {"type": "integer"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": string}))
        return obj(properties)
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
    if purpose == "parse":
        for goal in result["goals"]:
            conditions = goal["conditions"]
            mapped = {c["predicate"]: c["value"] for c in conditions}
            if len(mapped) != len(conditions):
                raise ValueError("Duplicate goal predicates")
            goal["conditions"] = mapped
    return result
