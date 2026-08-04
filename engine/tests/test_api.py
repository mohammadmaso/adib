from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from adib_engine import __version__
from adib_engine.api.app import create_app
from adib_engine.settings import RuntimeSettings, configure
from tests.conftest import TEST_TOKEN


@pytest.fixture
async def client(settings: RuntimeSettings):
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://engine") as c:
            yield c


async def test_health_is_public_and_reports_version(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["pid"] > 0


async def test_protected_path_without_token_is_401_not_500(client: httpx.AsyncClient):
    # Regression guard: raising HTTPException inside an HTTP middleware escapes
    # FastAPI's handlers and surfaces as a 500, so the check must return a response.
    resp = await client.get("/does-not-exist")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid or missing auth token"


async def test_protected_path_with_wrong_token_is_401(client: httpx.AsyncClient):
    resp = await client.get("/does-not-exist", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


async def test_protected_path_with_valid_token_passes_through(client: httpx.AsyncClient):
    # 404 rather than 401 proves the token was accepted and routing ran.
    resp = await client.get("/does-not-exist", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
    assert resp.status_code == 404


async def test_auth_disabled_when_no_token_configured(tmp_path):
    configure(RuntimeSettings(auth_token="", data_dir=tmp_path / "adib"))
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://engine") as c:
            resp = await c.get("/does-not-exist")
    assert resp.status_code == 404
