"""Shared on-disk encoding; no database or optional serialization dependency."""
import json
from pathlib import Path
import re

from primitive_vla.contracts import Record


def leaf_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("record/resource/session ID must be a single safe filename")
    return value


def write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, value) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def render(record: Record) -> str:
    metadata = record.to_dict()
    payload = metadata.pop("payload")
    if record.event_type == "note" and isinstance(payload.get("content"), str):
        metadata["body_format"] = "markdown"
        metadata["attributes"] = {key: value for key, value in payload.items() if key != "content"}
        body = payload["content"]
    else:
        metadata["body_format"] = "json"
        body = "```json\n" + json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n```\n"
    return "---\n" + json.dumps(metadata, ensure_ascii=False, allow_nan=False, indent=2) + "\n---\n" + body


def parse(content: str) -> Record:
    if not content.startswith("---\n"):
        raise ValueError("Memory documents require JSON frontmatter")
    header, body = content[4:].split("\n---\n", 1)
    metadata = json.loads(header)
    body_format = metadata.pop("body_format")
    if body_format == "markdown":
        payload = {**metadata.pop("attributes"), "content": body}
    elif body_format == "json" and body.startswith("```json\n") and body.endswith("\n```\n"):
        payload = json.loads(body[8:-5])
    else:
        raise ValueError("unsupported memory document body format")
    return Record.from_dict({**metadata, "payload": payload})
