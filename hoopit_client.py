"""Async Python client for the unofficial Hoopit mobile API and Stream Chat."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.hoopit.io"
STREAM_CHAT_BASE_URL = "https://chat.stream-io-api.com"
STREAM_CHAT_API_KEY = "y8afyfq3u8a5"

DEFAULT_FIREBASE_API_KEY = "AIzaSyArHpOrsELc6a9AU4fnOrUVuqs0KxMjAk0"
FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
FIREBASE_SIGNIN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"

APP_PLATFORM = "android"
APP_VERSION = "2.172.3-15567"

RESPONSE_NOT_ANSWERED = 0
RESPONSE_DECLINED = 1
RESPONSE_ACCEPTED = 2

NOTIFICATION_VERSION_PARAMS: dict[str, str] = {
    "occurrence_version": "19",
    "occurrence_response_version": "1",
    "occurrence_reminder_version": "1",
    "occurrence_comment_version": "1",
    "payment_version": "7",
    "post_version": "5",
    "comment_version": "4",
    "survey_version": "2",
    "relationship_version": "2",
    "poll_version": "1",
    "poll_comment_version": "1",
    "claim_version": "2",
    "dugnad_version": "1",
    "membership_version": "2",
    "task_response_version": "1",
    "task_version": "1",
}


class HoopitError(Exception):
    """Base exception for Hoopit client errors."""


class HoopitAuthError(HoopitError):
    """Raised when Hoopit/Firebase authentication fails or token is rejected."""


class HoopitAPIError(HoopitError):
    """Raised when the Hoopit or Stream Chat API returns a non-2xx response."""

    def __init__(self, status_code: int, path: str, message: str = ""):
        self.status_code = status_code
        self.path = path
        super().__init__(message or f"Hoopit API svarte {status_code} på {path}")


class TTLCache:
    """Simple in-memory monotonic TTL cache."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, ttl: float) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, ts = entry
        if time.monotonic() - ts < ttl:
            return value
        self._store.pop(key, None)
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.monotonic())

    def invalidate_prefix(self, prefix: str) -> None:
        for key in [k for k in self._store if k.startswith(prefix)]:
            self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


class HoopitClient:
    """Async Hoopit API client with Firebase auth/refresh, TTL caching, and Stream Chat support."""

    GROUPS_TTL = 3600.0  # 1 hour
    USER_TTL = 3600.0  # 1 hour
    EVENTS_TTL = 300.0  # 5 minutes
    POSTS_TTL = 300.0  # 5 minutes
    FILES_TTL = 300.0  # 5 minutes
    CHATS_TTL = 60.0  # 1 minute
    TOKEN_REFRESH_MARGIN = 60.0  # Refresh 60s before expiry

    def __init__(
        self,
        *,
        refresh_token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        id_token: str | None = None,
        firebase_api_key: str | None = None,
        language: str = "no",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._refresh_token = (refresh_token or "").strip() or None
        self._email = (email or "").strip() or None
        self._password = (password or "").strip() or None
        self._id_token = (id_token or "").strip() or None
        self._id_token_expires_at: float | None = None
        self._firebase_api_key = (
            firebase_api_key or os.environ.get("HOOPIT_FIREBASE_API_KEY") or DEFAULT_FIREBASE_API_KEY
        )
        self._language = language
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._auth_lock = asyncio.Lock()
        self._cache = TTLCache()
        self._stream_token: str | None = None
        self._user_uuid: str | None = None

    @property
    def token(self) -> str | None:
        return self._id_token

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    def set_refresh_token(self, token: str | None) -> None:
        cleaned = (token or "").strip() or None
        if cleaned:
            self._refresh_token = cleaned

    @property
    def user_uuid(self) -> str | None:
        return self._user_uuid

    @property
    def can_refresh(self) -> bool:
        return bool(self._refresh_token or (self._email and self._password))

    def clear_cache(self) -> None:
        self._cache.clear()

    async def close(self) -> None:
        if self._owns_client and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> HoopitClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    def _token_needs_refresh(self) -> bool:
        if not self._id_token:
            return True
        if self._id_token_expires_at is None:
            return False
        return time.monotonic() >= self._id_token_expires_at - self.TOKEN_REFRESH_MARGIN

    async def login(self, force: bool = False) -> str:
        """Obtain or refresh the Firebase ID token."""
        async with self._auth_lock:
            if not force and not self._token_needs_refresh() and self._id_token:
                return self._id_token

            if self._refresh_token:
                return await self._refresh_firebase_token()

            if self._email and self._password:
                return await self._signin_with_password()

            if self._id_token and not force:
                return self._id_token

            raise HoopitAuthError(
                "Ingen gyldig Hoopit-legitimasjon konfigurert (sett HOOPIT_REFRESH_TOKEN, "
                "HOOPIT_EMAIL/HOOPIT_PASSWORD, eller HOOPIT_ID_TOKEN)."
            )

    async def _refresh_firebase_token(self) -> str:
        resp = await self._http.post(
            FIREBASE_REFRESH_URL,
            params={"key": self._firebase_api_key},
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
        )
        if resp.status_code != 200:
            raise HoopitAuthError(f"Firebase token-fornyelse feilet ({resp.status_code})")
        data = resp.json()
        id_token = data.get("id_token")
        if not id_token:
            raise HoopitAuthError("Firebase returnerte ingen id_token ved fornyelse")
        self._id_token = id_token
        if data.get("refresh_token"):
            self._refresh_token = data["refresh_token"]
        expires_in = float(data.get("expires_in", 3600))
        self._id_token_expires_at = time.monotonic() + expires_in
        return id_token

    async def _signin_with_password(self) -> str:
        resp = await self._http.post(
            FIREBASE_SIGNIN_URL,
            params={"key": self._firebase_api_key},
            json={
                "email": self._email,
                "password": self._password,
                "returnSecureToken": True,
            },
        )
        if resp.status_code != 200:
            raise HoopitAuthError(f"Firebase-innlogging feilet ({resp.status_code})")
        data = resp.json()
        id_token = data.get("idToken")
        if not id_token:
            raise HoopitAuthError("Firebase returnerte ingen idToken ved innlogging")
        self._id_token = id_token
        if data.get("refreshToken"):
            self._refresh_token = data["refreshToken"]
        expires_in = float(data.get("expiresIn", 3600))
        self._id_token_expires_at = time.monotonic() + expires_in
        return id_token

    def _headers(self, token: str, version: int) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": f"application/problem+json; version={version};",
            "Accept-Language": self._language,
            "X-Client-Name": "app",
            "App-Platform": APP_PLATFORM,
            "App-Version": APP_VERSION,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        version: int = 1,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        _retried: bool = False,
    ) -> Any:
        token = await self.login()
        url = f"{API_BASE_URL}{path}"
        resp = await self._http.request(
            method,
            url,
            headers=self._headers(token, version),
            params=params,
            json=json_body,
        )
        if resp.status_code == 401:
            if not _retried and self.can_refresh:
                logger.info("Hoopit returned 401 on %s; refreshing Firebase token", path)
                await self.login(force=True)
                return await self._request(
                    method,
                    path,
                    version=version,
                    params=params,
                    json_body=json_body,
                    _retried=True,
                )
            raise HoopitAuthError(f"Hoopit avviste token (401) på {path}")

        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = body.get("localized_message") or body.get("title") or ""
            except Exception:
                detail = resp.text[:200]
            msg = f"Hoopit API svarte {resp.status_code} på {path}"
            if detail:
                msg = f"{msg}: {detail}"
            raise HoopitAPIError(resp.status_code, path, msg)

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def _get_paginated(
        self,
        path: str,
        *,
        version: int,
        extra_params: dict[str, Any] | None = None,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {"page": str(page), "pagination_count": "false"}
            if extra_params:
                params.update(extra_params)
            data = await self._request("GET", path, version=version, params=params)
            if isinstance(data, list):
                results.extend(data)
                break
            page_items = (data or {}).get("results") or []
            results.extend(page_items)
            if not (data or {}).get("has_next"):
                break
        return results

    # --- Core Hoopit API methods ---

    async def get_current_user(self) -> dict[str, Any]:
        cached = self._cache.get("current_user", self.USER_TTL)
        if cached is not None:
            return cached
        user = await self._request("GET", "/app/users/current/", version=6)
        self._cache.set("current_user", user)
        return user

    async def get_groups(self) -> list[dict[str, Any]]:
        cached = self._cache.get("groups", self.GROUPS_TTL)
        if cached is not None:
            return cached
        groups = await self._request("GET", "/app/users/current/groups/", version=2) or []
        self._cache.set("groups", groups)
        return groups

    async def get_group_hierarchy(self) -> list[dict[str, Any]]:
        cached = self._cache.get("group_hierarchy", self.GROUPS_TTL)
        if cached is not None:
            return cached
        data = (
            await self._request(
                "GET",
                "/app/organizations/my/groups/hierarchy/",
                version=1,
                params={"scope": "your_groups"},
            )
            or []
        )
        self._cache.set("group_hierarchy", data)
        return data

    async def get_events(self, max_pages: int = 5) -> list[dict[str, Any]]:
        cache_key = f"events:{max_pages}"
        cached = self._cache.get(cache_key, self.EVENTS_TTL)
        if cached is not None:
            return cached
        events = await self._get_paginated(
            "/app/users/current/events/",
            version=3,
            max_pages=max_pages,
        )
        self._cache.set(cache_key, events)
        return events

    async def get_event_details(self, event_id: int, occurrence_id: int) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/app/events/{event_id}/occurrences/{occurrence_id}/",
            version=12,
        )

    async def get_my_responses(self, event_id: int, occurrence_id: int) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/app/events/{event_id}/occurrences/{occurrence_id}/my/responses/",
            version=1,
        )

    async def get_response_detail(self, event_id: int, occurrence_id: int, user_id: int) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/app/events/{event_id}/occurrences/{occurrence_id}/responses/{user_id}/",
            version=4,
        )

    async def respond_to_event(
        self,
        event_id: int,
        occurrence_id: int,
        user_id: int,
        accept: bool,
        decline_reason: str = "",
    ) -> dict[str, Any]:
        """Accept (response=2) or decline (response=1) an occurrence for a controllable user."""
        payload: dict[str, Any] = {
            "response": RESPONSE_ACCEPTED if accept else RESPONSE_DECLINED,
        }
        if not accept and decline_reason:
            payload["response_reason"] = decline_reason
        result = await self._request(
            "PATCH",
            f"/app/events/{event_id}/occurrences/{occurrence_id}/responses/{user_id}/",
            version=4,
            json_body=payload,
        )
        self._cache.invalidate_prefix("events:")
        return result

    async def get_notifications(self, max_pages: int = 2) -> list[dict[str, Any]]:
        return await self._get_paginated(
            "/app/users/current/notifications/",
            version=10,
            extra_params=NOTIFICATION_VERSION_PARAMS,
            max_pages=max_pages,
        )

    async def get_posts(self, max_pages: int = 2) -> list[dict[str, Any]]:
        cache_key = f"posts:{max_pages}"
        cached = self._cache.get(cache_key, self.POSTS_TTL)
        if cached is not None:
            return cached
        posts = await self._get_paginated(
            "/app/users/current/posts/",
            version=5,
            max_pages=max_pages,
        )
        self._cache.set(cache_key, posts)
        return posts

    async def get_post(self, post_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/app/posts/{post_id}/", version=6)

    async def get_post_comments(self, post_id: int, max_pages: int = 3) -> list[dict[str, Any]]:
        return await self._get_paginated(
            f"/app/posts/{post_id}/comments/",
            version=4,
            max_pages=max_pages,
        )

    async def get_drive_files(self, max_pages: int = 3) -> list[dict[str, Any]]:
        cache_key = f"drive_files:{max_pages}"
        cached = self._cache.get(cache_key, self.FILES_TTL)
        if cached is not None:
            return cached
        files = await self._get_paginated(
            "/app/drive/my/files/",
            version=1,
            max_pages=max_pages,
        )
        self._cache.set(cache_key, files)
        return files

    async def get_drive_file(self, file_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/app/drive/files/{file_id}/", version=1)

    async def get_tokens(self) -> dict[str, Any]:
        return await self._request("GET", "/app/users/current/tokens/", version=1)

    # --- Stream Chat read-only methods ---

    async def _ensure_stream_chat(self, force: bool = False) -> tuple[str, str]:
        if not force and self._user_uuid and self._stream_token:
            return self._user_uuid, self._stream_token
        tokens = await self.get_tokens()
        uuid = tokens.get("uuid")
        stream_token = tokens.get("stream_chat_token")
        if not uuid or not stream_token:
            raise HoopitAPIError(500, "/app/users/current/tokens/", "Mangler uuid eller stream_chat_token")
        self._user_uuid = uuid
        self._stream_token = stream_token
        return uuid, stream_token

    async def _stream_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        _retried: bool = False,
    ) -> Any:
        user_uuid, stream_token = await self._ensure_stream_chat()
        query = {"api_key": STREAM_CHAT_API_KEY, "user_id": user_uuid}
        if params:
            query.update(params)
        url = f"{STREAM_CHAT_BASE_URL}{path}?{urlencode(query)}"
        headers = {
            "Authorization": stream_token,
            "Stream-Auth-Type": "jwt",
        }
        resp = await self._http.request(method, url, headers=headers, json=json_body)
        if resp.status_code == 401 and not _retried:
            await self._ensure_stream_chat(force=True)
            return await self._stream_request(method, path, params=params, json_body=json_body, _retried=True)
        if resp.status_code >= 400:
            raise HoopitAPIError(resp.status_code, path, f"Stream Chat svarte {resp.status_code} på {path}")
        return resp.json()

    async def get_chats(self, limit: int = 20) -> list[dict[str, Any]]:
        cache_key = f"chats:{limit}"
        cached = self._cache.get(cache_key, self.CHATS_TTL)
        if cached is not None:
            return cached
        user_uuid, _ = await self._ensure_stream_chat()
        payload = {
            "state": True,
            "watch": False,
            "presence": False,
            "sort": [{"field": "last_message_at", "direction": -1}],
            "filter_conditions": {
                "$and": [
                    {"members": {"$in": [user_uuid]}},
                    {"type": {"$in": ["messaging", "guardians"]}},
                    {"last_message_at": {"$gt": "2000-01-01T00:00:00.00Z"}},
                ]
            },
            "message_limit": 1,
            "limit": limit,
        }
        data = await self._stream_request("GET", "/channels", params={"payload": json.dumps(payload)})
        channels = (data or {}).get("channels") or []
        self._cache.set(cache_key, channels)
        return channels

    async def get_chat_messages(
        self,
        channel_type: str,
        channel_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        return await self._stream_request(
            "POST",
            f"/channels/{channel_type}/{channel_id}/query",
            json_body={
                "state": True,
                "watch": False,
                "presence": False,
                "messages": {"limit": limit},
            },
        )
