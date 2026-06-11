"""Unit tests for OAuth 2.0 Dynamic Client Registration operations (RFC 7591)."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.oauth.exceptions import OAuthHttpError, OAuthProtocolError
from keycardai.oauth.http._context import build_http_context
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

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {}

        context = build_http_context(
            endpoint="https://auth.example.com/register",
            transport=Mock(),
            auth=mock_auth,
            user_agent="TestClient/1.0"
        )

        http_req = build_client_registration_http_request(req, context)

        assert http_req.method == "POST"
        assert http_req.url == "https://auth.example.com/register"
        assert http_req.headers["Accept"] == "application/json"
        assert http_req.headers["Content-Type"] == "application/json"
        assert http_req.headers["User-Agent"] == "TestClient/1.0"
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

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {"Authorization": "Bearer token"}

        context = build_http_context(
            endpoint="https://auth.example.com/register",
            transport=Mock(),
            auth=mock_auth,
            user_agent="TestClient/1.0",
            custom_headers={"X-Custom": "value"}
        )

        http_req = build_client_registration_http_request(req, context)

        assert http_req.method == "POST"
        assert http_req.url == "https://auth.example.com/register"
        assert http_req.headers["User-Agent"] == "TestClient/1.0"
        assert http_req.headers["X-Custom"] == "value"
        assert http_req.headers["Authorization"] == "Bearer token"
        assert http_req.body is not None

        # Parse the body to verify content
        body_data = json.loads(http_req.body.decode('utf-8'))
        assert body_data["client_name"] == "Enterprise Client"
        assert len(body_data["redirect_uris"]) == 2
        assert "authorization_code" in body_data["grant_types"]

    def test_minimal_request_omits_unset_defaults(self):
        """An RFC-minimal request injects no defaults into the POST body."""
        req = ClientRegistrationRequest(client_name="My App")

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {}

        context = build_http_context(
            endpoint="https://auth.example.com/register",
            transport=Mock(),
            auth=mock_auth,
            user_agent="TestClient/1.0",
        )

        http_req = build_client_registration_http_request(req, context)
        body_data = json.loads(http_req.body.decode("utf-8"))

        assert body_data == {"client_name": "My App"}
        for omitted in (
            "redirect_uris",
            "scope",
            "grant_types",
            "response_types",
            "token_endpoint_auth_method",
        ):
            assert omitted not in body_data

    def test_minimal_request_model_dump_omits_unset_defaults(self):
        """The minimal request serializes without injected RFC defaults."""
        req = ClientRegistrationRequest(client_name="My App")

        dumped = req.model_dump(mode="json", exclude_none=True, exclude={"timeout"})

        assert dumped == {"client_name": "My App"}

    def test_request_without_client_name_is_valid(self):
        """client_name is optional; an empty request raises no ValidationError."""
        req = ClientRegistrationRequest()

        assert req.client_name is None
        assert req.redirect_uris is None
        assert req.scope is None
        assert req.grant_types is None
        assert req.response_types is None
        assert req.token_endpoint_auth_method is None

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

    def test_parse_registration_http_response_protocol_error(self):
        """A 4xx with an RFC 7591 error body raises OAuthProtocolError."""
        http_response = HttpResponse(
            status=400,
            headers={"Content-Type": "application/json"},
            body=b'{"error": "invalid_client_metadata", "error_description": "bad redirect_uri"}'
        )

        with pytest.raises(OAuthProtocolError) as exc_info:
            parse_client_registration_http_response(http_response)

        assert exc_info.value.error == "invalid_client_metadata"
        assert exc_info.value.error_description == "bad redirect_uri"

    def test_parse_registration_http_response_non_json_error(self):
        """A 4xx with a non-JSON body falls back to OAuthHttpError."""
        http_response = HttpResponse(
            status=400,
            headers={"Content-Type": "text/plain"},
            body=b"Bad Request"
        )

        with pytest.raises(OAuthHttpError, match="HTTP 400"):
            parse_client_registration_http_response(http_response)

    def test_parse_registration_http_response_json_without_error_key(self):
        """A 4xx JSON body without an `error` key falls back to OAuthHttpError."""
        http_response = HttpResponse(
            status=400,
            headers={"Content-Type": "application/json"},
            body=b'{"message": "something went wrong"}'
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

        context = build_http_context(
            endpoint="https://auth.example.com/register",
            transport=mock_transport,
            auth=mock_auth,
            user_agent="TestClient/1.0",
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

        context = build_http_context(
            endpoint="https://auth.example.com/register",
            transport=mock_transport,
            auth=mock_auth,
            user_agent="TestClient/1.0",
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
