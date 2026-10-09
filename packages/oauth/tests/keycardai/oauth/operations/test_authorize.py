"""Unit tests for OAuth 2.0 Authorization Code operations."""

from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import pytest

from keycardai.oauth.exceptions import (
    PERMANENT_ERROR_CODES,
    OAuthHttpError,
    OAuthProtocolError,
    RefreshGrantError,
)
from keycardai.oauth.http._context import HTTPContext
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.http.auth import BasicAuth, NoneAuth
from keycardai.oauth.operations._authorize import (
    build_authorization_code_http_request,
    build_authorize_url,
    build_refresh_token_http_request,
    exchange_authorization_code,
    exchange_authorization_code_async,
    parse_authorization_code_http_response,
    parse_refresh_token_http_response,
    refresh_token_grant,
    refresh_token_grant_async,
)
from keycardai.oauth.types.models import TokenResponse
from keycardai.oauth.utils.pkce import PKCEChallenge


class TestBuildAuthorizeUrl:
    """Test authorize URL construction."""

    def _make_pkce(self) -> PKCEChallenge:
        return PKCEChallenge(
            code_verifier="test_verifier",
            code_challenge="test_challenge",
            code_challenge_method="S256",
        )

    def test_minimal(self):
        url = build_authorize_url(
            "https://auth.example.com/authorize",
            client_id="my-client",
            redirect_uri="http://localhost:9999/callback",
            pkce=self._make_pkce(),
        )
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert parsed.netloc == "auth.example.com"
        assert parsed.path == "/authorize"
        assert qs["response_type"] == ["code"]
        assert qs["client_id"] == ["my-client"]
        assert qs["redirect_uri"] == ["http://localhost:9999/callback"]
        assert qs["code_challenge"] == ["test_challenge"]
        assert qs["code_challenge_method"] == ["S256"]
        assert "resource" not in qs
        assert "scope" not in qs
        assert "state" not in qs

    def test_single_resource(self):
        url = build_authorize_url(
            "https://auth.example.com/authorize",
            client_id="my-client",
            redirect_uri="http://localhost:9999/callback",
            pkce=self._make_pkce(),
            resources=["https://graph.microsoft.com"],
        )
        qs = parse_qs(urlparse(url).query)
        assert qs["resource"] == ["https://graph.microsoft.com"]

    def test_multiple_resources(self):
        url = build_authorize_url(
            "https://auth.example.com/authorize",
            client_id="my-client",
            redirect_uri="http://localhost:9999/callback",
            pkce=self._make_pkce(),
            resources=[
                "https://graph.microsoft.com",
                "https://api.github.com",
                "https://api.linear.app",
            ],
        )
        qs = parse_qs(urlparse(url).query)
        assert qs["resource"] == [
            "https://graph.microsoft.com",
            "https://api.github.com",
            "https://api.linear.app",
        ]

    def test_with_scope_and_state(self):
        url = build_authorize_url(
            "https://auth.example.com/authorize",
            client_id="my-client",
            redirect_uri="http://localhost:9999/callback",
            pkce=self._make_pkce(),
            scope="openid email",
            state="csrf-token-123",
        )
        qs = parse_qs(urlparse(url).query)
        assert qs["scope"] == ["openid email"]
        assert qs["state"] == ["csrf-token-123"]

    def test_empty_resources_omitted(self):
        url = build_authorize_url(
            "https://auth.example.com/authorize",
            client_id="my-client",
            redirect_uri="http://localhost:9999/callback",
            pkce=self._make_pkce(),
            resources=[],
        )
        qs = parse_qs(urlparse(url).query)
        assert "resource" not in qs


class TestBuildAuthorizationCodeHttpRequest:
    """Test HTTP request construction for code exchange."""

    def test_public_client(self):
        auth = NoneAuth()
        ctx = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=Mock(),
            auth=auth,
        )
        http_req = build_authorization_code_http_request(
            code="AUTH_CODE_123",
            redirect_uri="http://localhost:9999/callback",
            code_verifier="test_verifier",
            client_id="public-client-id",
            context=ctx,
        )

        assert http_req.method == "POST"
        assert http_req.url == "https://auth.example.com/token"
        assert http_req.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert "Authorization" not in http_req.headers

        body = http_req.body.decode("utf-8")
        form = parse_qs(body)
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["AUTH_CODE_123"]
        assert form["redirect_uri"] == ["http://localhost:9999/callback"]
        assert form["code_verifier"] == ["test_verifier"]
        assert form["client_id"] == ["public-client-id"]

    def test_confidential_client(self):
        auth = BasicAuth("conf-client", "conf-secret")
        ctx = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=Mock(),
            auth=auth,
        )
        http_req = build_authorization_code_http_request(
            code="AUTH_CODE_456",
            redirect_uri="http://localhost:9999/callback",
            code_verifier="test_verifier",
            client_id=None,
            context=ctx,
        )

        body = http_req.body.decode("utf-8")
        form = parse_qs(body)
        assert "client_id" not in form
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["AUTH_CODE_456"]
        assert "Authorization" in http_req.headers
        assert http_req.headers["Authorization"].startswith("Basic ")


class TestParseAuthorizationCodeHttpResponse:
    """Test response parsing for code exchange."""

    def test_success(self):
        res = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token":"at_123","token_type":"Bearer","expires_in":3600,"refresh_token":"rt_456","id_token":"ey.header.sig","scope":"openid email"}',
        )
        result = parse_authorization_code_http_response(res)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "at_123"
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600
        assert result.refresh_token == "rt_456"
        assert result.id_token == "ey.header.sig"
        assert result.scope == ["openid", "email"]
        assert result.raw["access_token"] == "at_123"

    def test_minimal_success(self):
        res = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token":"at_minimal"}',
        )
        result = parse_authorization_code_http_response(res)
        assert result.access_token == "at_minimal"
        assert result.token_type == "Bearer"
        assert result.refresh_token is None
        assert result.id_token is None

    def test_oauth_error(self):
        res = HttpResponse(
            status=400,
            headers={"Content-Type": "application/json"},
            body=b'{"error":"invalid_grant","error_description":"Code expired"}',
        )
        with pytest.raises(OAuthProtocolError, match="invalid_grant") as exc_info:
            parse_authorization_code_http_response(res)
        assert exc_info.value.error_description == "Code expired"

    def test_http_error_non_json(self):
        res = HttpResponse(
            status=500,
            headers={"Content-Type": "text/plain"},
            body=b"Internal Server Error",
        )
        with pytest.raises(OAuthHttpError, match="HTTP 500"):
            parse_authorization_code_http_response(res)

    def test_invalid_json(self):
        res = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b"not json{",
        )
        with pytest.raises(OAuthProtocolError, match="Invalid JSON"):
            parse_authorization_code_http_response(res)

    def test_missing_access_token(self):
        res = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"token_type":"Bearer"}',
        )
        with pytest.raises(OAuthProtocolError, match="Missing required"):
            parse_authorization_code_http_response(res)


class TestExchangeAuthorizationCode:
    """Test the sync exchange function."""

    def test_sync_exchange(self):
        mock_transport = Mock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token":"sync_at","token_type":"Bearer","expires_in":3600}',
        )
        ctx = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=mock_transport,
            auth=NoneAuth(),
            timeout=30.0,
        )

        result = exchange_authorization_code(
            code="CODE",
            redirect_uri="http://localhost:9999/callback",
            code_verifier="verifier",
            client_id="pub-client",
            context=ctx,
        )

        assert result.access_token == "sync_at"
        assert result.expires_in == 3600
        mock_transport.request_raw.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_exchange(self):
        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token":"async_at","token_type":"Bearer","expires_in":7200}',
        )
        ctx = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=mock_transport,
            auth=NoneAuth(),
            timeout=30.0,
        )

        result = await exchange_authorization_code_async(
            code="CODE",
            redirect_uri="http://localhost:9999/callback",
            code_verifier="verifier",
            client_id="pub-client",
            context=ctx,
        )

        assert result.access_token == "async_at"
        assert result.expires_in == 7200
        mock_transport.request_raw.assert_called_once()


class TestRefreshTokenGrant:
    """Spec row 15: the refresh step of the authorization-code flow."""

    def _ctx(self, transport, auth=None):
        return HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=transport,
            auth=auth or NoneAuth(),
            issuer="https://auth.example.com",
            timeout=30.0,
        )

    def _transport(self, status=200, body=b'{"access_token":"new_at","token_type":"Bearer"}'):
        transport = Mock()
        transport.request_raw.return_value = HttpResponse(
            status=status, headers={"Content-Type": "application/json"}, body=body
        )
        return transport

    def test_public_client_sends_client_id_in_body_and_no_basic_header(self):
        req = build_refresh_token_http_request(
            refresh_token="rt",
            client_id="pub-client",
            context=self._ctx(Mock()),
        )
        assert req.body is not None
        body = parse_qs(req.body.decode())
        assert body["grant_type"] == ["refresh_token"]
        assert body["refresh_token"] == ["rt"]
        assert body["client_id"] == ["pub-client"]
        assert "Authorization" not in req.headers
        assert "resource" not in body
        assert "scope" not in body

    def test_confidential_client_uses_basic_and_omits_client_id(self):
        req = build_refresh_token_http_request(
            refresh_token="rt",
            client_id=None,
            context=self._ctx(Mock(), auth=BasicAuth("cid", "secret")),
        )
        assert req.body is not None
        body = parse_qs(req.body.decode())
        assert req.headers["Authorization"].startswith("Basic ")
        assert "client_id" not in body

    def test_one_resource_parameter_per_entry_and_scope_joined(self):
        req = build_refresh_token_http_request(
            refresh_token="rt",
            client_id="pub-client",
            context=self._ctx(Mock()),
            resources=["https://a.example.com", "https://b.example.com"],
            scope="read write",
        )
        assert req.body is not None
        body = parse_qs(req.body.decode())
        assert body["resource"] == ["https://a.example.com", "https://b.example.com"]
        assert body["scope"] == ["read write"]

    def test_rotated_refresh_token_is_returned(self):
        res = HttpResponse(
            status=200,
            headers={},
            body=b'{"access_token":"new_at","refresh_token":"rt2","token_type":"Bearer"}',
        )
        assert parse_refresh_token_http_response(res).refresh_token == "rt2"

    def test_absent_refresh_token_is_none(self):
        res = HttpResponse(
            status=200, headers={}, body=b'{"access_token":"new_at","token_type":"Bearer"}'
        )
        assert parse_refresh_token_http_response(res).refresh_token is None

    def test_invalid_grant_is_a_non_retryable_refresh_grant_error(self):
        res = HttpResponse(
            status=400,
            headers={},
            body=b'{"error":"invalid_grant","error_description":"expired"}',
        )
        with pytest.raises(RefreshGrantError) as exc:
            parse_refresh_token_http_response(res)
        assert exc.value.error == "invalid_grant"
        assert exc.value.retryable is False
        assert isinstance(exc.value, OAuthProtocolError)
        assert "invalid_grant" not in PERMANENT_ERROR_CODES

    def test_other_oauth_errors_follow_the_parent_classification(self):
        res = HttpResponse(
            status=400, headers={}, body=b'{"error":"temporarily_unavailable"}'
        )
        with pytest.raises(RefreshGrantError) as exc:
            parse_refresh_token_http_response(res)
        assert exc.value.retryable is True

    def test_5xx_is_retryable(self):
        res = HttpResponse(status=503, headers={}, body=b"down")
        with pytest.raises(OAuthHttpError) as exc:
            parse_refresh_token_http_response(res)
        assert exc.value.retryable is True

    def test_sync_grant(self):
        transport = self._transport()
        result = refresh_token_grant(
            refresh_token="rt", client_id="pub-client", context=self._ctx(transport)
        )
        assert result.access_token == "new_at"
        sent = transport.request_raw.call_args.args[0]
        assert parse_qs(sent.body.decode())["grant_type"] == ["refresh_token"]

    @pytest.mark.asyncio
    async def test_async_grant(self):
        transport = AsyncMock()
        transport.request_raw.return_value = HttpResponse(
            status=200, headers={}, body=b'{"access_token":"new_at","token_type":"Bearer"}'
        )
        result = await refresh_token_grant_async(
            refresh_token="rt", client_id="pub-client", context=self._ctx(transport)
        )
        assert result.access_token == "new_at"


class TestClientRefreshTokenGrant:
    """The AsyncClient and Client methods join scopes and pass resources through."""

    def test_sync_client_method(self):
        from keycardai.oauth import Client, ClientConfig, Endpoints

        transport = Mock()
        transport.request_raw.return_value = HttpResponse(
            status=200, headers={}, body=b'{"access_token":"new_at","token_type":"Bearer"}'
        )
        client = Client(
            "https://auth.example.com",
            auth=BasicAuth("cid", "secret"),
            config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False),
            endpoints=Endpoints(token="https://auth.example.com/token"),
            transport=transport,
        )
        result = client.refresh_token_grant(
            refresh_token="rt", resources=["https://a.example.com"], scopes=["read", "write"]
        )
        assert result.access_token == "new_at"
        sent = transport.request_raw.call_args.args[0]
        body = parse_qs(sent.body.decode())
        assert body["scope"] == ["read write"]
        assert body["resource"] == ["https://a.example.com"]
        assert "client_id" not in body
        assert sent.headers["Authorization"].startswith("Basic ")

    @pytest.mark.asyncio
    async def test_async_client_method(self):
        from keycardai.oauth import AsyncClient, ClientConfig, Endpoints

        transport = AsyncMock()
        transport.request_raw.return_value = HttpResponse(
            status=200, headers={}, body=b'{"access_token":"new_at","token_type":"Bearer"}'
        )
        client = AsyncClient(
            "https://auth.example.com",
            auth=NoneAuth(),
            config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False),
            endpoints=Endpoints(token="https://auth.example.com/token"),
            transport=transport,
        )
        result = await client.refresh_token_grant(refresh_token="rt", client_id="pub-client")
        assert result.access_token == "new_at"
        body = parse_qs(transport.request_raw.call_args.args[0].body.decode())
        assert body["client_id"] == ["pub-client"]
