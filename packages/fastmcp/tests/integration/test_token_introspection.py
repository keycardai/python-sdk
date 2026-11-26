"""FastMCP integration v2 conformance over the HTTP transport."""

import time
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.http import StreamableHTTPASGIApp
from fixtures.auth_provider import mock_zone_url
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from keycardai.fastmcp import AuthProvider, AuthProviderConfigurationError, ClientSecret
from keycardai.oauth import OAuthProtocolError, TokenIntrospection

CREDENTIAL = ClientSecret(("resource-server", "secret"))
RESOURCE = "https://api.example.com/mcp"


@pytest.fixture(scope="module")
def signing_key():
    return RSAKey.generate_key(2048)


@pytest.fixture
def http_request(monkeypatch, signing_key, mock_client_factory):
    monkeypatch.setattr(
        JWTVerifier, "_get_verification_key",
        AsyncMock(return_value=signing_key.as_pem()),
    )

    handled = []

    async def handle(_, scope, receive, send):
        handled.append(scope["auth"].scopes)
        await PlainTextResponse("handler")(scope, receive, send)

    monkeypatch.setattr(StreamableHTTPASGIApp, "__call__", handle)

    def setup(*, issuer=mock_zone_url, audience=RESOURCE, **options):
        provider = AuthProvider(
            zone_url=mock_zone_url, mcp_server_url=RESOURCE,
            application_credential=CREDENTIAL, client_factory=mock_client_factory,
            **options,
        )
        server = FastMCP("test", auth=provider.get_remote_auth_provider())
        token = jwt.encode(
            {"alg": "RS256", "kid": "signing-key", "typ": "at+jwt"},
            {
                "iss": issuer, "aud": audience, "sub": "user",
                "client_id": "other-client", "exp": int(time.time()) + 60,
            },
            signing_key,
        )
        client = TestClient(server.http_app(transport="streamable-http", path="/mcp"))
        return lambda: client.post("/mcp", headers={"Authorization": f"Bearer {token}"}), token, handled

    return setup


def assert_inactive(response):
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith('Bearer error="invalid_token"')
    assert (
        'resource_metadata="https://api.example.com/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    assert response.json()["error"] == "invalid_token"


def assert_unavailable(response):
    assert response.status_code == 503
    assert response.json() == {"error": "temporarily_unavailable"}
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.parametrize("options", [{}, {"introspect": False}])
def test_row_1_default_and_false_do_not_introspect(
    http_request, mock_async_client, options
):
    request, _, handled = http_request(**options)
    assert request().text == "handler"
    assert handled == [[]]
    mock_async_client.introspect_token.assert_not_called()


def test_row_2_active_token_uses_resource_owner_credential(
    http_request, mock_async_client, mock_client_factory
):
    mock_async_client.introspect_token.return_value = TokenIntrospection(active=True)
    request, token, handled = http_request(introspect=True, introspection_timeout=1.5)
    assert request().text == "handler"
    assert handled == [[]]
    mock_async_client.introspect_token.assert_awaited_once_with(
        token, credential=CREDENTIAL, timeout=1.5
    )
    discovery_client = mock_client_factory.create_async_client.call_args_list[0]
    assert discovery_client.args == (mock_zone_url,)
    assert discovery_client.kwargs["config"].timeout == 1.5
    assert not discovery_client.kwargs["config"].auto_register_client


def test_row_3_inactive_token_challenges_without_dispatch(http_request, mock_async_client):
    mock_async_client.introspect_token.return_value = TokenIntrospection(active=False)
    request, token, handled = http_request(
        introspect=True, audience=["https://other.example.com/mcp", RESOURCE]
    )
    assert_inactive(request())
    mock_async_client.introspect_token.assert_awaited_once_with(
        token, credential=CREDENTIAL, timeout=10.0
    )
    assert handled == []


@pytest.mark.parametrize(("status", "code"), [(401, "invalid_client"), (500, "server_error")])
def test_row_4_failure_returns_503_without_challenge(
    http_request, mock_async_client, status, code
):
    mock_async_client.introspect_token.side_effect = OAuthProtocolError(
        error=code, status_code=status
    )
    request, token, handled = http_request(introspect=True)
    assert_unavailable(request())
    mock_async_client.introspect_token.assert_awaited_once_with(
        token, credential=CREDENTIAL, timeout=10.0
    )
    assert handled == []


@pytest.mark.parametrize(
    ("credential", "message"),
    [(None, "requires a credential"), (object(), "does not provide client authentication")],
)
def test_row_5_no_client_authentication_is_a_configuration_error(
    mock_client_factory, credential, message
):
    with pytest.raises(AuthProviderConfigurationError, match=message):
        AuthProvider(
            zone_url=mock_zone_url, mcp_server_url=RESOURCE, introspect=True,
            application_credential=credential, client_factory=mock_client_factory,
        )
    mock_client_factory.create_client.assert_not_called()
    mock_client_factory.create_async_client.assert_not_called()


def test_invalid_local_token_is_not_introspected(http_request, mock_async_client):
    request, _, handled = http_request(introspect=True, issuer="https://other.example.com")
    assert_inactive(request())
    assert handled == []
    mock_async_client.introspect_token.assert_not_called()


def test_same_token_is_introspected_on_every_request(http_request, mock_async_client):
    mock_async_client.introspect_token.side_effect = [
        TokenIntrospection(active=True), TokenIntrospection(active=False)
    ]
    request, token, handled = http_request(introspect=True)
    assert request().text == "handler"
    assert_inactive(request())
    assert handled == [[]]
    assert [call.args for call in mock_async_client.introspect_token.await_args_list] == [
        (token,), (token,)
    ]


def test_insufficient_scope_is_rejected_before_introspection(http_request, mock_async_client):
    request, _, handled = http_request(introspect=True, required_scopes=["write"])
    response = request()
    assert response.status_code == 403
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith('Bearer error="insufficient_scope"')
    assert (
        'resource_metadata="https://api.example.com/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    assert response.json()["error"] == "insufficient_scope"
    assert handled == []
    mock_async_client.introspect_token.assert_not_called()
