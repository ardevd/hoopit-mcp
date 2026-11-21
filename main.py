"""Hoopit MCP server entry point — Starlette app with /health, /sse, and optional bearer auth."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from hoopit_client import HoopitAuthError, HoopitClient
from server import TOOLS, HoopitService, handle_tool_call

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hoopit-mcp")


class LoginFailed(Exception):
    pass


def create_service() -> HoopitService:
    client = HoopitClient(
        refresh_token=os.environ.get("HOOPIT_REFRESH_TOKEN"),
        email=os.environ.get("HOOPIT_EMAIL"),
        password=os.environ.get("HOOPIT_PASSWORD"),
        id_token=os.environ.get("HOOPIT_ID_TOKEN"),
        firebase_api_key=os.environ.get("HOOPIT_FIREBASE_API_KEY"),
    )
    return HoopitService(client=client)


class HoopitAuth:
    """Shared HoopitService with 401 re-login and 15-minute backoff after rejected credentials."""

    BACKOFF = 900.0

    def __init__(self, factory=create_service) -> None:
        self._factory = factory
        self._lock = asyncio.Lock()
        self.service: HoopitService | None = None
        self.failed_at: float | None = None

    def _backoff_error(self) -> LoginFailed:
        assert self.failed_at is not None
        mins = max(1, round((self.BACKOFF - (time.monotonic() - self.failed_at)) / 60))
        return LoginFailed(f"Innlogging mot Hoopit feilet (prøver igjen om {mins} min)")

    async def _drop(self) -> None:
        svc, self.service = self.service, None
        if svc is not None:
            await svc.close()

    async def ensure(self) -> HoopitService:
        async with self._lock:
            if self.failed_at is not None and time.monotonic() - self.failed_at < self.BACKOFF:
                raise self._backoff_error()
            svc = self.service
            if svc is None:
                svc = self._factory()
                self.service = svc
            client = svc._client
            if not client.token:
                try:
                    await client.login()
                except HoopitAuthError:
                    logger.error("Hoopit login rejected; backing off %d min", self.BACKOFF // 60)
                    self.failed_at = time.monotonic()
                    await self._drop()
                    raise self._backoff_error() from None
                except Exception as e:
                    logger.error("Hoopit login error: %s", type(e).__name__)
                    await self._drop()
                    raise LoginFailed("Kunne ikke nå Hoopit, prøv igjen senere") from None
            self.failed_at = None
            return svc

    async def run(self, fn):
        svc = await self.ensure()
        try:
            return await fn(svc)
        except HoopitAuthError:
            logger.info("Hoopit token rejected; refreshing session")
            async with self._lock:
                if self.service is svc:
                    await self._drop()
            return await fn(await self.ensure())

    async def call(self, name: str, arguments: dict | None) -> str:
        return await self.run(lambda svc: handle_tool_call(svc, name, arguments))


class OptionalBearerAuth:
    """ASGI wrapper requiring `Authorization: Bearer <token>` when a token is configured."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self._app = app
        self._token = (token or "").strip() or None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._token is None:
            await self._app(scope, receive, send)
            return

        auth_header = ""
        for k, v in scope.get("headers") or []:
            if k.lower() == b"authorization":
                auth_header = v.decode("latin-1")
                break

        prefix = "Bearer "
        provided = auth_header[len(prefix) :] if auth_header.startswith(prefix) else ""
        if not provided or not hmac.compare_digest(provided, self._token):
            resp = PlainTextResponse("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await resp(scope, receive, send)
            return

        await self._app(scope, receive, send)


auth = HoopitAuth()

mcp_server = Server("hoopit")
sse = SseServerTransport("/messages/")


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    return [Tool(**t) for t in TOOLS]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    try:
        result = await auth.call(name, arguments)
    except LoginFailed as e:
        result = str(e)
    except HoopitAuthError:
        result = "Hoopit avviste innloggingen, prøv igjen senere"
    except Exception as e:
        logger.exception("Tool %s failed", name)
        result = f"Feil: {e}"
    return [TextContent(type="text", text=result)]


async def handle_sse(request: Request) -> Response:
    async with sse.connect_sse(request.scope, request.receive, request._send) as (
        read_stream,
        write_stream,
    ):
        await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
    return Response()


async def health(request: Request) -> Response:
    return PlainTextResponse("ok")


@asynccontextmanager
async def lifespan(app: Starlette):
    yield
    await auth._drop()


def build_app(auth_token: str | None = None) -> Starlette:
    mcp_app = Starlette(
        routes=[
            Route("/sse", handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )
    return Starlette(
        routes=[
            Route("/health", health),
            Mount("/", app=OptionalBearerAuth(mcp_app, auth_token)),
        ],
        lifespan=lifespan,
    )


def require_credentials() -> None:
    has_refresh = bool(os.environ.get("HOOPIT_REFRESH_TOKEN", "").strip())
    has_password = bool(os.environ.get("HOOPIT_EMAIL", "").strip() and os.environ.get("HOOPIT_PASSWORD", "").strip())
    has_id_token = bool(os.environ.get("HOOPIT_ID_TOKEN", "").strip())
    if not (has_refresh or has_password or has_id_token):
        raise SystemExit(
            "Ingen Hoopit-legitimasjon satt (sett HOOPIT_REFRESH_TOKEN, "
            "HOOPIT_EMAIL+HOOPIT_PASSWORD, eller HOOPIT_ID_TOKEN); avslutter."
        )


if __name__ == "__main__":
    require_credentials()
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip() or None
    port = int(os.environ.get("PORT", "8080"))
    mode = "auth enabled" if token else "auth disabled (set MCP_AUTH_TOKEN to enable)"
    logger.info("hoopit MCP server starting on :%d (%s)", port, mode)
    uvicorn.run(build_app(token), host="0.0.0.0", port=port, log_level="info", http="h11")
