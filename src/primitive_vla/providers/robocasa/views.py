"""Rate-limited camera rendering on the existing MuJoCo/EGL worker."""
import asyncio
from dataclasses import dataclass, field
import time

from primitive_vla.tools.state.views import ViewFrame, ViewSpec


@dataclass(eq=False)
class _Channel:
    view_id: str
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    next_capture: float = 0
    error: Exception | None = None


class RoboCasaViews:
    """One renderer per subscribed camera, with a one-frame buffer per consumer."""

    def __init__(self, owner):
        self.owner = owner
        self._loop = asyncio.get_running_loop()
        self._channels: dict[str, _Channel] = {}
        self._active: tuple[_Channel, ...] = ()
        self._pump: asyncio.Task | None = None
        self._closed = False
        self._interval = 1 / 3  # 3 FPS target; rendering never advances physics.

    def list_views(self):
        return (
            ViewSpec("agentview", "前视相机", is_default=True),
            ViewSpec("agentview_right", "右前视相机"),
            ViewSpec("wrist", "腕部相机"),
            ViewSpec("navview", "导航相机"),
        )

    async def subscribe_view(self, view_id):
        if self._closed:
            raise RuntimeError("机器人视角已关闭")
        # The caller discovers and validates view IDs at the registration/API boundary.
        channel = self._channels.setdefault(view_id, _Channel(view_id))
        queue = asyncio.Queue(maxsize=1)
        channel.subscribers.add(queue)
        self._active = tuple(self._channels.values())
        if channel.error is not None:
            queue.put_nowait(channel.error)
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._refresh_idle())
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            channel.subscribers.remove(queue)
            if not channel.subscribers:
                del self._channels[view_id]
            self._active = () if self._closed else tuple(self._channels.values())

    async def _refresh_idle(self):
        # During an action this one queued job waits; the action's step hook renders
        # in the meantime. No timer enqueues an unbounded backlog of capture jobs.
        while self._active and not self._closed:
            await self._loop.run_in_executor(self.owner._executor, self.render_due, self.owner._scene)
            await asyncio.sleep(self._interval)

    def render_due(self, scene):
        """Called only on the EGL worker, including between native action steps."""
        now = time.monotonic()
        for channel in self._active:
            if channel.error is not None or now < channel.next_capture:
                continue
            channel.next_capture = now + self._interval
            try:
                frame = scene.render_view(channel.view_id)
            except Exception as error:
                # Preview failure belongs to this subscription, never to robot control.
                channel.error = error
                self._loop.call_soon_threadsafe(self._deliver, channel, error)
            else:
                self._loop.call_soon_threadsafe(self._deliver, channel, frame)

    def _deliver(self, channel, item: ViewFrame | Exception):
        for queue in channel.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)

    async def close(self):
        self._closed = True
        self._active = ()
        if self._pump is not None:
            await self._pump
        for channel in self._channels.values():
            self._deliver(channel, RuntimeError("机器人视角已关闭"))
