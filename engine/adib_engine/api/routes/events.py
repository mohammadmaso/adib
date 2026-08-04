"""SSE progress stream for one project's active background stage."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sse_starlette import EventSourceResponse

from adib_engine.api.deps import project_path
from adib_engine.api.progress import hub

router = APIRouter(prefix="/projects", tags=["events"])


@router.get("/{project_id}/events")
async def stream_events(project_id: str, _path=Depends(project_path)) -> EventSourceResponse:
    async def generator():
        async for event in hub.subscribe(project_id):
            yield {"data": json.dumps(event)}

    return EventSourceResponse(generator())
