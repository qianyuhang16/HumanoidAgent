"""Local preview transport; no Planner, Tool calls, or Memory writes."""
from base64 import b64encode
from contextlib import aclosing
from dataclasses import asdict
import json
from typing import Mapping

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from primitive_vla.tools.state.views import RobotViewSource


def view_routes(robot_ids: set[str], sources: Mapping[str, RobotViewSource]) -> APIRouter:
    if not sources.keys() <= robot_ids:
        raise ValueError("view sources must belong to registered robots")
    catalogs = {robot_id: source.list_views() for robot_id, source in sources.items()}
    for views in catalogs.values():
        if (len({view.id for view in views}) != len(views)
                or any(not view.id or not view.label for view in views)
                or (views and sum(view.is_default for view in views) != 1)):
            raise ValueError("robot views require unique IDs, labels and one default")
    router = APIRouter(prefix="/api/robots")

    @router.get("/{robot_id}/views")
    async def catalog(robot_id: str):
        if robot_id not in robot_ids:
            raise HTTPException(404, "机器人不存在")
        return {"views": [asdict(view) for view in catalogs.get(robot_id, ())]}

    @router.get("/{robot_id}/views/{view_id}/stream")
    async def stream(robot_id: str, view_id: str):
        if view_id not in {view.id for view in catalogs.get(robot_id, ())}:
            raise HTTPException(404, "机器人视角不存在")

        async def events():
            try:
                async with aclosing(sources[robot_id].subscribe_view(view_id)) as frames:
                    async for frame in frames:
                        data = {"id": frame.id, "captured_at": frame.captured_at,
                                "width": frame.width, "height": frame.height,
                                "media_type": frame.media_type,
                                "image": b64encode(frame.data).decode("ascii"),
                                "metadata": frame.metadata}
                        yield f"event: frame\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n"
            except Exception as error:
                yield f"event: view_error\ndata: {json.dumps({'message': str(error)}, ensure_ascii=False)}\n\n"
            else:
                yield 'event: view_closed\ndata: {}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return router
