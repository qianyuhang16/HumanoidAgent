"""One bounded, read-only TCP snapshot exchange with the MagicBot ROS bridge."""

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
import json
import struct
from time import monotonic

from PIL import Image


@dataclass(frozen=True)
class Frame:
    name: str
    jpeg: bytes
    timestamp_ns: int
    age_ms: float
    width: int
    height: int


def timestamp(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"Missing or invalid {label}")
    return value


def decode_frames(reply, cameras, max_age_s, max_image_bytes):
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        reason = reply.get("error", "Invalid response") if isinstance(reply, dict) else "Expected a JSON object"
        raise RuntimeError(f"Camera bridge: {reason}")
    sampled_at = timestamp(reply.get("timestamp_ns"), "bridge timestamp")
    images, stamps = reply.get("images"), reply.get("image_timestamps_ns")
    if not isinstance(images, dict) or not isinstance(stamps, dict):
        raise ValueError("Camera bridge did not provide images and timestamps")
    frames = []
    for name in cameras:
        stamp = timestamp(stamps.get(name), f"{name} image timestamp")
        age_s = (sampled_at - stamp) / 1e9
        if age_s < -.25:
            raise ValueError(f"Camera {name}: timestamp is ahead of bridge clock; check clock synchronization")
        if age_s > max_age_s:
            raise ValueError(f"Camera {name}: stale frame ({age_s:.3f}s > {max_age_s:.3f}s)")
        encoded = images.get(name)
        if not isinstance(encoded, str) or len(encoded) > 4 * ((max_image_bytes + 2) // 3):
            raise ValueError(f"Camera {name}: missing or oversized JPEG")
        try:
            jpeg = base64.b64decode(encoded, validate=True)
            if len(jpeg) > max_image_bytes:
                raise ValueError("Oversized image")
            with Image.open(BytesIO(jpeg)) as image:
                if image.format != "JPEG" or image.width * image.height > 20_000_000:
                    raise ValueError("Expected JPEG of at most 20 megapixels")
                image.load()
                width, height = image.size
        except (ValueError, OSError, Image.DecompressionBombError) as error:
            raise ValueError(f"Camera {name}: invalid JPEG") from error
        frames.append(Frame(name, jpeg, stamp, round(age_s * 1000, 3), width, height))
    return sampled_at, frames


class CameraBridge:
    def __init__(self, host, port, connect_timeout_s=5, request_timeout_s=8,
                 max_response_bytes=32 * 1024 * 1024):
        self.host, self.port = host, port
        self.connect_timeout_s, self.request_timeout_s = connect_timeout_s, request_timeout_s
        self.max_response_bytes = max_response_bytes
        self.writers = set()
        self.closed = False

    async def observation(self, context):
        context.check_active()
        if self.closed:
            raise RuntimeError("Camera bridge client is closed")

        async def exchange():
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout_s)
            self.writers.add(writer)
            try:
                context.check_active()
                if self.closed:
                    raise RuntimeError("Camera bridge client is closed")
                # No caller-supplied operation or control payload can reach the robot.
                body = b'{"op":"get_observation"}'
                writer.write(struct.pack("!I", len(body)) + body)
                await writer.drain()
                size, = struct.unpack("!I", await reader.readexactly(4))
                if not 0 < size <= self.max_response_bytes:
                    raise ValueError(f"Camera bridge response length is invalid: {size}")
                return json.loads(await reader.readexactly(size))
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), .5)
                except (OSError, asyncio.TimeoutError):
                    pass
                finally:
                    self.writers.discard(writer)

        return await asyncio.wait_for(exchange(), min(self.request_timeout_s, max(.001, context.deadline - monotonic())))

    async def close(self):
        self.closed = True
        writers = list(self.writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(*(asyncio.wait_for(writer.wait_closed(), .5) for writer in writers), return_exceptions=True)
