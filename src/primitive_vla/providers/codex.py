"""Run authenticated Codex CLI as a bounded multimodal decision provider."""

import asyncio
import json
import mimetypes
import os
import signal
import tempfile
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx

from .base import ProviderError, request_summary
from .codex_schema import normalize, output_schema


class CodexClient:
    def __init__(self, config, media_root: Path, log=None):
        self.config, self.media_root, self.log = config, media_root.resolve(), log
        self.processes = set()

    async def _image(self, reference, directory, index):
        if reference.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=self.config.timeout_s) as http:
                async with http.stream("GET", reference) as response:
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "").split(";")[0]
                    extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
                    if mime not in extensions:
                        raise ProviderError("Codex 图像 URL 必须返回 PNG/JPEG/WebP/GIF")
                    path = directory / f"image-{index}{extensions[mime]}"
                    size = 0
                    with path.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.config.max_image_bytes:
                                raise ProviderError("Image exceeds configured size limit")
                            stream.write(chunk)
                    return path
        path = (self.media_root / reference).resolve()
        if not path.is_relative_to(self.media_root):
            raise ProviderError("Image must be inside the workspace media directory")
        if mimetypes.guess_type(path.name)[0] not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            raise ProviderError("Unsupported image format")
        if path.stat().st_size > self.config.max_image_bytes:
            raise ProviderError("Image exceeds configured size limit")
        return path

    async def _stop(self, process):
        # The CLI can create descendants. Reap its whole isolated process group.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def complete(self, purpose, prompt, context, images=None):
        request_id, started = uuid4().hex, monotonic()
        sources = list((images or {}).items())[:self.config.max_images]

        def emit(event, **data):
            if self.log:
                self.log.write(event, request_id=request_id, purpose=purpose, provider="codex",
                               model=self.config.model, effort=self.config.codex.reasoning_effort, **data)

        emit("model_requested", image_sources=[s for s, _ in sources], input=request_summary(context))
        try:
            result, usage = await asyncio.wait_for(
                self._invoke(purpose, prompt, context, sources, emit), self.config.timeout_s)
        except asyncio.CancelledError:
            emit("model_cancelled", reason="任务取消，Codex 进程已终止")
            raise
        except asyncio.TimeoutError:
            emit("model_failed", reason="Codex 请求超时")
            raise ProviderError("Codex 请求超时；可调整 vlm.timeout_s") from None
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError, ProviderError) as error:
            reason = str(error) if isinstance(error, ProviderError) else "Codex 调用失败；检查 CLI 路径、图像和结构化返回"
            emit("model_failed", reason=reason)
            raise ProviderError(reason) from None
        emit("model_response", result=result, usage=usage, latency_ms=round((monotonic() - started) * 1000))
        return result

    async def _invoke(self, purpose, prompt, context, sources, emit):
        with tempfile.TemporaryDirectory(prefix="primitive-codex-") as temporary:
            directory = Path(temporary)
            schema_path, output_path = directory / "schema.json", directory / "decision.json"
            schema_path.write_text(json.dumps(output_schema(purpose, context)))
            args = [self.config.codex.executable, "exec", "--ignore-user-config", "--ephemeral",
                    "--skip-git-repo-check", "--sandbox", "read-only", "--model", self.config.model,
                    "-c", f'model_reasoning_effort="{self.config.codex.reasoning_effort}"',
                    "-c", "features.shell_tool=false",
                    "--json", "--output-schema", str(schema_path), "-o", str(output_path), "-C", str(directory)]
            for index, (_, reference) in enumerate(sources):
                args.extend(["--image", str(await self._image(reference, directory, index))])
            args.append("-")
            context = {k: v for k, v in context.items() if k != "schema"}
            instruction = ("You are the decision module of an embodied agent. Use only the provided observations, "
                           "capability descriptions and supplied interfaces. Do not use external tools or execute code. "
                           "Return the required JSON, with brief user-facing decision/evidence summaries in Chinese. "
                           "Do not provide private chain-of-thought. Follow the output schema.\n" + prompt)
            if purpose == "parse":
                instruction += "\nEncode each goal's conditions as a list of {predicate,value}, as required by the output schema."
            if purpose == "tool_step":
                instruction += "\nFor each tool call encode its arguments object as a JSON string in arguments_json, as required by the output schema."
                instruction += "\nFor each state claim encode its value as JSON in value_json. Return empty claims when no state update is needed."
            payload = json.dumps({"instructions": instruction, "context": context,
                                  "image_sources_in_order": [s for s, _ in sources]}, ensure_ascii=False).encode()
            process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
                limit=1024 * 1024)
            self.processes.add(process)
            usage, failed = {}, False

            async def stdout():
                nonlocal usage, failed
                async for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    kind = event.get("type")
                    if kind in ("thread.started", "turn.started"):
                        emit("model_progress", phase=kind)
                    elif kind == "turn.completed":
                        usage = event.get("usage", {})
                    elif kind in ("error", "turn.failed"):
                        failed = True
                    # Raw reasoning, tool payloads and provider diagnostics stay out of task records.

            async def stderr():
                while await process.stderr.read(8192):
                    pass

            readers = [asyncio.create_task(stdout()), asyncio.create_task(stderr())]
            try:
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                await asyncio.gather(*readers)
                await process.wait()
                if process.returncode or failed or not output_path.exists():
                    raise ProviderError(f"Codex 未完成决策（exit={process.returncode}）；检查 codex login status、模型访问权限和网络")
                return normalize(purpose, json.loads(output_path.read_text())), usage
            finally:
                await asyncio.shield(self._stop(process))
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                self.processes.discard(process)

    async def close(self):
        await asyncio.gather(*(self._stop(process) for process in list(self.processes)))
