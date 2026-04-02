"""Unit tests for OAuth 2.0 Authorization Code operations."""

from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import pytest

from keycardai.oauth.exceptions import OAuthHttpError, OAuthProtocolError
from keycardai.oauth.http._context import HTTPContext
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.http.auth import BasicAuth, NoneAuth
from keycardai.oauth.operations._authorize import (
    build_authorization_code_http_request,
    build_authorize_url,
    exchange_authorization_code,
    exchange_authorization_code_async,
    parse_authorization_code_http_response,
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
