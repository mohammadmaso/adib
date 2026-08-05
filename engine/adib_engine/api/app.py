"""FastAPI application factory."""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adib_engine import __version__
from adib_engine.api.routes import (
    cover,
    events,
    gate1,
    gate2,
    gate3,
    image_provider,
    ingest,
    presets,
    projects,
    provider,
)
from adib_engine.api.watchdog import Heartbeat
from adib_engine.settings import get_settings

log = logging.getLogger(__name__)

# Paths reachable without the bearer token. Kept to the bare minimum: the shell
# needs /health to confirm the sidecar came up before it knows anything else works.
PUBLIC_PATHS = {"/health", "/openapi.json", "/docs", "/redoc"}


class HealthResponse(BaseModel):
    status: str
    version: str
    pid: int


def create_app() -> FastAPI:
    settings = get_settings()
    heartbeat = Heartbeat(settings.heartbeat_timeout)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        heartbeat.start()
        log.info("adib-engine %s ready on %s:%s", __version__, settings.host, settings.port)
        try:
            yield
        finally:
            await heartbeat.stop()

    app = FastAPI(
        title="Adib Engine",
        version=__version__,
        lifespan=lifespan,
    )

    # The webview origin is tauri://localhost (macOS/iOS) or http://tauri.localhost
    # (Windows/Linux). Dev runs from the Vite server instead.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "http://localhost:1420",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        if not settings.auth_token:  # standalone dev run, no token configured
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        # compare_digest to keep the check constant-time; the token is a shared secret.
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented, settings.auth_token):
            # Returned rather than raised: HTTPException thrown inside an HTTP
            # middleware escapes FastAPI's handlers and surfaces as a 500.
            return JSONResponse(
                status_code=401, content={"detail": "invalid or missing auth token"}
            )
        return await call_next(request)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        heartbeat.touch()
        return HealthResponse(status="ok", version=__version__, pid=os.getpid())

    app.include_router(projects.router)
    app.include_router(ingest.router)
    app.include_router(gate1.router)
    app.include_router(gate2.router)
    app.include_router(gate3.router)
    app.include_router(cover.router)
    app.include_router(presets.router)
    app.include_router(provider.router)
    app.include_router(provider.probe_router)
    app.include_router(image_provider.router)
    app.include_router(events.router)

    return app
