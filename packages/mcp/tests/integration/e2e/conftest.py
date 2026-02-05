"""Shared fixtures for E2E tests.

These fixtures extend the base auth_provider fixtures with E2E-specific
configurations for testing complete flows.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.mcp.server.auth.client_factory import ClientFactory
from keycardai.oauth.types.models import AuthorizationServerMetadata, TokenResponse

# E2E test constants
E2E_ZONE_ID = "e2e-test"
E2E_ZONE_URL = "https://e2e-test.keycard.cloud"


@pytest.fixture
def e2e_oauth_metadata():
    """Standard OAuth metadata for E2E tests."""
    return AuthorizationServerMetadata(
        issuer=E2E_ZONE_URL,
        authorization_endpoint=f"{E2E_ZONE_URL}/auth",
        token_endpoint=f"{E2E_ZONE_URL}/token",
        registration_endpoint=f"{E2E_ZONE_URL}/register",
        jwks_uri=f"{E2E_ZONE_URL}/.well-known/jwks.json",
    )


@pytest.fixture
def e2e_client_factory(e2e_oauth_metadata):
    """Create a reusable mock client factory for E2E tests."""
    factory = Mock(spec=ClientFactory)

    # Mock sync client for metadata discovery
    mock_sync_client = Mock()
    mock_sync_client.discover_server_metadata.return_value = e2e_oauth_metadata
    factory.create_client.return_value = mock_sync_client

    # Mock async client for token exchange
    mock_async_client = AsyncMock()
    mock_async_client.config = Mock()
    mock_async_client.config.client_id = "e2e_test_client"

    def default_exchange(request):
        """Generate resource-specific tokens for E2E testing."""
        resource = request.resource if hasattr(request, "resource") else str(request)
        # Create deterministic token based on resource
        token_suffix = resource.replace("https://", "").replace("/", "_").replace(".", "_")
        return TokenResponse(
            access_token=f"e2e_token_for_{token_suffix}",
            token_type="Bearer",
            expires_in=3600,
        )

    mock_async_client.exchange_token.side_effect = default_exchange
    factory.create_async_client.return_value = mock_async_client

    return factory, mock_async_client


@pytest.fixture
def e2e_auth_provider_config():
    """Standard AuthProvider configuration for E2E tests."""
    return {
        "zone_id": E2E_ZONE_ID,
        "mcp_server_name": "E2E Test Server",
        "mcp_server_url": "http://localhost:8000/",
    }
