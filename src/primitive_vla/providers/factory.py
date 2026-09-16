"""Select the configured model transport without coupling callers to it."""

from .codex import CodexClient
from .vlm import VLMClient


def create_client(config, media_root, api_key=None, log=None, transport=None):
    if config.provider == "codex":
        return CodexClient(config, media_root, log)
    return VLMClient(config, media_root, api_key, log, transport)
