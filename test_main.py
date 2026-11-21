"""Unit tests for main.py (HoopitAuth backoff/retry, OptionalBearerAuth, health, credentials)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp.types import Tool
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from hoopit_client import HoopitAuthError
from main import HoopitAuth, LoginFailed, OptionalBearerAuth, build_app, require_credentials
from server import TOOLS


def test_tools_definitions_expose_all_12_tools():
    tools = [Tool(**t) for t in TOOLS]
    names = {t.name for t in tools}
    assert len(tools) == 12
    assert {
        "list_groups",
        "get_upcoming_events",
        "get_event_details",
        "get_attendance",
        "respond_to_event",
        "search_events",
        "get_updates",
        "get_posts",
        "get_post_comments",
        "get_chats",
        "get_chat_messages",
        "get_attachment",
    } == names


async def test_hoopit_auth_login_backoff():
    bad_client = MagicMock()
    bad_client.token = None
    bad_client.login = AsyncMock(side_effect=HoopitAuthError("rejected"))
    bad_client.close = AsyncMock()
    svc = MagicMock()
    svc._client = bad_client
    svc.close = AsyncMock()

    auth = HoopitAuth(factory=lambda: svc)
    with pytest.raises(LoginFailed, match="prøver igjen om"):
        await auth.ensure()

    # Second call within backoff window should not retry login
    bad_client.login.reset_mock()
    with pytest.raises(LoginFailed, match="prøver igjen om"):
        await auth.ensure()
    bad_client.login.assert_not_called()


async def test_hoopit_auth_retries_once_on_401():
    created: list[MagicMock] = []

    def factory():
        c = MagicMock()
        c.token = "tok"
        c.login = AsyncMock(return_value="tok")
        s = MagicMock()
        s._client = c
        s.close = AsyncMock()
        created.append(s)
        return s

    auth = HoopitAuth(factory=factory)
    calls = {"n": 0}

    async def action(svc):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HoopitAuthError("expired")
        return "ok"

    assert await auth.run(action) == "ok"
    assert len(created) == 2
    created[0].close.assert_awaited_once()


async def test_optional_bearer_auth_enabled():
    inner = Starlette(routes=[Route("/secret", lambda req: PlainTextResponse("secret-ok"))])
    app = OptionalBearerAuth(inner, token="my-secret-token")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/secret")
        assert r1.status_code == 401
        assert r1.headers["www-authenticate"] == "Bearer"

        r2 = await client.get("/secret", headers={"Authorization": "Bearer wrong"})
        assert r2.status_code == 401

        r3 = await client.get("/secret", headers={"Authorization": "Bearer my-secret-token"})
        assert r3.status_code == 200
        assert r3.text == "secret-ok"


async def test_optional_bearer_auth_disabled():
    inner = Starlette(routes=[Route("/open", lambda req: PlainTextResponse("open-ok"))])
    app = OptionalBearerAuth(inner, token=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/open")
        assert r.status_code == 200
        assert r.text == "open-ok"


async def test_health_endpoint_is_always_open():
    app = build_app(auth_token="super-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.text == "ok"

        # Protected route rejects without token
        r_sse = await client.get("/sse")
        assert r_sse.status_code == 401


def test_require_credentials(monkeypatch):
    for var in ("HOOPIT_REFRESH_TOKEN", "HOOPIT_EMAIL", "HOOPIT_PASSWORD", "HOOPIT_ID_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit):
        require_credentials()

    monkeypatch.setenv("HOOPIT_REFRESH_TOKEN", "some-refresh-token")
    require_credentials()
