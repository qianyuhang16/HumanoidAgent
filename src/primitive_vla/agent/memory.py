"""Append-only session history with bounded views and explicit retrieval."""

from copy import deepcopy
import json

from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from primitive_vla.verification.images import paired_images
from .contracts import ModelContext


class Session:
    def __init__(self, task="", *, instructions=(), history_limit=20, result_chars=12000,
                 memory_chars=12000, metadata=None):
        self.task, self.instructions = task, tuple(instructions)
        self.history_limit, self.result_chars, self.memory_chars = history_limit, result_chars, memory_chars
        self.metadata = deepcopy(metadata or {})
        self.events: list[dict] = []
        self.results: list[dict] = []
        self.working_memory = ""
        self.images = {}
        self.evidence_images = {}
        self.outcomes = []
        self.verification_mode = "main_agent"
        self.finished = False

    def add(self, kind, **data):
        record = {"index": len(self.events), "kind": kind, **deepcopy(data)}
        self.events.append(record)
        return record

    def restore(self, snapshot):
        """Carry conversational evidence forward, without restoring tool handles."""
        self.events = deepcopy(snapshot.get("history", []))
        if self.events and not any(e["kind"] == "user" for e in self.events):
            # Keep legacy indices stable for recorded session.read references.
            self.add("user", instruction=snapshot.get("instruction", ""), historical=True)
        self.results = [{k: deepcopy(v) for k, v in event.items() if k not in ("index", "kind")}
                        for event in self.events if event["kind"] == "tool_result"]
        self.working_memory = snapshot.get("working_memory", "")[:self.memory_chars]
        # Old images remain retrievable through session.read; don't present them
        # as current observations to the next turn.
        self.images = {}
        self.evidence_images = {}
        self.outcomes = [{**deepcopy(item), "historical": True} for item in snapshot.get("outcomes", [])]
        self.add("feedback", text="Continuing the conversation. Prior tool results are historical evidence; live tool handles belong to the previous turn. Obtain fresh observations and plans for new physical actions.")

    def record_reply(self, reply):
        self.add("model", **reply.model_dump())
        if reply.memory is not None:
            self.working_memory = reply.memory[:self.memory_chars]

    def record_result(self, call, result):
        item = {"call_id": call.id, "tool": call.name, "arguments": deepcopy(call.arguments),
                **result.model_dump()}
        self.results.append(deepcopy(item))
        self.add("tool_result", **item)
        for source, reference in result.images.items():
            self.images[f"{call.id}:{source}"] = reference
        if result.before_images and result.images:
            self.evidence_images = paired_images(call.id, result.before_images, result.images)

    def _view(self, event):
        text = json.dumps(event, ensure_ascii=False)
        if len(text) <= self.result_chars:
            return deepcopy(event)
        return {"index": event["index"], "kind": event["kind"],
                "call_id": event.get("call_id"), "tool": event.get("tool"),
                "status": event.get("status"), "preview": text[:self.result_chars],
                "truncated": True, "read_with": "session.read"}

    def context(self, *, remaining_turns, remaining_calls, remaining_seconds):
        return ModelContext(self.task, self.instructions,
                            tuple(self._view(e) for e in self.events[-self.history_limit:]),
                            len(self.events), self.working_memory, dict(self.images),
                            remaining_turns, remaining_calls, remaining_seconds, deepcopy(self.metadata),
                            tuple({k: deepcopy(v) for k, v in outcome.items() if k in
                                   ("call_id", "tool", "arguments", "goal", "verdict", "reason", "evidence", "historical", "source", "revision")}
                                  for outcome in self.outcomes), dict(self.evidence_images), self.verification_mode)

    def snapshot(self):
        return {"instruction": self.task, "working_memory": self.working_memory,
                "history_count": len(self.events), "history": deepcopy(self.events),
                "images": dict(self.images), "instructions": list(self.instructions),
                "metadata": deepcopy(self.metadata), "outcomes": deepcopy(self.outcomes)}

    def tools(self):
        async def read(args, context):
            index = args["index"]
            if index >= len(self.events):
                return ToolResult(status="rejected", text="History index does not exist")
            event = self.events[index]
            text = json.dumps(event, ensure_ascii=False)
            start = args.get("offset", 0)
            size = args.get("length", 8000)
            return ToolResult(data={"index": index, "total_chars": len(text),
                                    "content": text[start:start + size],
                                    "next_offset": start + size if start + size < len(text) else None},
                              images=dict(event.get("images", {})))

        async def search(args, context):
            query = args["query"].casefold()
            matches = [e for e in self.events if query in json.dumps(e, ensure_ascii=False).casefold()]
            return ToolResult(data={"matches": [{"index": e["index"], "kind": e["kind"],
                                                 "tool": e.get("tool"), "call_id": e.get("call_id")}
                                                for e in matches[-50:]], "total": len(matches)})

        return [
            FunctionTool(ToolDefinition(name="session.read", description="Read an earlier history event by index, including its images. Paginate large results using character offsets.",
                parameters={"type": "object", "properties": {"index": {"type": "integer", "minimum": 0},
                    "offset": {"type": "integer", "minimum": 0},
                    "length": {"type": "integer", "minimum": 1, "maximum": 16000}},
                    "required": ["index"], "additionalProperties": False}), read),
            FunctionTool(ToolDefinition(name="session.search", description="Find earlier attempts, tool results and evidence in the complete session history.",
                parameters={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}},
                            "required": ["query"], "additionalProperties": False}), search),
        ]
