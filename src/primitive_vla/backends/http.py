"""Optional HTTP robot gateway adapter. Protocol is documented in docs/backends.md."""

import asyncio
import os
from dataclasses import asdict

import httpx

from primitive_vla.domain.models import ExecutionStatus, Fact, Observation, SkillFeedback, Truth
from primitive_vla.config.schema import BackendConfig


class HTTPBackend:
    def __init__(self, config: BackendConfig):
        self.config, self.model_version = config, config.model_version
        key = os.environ.get(config.api_key_env, "")
        self.http = httpx.AsyncClient(base_url=config.base_url.rstrip("/"), timeout=config.request_timeout_s,
                                     headers={"Authorization": f"Bearer {key}"} if key else {})

    async def request(self, method, path, **kwargs):
        try:
            response = await self.http.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            raise RuntimeError("Robot gateway request failed; physical effects may be unknown") from None

    async def observe(self):
        data = await self.request("GET", "/observation")
        facts = {key: Fact(value=Truth(value["value"]), source=value["source"], timestamp=value["timestamp"],
                           confidence=value.get("confidence", 1), ttl_s=value.get("ttl_s", 5))
                 for key, value in data["facts"].items()}
        return Observation(facts, timestamp=data["timestamp"], images=data.get("images", {}),
                           proprioception=data.get("proprioception", {}), frames=data.get("frames", {}),
                           sensors=tuple(data.get("sensors", [])))

    async def warmup(self) -> None:
        pass

    async def run(self, call, candidate, lease, authority, feedback):
        if not authority.accepts(call.call_id, lease.generation):
            return ExecutionStatus.CANCELLED
        accepted = await self.request("POST", "/calls", json={"call": asdict(call), "generation": lease.generation,
                                                               "resources": lease.resources})
        if accepted.get("call_id") != call.call_id:
            raise RuntimeError("Gateway returned a mismatched call ID")
        while authority.accepts(call.call_id, lease.generation):
            result = await self.request("GET", f"/calls/{call.call_id}")
            if result.get("call_id") != call.call_id or result.get("generation") != lease.generation:
                raise RuntimeError("Gateway returned stale invocation feedback")
            feedback(SkillFeedback(call.call_id, lease.generation, result.get("phase", "running"),
                                   control_healthy=result.get("control_healthy", True)))
            status = result["status"]
            if status in {s.value for s in ExecutionStatus}:
                return ExecutionStatus(status)
            if status not in ("accepted", "running"):
                raise RuntimeError("Invalid gateway execution status")
            await asyncio.sleep(self.config.poll_s)
        return ExecutionStatus.CANCELLED

    async def stop(self, call_id, reason):
        result = await self.request("POST", f"/calls/{call_id}/cancel", json={"reason": reason})
        return result.get("call_id") == call_id and result.get("stopped") is True

    async def close(self):
        await self.http.aclose()
