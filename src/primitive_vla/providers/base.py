"""The JSON decision interface shared by hosted VLMs and Codex CLI."""

from typing import Protocol


class ProviderError(RuntimeError):
    pass


class ModelClient(Protocol):
    async def complete(self, purpose: str, prompt: str, context: dict,
                       images: dict[str, str] | None = None) -> dict: ...

    async def close(self): ...


def request_summary(context: dict) -> dict:
    """Public decision inputs; never raw model reasoning or authentication data."""
    return {key: context[key] for key in (
        "instruction", "remaining_goals", "goals", "candidates", "facts", "state_version",
        "task", "history_count", "remaining_calls", "remaining_turns", "remaining_seconds"
    ) if key in context}
