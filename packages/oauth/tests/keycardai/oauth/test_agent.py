"""Unit tests for the local Keycard agent token source.

Each test is named for its row in the Unit Tests table of the spec
(keycard-sdk-spec, specs/delegated-access/local-agent-token-source.md), so
``test_row_09_...`` is row 9. This SDK does not cache tokens, so the
caching-only rows 19 to 23 have no test, and row 18 expects two requests.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import httpx
import pytest

from keycardai.oauth import agent as agent_module
from keycardai.oauth import (
    AgentCode,
    AgentError,
    AgentResponseError,
    AgentTokenSource,
    ConfigError,
    NetworkError,
    OAuthError,
    OAuthHttpError,
    ResourceAuth,
    ResourceTokenSource,
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

unix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="the default agent socket is Unix-only"
)


class _Agent:
    """A stub agent behind an injected transport, recording each request."""

    def __init__(self, *responses: httpx.Response):
        self.requests: list[httpx.Request] = []
        self._responses = list(responses) or [httpx.Response(200, json={"token": "tok"})]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    def source(self, **kwargs) -> AgentTokenSource:
        kwargs.setdefault("socket", "/unused.sock")
        return AgentTokenSource(_transport=httpx.MockTransport(self._handle), **kwargs)

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


def _never_dialled() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    return httpx.MockTransport(handle)


RESOURCE = "https://api.example.com"


@pytest.mark.asyncio
async def test_row_01_request_shape():
    agent = _Agent()

    await agent.source(workload="unit-test").resource_token(RESOURCE, ["read", "write"])

    assert len(agent.requests) == 1
    request = agent.requests[0]
    assert request.method == "POST"
    assert request.url.path == ISSUE_TOKEN_PATH
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Connect-Protocol-Version"] == "1"
    assert agent.body() == {
        "resource": RESOURCE,
        "scopes": ["read", "write"],
        "workload": "unit-test",
    }


@pytest.mark.asyncio
async def test_row_01_unset_scopes_and_workload_are_sent_empty():
    agent = _Agent()

    await agent.source().resource_token(RESOURCE)

    assert agent.body() == {"resource": RESOURCE, "scopes": [], "workload": ""}


@pytest.mark.asyncio
async def test_row_02_tenant_headers():
    agent = _Agent()

    await agent.source(org="org-1", zone="zone-a").resource_token(RESOURCE)
    await agent.source().resource_token(RESOURCE)

    assert agent.requests[0].headers["Keycard-Org"] == "org-1"
    assert agent.requests[0].headers["Keycard-Zone"] == "zone-a"
    assert "Keycard-Org" not in agent.requests[1].headers
    assert "Keycard-Zone" not in agent.requests[1].headers


@pytest.mark.asyncio
async def test_row_03_app_id():
    agent = _Agent()

    await agent.source(app_id="incident-digest").resource_token(RESOURCE)
    await agent.source().resource_token(RESOURCE)

    assert agent.body(0)["appId"] == "incident-digest"
    assert "appId" not in agent.body(1)
    assert "app_id" not in agent.body(0)


@pytest.mark.asyncio
async def test_row_04_resource_is_not_normalized():
    messy = "https://API.Example.com:443/a/./b/../c"
    agent = _Agent()

    await agent.source().resource_token(messy, ["Scope:B", "a"])

    assert agent.body()["resource"] == messy
    assert agent.body()["scopes"] == ["Scope:B", "a"]


@pytest.mark.asyncio
async def test_row_05_empty_resource(monkeypatch):
    # An unresolvable env would raise ConfigError too, so resolution itself
    # must be shown not to run.
    def unreachable(*args):
        raise AssertionError("the socket must not be resolved")

    monkeypatch.setattr(agent_module, "_resolve_socket", unreachable)
    source = AgentTokenSource(env={}, _transport=_never_dialled())

    with pytest.raises(ConfigError, match="resource"):
        await source.resource_token("")


@pytest.mark.asyncio
async def test_row_06_socket_option_wins(short_tmp, agent_server):
    chosen = await agent_server(os.path.join(short_tmp, "chosen.sock"))
    other = await agent_server(os.path.join(short_tmp, "other.sock"))
    source = AgentTokenSource(socket=chosen.path, env={"KEYCARD_SOCKET": other.path})

    await source.resource_token(RESOURCE)

    assert len(chosen.calls) == 1
    assert other.calls == []


@pytest.mark.asyncio
async def test_row_07_socket_from_env_mapping(short_tmp, agent_server, monkeypatch):
    named = await agent_server(os.path.join(short_tmp, "named.sock"))
    process = await agent_server(os.path.join(short_tmp, "process.sock"))
    monkeypatch.setenv("KEYCARD_SOCKET", process.path)

    await AgentTokenSource(env={"KEYCARD_SOCKET": named.path}).resource_token(RESOURCE)

    assert len(named.calls) == 1
    assert process.calls == []


async def _dialled(env: dict[str, str]) -> str:
    """The socket path a mint resolved from ``env``, read from the network
    error that dialling a path with nothing behind it produces."""
    with pytest.raises(NetworkError) as exc_info:
        await AgentTokenSource(env=env).resource_token(RESOURCE)
    return str(exc_info.value)


@unix_only
@pytest.mark.asyncio
async def test_row_08_empty_keycard_socket_is_unset():
    message = await _dialled({"KEYCARD_SOCKET": "", "HOME": "/h"})
    assert " /h/.local/state/keycard/run/agent.sock " in message


@unix_only
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "env", "want"),
    [
        (9, {"HOME": "/h"}, "/h/.local/state/keycard/run/agent.sock"),
        (
            10,
            {"HOME": "/h", "KEYCARD_HOME": "/k"},
            "/k/.local/state/keycard/run/agent.sock",
        ),
        (
            11,
            {"HOME": "/h", "XDG_STATE_HOME": "/s"},
            "/s/keycard/run/agent.sock",
        ),
        (
            12,
            {"HOME": "/h", "XDG_STATE_HOME": "/s", "XDG_RUNTIME_DIR": "/r"},
            "/r/keycard/agent.sock",
        ),
        (
            13,
            {
                "HOME": "/h",
                "XDG_STATE_HOME": "/s",
                "XDG_RUNTIME_DIR": "/r",
                "KEYCARD_AGENT_DIR": "/a",
            },
            "/a/agent.sock",
        ),
    ],
    ids=lambda v: f"row-{v:02d}" if isinstance(v, int) else None,
)
async def test_rows_09_to_13_default_socket(row, env, want):
    assert f" {want} " in await _dialled(env)


@unix_only
@pytest.mark.asyncio
async def test_row_14_keycard_toml_is_not_read(short_tmp, monkeypatch):
    with open(os.path.join(short_tmp, "keycard.toml"), "w") as f:
        f.write('[home]\ndir = "/elsewhere"\n\n[agent]\nDir = "/agent-dir"\n')
    monkeypatch.chdir(short_tmp)

    message = await _dialled({"HOME": "/h"})

    assert " /h/.local/state/keycard/run/agent.sock " in message


@unix_only
@pytest.mark.asyncio
async def test_row_15_no_home():
    source = AgentTokenSource(env={}, _transport=_never_dialled())

    with pytest.raises(ConfigError) as exc_info:
        await source.resource_token(RESOURCE)

    assert "KEYCARD_SOCKET" in str(exc_info.value)
    assert "HOME" in str(exc_info.value)


@pytest.mark.asyncio
async def test_windows_default_socket_is_a_configuration_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    source = AgentTokenSource(env={"HOME": "/h"}, _transport=_never_dialled())

    with pytest.raises(ConfigError, match="KEYCARD_SOCKET"):
        await source.resource_token(RESOURCE)


@unix_only
@pytest.mark.asyncio
async def test_row_16_unreachable_default_socket(short_tmp):
    with pytest.raises(NetworkError) as exc_info:
        await AgentTokenSource(env={"HOME": short_tmp}).resource_token(RESOURCE)

    message = str(exc_info.value)
    assert os.path.join(short_tmp, ".local/state/keycard/run/agent.sock") in message
    assert "`keycard agent start`" in message
    assert "`keycard run`" in message
    assert exc_info.value.retryable
    assert isinstance(exc_info.value.cause, httpx.TransportError)
    assert os.listdir(short_tmp) == []


@pytest.mark.asyncio
async def test_network_error_from_keycard_socket_does_not_suggest_the_daemon(short_tmp):
    path = os.path.join(short_tmp, "missing.sock")

    with pytest.raises(NetworkError) as exc_info:
        await AgentTokenSource(env={"KEYCARD_SOCKET": path}).resource_token(RESOURCE)

    message = str(exc_info.value)
    assert path in message
    assert "KEYCARD_SOCKET" in message
    assert "keycard agent start" not in message


@pytest.mark.asyncio
async def test_row_17_real_unix_socket(short_tmp, agent_server):
    agent = await agent_server(os.path.join(short_tmp, "agent.sock"))

    token = await AgentTokenSource(socket=agent.path).resource_token(RESOURCE, ["read"])

    assert token.access_token == "agent-token"
    assert agent.calls[0].body == {"resource": RESOURCE, "scopes": ["read"], "workload": ""}
    assert agent.calls[0].headers["connect-protocol-version"] == "1"


@pytest.mark.asyncio
async def test_row_18_no_sdk_cache():
    agent = _Agent()
    source = agent.source()

    first = await source.resource_token(RESOURCE, ["read"])
    second = await source.resource_token(RESOURCE, ["read"])

    assert len(agent.requests) == 2
    assert first.access_token == second.access_token == "tok"


def _issued(**body) -> httpx.Response:
    return httpx.Response(200, json={"token": "tok", **body})


@pytest.mark.asyncio
async def test_row_24_no_expiry():
    token = await _Agent(_issued()).source().resource_token(RESOURCE)

    assert token.expires_in is None
    assert token.token_type == TokenType.BEARER


@pytest.mark.asyncio
async def test_row_25_expiry_in_the_past():
    agent = _Agent(_issued(expiresAt="2020-01-01T00:00:00Z"))

    token = await agent.source().resource_token(RESOURCE)

    assert token.expires_in == 0


@pytest.mark.asyncio
async def test_row_26_nanosecond_expiry_against_injected_clock():
    now = datetime(2030, 6, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    agent = _Agent(_issued(expiresAt="2030-06-01T13:00:00.999999999Z"))

    token = await agent.source(clock=lambda: now).resource_token(RESOURCE)

    assert token.expires_in == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stamp", "want"),
    [
        ("2030-06-01T13:00:00.5Z", 3600),
        ("2030-06-01T14:00:00+01:00", 3600),
        ("2030-06-01t13:00:00z", 3600),
        ("2030-06-01T12:59:59.999-00:00", 3599),
    ],
)
async def test_rfc3339_forms(stamp, want):
    now = datetime(2030, 6, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    token = await _Agent(_issued(expiresAt=stamp)).source(
        clock=lambda: now
    ).resource_token(RESOURCE)

    assert token.expires_in == want


@pytest.mark.asyncio
async def test_row_27_granted_scopes_as_reported():
    agent = _Agent(_issued(scopes=["a", "b"]))

    token = await agent.source().resource_token(RESOURCE, ["a"])

    assert token.scope == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", [None, []], ids=["absent", "empty"])
async def test_row_28_scope_never_derived_from_request(reported):
    body = {} if reported is None else {"scopes": reported}

    token = await _Agent(_issued(**body)).source().resource_token(RESOURCE, ["a"])

    assert token.scope is None


PREDICATES = {
    AgentCode.PERMISSION_DENIED: is_declined,
    AgentCode.RESOURCE_EXHAUSTED: is_rate_limited,
    AgentCode.FAILED_PRECONDITION: is_prompter_unavailable,
    AgentCode.UNAUTHENTICATED: is_authorization_required,
    AgentCode.NOT_FOUND: is_application_not_found,
    AgentCode.UNAVAILABLE: is_unavailable,
}

CODES = [
    (AgentCode.PERMISSION_DENIED, 403),
    (AgentCode.RESOURCE_EXHAUSTED, 429),
    (AgentCode.FAILED_PRECONDITION, 400),
    (AgentCode.UNAUTHENTICATED, 401),
    (AgentCode.NOT_FOUND, 404),
    (AgentCode.UNAVAILABLE, 503),
    (AgentCode.INVALID_ARGUMENT, 400),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "status"), CODES, ids=[c.value for c, _ in CODES])
async def test_row_29_connect_error_per_code(code, status):
    agent = _Agent(
        httpx.Response(status, json={"code": code.value, "message": "the remedy"})
    )

    with pytest.raises(AgentError) as exc_info:
        await agent.source().resource_token(RESOURCE)

    err = exc_info.value
    assert err.code == code.value
    assert err.message == "the remedy"
    assert isinstance(err, OAuthError)
    for other, predicate in PREDICATES.items():
        assert predicate(err) == (other == code), other.value


def test_row_30_predicates_follow_the_cause_chain():
    try:
        try:
            raise AgentError(AgentCode.PERMISSION_DENIED.value, "declined")
        except AgentError as inner:
            raise RuntimeError("caller wrapped it") from inner
    except RuntimeError as outer:
        assert is_declined(outer)
        assert not is_unavailable(outer)
    assert not is_declined(RuntimeError("unrelated"))
    assert not is_declined(None)


def test_predicates_follow_oauth_error_cause():
    # OAuthError(message, cause) is this SDK's wrapping idiom, and it sets
    # .cause without setting __cause__.
    declined = AgentError(AgentCode.PERMISSION_DENIED.value, "declined")
    wrapped = OAuthError("token exchange failed", cause=declined)

    assert is_declined(wrapped)
    deep = RuntimeError("outer")
    deep.__cause__ = OAuthError("middle", cause=declined)
    assert is_declined(deep)


def test_predicates_stop_on_a_cause_cycle():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    c = OAuthError("c")
    c.cause = c

    assert not is_declined(a)
    assert not is_declined(c)


def test_predicates_ignore_implicit_context():
    # Raising inside an `except AgentError:` block is a new failure, not a
    # wrapped refusal.
    try:
        try:
            raise AgentError(AgentCode.PERMISSION_DENIED.value, "declined")
        except AgentError:
            raise RuntimeError("fallback failed")  # noqa: B904
    except RuntimeError as err:
        assert not is_declined(err)


@pytest.mark.asyncio
async def test_row_31_non_json_error_body_is_an_http_error():
    agent = _Agent(httpx.Response(502, text="<html>bad gateway</html>"))

    with pytest.raises(OAuthHttpError) as exc_info:
        await agent.source().resource_token(RESOURCE)

    assert exc_info.value.status_code == 502
    assert not isinstance(exc_info.value, AgentError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{"message": "no code"}, {"code": "", "message": "empty"}, {"code": 7}, ["x"]],
    ids=["no-code", "empty-code", "non-string-code", "not-an-object"],
)
async def test_row_31_json_error_without_a_code_is_an_http_error(body):
    agent = _Agent(httpx.Response(500, json=body))

    with pytest.raises(OAuthHttpError) as exc_info:
        await agent.source().resource_token(RESOURCE)

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_error_body_is_read_to_a_bounded_prefix():
    # A Connect error whose JSON closes only after the bound reads as a
    # truncated, unparseable prefix.
    padded = json.dumps({"code": "unavailable", "message": "x" * (128 * 1024)})
    agent = _Agent(httpx.Response(503, content=padded.encode()))

    with pytest.raises(OAuthHttpError) as exc_info:
        await agent.source().resource_token(RESOURCE)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_success_body_is_bounded():
    padded = {"token": "tok", "padding": "x" * (1024 * 1024)}
    agent = _Agent(httpx.Response(200, json=padded))

    with pytest.raises(AgentResponseError, match="too large") as exc_info:
        await agent.source().resource_token(RESOURCE)

    assert "tok" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"), [(200, AgentResponseError), (503, OAuthHttpError)]
)
async def test_endless_body_is_not_read_to_the_end(status, error):
    async def endless():
        while True:
            # Yields to the loop so wait_for can cancel a read that never stops.
            await asyncio.sleep(0)
            yield b"x" * 4096

    agent = _Agent(httpx.Response(status, content=endless()))

    with pytest.raises(error):
        await asyncio.wait_for(agent.source().resource_token(RESOURCE), timeout=5)


@pytest.mark.asyncio
async def test_success_body_just_under_the_bound_is_read():
    body = json.dumps({"token": "tok", "padding": ""}).encode()
    padded = body[:-2] + b"x" * (1024 * 1024 - len(body)) + b'"}'
    assert len(padded) == 1024 * 1024
    agent = _Agent(httpx.Response(200, content=padded))

    token = await agent.source().resource_token(RESOURCE)

    assert token.access_token == "tok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"token": ""}),
        httpx.Response(200, json={"expiresAt": "2030-01-01T00:00:00Z"}),
        httpx.Response(200, json={"token": 42}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, text='{"token": "secret-value" trailing'),
        httpx.Response(200, json=["tok"]),
    ],
    ids=[
        "empty-token",
        "missing-token",
        "non-string-token",
        "not-json",
        "malformed-json-with-token",
        "not-object",
    ],
)
async def test_row_32_unusable_success(response):
    with pytest.raises(AgentResponseError) as exc_info:
        await _Agent(response).source().resource_token(RESOURCE)

    assert not exc_info.value.retryable
    # A JSONDecodeError holds the whole document in .doc, so it must be
    # neither the cause nor the suppressed context.
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expires_at",
    [
        "tomorrow",
        "2030-01-01",
        "2030-01-01T00:00:00",
        "2030-13-01T00:00:00Z",
        1893456000,
        "٢٠٣٠-01-01T00:00:00Z",  # Arabic-Indic digits, which \d matches
    ],
)
async def test_unparseable_expiry_is_an_agent_response_error(expires_at):
    with pytest.raises(AgentResponseError, match="expiresAt"):
        await _Agent(_issued(expiresAt=expires_at)).source().resource_token(RESOURCE)


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", ["a b", [1], ["a", 1], {"a": 1}])
async def test_malformed_scopes_is_treated_as_absent(scopes):
    # The spec's scope is the agent's list "when the response carries a
    # non-empty list; absent otherwise", and its error table does not make
    # a malformed one an error, so the token is kept.
    token = await _Agent(_issued(scopes=scopes)).source().resource_token(RESOURCE, ["a"])

    assert token.access_token == "tok"
    assert token.scope is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["\r\n", "\n", "\x00", "\t", "\x1f", "\x7f"], ids=repr)
async def test_row_33_control_character_in_token(bad):
    secret = f"secret{bad}value"
    agent = _Agent(httpx.Response(200, json={"token": secret}))

    with pytest.raises(AgentResponseError) as exc_info:
        await agent.source().resource_token(RESOURCE)

    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize(("code", "status"), CODES, ids=[c.value for c, _ in CODES])
def test_row_34_retryable(code, status):
    err = AgentError(code.value, "")
    assert err.retryable == (
        code in (AgentCode.RESOURCE_EXHAUSTED, AgentCode.UNAVAILABLE)
    )
    with pytest.raises(AttributeError):
        err.retryable = True  # type: ignore[misc]


class _StubSource:
    def __init__(self, token: str = "tok-123", error: Exception | None = None):
        self.token = token
        self.error = error
        self.asked: list[tuple[str, list[str] | None]] = []

    async def resource_token(
        self, resource: str, scopes: list[str] | None = None
    ) -> TokenResponse:
        self.asked.append((resource, scopes))
        if self.error is not None:
            raise self.error
        return TokenResponse(access_token=self.token)


class _Resource:
    """A stub resource server echoing the Authorization header it received."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json={"authorization": request.headers.get("Authorization")})

        return httpx.MockTransport(handle)


def test_stub_source_satisfies_protocol():
    assert isinstance(_StubSource(), ResourceTokenSource)
    assert isinstance(AgentTokenSource(), ResourceTokenSource)


@pytest.mark.asyncio
async def test_row_35_resource_auth_attaches_bearer():
    source = _StubSource()
    server = _Resource()
    auth = ResourceAuth(source, RESOURCE, ["read"])

    async with httpx.AsyncClient(transport=server.transport(), auth=auth) as client:
        response = await client.get("https://elsewhere.example.net/items")

    assert response.json() == {"authorization": "Bearer tok-123"}
    # The bound resource, not one derived from the request URL.
    assert source.asked == [(RESOURCE, ["read"])]


@pytest.mark.asyncio
async def test_row_36_source_error_propagates_and_nothing_is_sent():
    declined = AgentError(AgentCode.PERMISSION_DENIED.value, "the human declined")
    server = _Resource()
    auth = ResourceAuth(_StubSource(error=declined), RESOURCE)

    async with httpx.AsyncClient(transport=server.transport(), auth=auth) as client:
        with pytest.raises(AgentError) as exc_info:
            await client.get("https://api.example.com/items")

    assert exc_info.value is declined
    assert is_declined(exc_info.value)
    assert server.requests == []


def test_row_37_resource_auth_requires_source_and_resource():
    with pytest.raises(ConfigError):
        ResourceAuth(None, RESOURCE)  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        ResourceAuth(_StubSource(), "")


@pytest.mark.asyncio
async def test_row_38_existing_authorization_is_replaced_and_caller_headers_untouched():
    server = _Resource()
    caller_headers = {"Authorization": "Bearer stale"}
    auth = ResourceAuth(_StubSource(), RESOURCE)

    async with httpx.AsyncClient(transport=server.transport(), auth=auth) as client:
        response = await client.get("https://api.example.com/items", headers=caller_headers)

    assert response.json() == {"authorization": "Bearer tok-123"}
    assert server.requests[0].headers.get_list("Authorization") == ["Bearer tok-123"]
    assert caller_headers == {"Authorization": "Bearer stale"}


def test_resource_auth_refuses_a_sync_client():
    server = _Resource()
    auth = ResourceAuth(_StubSource(), RESOURCE)

    with httpx.Client(transport=server.transport(), auth=auth) as client:
        with pytest.raises(ConfigError, match="AsyncClient"):
            client.get("https://api.example.com/items")

    assert server.requests == []


@pytest.mark.asyncio
async def test_scopes_given_as_a_string_are_refused():
    source = AgentTokenSource(socket="/unused.sock", _transport=_never_dialled())

    with pytest.raises(ConfigError):
        await source.resource_token(RESOURCE, "read")  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        ResourceAuth(source, RESOURCE, "read")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_construction_is_inert():
    # No socket can be resolved from an empty env, yet construction succeeds:
    # resolution, and its error, wait for the first mint.
    source = AgentTokenSource(env={}, _transport=_never_dialled())

    with pytest.raises(ConfigError, match="KEYCARD_SOCKET"):
        await source.resource_token(RESOURCE)


@pytest.mark.asyncio
async def test_resolution_runs_on_every_call(short_tmp, agent_server):
    first = await agent_server(os.path.join(short_tmp, "first.sock"))
    second = await agent_server(os.path.join(short_tmp, "second.sock"))
    env = {"KEYCARD_SOCKET": first.path}
    source = AgentTokenSource(env=env)

    await source.resource_token(RESOURCE)
    env["KEYCARD_SOCKET"] = second.path
    await source.resource_token(RESOURCE)

    assert len(first.calls) == 1
    assert len(second.calls) == 1


@pytest.mark.asyncio
async def test_connection_failure_mid_request_is_a_network_error():
    def drop(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset", request=request)

    source = AgentTokenSource(socket="/s/agent.sock", _transport=httpx.MockTransport(drop))

    with pytest.raises(NetworkError) as exc_info:
        await source.resource_token(RESOURCE)

    assert "/s/agent.sock (the socket option)" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.ReadError)
