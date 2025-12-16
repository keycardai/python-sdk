"""Tests for A2A client with OAuth discovery."""

import pytest

# Skip this entire test module - OAuth PKCE feature not yet implemented
pytestmark = pytest.mark.skip(reason="A2AServiceClientWithOAuth not yet implemented")

from unittest.mock import AsyncMock, Mock, patch

import httpx

from keycardai.agents import AgentServiceConfig


@pytest.fixture
def service_config():
    """Create test service configuration."""
    return AgentServiceConfig(
        service_name="Test Client Service",
        client_id="client_service",
        client_secret="client_secret",
        identity_url="https://client.example.com",
        zone_id="test_zone_123",
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


class TestOAuthDiscovery:
    """Test OAuth discovery utilities."""

    def test_extract_metadata_url(self, mock_www_authenticate_header):
        """Test extracting resource_metadata URL from WWW-Authenticate header."""
        url = OAuthDiscovery.extract_metadata_url(mock_www_authenticate_header)
        assert url == "https://protected-service.example.com/.well-known/oauth-protected-resource/invoke"

    def test_extract_metadata_url_missing(self):
        """Test handling missing resource_metadata in header."""
        header = 'Bearer error="invalid_token"'
        url = OAuthDiscovery.extract_metadata_url(header)
        assert url is None

    @pytest.mark.asyncio
    async def test_fetch_resource_metadata(self, mock_resource_metadata):
        """Test fetching OAuth protected resource metadata."""
        metadata_url = "https://protected-service.example.com/.well-known/oauth-protected-resource"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = mock_resource_metadata
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            metadata = await OAuthDiscovery.fetch_resource_metadata(metadata_url)

            assert metadata == mock_resource_metadata
            mock_client.get.assert_called_once_with(metadata_url)

    @pytest.mark.asyncio
    async def test_fetch_authorization_server_metadata(self, mock_auth_server_metadata):
        """Test fetching authorization server metadata."""
        auth_server_url = "https://test_zone_123.keycard.cloud"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = mock_auth_server_metadata
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            metadata = await OAuthDiscovery.fetch_authorization_server_metadata(auth_server_url)

            assert metadata == mock_auth_server_metadata
            expected_url = f"{auth_server_url}/.well-known/oauth-authorization-server"
            mock_client.get.assert_called_once_with(expected_url)


class TestA2AServiceClientWithOAuth:
    """Test enhanced A2A client with OAuth discovery."""

    @pytest.mark.asyncio
    async def test_discover_service(self, service_config):
        """Test service discovery."""
        client = A2AServiceClientWithOAuth(service_config)

        mock_agent_card = {
            "name": "Protected Service",
            "endpoints": {"invoke": "https://protected-service.example.com/invoke"},
            "auth": {"type": "oauth2"},
        }

        with patch.object(client.http_client, "get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = mock_agent_card
            mock_get.return_value = mock_response

            card = await client.discover_service("https://protected-service.example.com")

            assert card == mock_agent_card
            mock_get.assert_called_once_with("https://protected-service.example.com/.well-known/agent-card.json")

        await client.close()

    @pytest.mark.asyncio
    async def test_get_token_with_oauth_discovery(
        self,
        service_config,
        mock_www_authenticate_header,
        mock_resource_metadata,
        mock_auth_server_metadata,
    ):
        """Test obtaining token using OAuth discovery with client credentials."""
        client = A2AServiceClientWithOAuth(service_config)

        # Mock httpx client for token request
        mock_token_response = Mock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {
            "access_token": "new_token_123",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        # Mock OAuth discovery calls and httpx client for token request
        with patch.object(OAuthDiscovery, "fetch_resource_metadata") as mock_fetch_resource, \
             patch.object(OAuthDiscovery, "fetch_authorization_server_metadata") as mock_fetch_auth, \
             patch("httpx.AsyncClient") as mock_httpx_client:

            mock_fetch_resource.return_value = mock_resource_metadata
            mock_fetch_auth.return_value = mock_auth_server_metadata
            
            # Mock the httpx AsyncClient context manager
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_token_response)
            mock_httpx_client.return_value.__aenter__.return_value = mock_client_instance

            token = await client.get_token_with_oauth_discovery(
                "https://protected-service.example.com",
                mock_www_authenticate_header,
            )

            assert token == "new_token_123"
            assert client._token_cache["https://protected-service.example.com"] == "new_token_123"

            # Verify OAuth discovery flow
            mock_fetch_resource.assert_called_once()
            mock_fetch_auth.assert_called_once_with("https://test_zone_123.keycard.cloud")
            
            # Verify client credentials token request was made
            mock_client_instance.post.assert_called_once()
            call_args = mock_client_instance.post.call_args
            assert call_args.args[0] == "https://test_zone_123.keycard.cloud/oauth/token"
            assert call_args.kwargs["data"]["grant_type"] == "client_credentials"
            assert "resource" in call_args.kwargs["data"]

        await client.close()

    @pytest.mark.asyncio
    async def test_invoke_service_with_automatic_oauth(
        self,
        service_config,
        mock_www_authenticate_header,
        mock_resource_metadata,
        mock_auth_server_metadata,
    ):
        """Test automatic OAuth handling when service returns 401."""
        client = A2AServiceClientWithOAuth(service_config)

        # Mock the initial 401 response
        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.headers = {"WWW-Authenticate": mock_www_authenticate_header}
        mock_401_response.text = "Unauthorized"

        # Mock the successful response after OAuth
        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {
            "result": "Task completed successfully",
            "delegation_chain": ["client_service"],
        }

        # Mock token response for client credentials
        mock_token_response = Mock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {
            "access_token": "auto_obtained_token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        # Mock OAuth discovery and client credentials grant
        with patch.object(client.http_client, "post") as mock_post, \
             patch.object(OAuthDiscovery, "fetch_resource_metadata") as mock_fetch_resource, \
             patch.object(OAuthDiscovery, "fetch_authorization_server_metadata") as mock_fetch_auth, \
             patch("httpx.AsyncClient") as mock_httpx_client:

            # First call returns 401, second call succeeds
            mock_post.side_effect = [
                httpx.HTTPStatusError("Unauthorized", request=Mock(), response=mock_401_response),
                mock_success_response,
            ]

            mock_fetch_resource.return_value = mock_resource_metadata
            mock_fetch_auth.return_value = mock_auth_server_metadata
            
            # Mock the httpx AsyncClient for token request
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_token_response)
            mock_httpx_client.return_value.__aenter__.return_value = mock_client_instance

            # Call service - OAuth should be handled automatically
            result = await client.invoke_service(
                "https://protected-service.example.com",
                {"task": "Process data"},
            )

            assert result["result"] == "Task completed successfully"
            assert mock_post.call_count == 2  # Initial attempt + retry after OAuth

            # Verify first call had no auth header
            first_call_headers = mock_post.call_args_list[0].kwargs.get("headers", {})
            assert "Authorization" not in first_call_headers

            # Verify second call had auth header
            second_call_headers = mock_post.call_args_list[1].kwargs["headers"]
            assert second_call_headers["Authorization"] == "Bearer auto_obtained_token"

        await client.close()

    @pytest.mark.asyncio
    async def test_invoke_service_with_cached_token(self, service_config):
        """Test that cached tokens are reused."""
        client = A2AServiceClientWithOAuth(service_config)

        # Pre-populate token cache
        client._token_cache["https://protected-service.example.com"] = "cached_token_123"

        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {
            "result": "Task completed with cached token",
            "delegation_chain": ["client_service"],
        }

        with patch.object(client.http_client, "post") as mock_post:
            mock_post.return_value = mock_success_response

            result = await client.invoke_service(
                "https://protected-service.example.com",
                {"task": "Process data"},
            )

            assert result["result"] == "Task completed with cached token"
            assert mock_post.call_count == 1

            # Verify cached token was used
            call_headers = mock_post.call_args.kwargs["headers"]
            assert call_headers["Authorization"] == "Bearer cached_token_123"

        await client.close()

    @pytest.mark.asyncio
    async def test_invoke_service_without_auto_authenticate(self, service_config):
        """Test that auto_authenticate=False prevents OAuth discovery."""
        client = A2AServiceClientWithOAuth(service_config)

        mock_401_response = Mock()
        mock_401_response.status_code = 401
        mock_401_response.headers = {"WWW-Authenticate": "Bearer error=\"invalid_token\""}
        mock_401_response.text = "Unauthorized"

        with patch.object(client.http_client, "post") as mock_post:
            mock_post.side_effect = httpx.HTTPStatusError(
                "Unauthorized",
                request=Mock(),
                response=mock_401_response
            )

            # Should raise without attempting OAuth
            with pytest.raises(httpx.HTTPStatusError):
                await client.invoke_service(
                    "https://protected-service.example.com",
                    {"task": "Process data"},
                    auto_authenticate=False,
                )

            assert mock_post.call_count == 1  # Only one attempt, no retry

        await client.close()

