from __future__ import annotations

import json

from primitive_vla.memory import Memory
from primitive_vla.planning.actions import ACTION_SCHEMA
from primitive_vla.planning.base import PlanningContext
from primitive_vla.runtime.session import Session
from primitive_vla.skills import SkillStore
from primitive_vla.tools.registry import ToolRegistry

INSTRUCTIONS = """You are the decision-maker for an embodied agent. Choose tools dynamically from the
catalog. Inspect their schemas and read referenced Skills before using unfamiliar capabilities.
Skill conditions, preparation, and recovery are your decisions; the runtime has no task workflow.
Use memory search and resource references as needed. Tool completion only proves execution ended.
Verification conclusions and evidence establish effects; failure and uncertainty require distinct
reasoning. Never claim a physical goal succeeded merely because an execution tool completed.
Use wait for background work, ask_user for missing information, and finish when justified.
Read truncated records/Skills in pages. The action schema describes framework operations.
"""


class ContextBuilder:
    def __init__(self, memory: Memory, registry: ToolRegistry, skills: SkillStore, max_chars: int):
        self.memory, self.registry, self.skills, self.max_chars = memory, registry, skills, max_chars

    def build(self, session: Session, active_calls: list[dict]) -> PlanningContext:
        data = {
            "instructions": INSTRUCTIONS,
            "session": {"id": session.id, "task_id": session.task_id, "goal": session.goal,
                        "robot_id": session.robot_id, "robot_type": session.robot_type,
                        "status": session.status, "decisions": session.decisions,
                        "tool_calls": session.tool_calls,
                        "last_result_record_id": session.last_result_record_id},
            "last_result": {}, "active_calls": [], "tools": [], "skills": [], "records": [],
            "catalog_note": "Use discover_tools/list_skills for additional pages.",
        }
        schema_size = len(json.dumps(ACTION_SCHEMA, ensure_ascii=False))
        available = self.max_chars - schema_size

        def size() -> int:
            return len(json.dumps(data, ensure_ascii=False))

        if size() > available:
            raise ValueError("context budget too small for goal and framework action schema")
        result_text = json.dumps(session.last_result, ensure_ascii=False)
        allowance = max(0, (available - size()) // 2)
        data["last_result"] = session.last_result if len(result_text) <= allowance else {
            "truncated": True, "preview": result_text[:max(0, allowance - 150)],
            "hint": "Use memory_get with last_result_record_id and a smaller page.",
        }
        candidates = [
            ("active_calls", active_calls),
            ("tools", self.registry.discover(session.robot_type, session.allowed_tools)),
            ("skills", self.skills.catalog()[:20]),
        ]
        recent = self.memory.search(filters={"session_id": session.id}, limit=20)
        relevant = self.memory.search(session.goal, {"lifetime": "long"}, limit=10)
        seen: set[str] = set()
        records = []
        for record in recent + relevant:
            if record.id in seen:
                continue
            seen.add(record.id)
            item = record.to_dict()
            serialized = json.dumps(item["payload"], ensure_ascii=False)
            if len(serialized) > 1600:
                item["payload"] = {"truncated": True, "preview": serialized[:1200]}
            records.append(item)
        candidates.append(("records", records))
        for key, items in candidates:
            for item in items:
                data[key].append(item)
                if size() > available:
                    data[key].pop()
        return PlanningContext(data, ACTION_SCHEMA)
