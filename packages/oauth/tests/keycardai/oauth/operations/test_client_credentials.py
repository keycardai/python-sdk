"""Unit tests for OAuth 2.0 Client Credentials Grant operations (RFC 6749 Section 4.4)."""

from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import pytest

from keycardai.oauth.exceptions import OAuthHttpError, OAuthProtocolError
from keycardai.oauth.http._context import HTTPContext
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.http.auth import BasicAuth, NoneAuth
from keycardai.oauth.operations._client_credentials import (
    build_client_credentials_http_request,
    client_credentials_grant,
    client_credentials_grant_async,
    parse_client_credentials_http_response,
)
from keycardai.oauth.types.models import ClientCredentialsRequest, TokenResponse


class TestClientCredentialsOperations:
    """Test client credentials operation functions directly."""

    def test_build_client_credentials_http_request_minimal(self):
        """Test building minimal client credentials HTTP request."""
        req = ClientCredentialsRequest()
        auth = BasicAuth("client", "secret")

        http_req = build_client_credentials_http_request(req, HTTPContext(endpoint="https://auth.example.com/token", transport=Mock(), auth=auth))

        assert http_req.method == "POST"
        assert http_req.url == "https://auth.example.com/token"
        assert http_req.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert http_req.headers["Authorization"] == "Basic Y2xpZW50OnNlY3JldA=="

        form = parse_qs(http_req.body.decode("utf-8"))
        assert form == {"grant_type": ["client_credentials"]}

    def test_build_client_credentials_http_request_full(self):
        """Test building full client credentials HTTP request."""
        req = ClientCredentialsRequest(
            resource="https://api.example.com",
            scope="read write",
            client_assertion="assertion_jwt",
            client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        )
        auth = NoneAuth()

        http_req = build_client_credentials_http_request(req, HTTPContext(endpoint="https://auth.example.com/token", transport=Mock(), auth=auth))

        form = parse_qs(http_req.body.decode("utf-8"))
        assert form == {
            "grant_type": ["client_credentials"],
            "resource": ["https://api.example.com"],
            "scope": ["read write"],
            "client_assertion": ["assertion_jwt"],
            "client_assertion_type": ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"],
        }

    def test_parse_client_credentials_http_response_success(self):
        """Test parsing successful client credentials response."""
        response_body = b'''{
            "access_token": "issued_access_token_123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read write"
        }'''

        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=response_body
        )

        result = parse_client_credentials_http_response(http_response)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "issued_access_token_123"
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600
        assert " ".join(result.scope) == "read write"  # scope is parsed as a list

    def test_parse_client_credentials_http_response_token_type_default(self):
        """Test token_type defaults to Bearer when omitted."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token": "issued_access_token_123"}'
        )

        result = parse_client_credentials_http_response(http_response)

        assert result.token_type == "Bearer"

    def test_parse_client_credentials_http_response_oauth_error(self):
        """Test parsing HTTP error response with structured OAuth error body."""
        http_response = HttpResponse(
            status=400,
            headers={"Content-Type": "application/json"},
            body=b'{"error": "invalid_scope", "error_description": "Unknown scope"}'
        )

        with pytest.raises(OAuthProtocolError, match="invalid_scope") as exc_info:
            parse_client_credentials_http_response(http_response)

        assert exc_info.value.error == "invalid_scope"
        assert exc_info.value.error_description == "Unknown scope"

    def test_parse_client_credentials_http_response_http_error_non_json(self):
        """Test parsing HTTP error response with non-JSON body."""
        http_response = HttpResponse(
            status=500,
            headers={"Content-Type": "text/plain"},
            body=b"Internal Server Error"
        )

        with pytest.raises(OAuthHttpError, match="HTTP 500"):
            parse_client_credentials_http_response(http_response)

    def test_parse_client_credentials_http_response_invalid_json(self):
        """Test parsing invalid JSON response."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b"invalid json {"
        )

        with pytest.raises(OAuthProtocolError, match="Invalid JSON"):
            parse_client_credentials_http_response(http_response)

    def test_parse_client_credentials_http_response_missing_access_token(self):
        """Test parsing response missing the required access_token field."""
        http_response = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"token_type": "Bearer"}'
        )

        with pytest.raises(OAuthProtocolError, match="access_token") as exc_info:
            parse_client_credentials_http_response(http_response)

        assert exc_info.value.error == "invalid_response"

    def test_client_credentials_grant_sync(self):
        """Test synchronous client credentials grant."""
        mock_transport = Mock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token": "sync_issued_token", "token_type": "Bearer", "expires_in": 3600}'
        )

        context = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=mock_transport,
            auth=BasicAuth("client", "secret"),
            timeout=30.0
        )

        req = ClientCredentialsRequest(scope="read")

        result = client_credentials_grant(req, context)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "sync_issued_token"
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600

        sent_request = mock_transport.request_raw.call_args[0][0]
        assert sent_request.headers["Authorization"] == "Basic Y2xpZW50OnNlY3JldA=="
        form = parse_qs(sent_request.body.decode("utf-8"))
        assert form == {"grant_type": ["client_credentials"], "scope": ["read"]}

    @pytest.mark.asyncio
    async def test_client_credentials_grant_async(self):
        """Test asynchronous client credentials grant."""
        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"access_token": "async_issued_token", "token_type": "Bearer", "expires_in": 7200}'
        )

        context = HTTPContext(
            endpoint="https://auth.example.com/token",
            transport=mock_transport,
            auth=BasicAuth("client", "secret"),
            timeout=30.0
        )

        req = ClientCredentialsRequest(resource="https://api.example.com")

        result = await client_credentials_grant_async(req, context)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "async_issued_token"
        assert result.token_type == "Bearer"
        assert result.expires_in == 7200

        sent_request = mock_transport.request_raw.call_args[0][0]
        assert sent_request.headers["Authorization"] == "Basic Y2xpZW50OnNlY3JldA=="
        form = parse_qs(sent_request.body.decode("utf-8"))
        assert form == {
            "grant_type": ["client_credentials"],
            "resource": ["https://api.example.com"],
        }
