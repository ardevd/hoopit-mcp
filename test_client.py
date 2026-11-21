"""Unit tests for HoopitClient (Firebase auth, 401 retry, pagination, TTL cache, RSVP PATCH, Stream Chat)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hoopit_client import (
    RESPONSE_ACCEPTED,
    RESPONSE_DECLINED,
    HoopitAPIError,
    HoopitAuthError,
    HoopitClient,
    TTLCache,
)


def make_client(handler, **kwargs) -> HoopitClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return HoopitClient(http_client=http_client, **kwargs)


def test_ttl_cache_hit_expire_and_clear():
    cache = TTLCache()
    cache.set("k", [1, 2, 3])
    assert cache.get("k", ttl=60.0) == [1, 2, 3]
    assert cache.get("k", ttl=0.0) is None
    cache.set("k2", "v2")
    cache.clear()
    assert cache.get("k2", ttl=60.0) is None


async def test_refresh_token_login_and_caching():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.host}{request.url.path}")
        if request.url.host == "securetoken.googleapis.com":
            return httpx.Response(
                200,
                json={"id_token": "fb-id-1", "refresh_token": "fb-ref-2", "expires_in": "3600"},
            )
        if request.url.path == "/app/users/current/groups/":
            assert request.headers["Authorization"] == "Bearer fb-id-1"
            assert "version=2" in request.headers["Accept"]
            return httpx.Response(200, json=[{"id": 1, "name": "Lag A"}])
        return httpx.Response(404)

    async with make_client(handler, refresh_token="fb-ref-1") as client:
        groups1 = await client.get_groups()
        groups2 = await client.get_groups()
        assert groups1 == [{"id": 1, "name": "Lag A"}]
        assert groups2 == groups1
        # Only 1 token refresh and 1 groups GET due to TTL cache
        assert len(calls) == 2


async def test_email_password_login():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "identitytoolkit.googleapis.com":
            body = json.loads(request.content)
            assert body["email"] == "test@example.com"
            assert body["password"] == "secret"
            return httpx.Response(
                200,
                json={"idToken": "pw-id-1", "refreshToken": "pw-ref-1", "expiresIn": "3600"},
            )
        return httpx.Response(200, json={"id": 42})

    async with make_client(handler, email="test@example.com", password="secret") as client:
        tok = await client.login()
        assert tok == "pw-id-1"
        user = await client.get_current_user()
        assert user["id"] == 42


async def test_missing_credentials_raises_auth_error():
    async with make_client(lambda r: httpx.Response(200)) as client:
        with pytest.raises(HoopitAuthError):
            await client.login()


async def test_401_triggers_refresh_and_retry():
    attempts = {"groups": 0, "refresh": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "securetoken.googleapis.com":
            attempts["refresh"] += 1
            return httpx.Response(
                200,
                json={"id_token": f"tok-{attempts['refresh']}", "expires_in": "3600"},
            )
        if request.url.path == "/app/users/current/groups/":
            attempts["groups"] += 1
            if attempts["groups"] == 1:
                return httpx.Response(401, json={"title": "Expired"})
            assert request.headers["Authorization"] == "Bearer tok-2"
            return httpx.Response(200, json=[{"id": 9, "name": "Gutter 2015"}])
        return httpx.Response(404)

    async with make_client(handler, refresh_token="ref-ok") as client:
        groups = await client.get_groups()
        assert groups == [{"id": 9, "name": "Gutter 2015"}]
        assert attempts == {"groups": 2, "refresh": 2}


async def test_401_without_refresh_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"title": "Invalid"})

    async with make_client(handler, id_token="static-tok") as client:
        with pytest.raises(HoopitAuthError):
            await client.get_groups()


async def test_api_error_surfaces_localized_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"localized_message": "Ugyldig forespørsel"})

    async with make_client(handler, id_token="tok") as client:
        with pytest.raises(HoopitAPIError) as exc_info:
            await client.get_groups()
        assert exc_info.value.status_code == 400
        assert "Ugyldig forespørsel" in str(exc_info.value)


async def test_paginated_events():
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(200, json={"has_next": True, "results": [{"id": 100, "event_id": 1}]})
        return httpx.Response(200, json={"has_next": False, "results": [{"id": 200, "event_id": 2}]})

    async with make_client(handler, id_token="tok") as client:
        events = await client.get_events(max_pages=5)
        assert [e["id"] for e in events] == [100, 200]


async def test_respond_to_event_patch_and_cache_clear():
    captured: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app/users/current/groups/":
            return httpx.Response(200, json=[{"id": 1, "name": "Lag A"}])
        if request.method == "PATCH":
            body = json.loads(request.content)
            captured.append((request.method, request.url.path, body))
            assert "version=4" in request.headers["Accept"]
            return httpx.Response(200, json={"id": 55, "response": body["response"]})
        return httpx.Response(404)

    async with make_client(handler, id_token="tok") as client:
        await client.get_groups()
        assert client._cache.get("groups", 60.0) is not None

        res_acc = await client.respond_to_event(10, 20, 55, accept=True)
        assert res_acc["response"] == RESPONSE_ACCEPTED
        assert client._cache.get("groups", 60.0) is None

        res_dec = await client.respond_to_event(10, 20, 55, accept=False, decline_reason="Syk")
        assert res_dec["response"] == RESPONSE_DECLINED

    assert captured == [
        ("PATCH", "/app/events/10/occurrences/20/responses/55/", {"response": RESPONSE_ACCEPTED}),
        (
            "PATCH",
            "/app/events/10/occurrences/20/responses/55/",
            {"response": RESPONSE_DECLINED, "response_reason": "Syk"},
        ),
    ]


async def test_stream_chat_channels_and_messages():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.hoopit.io" and request.url.path == "/app/users/current/tokens/":
            return httpx.Response(200, json={"id": 1, "uuid": "user-uuid-1", "stream_chat_token": "stream-jwt"})
        if request.url.host == "chat.stream-io-api.com" and request.url.path == "/channels":
            assert request.headers["Authorization"] == "stream-jwt"
            assert request.headers["Stream-Auth-Type"] == "jwt"
            payload = json.loads(request.url.params["payload"])
            assert payload["watch"] is False
            assert payload["presence"] is False
            return httpx.Response(200, json={"channels": [{"channel": {"cid": "messaging:c1", "name": "Lagchat"}}]})
        if request.url.host == "chat.stream-io-api.com" and request.url.path == "/channels/messaging/c1/query":
            body = json.loads(request.content)
            assert body["watch"] is False
            return httpx.Response(
                200,
                json={"channel": {"cid": "messaging:c1", "name": "Lagchat"}, "messages": [{"text": "Hei!"}]},
            )
        return httpx.Response(404)

    async with make_client(handler, id_token="tok") as client:
        chats = await client.get_chats(limit=5)
        assert len(chats) == 1
        msgs = await client.get_chat_messages("messaging", "c1", limit=10)
        assert msgs["messages"][0]["text"] == "Hei!"
