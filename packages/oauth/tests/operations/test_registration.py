"""Unit tests for OAuth 2.0 Dynamic Client Registration operations (RFC 7591)."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.oauth.exceptions import OAuthHttpError, OAuthProtocolError
from keycardai.oauth.http import HTTPContext
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.operations._registration import (
    build_client_registration_http_request,
    parse_client_registration_http_response,
    register_client,
    register_client_async,
)
from keycardai.oauth.types.models import (
    ClientRegistrationRequest,
    ClientRegistrationResponse,
)
from keycardai.oauth.types.oauth import GrantType, ResponseType, TokenEndpointAuthMethod


class TestRegistrationOperations:
    """Test registration operation functions directly."""

    def test_build_registration_http_request_minimal(self):
        """Test building minimal registration HTTP request."""
        req = ClientRegistrationRequest(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            grant_types=[GrantType.AUTHORIZATION_CODE]
        )
        auth_headers = {"Authorization": "Bearer token"}

        http_req = build_client_registration_http_request(req, "https://auth.example.com/register", auth_headers)

        assert http_req.method == "POST"
        assert http_req.url == "https://auth.example.com/register"
        assert http_req.headers["Accept"] == "application/json"
        assert http_req.headers["Content-Type"] == "application/json"
        assert http_req.headers["Authorization"] == "Bearer token"
        assert http_req.body is not None

    def test_build_registration_http_request_full(self):
        """Test building full registration HTTP request."""
        req = ClientRegistrationRequest(
            client_name="Enterprise Client",
            redirect_uris=["https://app.example.com/callback", "https://app.example.com/auth"],
            grant_types=[GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN],
            response_types=[ResponseType.CODE],
            token_endpoint_auth_method=TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
            scope="read write admin"
        )
        auth_headers = {}

        http_req = build_client_registration_http_request(req, "https://auth.example.com/register", auth_headers)

        assert http_req.method == "POST"
        assert http_req.url == "https://auth.example.com/register"
        assert http_req.body is not None

        # Parse the body to verify content
        body_data = json.loads(http_req.body.decode('utf-8'))
        assert body_data["client_name"] == "Enterprise Client"
        assert len(body_data["redirect_uris"]) == 2
        assert "authorization_code" in body_data["grant_types"]

    def test_build_registration_http_request_validation_error(self):
        """Test validation error for missing client_name."""
        req = ClientRegistrationRequest(
            client_name="",  # Empty client name should fail
            redirect_uris=["https://example.com/callback"],
            grant_types=[GrantType.AUTHORIZATION_CODE]
        )

        with pytest.raises(ValueError, match="client_name required"):
            build_client_registration_http_request(req, "https://auth.example.com/register", {})

    def test_parse_registration_http_response_success(self):
        """Test parsing successful registration response."""
        response_body = b'''{
            "client_id": "generated_client_123",
            "client_secret": "generated_secret_456",
            "client_name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_basic"
        }'''

        http_response = HttpResponse(
            status=201,
            headers={"Content-Type": "application/json"},
            body=response_body
        )

        result = parse_client_registration_http_response(http_response)

        assert isinstance(result, ClientRegistrationResponse)
        assert result.client_id == "generated_client_123"
        assert result.client_secret == "generated_secret_456"
        assert result.client_name == "Test Client"

    def test_parse_registration_http_response_http_error(self):
        """Test parsing HTTP error response."""
        http_response = HttpResponse(
            status=400,
            headers={"Content-Type": "application/json"},
            body=b'{"error": "invalid_request", "error_description": "Missing client_name"}'
        )

        with pytest.raises(OAuthHttpError, match="HTTP 400"):
            parse_client_registration_http_response(http_response)

    def test_parse_registration_http_response_invalid_json(self):
        """Test parsing invalid JSON response."""
        http_response = HttpResponse(
            status=201,
            headers={"Content-Type": "application/json"},
            body=b"invalid json {"
        )

        with pytest.raises(OAuthProtocolError, match="Invalid JSON"):
            parse_client_registration_http_response(http_response)

    def test_register_client_sync(self):
        """Test synchronous client registration."""
        mock_transport = Mock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=201,
            headers={"Content-Type": "application/json"},
            body=b'{"client_id": "sync_client_123", "client_secret": "sync_secret_456"}'
        )

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {"Authorization": "Bearer token"}

        context = HTTPContext(
            endpoint="https://auth.example.com/register",
            transport=mock_transport,
            auth=mock_auth,
            timeout=30.0
        )

        req = ClientRegistrationRequest(
            client_name="Sync Test Client",
            redirect_uris=["https://example.com/callback"],
            grant_types=[GrantType.AUTHORIZATION_CODE]
        )

        result = register_client(req, context)

        assert isinstance(result, ClientRegistrationResponse)
        assert result.client_id == "sync_client_123"
        assert result.client_secret == "sync_secret_456"

    @pytest.mark.asyncio
    async def test_register_client_async(self):
        """Test asynchronous client registration."""
        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=201,
            headers={"Content-Type": "application/json"},
            body=b'{"client_id": "async_client_123", "client_secret": "async_secret_456"}'
        )

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {"Authorization": "Bearer token"}

        context = HTTPContext(
            endpoint="https://auth.example.com/register",
            transport=mock_transport,
            auth=mock_auth,
            timeout=30.0
        )

        req = ClientRegistrationRequest(
            client_name="Async Test Client",
            redirect_uris=["https://example.com/callback"],
            grant_types=[GrantType.AUTHORIZATION_CODE]
        )

        result = await register_client_async(req, context)

        assert isinstance(result, ClientRegistrationResponse)
        assert result.client_id == "async_client_123"
        assert result.client_secret == "async_secret_456"
