"""Integration tests for the local Keycard agent token source, against a fake agent.

Each test is named for its row in the Integration Tests table of the spec
(keycard-sdk-spec, specs/delegated-access/local-agent-token-source.md). The
spec runs those rows against a live ``keycard agent`` daemon; here a fake
agent on a real Unix socket stands in for it, so each test covers the part of
its row the SDK can observe: what reaches the agent, and what the caller gets
back. Token signatures, prompts and the agent's cache are the agent's and the
authorization server's to test. The resource server is served on a Unix
socket too, so nothing here touches the network.
"""

import asyncio
import os
import sys

import httpx
import pytest
import pytest_asyncio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from keycardai.oauth import (
    AgentCode,
    AgentError,
    AgentTokenSource,
    NetworkError,
    ResourceAuth,
    is_application_not_found,
    is_declined,
)

RESOURCE = "https://api.example.com"
ORG = "org-1"
ZONE = "zone-a"
KNOWN_APPS = {"incident-digest"}


def _daemon(call) -> Response:
    """Answers the way the multi-zone daemon does for a test zone.

    Refuses a request naming no tenant, an ``app_id`` naming no application,
    and the scope ``write`` (standing in for the human declining a new
    scope's prompt); otherwise issues a token bound to the resource.
    """
    body = call.body
    if call.headers.get("keycard-org") != ORG or call.headers.get("keycard-zone") != ZONE:
        return JSONResponse(
            {"code": AgentCode.PERMISSION_DENIED.value, "message": "no session for org/zone"},
            status_code=403,
        )
    if "appId" in body and body["appId"] not in KNOWN_APPS:
        return JSONResponse(
            {"code": AgentCode.NOT_FOUND.value, "message": f"no application {body['appId']}"},
            status_code=404,
        )
    if "write" in body["scopes"]:
        return JSONResponse(
            {"code": AgentCode.PERMISSION_DENIED.value, "message": "declined"},
            status_code=403,
        )
    token = f"token-for:{body['resource']}"
    if "appId" in body:
        token += f":declared:{body['appId']}"
    return JSONResponse({"token": token, "expiresAt": "2099-01-01T00:00:00Z"})


class _ResourceServer:
    """A resource server on a Unix socket that records each Authorization header."""

    def __init__(self, path: str):
        self.path = path
        self.authorizations: list[str | None] = []

    async def _items(self, request: Request) -> Response:
        auth = request.headers.get("authorization")
        self.authorizations.append(auth)
        if auth is None or not auth.startswith("Bearer token-for:"):
            return Response(status_code=401)
        return JSONResponse({"items": []})

    async def __aenter__(self) -> "_ResourceServer":
        app = Starlette(routes=[Route("/items", self._items)])
        self._server = uvicorn.Server(
            uvicorn.Config(app, uds=self.path, log_level="warning", lifespan="off")
        )
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.should_exit = True
        await self._task

    def client(self, auth: httpx.Auth) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self.path),
            base_url="http://resource",
            auth=auth,
        )


@pytest_asyncio.fixture
async def daemon(short_tmp, agent_server):
    return await agent_server(os.path.join(short_tmp, "agent.sock"), respond=_daemon)


def _source(daemon, **kwargs) -> AgentTokenSource:
    return AgentTokenSource(
        socket=daemon.path, org=ORG, zone=ZONE, workload="integration", **kwargs
    )


@pytest.mark.asyncio
async def test_row_1_mint_with_no_application_credential(daemon):
    token = await _source(daemon).resource_token(RESOURCE, ["read"])

    assert token.access_token == f"token-for:{RESOURCE}"
    assert token.expires_in is not None and token.expires_in > 0
    assert daemon.calls[0].body == {
        "resource": RESOURCE,
        "scopes": ["read"],
        "workload": "integration",
    }


@pytest.mark.asyncio
async def test_row_2_request_through_resource_transport(daemon, short_tmp):
    async with _ResourceServer(os.path.join(short_tmp, "resource.sock")) as server:
        async with server.client(ResourceAuth(_source(daemon), RESOURCE, ["read"])) as client:
            response = await client.get("/items")

    assert response.status_code == 200
    assert server.authorizations == [f"Bearer token-for:{RESOURCE}"]


@pytest.mark.asyncio
async def test_row_3_every_call_reaches_the_agent(daemon, short_tmp):
    source = _source(daemon)
    await source.resource_token(RESOURCE, ["read"])
    await source.resource_token(RESOURCE, ["read"])

    async with _ResourceServer(os.path.join(short_tmp, "resource.sock")) as server:
        async with server.client(ResourceAuth(source, RESOURCE, ["read"])) as client:
            response = await client.get("/items")

    assert response.status_code == 200
    assert len(daemon.calls) == 3


@pytest.mark.asyncio
async def test_row_4_declined_new_scope(daemon):
    with pytest.raises(AgentError) as exc_info:
        await _source(daemon).resource_token(RESOURCE, ["read", "write"])

    assert is_declined(exc_info.value)


@pytest.mark.asyncio
async def test_row_5_unknown_app_id(daemon):
    with pytest.raises(AgentError) as exc_info:
        await _source(daemon, app_id="no-such-app").resource_token(RESOURCE)

    assert is_application_not_found(exc_info.value)
    assert not is_declined(exc_info.value)


@pytest.mark.asyncio
async def test_row_6_declared_app_id_reaches_the_agent(daemon):
    token = await _source(daemon, app_id="incident-digest").resource_token(RESOURCE)

    assert daemon.calls[0].body["appId"] == "incident-digest"
    assert token.access_token.endswith(":declared:incident-digest")


def _isolate_environment(monkeypatch, home: str) -> None:
    for name in (
        "KEYCARD_SOCKET",
        "KEYCARD_HOME",
        "KEYCARD_AGENT_DIR",
        "XDG_RUNTIME_DIR",
        "XDG_STATE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", home)


unix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="the default agent socket is Unix-only"
)


@unix_only
@pytest.mark.asyncio
async def test_row_7_default_socket_reaches_the_daemon(short_tmp, agent_server, monkeypatch):
    # The path `keycard agent start` binds under this HOME with no XDG
    # variables set; the fixture creates its directories, as the CLI does.
    default = os.path.join(short_tmp, ".local", "state", "keycard", "run", "agent.sock")
    daemon = await agent_server(default, respond=_daemon)
    _isolate_environment(monkeypatch, short_tmp)

    token = await AgentTokenSource(org=ORG, zone=ZONE).resource_token(RESOURCE)

    assert token.access_token == f"token-for:{RESOURCE}"
    assert daemon.calls[0].headers["keycard-org"] == ORG
    assert daemon.calls[0].headers["keycard-zone"] == ZONE


@unix_only
@pytest.mark.asyncio
async def test_row_8_default_socket_with_the_daemon_stopped(short_tmp, monkeypatch):
    _isolate_environment(monkeypatch, short_tmp)

    with pytest.raises(NetworkError) as exc_info:
        await AgentTokenSource(org=ORG, zone=ZONE).resource_token(RESOURCE)

    message = str(exc_info.value)
    assert os.path.join(short_tmp, ".local/state/keycard/run/agent.sock") in message
    assert "`keycard agent start`" in message
