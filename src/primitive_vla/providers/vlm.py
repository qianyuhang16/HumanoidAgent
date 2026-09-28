"""Multimodal JSON chat client, usable with a compatible local or hosted VLM."""

import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx

from primitive_vla.config.schema import VLMConfig


from .base import ProviderError, ProviderOutputError, request_summary


class VLMClient:
    def __init__(self, config: VLMConfig, media_root: Path, api_key: str | None = None, log=None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.config, self.media_root, self.log = config, media_root.resolve(), log
        key = api_key if api_key is not None else os.environ.get(config.api_key_env, "")
        if config.require_api_key and not key:
            raise ProviderError(f"请在 .env 或环境变量中配置 {config.api_key_env}")
        self.http = httpx.AsyncClient(timeout=config.timeout_s, transport=transport,
                                     headers={"Authorization": f"Bearer {key}"} if key else {})

    def image_url(self, reference: str) -> str:
        if reference.startswith(("http://", "https://")):
            return reference
        path = (self.media_root / reference).resolve()
        if not path.is_relative_to(self.media_root):
            raise ProviderError("Image must be inside the workspace media directory")
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            raise ProviderError("Unsupported image format")
        if path.stat().st_size > self.config.max_image_bytes:
            raise ProviderError("Image exceeds configured size limit")
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()

    async def complete(self, purpose: str, prompt: str, context: dict, images: dict[str, str] | None = None) -> dict:
        if not self.config.model.strip():
            raise ProviderError("请先配置 VLM model 和 base_url")
        content = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        sources = list((images or {}).items())[:self.config.max_images]
        for source, reference in sources:
            content.extend([{"type": "text", "text": f"Observation source: {source}"},
                            {"type": "image_url", "image_url": {"url": self.image_url(reference), "detail": self.config.image_detail}}])
        payload = {"model": self.config.model, "temperature": self.config.temperature,
                   "max_tokens": self.config.max_tokens,
                   "messages": [{"role": "system", "content": prompt + "\nReturn a single JSON object. No markdown or executable code."},
                                {"role": "user", "content": content}]}
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = monotonic()
        request_id = uuid4().hex
        if self.log:
            self.log.write("model_requested", request_id=request_id, provider="http", input=request_summary(context), purpose=purpose, model=self.config.model, image_sources=[s for s, _ in sources])
        def failure(reason, error_type=ProviderError, **diagnostics):
            if self.log:
                self.log.write("model_failed", request_id=request_id, purpose=purpose,
                               provider="http", model=self.config.model, reason=reason,
                               latency_ms=round((monotonic() - started) * 1000), **diagnostics)
            return error_type(reason)
        diagnostics = {"max_tokens": self.config.max_tokens, "output_error": "invalid_response"}
        try:
            response = await self.http.post(self.config.base_url.rstrip("/") + "/chat/completions", json=payload)
            response.raise_for_status()
            body = response.json()
            choice = body["choices"][0]
            diagnostics["finish_reason"] = choice.get("finish_reason")
            diagnostics["usage"] = body.get("usage", {})
            raw = choice["message"]["content"]
            if isinstance(raw, list):
                raw = "".join(p.get("text", "") for p in raw if p.get("type") == "text")
            diagnostics["content_chars"] = len(raw) if isinstance(raw, str) else 0
            if choice.get("finish_reason") == "length":
                diagnostics["output_error"] = "truncated"
                raise ValueError("Incomplete generation")
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                diagnostics["output_error"] = "empty_content"
                raise ValueError("Empty content")
            raw = raw.strip()
            if raw.startswith("```"):
                if not raw.endswith("```"):
                    diagnostics["output_error"] = "extra_content"
                    raise ValueError("Incomplete fence or trailing content")
                raw = raw.split("\n", 1)[1][:-3].strip()
            diagnostics["output_error"] = "invalid_json"
            result = json.loads(raw)
            if not isinstance(result, dict):
                diagnostics["output_error"] = "not_object"
                raise ValueError("Expected a JSON object")
        except asyncio.CancelledError:
            if self.log:
                self.log.write("model_cancelled", request_id=request_id, purpose=purpose,
                               provider="http", model=self.config.model, reason="任务取消")
            raise
        except httpx.HTTPStatusError as error:
            # Provider responses can echo headers; do not persist raw bodies.
            raise failure(f"VLM HTTP {error.response.status_code}; 检查地址、模型名、密钥和 JSON 模式") from None
        except httpx.HTTPError:
            raise failure("VLM 连接失败或超时；请检查模型服务") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            if isinstance(error, json.JSONDecodeError):
                diagnostics["json_error_line"] = error.lineno
                diagnostics["json_error_column"] = error.colno
                if error.msg == "Extra data":
                    diagnostics["output_error"] = "extra_content"
            if diagnostics["output_error"] == "truncated":
                reason = f"VLM 输出被截断（finish_reason=length，max_tokens={self.config.max_tokens}）；请提高模型输出额度，思考也会占用该额度"
            elif diagnostics["output_error"] == "empty_content":
                reason = "VLM 返回空 content，没有可解析的 JSON 决策"
            elif diagnostics["output_error"] == "extra_content":
                reason = "VLM 返回了多个 JSON 值或 JSON 后仍有其他内容；只能返回一个 JSON 对象，不得追加示例或空对象"
            else:
                reason = "VLM 返回格式无效，需要 choices[0].message.content 中的完整 JSON 对象"
            # Persist completion metadata, not raw output, reasoning or credentials.
            raise failure(reason, ProviderOutputError, **diagnostics) from None
        if self.log:
            self.log.write("model_response", request_id=request_id, provider="http", result=result, purpose=purpose, model=self.config.model,
                           latency_ms=round((monotonic() - started) * 1000), usage=body.get("usage", {}))
        return result

    async def close(self):
        await self.http.aclose()
