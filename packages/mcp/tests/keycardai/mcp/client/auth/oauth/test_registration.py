"""Unit tests for OAuth client registration service.

This module tests the OAuthClientRegistrationService class, focusing on:
- Registering new OAuth clients
- Caching and reusing client registrations
- Handling redirect URI changes
- Error handling for registration failures
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from keycardai.mcp.client.auth.oauth.registration import OAuthClientRegistrationService
from keycardai.mcp.client.auth.storage_facades import OAuthStorage
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


@pytest.fixture
def oauth_storage():
    """Create OAuth storage for testing."""
    backend = InMemoryBackend()
    base_storage = NamespacedStorage(backend, "client:test:server:slack:connection:oauth")
    return OAuthStorage(base_storage)


@pytest.fixture
def auth_server_metadata():
    """Sample authorization server metadata."""
    return {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "registration_endpoint": "https://auth.example.com/register"
    }


@pytest.fixture
def redirect_uris():
    """Sample redirect URIs."""
    return ["http://localhost:8080/callback"]


def create_registration_service(oauth_storage, client_name="Test Client", client_factory=None):
    """Helper to create OAuthClientRegistrationService with optional client factory."""
    if client_factory is None:
        # Default mock client factory
        mock_client = MagicMock(spec=AsyncClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        def default_client_factory():
            return mock_client

        client_factory = default_client_factory
    return OAuthClientRegistrationService(
        storage=oauth_storage,
        client_name=client_name,
        client_factory=client_factory
    )


# ===== Registration Tests =====


@pytest.mark.asyncio
async def test_register_new_client_success(oauth_storage, auth_server_metadata, redirect_uris):
    """Test successful new client registration."""
    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock registration response
    registration_response = MagicMock()
    registration_response.json.return_value = {
        "client_id": "test_client_123",
        "client_secret": None,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "client_uri": "",
        "logo_uri": "",
        "policy_uri": "",
        "tos_uri": ""
    }
    mock_client.post = AsyncMock(return_value=registration_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthClientRegistrationService(
        storage=oauth_storage,
        client_name="Test Client",
        client_factory=client_factory
    )

    # Register client
    client_info = await service.get_or_register_client(
        auth_server_metadata,
        redirect_uris
    )

    # Verify result
    assert client_info["client_id"] == "test_client_123"
    assert client_info["redirect_uris"] == redirect_uris
    # Empty strings should be normalized to None
    assert client_info["client_uri"] is None
    assert client_info["logo_uri"] is None

    # Verify registration was called
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://auth.example.com/register"

    # Verify client info was cached
    cached = await oauth_storage.get_client_registration()
    assert cached == client_info


@pytest.mark.asyncio
async def test_reuse_existing_client_registration(oauth_storage, auth_server_metadata, redirect_uris):
    """Test that existing client registration is reused."""
    # Pre-cache client registration
    existing_client = {
        "client_id": "existing_client_456",
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"]
    }
    await oauth_storage.save_client_registration(existing_client)

    # Create mock client (should NOT be called)
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.post = AsyncMock()

    # Create service
    def client_factory():
        return mock_client

    service = OAuthClientRegistrationService(
        storage=oauth_storage,
        client_name="Test Client",
        client_factory=client_factory
    )

    # Get or register client
    client_info = await service.get_or_register_client(
        auth_server_metadata,
        redirect_uris
    )

    # Should return cached client
    assert client_info == existing_client

    # HTTP client should NOT have been called
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_reregister_when_redirect_uris_change(oauth_storage, auth_server_metadata):
    """Test that client is re-registered when redirect URIs change."""
    # Pre-cache client registration with old URIs
    old_uris = ["http://localhost:8080/callback"]
    existing_client = {
        "client_id": "existing_client_789",
        "redirect_uris": old_uris,
        "grant_types": ["authorization_code"]
    }
    await oauth_storage.save_client_registration(existing_client)

    # New redirect URIs
    new_uris = ["http://localhost:9000/callback"]

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock new registration response
    registration_response = MagicMock()
    registration_response.json.return_value = {
        "client_id": "new_client_999",
        "redirect_uris": new_uris,
        "grant_types": ["authorization_code", "refresh_token"]
    }
    mock_client.post = AsyncMock(return_value=registration_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthClientRegistrationService(
        storage=oauth_storage,
        client_name="Test Client",
        client_factory=client_factory
    )

    # Get or register client
    client_info = await service.get_or_register_client(
        auth_server_metadata,
        new_uris
    )

    # Should register new client
    assert client_info["client_id"] == "new_client_999"
    assert client_info["redirect_uris"] == new_uris

    # HTTP client should have been called
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_registration_missing_endpoint(oauth_storage, redirect_uris):
    """Test registration fails when registration endpoint is missing."""
    # Auth server metadata without registration endpoint
    bad_metadata = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token"
    }

    # Create service
    service = create_registration_service(oauth_storage)

    # Registration should fail
    with pytest.raises(ValueError, match="does not support dynamic registration"):
        await service.get_or_register_client(bad_metadata, redirect_uris)


@pytest.mark.asyncio
async def test_registration_http_error(oauth_storage, auth_server_metadata, redirect_uris):
    """Test registration handles HTTP errors."""
    # Create mock HTTP client that raises error
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock registration response with error
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("HTTP 400: Invalid request")
    mock_client.post = AsyncMock(return_value=mock_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthClientRegistrationService(
        storage=oauth_storage,
        client_name="Test Client",
        client_factory=client_factory
    )

    # Registration should fail
    with pytest.raises(Exception, match="Invalid request"):
        await service.get_or_register_client(auth_server_metadata, redirect_uris)


@pytest.mark.asyncio
async def test_registration_with_custom_client_name(oauth_storage, auth_server_metadata, redirect_uris):
    """Test registration uses custom client name."""
    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock registration response
    registration_response = MagicMock()
    registration_response.json.return_value = {
        "client_id": "custom_name_client",
        "redirect_uris": redirect_uris
    }
    mock_client.post = AsyncMock(return_value=registration_response)

    # Create service with custom name
    def client_factory():
        return mock_client

    service = OAuthClientRegistrationService(
        storage=oauth_storage,
        client_name="My Custom App Name",
        client_factory=client_factory
    )

    # Register client
    await service.get_or_register_client(auth_server_metadata, redirect_uris)

    # Verify client name was used in registration
    call_args = mock_client.post.call_args
    request_body = call_args[1]["json"]
    assert request_body["client_name"] == "My Custom App Name"


@pytest.mark.asyncio
async def test_registration_normalizes_empty_strings(oauth_storage, auth_server_metadata, redirect_uris):
    """Test that empty string fields are normalized to None."""
    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock registration response with empty strings
    registration_response = MagicMock()
    registration_response.json.return_value = {
        "client_id": "test_client",
        "redirect_uris": redirect_uris,
        "client_uri": "",
        "logo_uri": "",
        "policy_uri": "",
        "tos_uri": "",
        "other_field": "not_empty"
    }
    mock_client.post = AsyncMock(return_value=registration_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthClientRegistrationService(
        storage=oauth_storage,
        client_name="Test Client",
        client_factory=client_factory
    )

    # Register client
    client_info = await service.get_or_register_client(
        auth_server_metadata,
        redirect_uris
    )

    # Empty strings should be normalized to None
    assert client_info["client_uri"] is None
    assert client_info["logo_uri"] is None
    assert client_info["policy_uri"] is None
    assert client_info["tos_uri"] is None
    # Non-empty fields should be unchanged
    assert client_info["other_field"] == "not_empty"

