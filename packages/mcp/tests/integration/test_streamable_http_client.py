"""Integration coverage for the real MCP streamable HTTP transport."""

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from keycardai.mcp.client import Client
from keycardai.mcp.client.connection.http import StreamableHttpConnection
from keycardai.mcp.client.session import SessionStatus

API_KEY = "integration-test-key"


class ApiKeyMiddleware:
    """Require an API key for HTTP requests while forwarding ASGI lifespan."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if headers.get(b"x-api-key") != API_KEY.encode():
                await Response(status_code=401)(scope, receive, send)
                return

        await self.app(scope, receive, send)


@asynccontextmanager
async def run_streamable_http_server(
    require_api_key: bool,
) -> AsyncIterator[str]:
    """Serve a minimal MCP app on an ephemeral localhost port."""
    mcp = MCPServer("streamable-http-integration")

    @mcp.tool()
    def ping() -> str:
        return "pong"

    app: ASGIApp = mcp.streamable_http_app()
    if require_api_key:
        app = ApiKeyMiddleware(app)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    host, port = sock.getsockname()

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="warning",
            lifespan="on",
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[sock]))

    async def wait_until_started() -> None:
        while not server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(wait_until_started(), timeout=5)
        yield f"http://{host}:{port}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)
        with contextlib.suppress(OSError):
            sock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_config", "require_api_key"),
    [
        (None, False),
        (
            {
                "type": "api_key",
                "key": API_KEY,
                "header_name": "X-API-Key",
            },
            True,
        ),
    ],
    ids=["unauthenticated", "api-key"],
)
async def test_client_connects_over_real_streamable_http_transport(
    auth_config: dict[str, Any] | None,
    require_api_key: bool,
) -> None:
    """Client.connect reaches an operational session through the real transport."""
    async with run_streamable_http_server(require_api_key) as url:
        server_config: dict[str, Any] = {"url": url}
        if auth_config is not None:
            server_config["auth"] = auth_config

        client = Client({"test": server_config})
        try:
            await client.connect()

            session = client.sessions["test"]
            assert session.status is SessionStatus.CONNECTED
            assert session.is_operational

            connection = session._connection
            assert isinstance(connection, StreamableHttpConnection)
            http_client = connection._http_client
            assert http_client is not None
            assert http_client.follow_redirects is True
            assert http_client.timeout.connect == 30.0
            assert http_client.timeout.read == 300.0
            assert http_client.timeout.write == 30.0
            assert http_client.timeout.pool == 30.0

            tools = await client.list_tools("test")
            assert [tool.tool.name for tool in tools] == ["ping"]
        finally:
            await client.disconnect()

        assert http_client.is_closed
