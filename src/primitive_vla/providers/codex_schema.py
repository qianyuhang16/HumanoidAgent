"""Strict Codex wire schemas; normalize predicate lists to domain condition maps."""


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


def choice(values):
    return {"type": "string", "enum": list(values)}


def output_schema(purpose, context):
    string = {"type": "string"}
    if purpose == "parse":
        return obj({"goals": array(obj({
            "id": string,
            "conditions": array(obj({"predicate": choice(context["facts"]), "value": choice(["true", "false"])})),
            "after": array(string),
        })), "forbidden_skills": array(string)})
    if purpose == "plan":
        return obj({"action": choice(["skill", "observe", "stop"]), "index": {"type": ["integer", "null"]}, "reason": string})
    if purpose == "handoff":
        return obj({"action": choice(context["actions"]), "reason": string})
    if purpose == "verify":
        return obj({"criteria": array(obj({"id": choice(g["id"] for g in context["goals"]),
            "status": choice(["success", "failure", "unknown"]), "reason": string,
            "evidence_refs": array(choice(context["evidence_ids"]))}))})
    if purpose == "connection_test":
        return obj({"ok": {"type": "boolean"}})
    raise ValueError(f"Unsupported Codex purpose: {purpose}")


def normalize(purpose, result):
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    if purpose == "parse":
        for goal in result["goals"]:
            conditions = goal["conditions"]
            mapped = {c["predicate"]: c["value"] for c in conditions}
            if len(mapped) != len(conditions):
                raise ValueError("Duplicate goal predicates")
            goal["conditions"] = mapped
    return result
