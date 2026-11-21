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
    cache.set("events:3", ["e1"])
    cache.set("events:6", ["e2"])
    cache.set("groups", ["g1"])
    cache.invalidate_prefix("events:")
    assert cache.get("events:3", ttl=60.0) is None
    assert cache.get("events:6", ttl=60.0) is None
    assert cache.get("groups", ttl=60.0) == ["g1"]
    cache.clear()
    assert cache.get("groups", ttl=60.0) is None


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
        if request.url.path == "/app/users/current/events/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 20, "event_id": 10}]})
        if request.method == "PATCH":
            body = json.loads(request.content)
            captured.append((request.method, request.url.path, body))
            assert "version=4" in request.headers["Accept"]
            return httpx.Response(200, json={"id": 55, "response": body["response"]})
        return httpx.Response(404)

    async with make_client(handler, id_token="tok") as client:
        await client.get_groups()
        await client.get_events(max_pages=3)
        assert client._cache.get("groups", 60.0) is not None
        assert client._cache.get("events:3", 60.0) is not None

        res_acc = await client.respond_to_event(10, 20, 55, accept=True)
        assert res_acc["response"] == RESPONSE_ACCEPTED
        # Only events cache is invalidated; groups cache is preserved
        assert client._cache.get("events:3", 60.0) is None
        assert client._cache.get("groups", 60.0) is not None

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


async def test_event_comments_invites_and_tasks_endpoints():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["Accept"]))
        if request.url.path == "/app/events/10/occurrences/20/comments/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 1, "comment": "Husk ball"}]})
        if request.url.path == "/app/events/10/occurrences/20/invites/":
            return httpx.Response(200, json={"has_next": False, "results": [{"response": 2, "user": {"id": 5}}]})
        if request.url.path == "/app/tasks/events/10/occurrences/20/tasks/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 7, "title": "Kioskvakt"}]})
        return httpx.Response(404)

    async with make_client(handler, id_token="tok") as client:
        comments = await client.get_event_comments(10, 20)
        invites = await client.get_event_invites(10, 20)
        tasks = await client.get_event_tasks(10, 20)

    assert comments == [{"id": 1, "comment": "Husk ball"}]
    assert invites == [{"response": 2, "user": {"id": 5}}]
    assert tasks == [{"id": 7, "title": "Kioskvakt"}]
    assert ("/app/events/10/occurrences/20/comments/", "application/problem+json; version=3;") in seen
    assert ("/app/events/10/occurrences/20/invites/", "application/problem+json; version=1;") in seen
    assert ("/app/tasks/events/10/occurrences/20/tasks/", "application/problem+json; version=3;") in seen


async def test_payments_claims_inbox_and_drive_download_endpoints():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.headers["Accept"]
        if request.url.path == "/app/drive/files/900/file/":
            return httpx.Response(200, json={"id": 900, "download_url": "https://cdn.example.test/file.pdf"})
        if request.url.path == "/app/payments/user-payments/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 1, "payment_name": "Kontingent"}]})
        if request.url.path == "/app/payments/payments-history/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 2, "payment_name": "Cupavgift"}]})
        if request.url.path == "/app/payments/payments-history/2/":
            return httpx.Response(200, json={"id": 2, "total_sum": 25000, "currency": "NOK"})
        if request.url.path == "/app/payments/payment-plans/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 3, "name": "Delbetaling"}]})
        if request.url.path == "/app/expenses/claims/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 4, "title": "Dommerutlegg"}]})
        if request.url.path == "/app/users/current/inbox/":
            return httpx.Response(200, json={"has_next": False, "results": [{"id": 5, "title": "Vakt søndag"}]})
        return httpx.Response(404)

    async with make_client(handler, id_token="tok") as client:
        dl = await client.get_drive_file_download(900)
        pending = await client.get_pending_payments()
        history = await client.get_payment_history()
        detail = await client.get_payment_detail(2)
        plans = await client.get_payment_plans()
        claims = await client.get_expense_claims()
        inbox = await client.get_inbox()

    assert dl["download_url"] == "https://cdn.example.test/file.pdf"
    assert pending[0]["id"] == 1
    assert history[0]["id"] == 2
    assert detail["total_sum"] == 25000
    assert plans[0]["id"] == 3
    assert claims[0]["id"] == 4
    assert inbox[0]["id"] == 5
    assert "version=1" in seen["/app/drive/files/900/file/"]
    assert "version=2" in seen["/app/payments/user-payments/"]
    assert "version=1" in seen["/app/payments/payments-history/"]
    assert "version=1" in seen["/app/payments/payments-history/2/"]
    assert "version=1" in seen["/app/payments/payment-plans/"]
    assert "version=2" in seen["/app/expenses/claims/"]
    assert "version=17" in seen["/app/users/current/inbox/"]
