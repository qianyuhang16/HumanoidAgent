from __future__ import annotations

import json

from primitive_vla.contracts import Domain
from primitive_vla.memory import Memory
from primitive_vla.planning.actions import ACTION_SCHEMA
from primitive_vla.planning.base import PlanningContext
from primitive_vla.planning.conversation import build_conversation
from primitive_vla.planning.prompts import INSTRUCTIONS
from primitive_vla.runtime.session import Session
from primitive_vla.skills import SkillStore
from primitive_vla.tools.registry import ToolRegistry


def model_result(value: dict, image_aliases: dict[str, str], state_tools: set[str]) -> dict:
    """Project framework envelopes only; business output belongs to the tool."""
    if {"session_id", "event_type", "payload"} <= value.keys():
        content = value["payload"]
        if value["event_type"] in {"tool_finished", "verification"}:
            content = model_result({"call_id": value["call_id"], "name": value["source"],
                                    "status": value["status"], **content}, image_aliases, state_tools)
        return {"id": value["id"], "source": value["source"], "content": content}
    if {"call_id", "name", "status"} <= value.keys():
        # A finished call can still be queried or referenced by a verification tool.
        result = {key: value[key] for key in ("name", "call_id", "status")}
        if value.get("error"):
            result["error"] = value["error"]
        if value.get("result"):
            output = value["result"]
            if output["payload"]:
                result["output"] = output["payload"]
            if output.get("resource_refs"):
                local_images = {ref["id"] for ref in output["resource_refs"]
                                if ref["media_type"] in {"image/png", "image/jpeg", "image/webp", "image/gif"}
                                and ref["uri"].startswith("blob:")}
                if local_images:
                    result["images"] = [image_aliases[ref["id"]] for ref in output["resource_refs"]
                                        if ref["id"] in local_images and ref["id"] in image_aliases]
                resources = [{"id": ref["id"], "description": ref["description"]}
                             for ref in output["resource_refs"] if ref["id"] not in local_images]
                if resources:
                    result["resources"] = resources
            if output.get("verification"):
                result["verification"] = {key: output["verification"][key]
                    for key in ("subject", "conclusion", "reason", "failure_type") if output["verification"].get(key)}
        if result["name"] in state_tools and "images" in result and result["status"] == "completed" and not any(
                key in result for key in ("output", "error", "verification", "resources")):
            return {"images": result["images"]}
        return result
    if "input_schema" in value and "name" in value:
        result = {key: value[key] for key in ("name", "description", "input_schema")}
        if "skill" in value:
            result["skill"] = model_result(value["skill"], image_aliases, state_tools)
            result["skill"].pop("input_schema", None)  # Already present in this tool definition.
        return result
    if {"content", "offset", "total_chars", "version"} <= value.keys():
        result = {key: value[key] for key in ("id", "content", "offset", "total_chars")}
        if "input_schema" in value:
            result["input_schema"] = value["input_schema"]
        return result
    if "records" in value:
        return {**value, "records": [model_result(record, image_aliases, state_tools) for record in value["records"]]}
    if set(value) == {"event_version"}:
        return {"message": "Waiting ended."}
    return value


class ContextBuilder:
    def __init__(self, memory: Memory, registry: ToolRegistry, skills: SkillStore, max_chars: int):
        self.memory, self.registry, self.skills, self.max_chars = memory, registry, skills, max_chars

    def build(self, session: Session, active_calls: list[dict]) -> PlanningContext:
        # Keep action identity independently of high-rate telemetry and mutable working notes.
        journal = self.memory.history(session.id, limit=None)
        history = [r for r in journal if r.event_type in {"tool_started", "tool_finished", "verification"}]
        starts = {r.call_id: r for r in history if r.event_type == "tool_started"}
        # Images can outlive the recent-call window. Check the journal before truncating it.
        latest_execution = next((r for r in history
                                 if r.domain == Domain.EXECUTION and r.call_id in starts), None)
        recent_calls = []
        seen_calls = set()
        represented_calls = set()
        image_aliases = {value["resource_id"]: key for key, value in session.image_references.items()}
        reference_count = len(image_aliases)
        # Name model-visible images, not every frame of an unsubscribed background stream.
        for ref in session.images.values():
            if ref["id"] not in image_aliases:
                alias = f"image_{len(session.image_references) + 1}"
                session.image_references[alias] = {"resource_id": ref["id"], "record_id": ref["record_id"]}
                image_aliases[ref["id"]] = alias
        if len(image_aliases) != reference_count:
            self.memory.save_session(session.to_dict())
        tool_specs = self.registry.catalog()
        state_tools = {spec.name for spec in tool_specs if spec.domain == Domain.STATE}
        image_fields = {spec.name: self.registry.get(spec.name).image_parameters for spec in tool_specs}
        last_result = model_result(session.last_result, image_aliases, state_tools)
        for record in history[:32]:
            if record.call_id in seen_calls:
                continue
            seen_calls.add(record.call_id)
            start = starts.get(record.call_id)
            call = model_result({"call_id": record.call_id, "name": record.source,
                                 "status": record.status, **record.payload}, image_aliases, state_tools)
            if "name" in call:
                call["evidence_id"] = record.id
                call["arguments"] = dict(start.payload["arguments"]) if start else None
                if start:
                    for key in image_fields.get(record.source, []):
                        if isinstance(call["arguments"].get(key), str):
                            value = call["arguments"][key]
                            call["arguments"][key] = image_aliases.get(value, value)
            arguments_text = json.dumps(call.get("arguments"), ensure_ascii=False)
            if len(arguments_text) > 1000:
                call["arguments"] = {"truncated": True, "preview": arguments_text[:800], "record_id": start.id}
            output_text = json.dumps(call.get("output", {}), ensure_ascii=False)
            if len(output_text) > 1600:
                call["output"] = {"truncated": True, "preview": output_text[:1200]}
            if len(recent_calls) < 6:
                represented_calls.add(record.call_id)
                if (session.last_result.get("call_id"), session.last_result.get("status")) == (record.call_id, record.status):
                    last_result = call
                else:
                    recent_calls.append(call)
        image_resources = tuple(reversed(session.images.values()))
        images = [{"ref": image_aliases[ref["id"]],
                   **({"camera": ref["metadata"]["camera"]} if "camera" in ref["metadata"]
                      else {"description": ref["description"]}),
                   **{key: ref["metadata"][key] for key in ("width", "height") if key in ref["metadata"]}}
                  for ref in image_resources]
        if latest_execution is not None:
            for label, ref in zip(images, image_resources):
                if ref["recorded_at"] < latest_execution.timestamp:
                    label["execution_since_image"] = True
        data = {
            "instructions": INSTRUCTIONS,
            "goal": session.goal,
            "user_input": {}, "last_result": {}, "active_calls": [],
            "images": images, "recent_calls": [], "capabilities": [],
            "tools": [], "skills": [], "records": [],
            "catalog_note": "Use discover_tools/list_skills for additional pages.",
        }
        schema_size = len(json.dumps(ACTION_SCHEMA, ensure_ascii=False))
        available = self.max_chars - schema_size

        def size() -> int:
            return len(json.dumps(data, ensure_ascii=False))

        if size() > available:
            raise ValueError("context budget too small for goal, returned images and framework action schema")
        user_input = self.memory.evidence(session.user_input_record_id).payload if session.user_input_record_id else {}
        for key, value, record_id in (("user_input", user_input, session.user_input_record_id),
                                      ("last_result", last_result, session.last_result_record_id)):
            result_text = json.dumps(value, ensure_ascii=False)
            allowance = max(0, (available - size()) // 2)
            data[key] = value if len(result_text) <= allowance else {
                "truncated": True, "preview": result_text[:max(0, allowance - 200)],
                "record_id": record_id, "hint": "Use evidence_get to read the full record in pages.",
            }
        tool_catalog = self.registry.discover(session.robot_type, session.allowed_tools)
        candidates = [
            ("active_calls", [model_result(call, image_aliases, state_tools) for call in active_calls]),
            ("recent_calls", recent_calls),
            ("capabilities", [model_result(value, image_aliases, state_tools) for value in reversed(session.capability_reads.values())
                              if value != session.last_result]),
            ("tools", tool_catalog),
            ("skills", [{"id": s["id"], "description": s["description"]} for s in self.skills.catalog()[:20]]),
        ]
        recent = self.memory.search(filters={"session_id": session.id}, limit=20, collapse_progress=True)
        relevant = self.memory.search(session.goal, {"lifetime": "long"}, limit=10)
        relevant_ids = {record.id for record in relevant}
        seen: set[str] = set()
        records = []
        for record in recent + relevant:
            if record.id in seen:
                continue
            seen.add(record.id)
            if record.event_type in {"tool_started", "tool_finished", "verification"}:
                if record.call_id in represented_calls or record.id not in relevant_ids:
                    continue  # Recent calls are bounded separately; retain distinct relevant history.
            if record.event_type == "task_created" and record.session_id == session.id:
                continue
            if record.id == session.user_input_record_id:
                continue
            item = model_result(record.to_dict(), image_aliases, state_tools)
            serialized = json.dumps(item["content"], ensure_ascii=False)
            if len(serialized) > 1600:
                item["content"] = {"truncated": True, "preview": serialized[:1200]}
            if record.resource_refs:
                item["resources"] = [{"id": image_aliases.get(ref.id, ref.id), "description": ref.description}
                                     for ref in record.resource_refs]
            records.append(item)
        candidates.append(("records", records))
        for key, items in candidates:
            for item in items:
                data[key].append(item)
                overflow = size() - available
                if overflow > 0 and key == "records" and item["content"].get("truncated"):
                    preview = item["content"]["preview"]
                    item["content"]["preview"] = preview[:max(0, len(preview) - overflow)]
                if size() > available:
                    data[key].pop()
        data["recent_calls"].reverse()  # Read completed attempts in chronological order.
        messages = build_conversation({**data, "tools": tool_catalog}, list(reversed(journal)), image_resources,
                                      lambda value: model_result(value, image_aliases, state_tools),
                                      available - len(INSTRUCTIONS))
        return PlanningContext(data, ACTION_SCHEMA, image_resources, messages)
