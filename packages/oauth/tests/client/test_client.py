"""Tests for the unified OAuth client implementation (M1.2)."""

from unittest.mock import AsyncMock, patch

import pytest

from keycardai.oauth import AsyncClient, Client
from keycardai.oauth.client.auth import ClientCredentialsAuth
from keycardai.oauth.client.http import AsyncHTTPClient
from keycardai.oauth.types.responses import (
    ClientConfig,
    Endpoints,
    IntrospectionResponse,
)


class TestAsyncClient:
    """Test AsyncClient functionality."""

    def test_init_with_credentials(self):
        """Test AsyncClient initialization with client credentials."""
        client = AsyncClient(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
        )

        assert client.base_url == "https://auth.example.com"
        assert isinstance(client.auth_strategy, ClientCredentialsAuth)
        assert isinstance(client.http_client, AsyncHTTPClient)

    def test_init_with_custom_auth(self):
        """Test AsyncClient initialization with custom auth strategy."""
        custom_auth = ClientCredentialsAuth("custom_id", "custom_secret")
        client = AsyncClient(base_url="https://auth.example.com", auth=custom_auth)

        assert client.auth_strategy is custom_auth

    def test_init_missing_credentials_raises_config_error(self):
        """Test that missing credentials raises TypeError."""
        with pytest.raises(
            TypeError, match="missing 1 required keyword-only argument: 'auth'"
        ):
            AsyncClient("https://auth.example.com")

    def test_endpoint_resolution_defaults(self):
        """Test default endpoint resolution."""
        client = AsyncClient(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
        )

        endpoints = client.endpoints_summary()

        assert (
            endpoints["introspect"]["url"]
            == "https://auth.example.com/oauth2/introspect"
        )
        assert endpoints["token"]["url"] == "https://auth.example.com/oauth2/token"
        assert endpoints["revoke"]["url"] == "https://auth.example.com/oauth2/revoke"
        assert (
            endpoints["authorize"]["url"] == "https://auth.example.com/oauth2/authorize"
        )
        assert endpoints["par"]["url"] == "https://auth.example.com/oauth2/par"

    def test_endpoint_resolution_with_overrides(self):
        """Test endpoint resolution with custom overrides."""
        custom_endpoints = Endpoints(
            introspect="https://custom.introspect.com/validate",
            token="https://custom.token.com/token",
        )

        client = AsyncClient(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
            endpoints=custom_endpoints,
        )

        endpoints = client.endpoints_summary()

        assert (
            endpoints["introspect"]["url"] == "https://custom.introspect.com/validate"
        )
        assert endpoints["token"]["url"] == "https://custom.token.com/token"
        # Should still use defaults for unspecified endpoints
        assert endpoints["revoke"]["url"] == "https://auth.example.com/oauth2/revoke"

    def test_custom_config(self):
        """Test client with custom configuration."""
        config = ClientConfig(
            timeout=60.0, max_retries=5, verify_ssl=False, user_agent="Custom-Agent/1.0"
        )

        client = AsyncClient(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
            config=config,
        )

        assert client.config is config

    @pytest.mark.asyncio
    async def test_introspect_token_success(self):
        """Test successful token introspection."""
        # Mock HTTP client
        mock_http_client = AsyncMock()
        mock_response_data = {
            "active": True,
            "scope": "read write",
            "client_id": "test_client",
            "username": "testuser",
            "exp": 1234567890,
        }
        mock_http_client.request.return_value = mock_response_data

        client = AsyncClient(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
            http_client=mock_http_client,
        )

        response = await client.introspect_token("test_token")

        # Verify HTTP request was made correctly
        mock_http_client.request.assert_called_once()
        call_args = mock_http_client.request.call_args

        assert call_args[1]["method"] == "POST"
        assert call_args[1]["url"] == "https://auth.example.com/oauth2/introspect"
        assert "token" in call_args[1]["data"]
        assert call_args[1]["data"]["token"] == "test_token"

        # Verify response parsing
        assert isinstance(response, IntrospectionResponse)
        assert response.active is True
        assert response.scope == ["read", "write"]  # Normalized from space-delimited
        assert response.client_id == "test_client"
        assert response.username == "testuser"
        assert response.exp == 1234567890

    @pytest.mark.asyncio
    async def test_introspect_token_with_hint(self):
        """Test token introspection with type hint."""
        mock_http_client = AsyncMock()
        mock_http_client.request.return_value = {"active": False}

        client = AsyncClient(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
            http_client=mock_http_client,
        )

        await client.introspect_token("test_token", token_type_hint="access_token")

        # Verify type hint was included
        call_args = mock_http_client.request.call_args
        assert call_args[1]["data"]["token_type_hint"] == "access_token"


class TestClient:
    """Test synchronous Client implementation."""

    def test_init(self):
        """Test Client initialization."""
        client = Client(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
        )

        assert client.base_url == "https://auth.example.com"
        assert isinstance(client.auth_strategy, ClientCredentialsAuth)
        assert hasattr(client, "http_client")  # Should have sync HTTP client

    def test_endpoints_summary(self):
        """Test endpoints_summary method."""
        client = Client(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
        )

        endpoints = client.endpoints_summary()
        assert (
            endpoints["introspect"]["url"]
            == "https://auth.example.com/oauth2/introspect"
        )

    def test_introspect_token_sync(self):
        """Test synchronous introspect_token method."""

        mock_response = IntrospectionResponse(active=True)

        client = Client(
            base_url="https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
        )

        # Mock the sync introspection operation
        with patch(
            "keycardai.oauth.client.client.introspect_token", return_value=mock_response
        ) as mock_introspect:
            result = client.introspect_token("test_token")

            assert result is mock_response
            mock_introspect.assert_called_once_with(
                token="test_token",
                introspection_endpoint="https://auth.example.com/oauth2/introspect",
                auth_strategy=client.auth_strategy,
                http_client=client.http_client,
                token_type_hint=None,
                timeout=None,
            )


class TestClientCredentialsAuth:
    """Test ClientCredentialsAuth authentication strategy."""

    def test_init_basic_method(self):
        """Test initialization with basic auth method."""
        auth = ClientCredentialsAuth("client_id", "client_secret")

        assert auth.client_id == "client_id"
        assert auth.client_secret == "client_secret"
        assert auth.method == "basic"

    def test_init_post_method(self):
        """Test initialization with post auth method."""
        auth = ClientCredentialsAuth("client_id", "client_secret", method="post")

        assert auth.method == "post"

    def test_init_invalid_method(self):
        """Test initialization with invalid method raises ValueError."""
        with pytest.raises(ValueError, match="method must be 'basic' or 'post'"):
            ClientCredentialsAuth("client_id", "client_secret", method="invalid")

    def test_basic_auth_method(self):
        """Test basic auth method returns credentials."""
        auth = ClientCredentialsAuth("client_id", "client_secret", method="basic")

        assert auth.get_basic_auth() == ("client_id", "client_secret")
        assert auth.get_auth_data() == {}

    def test_post_auth_method(self):
        """Test post auth method returns form data."""
        auth = ClientCredentialsAuth("client_id", "client_secret", method="post")

        assert auth.get_basic_auth() is None
        assert auth.get_auth_data() == {
            "client_id": "client_id",
            "client_secret": "client_secret",
        }

    @pytest.mark.asyncio
    async def test_authenticate_headers(self):
        """Test authenticate method with headers."""
        auth = ClientCredentialsAuth("client_id", "client_secret")
        headers = {"Content-Type": "application/json"}

        result = await auth.authenticate(headers)

        # Should return the same headers (basic auth handled by HTTP client)
        assert result is headers


class TestIntrospectionResponse:
    """Test IntrospectionResponse model."""

    def test_from_response_basic(self):
        """Test basic response parsing."""
        data = {"active": True, "scope": "read write", "client_id": "test_client"}

        response = IntrospectionResponse.from_response(data)

        assert response.active is True
        assert response.scope == ["read", "write"]
        assert response.client_id == "test_client"

    def test_from_response_scope_normalization(self):
        """Test scope normalization from string to list."""
        # Test space-delimited string
        data = {"active": True, "scope": "read write admin"}
        response = IntrospectionResponse.from_response(data)
        assert response.scope == ["read", "write", "admin"]

        # Test empty string
        data = {"active": True, "scope": ""}
        response = IntrospectionResponse.from_response(data)
        assert response.scope is None

        # Test already a list
        data = {"active": True, "scope": ["read", "write"]}
        response = IntrospectionResponse.from_response(data)
        assert response.scope == ["read", "write"]

    def test_from_response_audience_normalization(self):
        """Test audience normalization."""
        # Test string to list
        data = {"active": True, "aud": "https://api.example.com"}
        response = IntrospectionResponse.from_response(data)
        assert response.aud == ["https://api.example.com"]

        # Test already a list
        data = {
            "active": True,
            "aud": ["https://api1.example.com", "https://api2.example.com"],
        }
        response = IntrospectionResponse.from_response(data)
        assert response.aud == ["https://api1.example.com", "https://api2.example.com"]

    def test_from_response_with_headers(self):
        """Test response creation with headers."""
        data = {"active": True}
        headers = {"Content-Type": "application/json"}

        response = IntrospectionResponse.from_response(data, headers)

        assert response.headers == headers
        assert response.raw == data
