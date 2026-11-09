"""Tests for the local Keycard agent token source.

Mirrors go-sdk's oauth/agent_token_source_test.go and resource_transport_test.go
(keycardai/go-sdk#51), which implement the same wire contract.
"""

import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from keycardai.oauth import (
    AgentCode,
    AgentError,
    AgentTokenSource,
    ConfigError,
    OAuthError,
    OAuthHttpError,
    ResourceAuth,
    TokenResponse,
    TokenType,
    is_application_not_found,
    is_authorization_required,
    is_declined,
    is_prompter_unavailable,
    is_rate_limited,
    is_unavailable,
)

ISSUE_TOKEN_PATH = "/keycard.agent.v1.AgentService/IssueToken"


def _rfc3339(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issued(token: str, expires_at: datetime | None) -> httpx.Response:
    body: dict[str, str] = {"token": token}
    if expires_at is not None:
        body["expiresAt"] = _rfc3339(expires_at)
    return httpx.Response(200, json=body)


class _Agent:
    """A fake agent that records each request and answers with ``respond``."""

    def __init__(self, respond):
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._respond(request)

        return httpx.MockTransport(handle)

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


def _source(agent: _Agent, **kwargs) -> AgentTokenSource:
    kwargs.setdefault("socket", "/unused.sock")
    return AgentTokenSource(_transport=agent.transport(), **kwargs)


def _in(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_request_shape():
    agent = _Agent(lambda r: _issued("h.p.s", _in(3600)))
    source = _source(agent, workload="unit-test", org="org-1", zone="zone-a")

    tok = await source.resource_token("https://mcp.example.com", "mcp:tools")

    assert tok.access_token == "h.p.s"
    assert tok.token_type == TokenType.BEARER
    assert tok.expires_in is not None and tok.expires_in > 0

    request = agent.requests[0]
    # The path is the wire contract this module implements by hand rather
    # than through generated stubs, so nothing else pins it.
    assert request.url.path == ISSUE_TOKEN_PATH
    assert request.method == "POST"
    assert request.headers["Connect-Protocol-Version"] == "1"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Keycard-Org"] == "org-1"
    assert request.headers["Keycard-Zone"] == "zone-a"
    assert agent.body() == {
        "resource": "https://mcp.example.com",
        "scopes": ["mcp:tools"],
        "workload": "unit-test",
    }


@pytest.mark.asyncio
async def test_tenant_headers_omitted_when_unset():
    agent = _Agent(lambda r: _issued("tok", _in(3600)))
    await _source(agent).resource_token("https://mcp.example.com")

    assert "Keycard-Org" not in agent.requests[0].headers
    assert "Keycard-Zone" not in agent.requests[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("app_id", "want"),
    [("app-1", "app-1"), ("", None)],
    ids=["declared", "undeclared"],
)
async def test_app_id(app_id, want):
    agent = _Agent(lambda r: _issued("tok", _in(3600)))
    await _source(agent, app_id=app_id).resource_token("https://mcp.example.com")

    body = agent.body()
    assert body.get("appId") == want
    assert ("appId" in body) == (want is not None)
    assert "app_id" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["\n", "\r", "\t", "\x00", "\x7f"], ids=repr)
async def test_rejects_control_characters_in_token(bad):
    agent = _Agent(lambda r: _issued(f"tok{bad}en", _in(3600)))

    with pytest.raises(OAuthError, match="control character"):
        await _source(agent).resource_token("https://mcp.example.com")


@pytest.mark.asyncio
async def test_resource_is_sent_unchanged():
    messy = "https://MCP.example.com:443/api/../api/"
    agent = _Agent(lambda r: _issued("tok", _in(3600)))

    await _source(agent).resource_token(messy)

    assert agent.body()["resource"] == messy


@pytest.mark.asyncio
async def test_caches_until_expiry():
    agent = _Agent(lambda r: _issued("tok", _in(3600)))
    source = _source(agent)

    for _ in range(3):
        await source.resource_token("https://mcp.example.com", "mcp:tools")
    assert len(agent.requests) == 1

    await source.resource_token("https://mcp.example.com", "mcp:tools", "extra")
    assert len(agent.requests) == 2


@pytest.mark.asyncio
async def test_remints_inside_the_leeway():
    agent = _Agent(lambda r: _issued("tok", _in(15)))
    source = _source(agent)

    await source.resource_token("https://mcp.example.com")
    await source.resource_token("https://mcp.example.com")

    assert len(agent.requests) == 2


@pytest.mark.asyncio
async def test_caches_a_credential_with_no_expiry():
    agent = _Agent(lambda r: _issued("static-key", None))
    source = _source(agent)

    for _ in range(3):
        tok = await source.resource_token("urn:secret:DD_API_KEY")
        assert tok.expires_in is None
    assert len(agent.requests) == 1


@pytest.mark.asyncio
async def test_leeway_follows_the_injected_clock():
    now = [1_000_000.0]
    expires = datetime.fromtimestamp(now[0] + 3600, timezone.utc)
    agent = _Agent(lambda r: _issued("tok", expires))
    source = _source(agent, clock=lambda: now[0])

    await source.resource_token("https://mcp.example.com")
    now[0] += 3600 - 31
    await source.resource_token("https://mcp.example.com")
    assert len(agent.requests) == 1

    now[0] += 2
    await source.resource_token("https://mcp.example.com")
    assert len(agent.requests) == 2


@pytest.mark.asyncio
async def test_concurrent_first_requests_share_one_mint():
    release = asyncio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        await release.wait()
        return _issued("tok", _in(3600))

    calls: list[httpx.Request] = []
    source = AgentTokenSource(
        socket="/unused.sock", _transport=httpx.MockTransport(handle)
    )

    waiters = [
        asyncio.create_task(source.resource_token("https://mcp.example.com"))
        for _ in range(5)
    ]
    await asyncio.sleep(0)
    release.set()
    tokens = await asyncio.gather(*waiters)

    assert len(calls) == 1
    assert {t.access_token for t in tokens} == {"tok"}


@pytest.mark.asyncio
async def test_parses_nanosecond_expiry():
    # protojson renders Timestamp with up to nine fractional digits.
    future = _in(3600)
    stamp = future.strftime("%Y-%m-%dT%H:%M:%S") + ".123456789Z"
    agent = _Agent(
        lambda r: httpx.Response(200, json={"token": "tok", "expiresAt": stamp})
    )

    tok = await _source(agent).resource_token("https://mcp.example.com")

    assert tok.expires_in is not None and 3500 < tok.expires_in <= 3600


PREDICATES = {
    AgentCode.DECLINED: is_declined,
    AgentCode.RATE_LIMITED: is_rate_limited,
    AgentCode.PROMPTER_UNAVAILABLE: is_prompter_unavailable,
    AgentCode.AUTHORIZATION_REQUIRED: is_authorization_required,
    AgentCode.APPLICATION_NOT_FOUND: is_application_not_found,
    AgentCode.UNAVAILABLE: is_unavailable,
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, AgentCode.DECLINED),
        (429, AgentCode.RATE_LIMITED),
        (412, AgentCode.PROMPTER_UNAVAILABLE),
        (401, AgentCode.AUTHORIZATION_REQUIRED),
        (404, AgentCode.APPLICATION_NOT_FOUND),
        (503, AgentCode.UNAVAILABLE),
    ],
)
async def test_maps_connect_codes(status, code):
    agent = _Agent(
        lambda r: httpx.Response(status, json={"code": code, "message": "nope"})
    )

    with pytest.raises(AgentError) as exc_info:
        await _source(agent).resource_token("https://mcp.example.com")

    err = exc_info.value
    assert err.code == code
    assert err.message == "nope"
    for other, predicate in PREDICATES.items():
        assert predicate(err) == (other == code), other
    assert err.retryable == (code in (AgentCode.RATE_LIMITED, AgentCode.UNAVAILABLE))


def test_predicates_follow_the_cause_chain():
    try:
        try:
            raise AgentError(AgentCode.DECLINED, "no")
        except AgentError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert is_declined(outer)
        assert not is_rate_limited(outer)


@pytest.mark.asyncio
async def test_non_json_refusal_is_an_http_error():
    agent = _Agent(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))

    with pytest.raises(OAuthHttpError) as exc_info:
        await _source(agent).resource_token("https://mcp.example.com")
    assert exc_info.value.status_code == 502
    assert not isinstance(exc_info.value, AgentError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"token": ""}),
        httpx.Response(200, json={"expiresAt": "2030-01-01T00:00:00Z"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["token"]),
        httpx.Response(200, json={"token": "tok", "expiresAt": "not a time"}),
    ],
    ids=["empty-token", "no-token", "not-json", "not-object", "bad-expiry"],
)
async def test_malformed_success_is_an_error(response):
    agent = _Agent(lambda r: response)

    with pytest.raises(OAuthError):
        await _source(agent).resource_token("https://mcp.example.com")


@pytest.mark.asyncio
async def test_no_socket_is_a_configuration_error():
    source = AgentTokenSource(env={})

    with pytest.raises(ConfigError, match="KEYCARD_SOCKET"):
        await source.resource_token("https://mcp.example.com")


@pytest.mark.asyncio
async def test_socket_comes_from_env():
    seen: list[str] = []

    class Recording(AgentTokenSource):
        def _socket_path(self) -> str:
            path = super()._socket_path()
            seen.append(path)
            return path

    agent = _Agent(lambda r: _issued("tok", _in(3600)))
    source = Recording(
        env={"KEYCARD_SOCKET": "/tmp/k.sock"}, _transport=agent.transport()
    )

    await source.resource_token("https://mcp.example.com")
    assert seen == ["/tmp/k.sock"]


@pytest.mark.asyncio
async def test_empty_resource_is_rejected_locally():
    agent = _Agent(lambda r: _issued("tok", _in(3600)))

    with pytest.raises(ConfigError):
        await _source(agent).resource_token("")
    assert agent.requests == []


@pytest.mark.asyncio
async def test_errors_are_not_cached():
    responses = [
        httpx.Response(503, json={"code": AgentCode.UNAVAILABLE, "message": "down"}),
        _issued("tok", _in(3600)),
    ]
    agent = _Agent(lambda r: responses.pop(0))
    source = _source(agent)

    with pytest.raises(AgentError):
        await source.resource_token("https://mcp.example.com")
    assert (
        await source.resource_token("https://mcp.example.com")
    ).access_token == "tok"


@pytest.mark.asyncio
async def test_real_unix_socket():
    # Uses tempfile.mkdtemp rather than tmp_path to stay under the platform's
    # Unix socket path length limit.
    socket_dir = tempfile.mkdtemp(prefix="kc")
    socket_path = os.path.join(socket_dir, "agent.sock")
    body = json.dumps({"token": "real-socket-token"}).encode()
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n" + body
    )
    request_lines: list[bytes] = []

    async def serve(reader, writer):
        request_lines.append(await reader.readline())
        await reader.read(4096)
        writer.write(response)
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(serve, path=socket_path)
    try:
        source = AgentTokenSource(env={"KEYCARD_SOCKET": socket_path})
        tok = await source.resource_token("https://mcp.example.com")
        assert tok.access_token == "real-socket-token"
        assert request_lines[0].startswith(b"POST " + ISSUE_TOKEN_PATH.encode())
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(socket_dir, ignore_errors=True)


class _StubSource:
    def __init__(self, token: str = "tok-123", error: Exception | None = None):
        self.token = token
        self.error = error
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    async def resource_token(self, resource: str, *scopes: str) -> TokenResponse:
        self.asked.append((resource, scopes))
        if self.error is not None:
            raise self.error
        return TokenResponse(access_token=self.token)


def _echo_authorization() -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda r: httpx.Response(
            200, json={"authorization": r.headers.get("Authorization")}
        )
    )


@pytest.mark.asyncio
async def test_resource_auth_attaches_bearer():
    src = _StubSource()
    auth = ResourceAuth(src, "https://mcp.example.com", "mcp:tools")

    async with httpx.AsyncClient(transport=_echo_authorization(), auth=auth) as client:
        response = await client.get("http://127.0.0.1:8099/anything")

    assert response.json() == {"authorization": "Bearer tok-123"}
    # The bound resource, not the host being called.
    assert src.asked == [("https://mcp.example.com", ("mcp:tools",))]


@pytest.mark.asyncio
async def test_resource_auth_surfaces_source_errors():
    src = _StubSource(error=AgentError(AgentCode.DECLINED, "declined"))
    auth = ResourceAuth(src, "https://mcp.example.com")

    async with httpx.AsyncClient(transport=_echo_authorization(), auth=auth) as client:
        with pytest.raises(AgentError) as exc_info:
            await client.get("http://127.0.0.1:8099/anything")
    assert is_declined(exc_info.value)


def test_resource_auth_requires_source_and_resource():
    with pytest.raises(ConfigError):
        ResourceAuth(None, "https://mcp.example.com")  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        ResourceAuth(_StubSource(), "")


def test_resource_auth_refuses_a_sync_client():
    # A sync client would otherwise run httpx's default flow and send the
    # request without the token.
    auth = ResourceAuth(_StubSource(), "https://mcp.example.com")

    with httpx.Client(transport=_echo_authorization(), auth=auth) as client:
        with pytest.raises(ConfigError, match="AsyncClient"):
            client.get("http://127.0.0.1:8099/anything")
