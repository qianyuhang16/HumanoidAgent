"""Expose real camera snapshots through the existing Agent tool Interface."""

from typing import Literal
from time import monotonic
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from primitive_vla.state.contracts import Observation
from .bridge import CameraBridge, decode_frames


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    cameras: list[Literal["front", "left", "right"]] = Field(default_factory=lambda: ["front", "left", "right"], min_length=1)
    connect_timeout_s: float = Field(default=5, gt=0)
    request_timeout_s: float = Field(default=8, gt=0)
    max_frame_age_s: float = Field(default=1, gt=0)
    max_image_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    max_response_bytes: int = Field(default=32 * 1024 * 1024, gt=0, le=64 * 1024 * 1024)

    @model_validator(mode="after")
    def unique_cameras(self):
        if len(set(self.cameras)) != len(self.cameras):
            raise ValueError("Configured cameras must be unique")
        return self


class CameraTools:
    def __init__(self, options, context):
        self.config = Options.model_validate(options)
        self.media = context.workspace / "media"
        self.media.mkdir(parents=True, exist_ok=True)
        self.bridge = CameraBridge(self.config.host, self.config.port,
            self.config.connect_timeout_s, self.config.request_timeout_s, self.config.max_response_bytes)
        self.tools = [FunctionTool(ToolDefinition(
            name="camera.observe",
            description="Read current RGB images from the real Cobot Magic cameras. Omit cameras for all configured views, or select front/left/right. Returns images, dimensions and per-camera timestamps; views are not synchronized. Stale or invalid images produce an error. This tool only observes; it provides no depth, calibration, motion or gripper control.",
            parameters={"type": "object", "properties": {"cameras": {
                "type": "array", "items": {"type": "string", "enum": self.config.cameras},
                "minItems": 1, "maxItems": len(self.config.cameras), "uniqueItems": True}},
                "additionalProperties": False},
            read_only=True, resources=["cobotmagic.cameras"], timeout_s=self.config.request_timeout_s + 1), self.observe)]

    async def observe(self, arguments, context):
        cameras = arguments.get("cameras", self.config.cameras)
        started = monotonic()
        reply = await self.bridge.observation(context)
        sampled_at, frames = decode_frames(reply, cameras, self.config.max_frame_age_s, self.config.max_image_bytes)
        elapsed_ms = (monotonic() - started) * 1000
        if any(max(0, frame.age_ms) + elapsed_ms > self.config.max_frame_age_s * 1000 for frame in frames):
            raise ValueError("Camera frame freshness budget exceeded during transfer or decoding")
        context.check_active()
        token = uuid4().hex
        images, metadata, written = {}, {}, []
        try:
            for frame in frames:
                ref = f"cobotmagic-{token}-{frame.name}.jpg"
                path = self.media / ref
                written.append(path)
                path.write_bytes(frame.jpeg)
                images[frame.name] = ref
                metadata[frame.name] = {"image_ref": ref, "width": frame.width, "height": frame.height,
                    "timestamp_ns": frame.timestamp_ns, "age_at_bridge_ms": frame.age_ms,
                    "age_upper_bound_at_receive_ms": round(max(0, frame.age_ms) + elapsed_ms, 3), "encoding": "jpeg"}
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        stamps = [frame.timestamp_ns for frame in frames]
        return ToolResult(data={"observation_id": token, "bridge_timestamp_ns": sampled_at,
            "cameras": metadata, "camera_skew_ms": round((max(stamps) - min(stamps)) / 1e6, 3),
            "synchronized": False, "depth_available": False,
            "timestamp_note": "Image ROS header stamps compared to bridge wall clock; requires compatible clocks."},
            images=images, observations=[Observation(id=f'{token}:{frame.name}', sensor=f'cobotmagic.{frame.name}',
                captured_at=frame.timestamp_ns/1e9, artifacts={'image':images[frame.name]},
                data={'width':frame.width,'height':frame.height}) for frame in frames])

    async def close(self):
        await self.bridge.close()
