"""Orphan protection.

The Tauri shell kills the sidecar on a clean quit, but a crashed or SIGKILL'd shell
leaves the engine running with an open port. The shell pings ``/health`` on an
interval; if those pings stop for longer than the configured timeout, we exit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.last_seen = time.monotonic()
        self._task: asyncio.Task | None = None

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def start(self) -> None:
        if self.timeout <= 0:
            log.info("heartbeat watchdog disabled")
            return
        self._task = asyncio.create_task(self._run(), name="adib-heartbeat")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        interval = max(1.0, self.timeout / 4)
        while True:
            await asyncio.sleep(interval)
            idle = time.monotonic() - self.last_seen
            if idle > self.timeout:
                log.warning("no heartbeat for %.0fs, exiting to avoid orphaning", idle)
                # os._exit rather than sys.exit: we are inside a task, and a clean
                # shutdown can block on in-flight LLM requests we no longer care about.
                os._exit(0)
