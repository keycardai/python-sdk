"""Unit tests for OAuth token exchange service.

This module tests the OAuthTokenExchangeService class, focusing on:
- Exchanging authorization codes for tokens
- Storing tokens after successful exchange
- Cleaning up PKCE state after exchange
- Error handling for missing PKCE state
- Error handling for token exchange failures
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from keycardai.mcp.client.auth.oauth.exchange import OAuthTokenExchangeService
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
def client_info():
    """Sample client registration info."""
    return {
        "client_id": "test_client_123",
        "redirect_uris": ["http://localhost:8080/callback"],
        "grant_types": ["authorization_code", "refresh_token"]
    }


@pytest_asyncio.fixture
async def stored_pkce_state(oauth_storage):
    """Pre-store PKCE state for testing."""
    state = "test_state_abc123"
    pkce_data = {
        "code_verifier": "test_verifier_123",
        "code_challenge": "test_challenge_456",
        "redirect_uri": "http://localhost:8080/callback",
        "resource_url": "https://api.example.com/resource"
    }
    await oauth_storage.save_pkce_state(
        state=state,
        pkce_data=pkce_data,
        ttl=timedelta(minutes=10)
    )
    return state, pkce_data


# ===== Token Exchange Tests =====


@pytest.mark.asyncio
async def test_exchange_code_for_tokens_success(
    oauth_storage,
    auth_server_metadata,
    client_info,
    stored_pkce_state
):
    """Test successful token exchange."""
    state, pkce_data = stored_pkce_state

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Mock token response
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "access_token_xyz",
        "refresh_token": "refresh_token_abc",
        "expires_in": 3600,
        "token_type": "Bearer"
    }
    mock_client.post = AsyncMock(return_value=token_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthTokenExchangeService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Exchange code for tokens
    tokens = await service.exchange_code_for_tokens(
        code="auth_code_123",
        state=state,
        auth_server_metadata=auth_server_metadata,
        client_info=client_info
    )

    # Verify tokens returned
    assert tokens["access_token"] == "access_token_xyz"
    assert tokens["refresh_token"] == "refresh_token_abc"
    assert tokens["expires_in"] == 3600
    assert tokens["token_type"] == "Bearer"

    # Verify tokens were stored
    stored_tokens = await oauth_storage.get_tokens()
    assert stored_tokens == tokens

    # Verify PKCE state was cleaned up
    remaining_pkce = await oauth_storage.get_pkce_state(state)
    assert remaining_pkce is None

    # Verify token endpoint was called correctly
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == auth_server_metadata["token_endpoint"]
    assert call_args[1]["data"]["grant_type"] == "authorization_code"
    assert call_args[1]["data"]["code"] == "auth_code_123"
    assert call_args[1]["data"]["code_verifier"] == pkce_data["code_verifier"]
    assert call_args[1]["data"]["client_id"] == client_info["client_id"]
    assert call_args[1]["data"]["redirect_uri"] == pkce_data["redirect_uri"]


@pytest.mark.asyncio
async def test_exchange_includes_resource_url(
    oauth_storage,
    auth_server_metadata,
    client_info,
    stored_pkce_state
):
    """Test that resource URL is included in token request."""
    state, pkce_data = stored_pkce_state

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "token_123",
        "token_type": "Bearer"
    }
    mock_client.post = AsyncMock(return_value=token_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthTokenExchangeService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Exchange code
    await service.exchange_code_for_tokens(
        code="auth_code_123",
        state=state,
        auth_server_metadata=auth_server_metadata,
        client_info=client_info
    )

    # Verify resource URL was included
    call_args = mock_client.post.call_args
    assert call_args[1]["data"]["resource"] == pkce_data["resource_url"]


@pytest.mark.asyncio
async def test_exchange_without_resource_url(oauth_storage, auth_server_metadata, client_info):
    """Test token exchange when PKCE state has no resource URL."""
    state = "test_state_no_resource"
    pkce_data = {
        "code_verifier": "test_verifier",
        "code_challenge": "test_challenge",
        "redirect_uri": "http://localhost:8080/callback"
        # No resource_url
    }
    await oauth_storage.save_pkce_state(
        state=state,
        pkce_data=pkce_data,
        ttl=timedelta(minutes=10)
    )

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "token_123",
        "token_type": "Bearer"
    }
    mock_client.post = AsyncMock(return_value=token_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthTokenExchangeService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Exchange code
    await service.exchange_code_for_tokens(
        code="auth_code_123",
        state=state,
        auth_server_metadata=auth_server_metadata,
        client_info=client_info
    )

    # Verify resource was NOT included
    call_args = mock_client.post.call_args
    assert "resource" not in call_args[1]["data"]


# ===== Error Handling Tests =====


@pytest.mark.asyncio
async def test_exchange_missing_pkce_state(oauth_storage, auth_server_metadata, client_info):
    """Test error when PKCE state not found."""
    service = OAuthTokenExchangeService(storage=oauth_storage)

    # Try to exchange without stored PKCE state
    with pytest.raises(ValueError, match="No PKCE state found"):
        await service.exchange_code_for_tokens(
            code="auth_code_123",
            state="nonexistent_state",
            auth_server_metadata=auth_server_metadata,
            client_info=client_info
        )


@pytest.mark.asyncio
async def test_exchange_missing_token_endpoint(oauth_storage, client_info, stored_pkce_state):
    """Test error when token endpoint is missing."""
    state, _ = stored_pkce_state

    bad_metadata = {
        "authorization_endpoint": "https://auth.example.com/authorize"
        # No token_endpoint
    }

    service = OAuthTokenExchangeService(storage=oauth_storage)

    with pytest.raises(ValueError, match="Missing token_endpoint"):
        await service.exchange_code_for_tokens(
            code="auth_code_123",
            state=state,
            auth_server_metadata=bad_metadata,
            client_info=client_info
        )


@pytest.mark.asyncio
async def test_exchange_missing_client_id(oauth_storage, auth_server_metadata, stored_pkce_state):
    """Test error when client_id is missing."""
    state, _ = stored_pkce_state

    bad_client_info = {
        "redirect_uris": ["http://localhost:8080/callback"]
        # No client_id
    }

    service = OAuthTokenExchangeService(storage=oauth_storage)

    with pytest.raises(ValueError, match="Missing client_id"):
        await service.exchange_code_for_tokens(
            code="auth_code_123",
            state=state,
            auth_server_metadata=auth_server_metadata,
            client_info=bad_client_info
        )


@pytest.mark.asyncio
async def test_exchange_http_error(
    oauth_storage,
    auth_server_metadata,
    client_info,
    stored_pkce_state
):
    """Test error handling when token endpoint returns error."""
    state, _ = stored_pkce_state

    # Create mock HTTP client that returns error
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    error_response = MagicMock()
    error_response.status_code = 400
    error_response.text = "invalid_grant: Authorization code expired"
    mock_client.post = AsyncMock(return_value=error_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthTokenExchangeService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Exchange should fail
    with pytest.raises(ValueError, match="Token exchange failed with status 400"):
        await service.exchange_code_for_tokens(
            code="expired_code",
            state=state,
            auth_server_metadata=auth_server_metadata,
            client_info=client_info
        )

    # PKCE state should still exist (not cleaned up on error)
    remaining_pkce = await oauth_storage.get_pkce_state(state)
    assert remaining_pkce is not None


@pytest.mark.asyncio
async def test_exchange_uses_correct_content_type(
    oauth_storage,
    auth_server_metadata,
    client_info,
    stored_pkce_state
):
    """Test that token request uses correct Content-Type header."""
    state, _ = stored_pkce_state

    # Create mock HTTP client
    mock_client = MagicMock(spec=AsyncClient)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "token_123",
        "token_type": "Bearer"
    }
    mock_client.post = AsyncMock(return_value=token_response)

    # Create service
    def client_factory():
        return mock_client

    service = OAuthTokenExchangeService(
        storage=oauth_storage,
        client_factory=client_factory
    )

    # Exchange code
    await service.exchange_code_for_tokens(
        code="auth_code_123",
        state=state,
        auth_server_metadata=auth_server_metadata,
        client_info=client_info
    )

    # Verify Content-Type header
    call_args = mock_client.post.call_args
    assert call_args[1]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"

