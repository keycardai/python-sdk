"""Unit tests for OAuth discovery service.

This module tests the OAuthDiscoveryService class, focusing on:
- Discovering protected resource metadata
- Discovering authorization server metadata
- Caching authorization server metadata
- Error handling for discovery failures
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, Response

from keycardai.mcp.client.auth.oauth.discovery import OAuthDiscoveryService
from keycardai.mcp.client.auth.storage_facades import OAuthStorage
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


@pytest.fixture
def oauth_storage():
    """Create OAuth storage for testing."""
    backend = InMemoryBackend()
    base_storage = NamespacedStorage(backend, "client:test:server:slack:connection:oauth")
    return OAuthStorage(base_storage)


@pytest.fixture
def mock_http_client():
    """Create a mock HTTP client."""
    return MagicMock(spec=AsyncClient)


def create_discovery_service(oauth_storage, client_factory=None):
    """Helper to create OAuthDiscoveryService with optional client factory."""
    if client_factory is None:
        # Default mock client factory
        mock_client = MagicMock(spec=AsyncClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        def default_client_factory():
            return mock_client

        client_factory = default_client_factory
    return OAuthDiscoveryService(storage=oauth_storage, client_factory=client_factory)


# ===== Resource Discovery Tests =====


@pytest.mark.asyncio
async def test_discover_resource_success(oauth_storage):
    """Test successful resource discovery."""
    # Create mock challenge response
    challenge_response = MagicMock(spec=Response)
    challenge_response.url.scheme = "https"
    challenge_response.url.netloc.decode.return_value = "api.example.com"

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock successful discovery response
    discovery_response = MagicMock()
    discovery_response.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "resource": "https://api.example.com"
    }
    mock_client.get = AsyncMock(return_value=discovery_response)

    # Create service with mock client
    def client_factory():
        return mock_client

    service = OAuthDiscoveryService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Discover resource
    metadata = await service.discover_resource(challenge_response)

    # Verify result
    assert metadata["authorization_servers"] == ["https://auth.example.com"]
    assert metadata["resource"] == "https://api.example.com"

    # Verify correct URL was called
    mock_client.get.assert_called_once_with(
        "https://api.example.com/.well-known/oauth-protected-resource/mcp"
    )


@pytest.mark.asyncio
async def test_discover_resource_missing_auth_servers(oauth_storage):
    """Test resource discovery fails when authorization_servers is missing."""
    # Create mock challenge response
    challenge_response = MagicMock(spec=Response)
    challenge_response.url.scheme = "https"
    challenge_response.url.netloc.decode.return_value = "api.example.com"

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock discovery response without authorization_servers
    discovery_response = MagicMock()
    discovery_response.json.return_value = {"resource": "https://api.example.com"}
    mock_client.get = AsyncMock(return_value=discovery_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthDiscoveryService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Discovery should fail
    with pytest.raises(ValueError, match="No authorization servers"):
        await service.discover_resource(challenge_response)


# ===== Authorization Server Discovery Tests =====


@pytest.mark.asyncio
async def test_discover_auth_server_success(oauth_storage):
    """Test successful authorization server discovery."""
    resource_metadata = {
        "authorization_servers": ["https://auth.example.com"]
    }

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock AS metadata response
    as_response = MagicMock()
    as_response.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "registration_endpoint": "https://auth.example.com/register"
    }
    mock_client.get = AsyncMock(return_value=as_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthDiscoveryService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Discover auth server
    metadata = await service.discover_auth_server(resource_metadata)

    # Verify result
    assert metadata["authorization_endpoint"] == "https://auth.example.com/authorize"
    assert metadata["token_endpoint"] == "https://auth.example.com/token"
    assert metadata["registration_endpoint"] == "https://auth.example.com/register"

    # Verify metadata was cached
    cached = await oauth_storage.get_auth_server_metadata()
    assert cached == metadata


@pytest.mark.asyncio
async def test_discover_auth_server_uses_cache(oauth_storage):
    """Test that cached metadata is reused."""
    # Pre-cache metadata
    cached_metadata = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "registration_endpoint": "https://auth.example.com/register"
    }
    await oauth_storage.save_auth_server_metadata(cached_metadata)

    # Create mock client (should NOT be called)
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.get = AsyncMock()

    # Create service
    def client_factory():
        return mock_client

    service = OAuthDiscoveryService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Discover auth server
    resource_metadata = {"authorization_servers": ["https://auth.example.com"]}
    metadata = await service.discover_auth_server(resource_metadata)

    # Should return cached metadata
    assert metadata == cached_metadata

    # HTTP client should NOT have been called
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_discover_auth_server_missing_registration_endpoint(oauth_storage):
    """Test auth server discovery fails when registration_endpoint is missing."""
    resource_metadata = {
        "authorization_servers": ["https://auth.example.com"]
    }

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock AS metadata response without registration_endpoint
    as_response = MagicMock()
    as_response.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token"
    }
    mock_client.get = AsyncMock(return_value=as_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthDiscoveryService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Discovery should fail
    with pytest.raises(ValueError, match="Failed to discover any authorization server"):
        await service.discover_auth_server(resource_metadata)


@pytest.mark.asyncio
async def test_discover_auth_server_tries_multiple_servers(oauth_storage):
    """Test that discovery tries multiple auth servers on failure."""
    resource_metadata = {
        "authorization_servers": [
            "https://auth1.example.com",  # Will fail
            "https://auth2.example.com"   # Will succeed
        ]
    }

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    call_count = 0

    async def mock_get(url):
        nonlocal call_count
        call_count += 1
        if "auth1" in url:
            # First server fails
            raise Exception("Connection failed")
        else:
            # Second server succeeds
            response = MagicMock()
            response.json.return_value = {
                "authorization_endpoint": "https://auth2.example.com/authorize",
                "token_endpoint": "https://auth2.example.com/token",
                "registration_endpoint": "https://auth2.example.com/register"
            }
            return response

    mock_client.get = mock_get

    # Create service
    def client_factory():
        return mock_client

    service = OAuthDiscoveryService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Discover auth server
    metadata = await service.discover_auth_server(resource_metadata)

    # Should succeed with second server
    assert metadata["authorization_endpoint"] == "https://auth2.example.com/authorize"
    assert call_count == 2  # Tried both servers


@pytest.mark.asyncio
async def test_discover_auth_server_no_servers(oauth_storage):
    """Test auth server discovery fails when no servers provided."""
    resource_metadata = {"authorization_servers": []}

    # Create service
    service = create_discovery_service(oauth_storage)

    # Discovery should fail
    with pytest.raises(ValueError, match="No authorization servers"):
        await service.discover_auth_server(resource_metadata)


@pytest.mark.asyncio
async def test_discover_auth_server_all_servers_fail(oauth_storage):
    """Test auth server discovery fails when all servers fail."""
    resource_metadata = {
        "authorization_servers": [
            "https://auth1.example.com",
            "https://auth2.example.com"
        ]
    }

    # Create mock HTTP client that always fails
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=Exception("Connection failed"))

    # Create service
    def client_factory():
        return mock_client

    service = OAuthDiscoveryService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Discovery should fail
    with pytest.raises(ValueError, match="Failed to discover any authorization server"):
        await service.discover_auth_server(resource_metadata)

