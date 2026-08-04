"""An in-process pub/sub hub for per-project SSE progress streams.

Background pipeline stages (ingest, analyze, translate, export) run as asyncio
tasks and publish events here; `GET /projects/{id}/events` subscribes and
forwards them to the webview. Kept in-process (not persisted) because progress
is inherently ephemeral — a reconnect just misses events until the next one,
which is fine since every stage also lands in the store as its terminal state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectProgress:
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)


class ProgressHub:
    def __init__(self) -> None:
        self._projects: dict[str, ProjectProgress] = {}

    def _get(self, project_id: str) -> ProjectProgress:
        return self._projects.setdefault(project_id, ProjectProgress())

    def publish(self, project_id: str, event: dict[str, Any]) -> None:
        for queue in self._get(project_id).subscribers:
            queue.put_nowait(event)

    async def subscribe(self, project_id: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        state = self._get(project_id)
        state.subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            state.subscribers.remove(queue)


#: Process-wide singleton. One hub is enough: events are keyed by project id.
hub = ProgressHub()


class PauseRegistry:
    """Tracks which projects have an in-flight translation run asked to pause.

    Plain in-process flags, not persisted: a running `translate_book` polls
    `should_pause(project_id)` between segments and stops picking up new work
    once it's set, then clears it on the way out. A flag set with no run
    underway (or after the run already finished) is harmless — it just gets
    cleared the next time a run starts.
    """

    def __init__(self) -> None:
        self._requested: set[str] = set()

    def request(self, project_id: str) -> None:
        self._requested.add(project_id)

    def clear(self, project_id: str) -> None:
        self._requested.discard(project_id)

    def should_pause(self, project_id: str) -> bool:
        return project_id in self._requested


#: Process-wide singleton, mirroring `hub`.
pause_registry = PauseRegistry()
