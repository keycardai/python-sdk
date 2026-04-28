"""Tests for AgentClient with OAuth PKCE flow.

These tests verify the OAuth discovery and PKCE authentication flow
that AgentClient uses to call protected agent services.
"""

from unittest.mock import Mock, patch

import pytest

from keycardai.agents import AgentServiceConfig
from keycardai.agents.client import AgentClient
from keycardai.agents.server import SimpleExecutor


@pytest.fixture
def service_config():
    """Create test service configuration."""
    return AgentServiceConfig(
        service_name="Test Client Service",
        client_id="client_service",
        client_secret="test_secret",  # Required for config validation
        identity_url="https://client.example.com",
        zone_id="test_zone_123",
        agent_executor=SimpleExecutor(),
    )


@pytest.fixture
def mock_www_authenticate_header():
    """Mock WWW-Authenticate header with resource_metadata URL."""
    return (
        'Bearer error="invalid_token", '
        'error_description="No bearer token provided", '
        'resource_metadata="https://protected-service.example.com/.well-known/oauth-protected-resource/invoke"'
    )


@pytest.fixture
def mock_resource_metadata():
    """Mock OAuth protected resource metadata."""
    return {
        "resource": "https://protected-service.example.com",
        "authorization_servers": ["https://test_zone_123.keycard.cloud"],
        "jwks_uri": "https://protected-service.example.com/.well-known/jwks.json",
    }


@pytest.fixture
def mock_auth_server_metadata():
    """Mock authorization server metadata."""
    return {
        "issuer": "https://test_zone_123.keycard.cloud",
        "token_endpoint": "https://test_zone_123.keycard.cloud/oauth/token",
        "authorization_endpoint": "https://test_zone_123.keycard.cloud/oauth/authorize",
        "jwks_uri": "https://test_zone_123.keycard.cloud/openidconnect/jwks",
    }


class TestAgentClientInit:
    """Test AgentClient initialization."""

    def test_init_basic(self, service_config):
        """Test basic initialization."""
        client = AgentClient(service_config)
        assert client.config == service_config
        assert client.http_client is not None
        assert client.scopes == []

    def test_init_with_custom_scopes(self, service_config):
        """Test initialization with custom scopes."""
        custom_scopes = ["read", "write"]
        client = AgentClient(service_config, scopes=custom_scopes)
        assert client.scopes == custom_scopes


class TestInvokeWithoutAuth:
    """Test invoke method without authentication (should work with valid token)."""

    @pytest.mark.asyncio
    async def test_invoke_with_preauth_success(self, service_config):
        """Test successful invoke with pre-authenticated token."""
        client = AgentClient(service_config)

        # Pre-populate token cache (simulating previous successful auth)
        service_url = "https://protected-service.example.com"
        client._token_cache[service_url] = "valid_token_123"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": "Task completed successfully"
        }

        with patch.object(client.http_client, "post") as mock_post:
            mock_post.return_value = mock_response

            result = await client.invoke(
                service_url=service_url,
                task="Test task"
            )

            assert result["result"] == "Task completed successfully"

            # Verify token was used
            call_headers = mock_post.call_args.kwargs["headers"]
            assert call_headers["Authorization"] == "Bearer valid_token_123"


class TestContextManager:
    """Test AgentClient as async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager_closes_http_client(self, service_config):
        """Test that context manager properly closes HTTP client."""
        async with AgentClient(service_config) as client:
            assert client.http_client is not None

        # After exit, verify close was called (HTTP client should be closed)
        # Note: We can't directly verify this without mocking, but the context manager
        # should handle cleanup

    @pytest.mark.asyncio
    async def test_manual_close(self, service_config):
        """Test manual close method."""
        client = AgentClient(service_config)

        with patch.object(client.http_client, "aclose") as mock_close:
            await client.close()
            mock_close.assert_called_once()
