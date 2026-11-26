"""FastMCP integration v2 conformance over the HTTP transport."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.auth.middleware import RequireAuthMiddleware
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.http import StreamableHTTPASGIApp
from fixtures.auth_provider import mock_zone_url
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from keycardai.fastmcp import AuthProvider, AuthProviderConfigurationError, ClientSecret
from keycardai.oauth import AsyncClient, OAuthProtocolError, TokenIntrospection
from keycardai.oauth.http._wire import HttpResponse

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

    def setup(
        *, issuer=mock_zone_url, audience=RESOURCE, transport="streamable-http",
        configure_server=None, **options
    ):
        provider = AuthProvider(
            zone_url=mock_zone_url, mcp_server_url=RESOURCE,
            application_credential=CREDENTIAL, client_factory=mock_client_factory,
            **options,
        )
        server = FastMCP("test", auth=provider.get_remote_auth_provider())
        if configure_server is not None:
            configure_server(server)

        @server.custom_route("/health", methods=["GET"])
        async def health(request):
            return PlainTextResponse("authenticated" if request.user.is_authenticated else "ok")

        token = jwt.encode(
            {"alg": "RS256", "kid": "signing-key", "typ": "at+jwt"},
            {
                "iss": issuer, "aud": audience, "sub": "user",
                "client_id": "other-client", "exp": int(time.time()) + 60,
            },
            signing_key,
        )
        client = TestClient(server.http_app(transport=transport, path="/mcp"))

        def request(path="/mcp", method="POST"):
            return client.request(method, path, headers={"Authorization": f"Bearer {token}"})

        return request, token, handled

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


@pytest.mark.parametrize("required_scopes", [[], ["write"]])
def test_auth_failure_does_not_block_public_routes(
    http_request, mock_async_client, required_scopes
):
    mock_async_client.introspect_token.side_effect = TimeoutError()
    request, _, handled = http_request(introspect=True, required_scopes=required_scopes)
    metadata = request("/.well-known/oauth-protected-resource/mcp", "GET")
    assert metadata.status_code == 200
    health = request("/health", "GET")
    assert health.status_code == 200
    assert health.text == "ok"
    if required_scopes:
        assert request().status_code == 403
        mock_async_client.introspect_token.assert_not_called()
    else:
        assert_unavailable(request())
    assert handled == []


def test_sse_message_mount_reports_introspection_failure(http_request, mock_async_client):
    mock_async_client.introspect_token.side_effect = TimeoutError()
    request, _, handled = http_request(introspect=True, transport="sse")
    assert_unavailable(request("/messages/"))
    assert handled == []


@pytest.mark.parametrize("required_scopes", [[], ["write"]])
def test_component_manager_preserves_introspection_errors(
    http_request, mock_async_client, required_scopes
):
    manager = pytest.importorskip("fastmcp.contrib.component_manager")
    mock_async_client.introspect_token.side_effect = TimeoutError()
    request, _, handled = http_request(
        introspect=True,
        required_scopes=required_scopes,
        configure_server=manager.set_up_component_manager,
    )
    response = request("/tools/example/disable")
    if required_scopes:
        assert response.status_code == 403
        assert response.headers["WWW-Authenticate"] == (
            'Bearer error="insufficient_scope", error_description="Insufficient scope", '
            'resource_metadata="https://api.example.com/.well-known/oauth-protected-resource/mcp"'
        )
        mock_async_client.introspect_token.assert_not_called()
    else:
        assert_unavailable(response)
    assert handled == []


@pytest.mark.parametrize("guard_as_middleware", [False, True])
@pytest.mark.parametrize("required_scopes", [[], ["write"]])
def test_custom_route_middleware_preserves_introspection_errors(
    http_request, mock_async_client, guard_as_middleware, required_scopes
):
    class Endpoint:
        async def __call__(self, scope, receive, send):
            pytest.fail("Authentication failure must not reach the protected route")

    async def passthrough(request, call_next):
        return await call_next(request)

    def configure_server(server):
        middleware = [Middleware(BaseHTTPMiddleware, dispatch=passthrough)]
        if guard_as_middleware:
            handler = Endpoint()
            middleware.append(Middleware(RequireAuthMiddleware, required_scopes))
        else:
            handler = RequireAuthMiddleware(Endpoint(), required_scopes)
        server._additional_http_routes.append(
            Route("/custom", endpoint=handler, methods=["POST"], middleware=middleware)
        )

    mock_async_client.introspect_token.side_effect = TimeoutError()
    request, _, _ = http_request(
        introspect=True, required_scopes=required_scopes, configure_server=configure_server
    )
    response = request("/custom")
    if required_scopes:
        assert response.status_code == 403
        assert 'error="insufficient_scope"' in response.headers["WWW-Authenticate"]
        mock_async_client.introspect_token.assert_not_called()
    else:
        assert_unavailable(response)


def test_introspection_never_discovers_metadata_over_plaintext(mock_client_factory):
    with pytest.raises(AuthProviderConfigurationError, match="requires an HTTPS issuer"):
        AuthProvider(
            zone_url="http://issuer.example",
            mcp_server_url=RESOURCE,
            introspect=True,
            application_credential=CREDENTIAL,
            client_factory=mock_client_factory,
        )
    mock_client_factory.create_client.assert_not_called()
    mock_client_factory.create_async_client.assert_not_called()


def test_introspection_deadline_covers_discovery_and_post(
    http_request, mock_client_factory, mock_async_client
):
    methods = []

    class Transport:
        async def request_raw(self, request, *, timeout=None):
            methods.append(request.method)
            await asyncio.sleep(0.08)
            body = (
                {
                    "issuer": mock_zone_url,
                    "introspection_endpoint": f"{mock_zone_url}/introspect",
                }
                if request.method == "GET"
                else {"active": True}
            )
            return HttpResponse(status=200, headers={}, body=json.dumps(body).encode())

    def create_client(issuer, *, auth=None, config=None):
        if config is None:
            return mock_async_client
        return AsyncClient(issuer, config=config, transport=Transport())

    mock_client_factory.create_async_client.side_effect = create_client
    request, _, handled = http_request(introspect=True, introspection_timeout=0.12)
    assert_unavailable(request())
    assert methods == ["GET", "POST"]
    assert handled == []


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
