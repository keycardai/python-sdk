"""Unit tests for OAuth 2.0 authorization server metadata discovery operations (RFC 8414)."""

from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.oauth.exceptions import OAuthHttpError, OAuthProtocolError
from keycardai.oauth.http._context import build_http_context
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.operations._discovery import (
    build_discovery_http_request,
    discover_server_metadata,
    discover_server_metadata_async,
    parse_discovery_http_response,
)
from keycardai.oauth.types.models import (
    AuthorizationServerMetadata,
    ServerMetadataRequest,
)


class TestDiscoveryOperations:
    """Test discovery operation functions directly."""

    def test_build_discovery_http_request_basic(self):
        """Test building discovery HTTP request."""
        req = ServerMetadataRequest(base_url="https://auth.example.com")

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {}

        context = build_http_context(
            endpoint="https://auth.example.com",
            transport=Mock(),
            auth=mock_auth,
            user_agent="TestClient/1.0"
        )

        http_req = build_discovery_http_request(req, context)

        assert http_req.method == "GET"
        assert http_req.url == "https://auth.example.com/.well-known/oauth-authorization-server"
        assert http_req.headers["Accept"] == "application/json"
        assert http_req.headers["User-Agent"] == "TestClient/1.0"
        assert http_req.body is None

    def test_build_discovery_http_request_trailing_slash(self):
        """Test URL construction with trailing slash."""
        req = ServerMetadataRequest(base_url="https://auth.example.com/")

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {}

        context = build_http_context(
            endpoint="https://auth.example.com/",
            transport=Mock(),
            auth=mock_auth,
            user_agent="TestClient/1.0"
        )

        http_req = build_discovery_http_request(req, context)

        assert http_req.url == "https://auth.example.com/.well-known/oauth-authorization-server"

    def test_parse_discovery_http_response_http_error(self):
        """Test parsing HTTP error response."""
        http_response = HttpResponse(
            status=404,
            headers={},
            body=b"Not Found"
        )

        with pytest.raises(OAuthHttpError, match="HTTP 404"):
            parse_discovery_http_response(http_response)

    def test_parse_discovery_http_response_invalid_json(self):
        """Test parsing invalid JSON response."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b"invalid json {"
        )

        with pytest.raises(OAuthProtocolError, match="Invalid JSON"):
            parse_discovery_http_response(http_response)

    def test_parse_discovery_http_response_missing_issuer(self):
        """Missing issuer raises the typed protocol error, not a bare ValueError."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"token_endpoint": "https://auth.example.com/token"}'
        )

        with pytest.raises(OAuthProtocolError, match="issuer"):
            parse_discovery_http_response(http_response)

    def test_parse_discovery_http_response_issuer_mismatch(self):
        """Issuer in the document must match the requested issuer (RFC 8414 Section 3.3)."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"issuer": "https://evil.example.com"}'
        )

        with pytest.raises(OAuthProtocolError, match="issuer"):
            parse_discovery_http_response(
                http_response, expected_issuer="https://auth.example.com"
            )

    def test_parse_discovery_http_response_issuer_match_ignores_trailing_slash(self):
        """A trailing slash difference is not a mismatch."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"issuer": "https://auth.example.com"}'
        )

        result = parse_discovery_http_response(
            http_response, expected_issuer="https://auth.example.com/"
        )

        assert result.issuer == "https://auth.example.com"

    def test_discover_server_metadata_rejects_issuer_mismatch(self):
        """The discovery operation validates the issuer against the request."""
        mock_transport = Mock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"issuer": "https://evil.example.com"}'
        )

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {}

        context = build_http_context(
            endpoint="https://auth.example.com/.well-known/oauth-authorization-server",
            transport=mock_transport,
            auth=mock_auth,
            user_agent="TestClient/1.0",
            timeout=30.0
        )

        req = ServerMetadataRequest(base_url="https://auth.example.com")

        with pytest.raises(OAuthProtocolError, match="issuer"):
            discover_server_metadata(req, context)

    def test_discover_server_metadata_sync(self):
        """Test synchronous discovery operation."""
        mock_transport = Mock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"issuer": "https://auth.example.com", "authorization_endpoint": "https://auth.example.com/authorize"}'
        )

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {"Authorization": "Bearer token"}

        context = build_http_context(
            endpoint="https://auth.example.com/.well-known/oauth-authorization-server",
            transport=mock_transport,
            auth=mock_auth,
            user_agent="TestClient/1.0",
            timeout=30.0
        )

        req = ServerMetadataRequest(base_url="https://auth.example.com")

        result = discover_server_metadata(req, context)

        assert isinstance(result, AuthorizationServerMetadata)
        assert result.issuer == "https://auth.example.com"
        assert result.authorization_endpoint == "https://auth.example.com/authorize"

    @pytest.mark.asyncio
    async def test_discover_server_metadata_async(self):
        """Test asynchronous discovery operation."""
        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"issuer": "https://auth.example.com", "authorization_endpoint": "https://auth.example.com/authorize"}'
        )

        mock_auth = Mock()
        mock_auth.apply_headers.return_value = {"Authorization": "Bearer token"}

        context = build_http_context(
            endpoint="https://auth.example.com/.well-known/oauth-authorization-server",
            transport=mock_transport,
            auth=mock_auth,
            user_agent="TestClient/1.0",
            timeout=30.0
        )

        req = ServerMetadataRequest(base_url="https://auth.example.com")

        result = await discover_server_metadata_async(req, context)

        assert isinstance(result, AuthorizationServerMetadata)
        assert result.issuer == "https://auth.example.com"
        assert result.authorization_endpoint == "https://auth.example.com/authorize"
