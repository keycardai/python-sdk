"""Unit tests for OAuth 2.0 Token Exchange operations (RFC 8693)."""

from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.oauth.exceptions import OAuthHttpError, OAuthProtocolError
from keycardai.oauth.http import HTTPContext
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.operations._token_exchange import (
    build_token_exchange_http_request,
    parse_token_exchange_http_response,
    token_exchange,
    token_exchange_async,
)
from keycardai.oauth.types.models import TokenExchangeRequest, TokenResponse
from keycardai.oauth.types.oauth import GrantType, TokenType


class TestTokenExchangeOperations:
    """Test token exchange operation functions directly."""

    def test_build_token_exchange_http_request_minimal(self):
        """Test building minimal token exchange HTTP request."""
        req = TokenExchangeRequest(
            subject_token="subject_jwt_token",
            subject_token_type=TokenType.ACCESS_TOKEN,
            grant_type=GrantType.TOKEN_EXCHANGE
        )
        auth_headers = {"Authorization": "Basic Y2xpZW50OnNlY3JldA=="}

        http_req = build_token_exchange_http_request(req, "https://auth.example.com/token", auth_headers)

        assert http_req.method == "POST"
        assert http_req.url == "https://auth.example.com/token"
        assert http_req.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert http_req.headers["Authorization"] == "Basic Y2xpZW50OnNlY3JldA=="
        assert http_req.body is not None

        # Parse the form data
        body_str = http_req.body.decode('utf-8')
        assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Atoken-exchange" in body_str
        assert "subject_token=subject_jwt_token" in body_str

    def test_build_token_exchange_http_request_full(self):
        """Test building full token exchange HTTP request."""
        req = TokenExchangeRequest(
            subject_token="subject_jwt_token",
            subject_token_type=TokenType.ACCESS_TOKEN,
            grant_type=GrantType.TOKEN_EXCHANGE,
            audience="https://api.example.com",
            scope="read write",
            requested_token_type=TokenType.ACCESS_TOKEN,
            actor_token="actor_jwt_token",
            actor_token_type=TokenType.ACCESS_TOKEN
        )
        auth_headers = {}

        http_req = build_token_exchange_http_request(req, "https://auth.example.com/token", auth_headers)

        assert http_req.method == "POST"
        body_str = http_req.body.decode('utf-8')
        assert "audience=https%3A%2F%2Fapi.example.com" in body_str
        assert "scope=read+write" in body_str
        assert "actor_token=actor_jwt_token" in body_str

    def test_build_token_exchange_http_request_validation_error(self):
        """Test validation error for missing subject_token."""
        req = TokenExchangeRequest(
            subject_token="",  # Empty subject token should fail
            subject_token_type=TokenType.ACCESS_TOKEN,
            grant_type=GrantType.TOKEN_EXCHANGE
        )

        with pytest.raises(ValueError, match="subject_token is required"):
            build_token_exchange_http_request(req, "https://auth.example.com/token", {})

    def test_parse_token_exchange_http_response_success(self):
        """Test parsing successful token exchange response."""
        response_body = b'''{
            "access_token": "exchanged_access_token_123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "exchanged_refresh_token_456",
            "scope": "read write"
        }'''

        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=response_body
        )

        result = parse_token_exchange_http_response(http_response)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "exchanged_access_token_123"
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600
        assert result.refresh_token == "exchanged_refresh_token_456"
        assert " ".join(result.scope) == "read write"  # scope is parsed as a list

    def test_parse_token_exchange_http_response_http_error(self):
        """Test parsing HTTP error response."""
        http_response = HttpResponse(
            status=400,
            headers={"Content-Type": "application/json"},
            body=b'{"error": "invalid_request", "error_description": "Invalid subject_token"}'
        )

        with pytest.raises(OAuthHttpError, match="HTTP 400"):
            parse_token_exchange_http_response(http_response)

    def test_parse_token_exchange_http_response_invalid_json(self):
        """Test parsing invalid JSON response."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b"invalid json {"
        )

        with pytest.raises(OAuthProtocolError, match="Invalid JSON"):
            parse_token_exchange_http_response(http_response)

    def test_token_exchange_sync(self):
        """Test synchronous token exchange."""
        mock_transport = Mock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token": "sync_exchanged_token", "token_type": "Bearer", "expires_in": 3600}'
        )

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {"Authorization": "Basic Y2xpZW50OnNlY3JldA=="}

        context = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=mock_transport,
            auth=mock_auth,
            timeout=30.0
        )

        req = TokenExchangeRequest(
            subject_token="subject_jwt_token",
            subject_token_type=TokenType.ACCESS_TOKEN,
            grant_type=GrantType.TOKEN_EXCHANGE
        )

        result = token_exchange(req, context)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "sync_exchanged_token"
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600

    @pytest.mark.asyncio
    async def test_token_exchange_async(self):
        """Test asynchronous token exchange."""
        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token": "async_exchanged_token", "token_type": "Bearer", "expires_in": 7200}'
        )

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {"Authorization": "Basic Y2xpZW50OnNlY3JldA=="}

        context = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=mock_transport,
            auth=mock_auth,
            timeout=30.0
        )

        req = TokenExchangeRequest(
            subject_token="subject_jwt_token",
            subject_token_type=TokenType.ACCESS_TOKEN,
            grant_type=GrantType.TOKEN_EXCHANGE
        )

        result = await token_exchange_async(req, context)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "async_exchanged_token"
        assert result.token_type == "Bearer"
        assert result.expires_in == 7200
