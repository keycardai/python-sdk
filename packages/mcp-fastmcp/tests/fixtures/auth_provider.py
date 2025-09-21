"""Shared fixtures for AuthProvider testing.

This module provides common fixtures that can be reused across different
test modules for AuthProvider and related functionality.
"""

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import requests
from pydantic import AnyHttpUrl

from keycardai.mcp.integrations.fastmcp.provider import ClientFactory
from keycardai.oauth.types.models import AuthorizationServerMetadata, TokenResponse

# Test constants
mock_zone_id = "test123"
mock_zone_url = "https://test123.keycard.cloud"
mock_custom_zone_url = AnyHttpUrl("https://custom.domain.com")
valid_mock_zone_urls = [mock_zone_url, f"{mock_custom_zone_url.scheme}://{mock_zone_id}.{mock_custom_zone_url.host}"]
mock_authorization_endpoint = "https://test123.keycard.cloud/auth"
mock_token_endpoint = "https://test123.keycard.cloud/token"
mock_jwks_uri = "https://test123.keycard.cloud/.well-known/jwks.json"


@pytest.fixture
def mock_metadata() -> AuthorizationServerMetadata:
    """Fixture providing mock OAuth server metadata."""
    return AuthorizationServerMetadata(
        issuer=mock_zone_url,
        authorization_endpoint=mock_authorization_endpoint,
        token_endpoint=mock_token_endpoint,
        jwks_uri=mock_jwks_uri
    )


@pytest.fixture
def mock_client(mock_metadata: AuthorizationServerMetadata) -> Mock:
    """Fixture providing a mock synchronous OAuth client."""
    client = Mock()

    def mock_discover_metadata():
        # In a real scenario, this would check the base_url used to create the client
        # For our mock, we'll simulate the correct behavior
        return mock_metadata

    client.discover_server_metadata.side_effect = mock_discover_metadata
    return client


@pytest.fixture
def mock_async_client():
    """Fixture providing a mock asynchronous OAuth client."""
    client = AsyncMock()

    # Default successful token exchange behavior
    def mock_exchange_token(subject_token: str, resource: str, subject_token_type: str):
        # Create different tokens based on resource for testing
        if "api1.example.com" in resource:
            return TokenResponse(
                access_token="token_api1_123",
                token_type="Bearer",
                expires_in=3600
            )
        elif "api2.example.com" in resource:
            return TokenResponse(
                access_token="token_api2_456",
                token_type="Bearer",
                expires_in=3600
            )
        elif "integration.com" in resource:
            return TokenResponse(
                access_token="delegated_access_token",
                token_type="Bearer",
                expires_in=3600,
                scope=["read", "write"]
            )
        else:
            # Default token for other resources
            return TokenResponse(
                access_token="exchanged_token_123",
                token_type="Bearer",
                expires_in=3600
            )

    client.exchange_token.side_effect = mock_exchange_token
    return client


def _create_mock_client_with_validation(mock_client: Mock, mock_metadata: AuthorizationServerMetadata):
    """Helper method to create a mock client with URL validation."""
    def create_client_with_validation(base_url, auth=None):
        if base_url in valid_mock_zone_urls:
            mock_client.discover_server_metadata.return_value = mock_metadata
        else:
            mock_client.discover_server_metadata.side_effect = requests.exceptions.ConnectionError(
                f"Failed to establish a new connection {base_url}: [Errno 61] Connection refused"
            )

        mock_client._base_url = base_url
        return mock_client

    return create_client_with_validation


@pytest.fixture
def mock_client_factory(mock_client: Mock, mock_async_client: Mock, mock_metadata: AuthorizationServerMetadata) -> Mock:
    """Fixture providing a mock client factory."""
    factory = Mock(spec=ClientFactory)
    factory.create_client.side_effect = _create_mock_client_with_validation(mock_client, mock_metadata)
    factory.create_async_client.return_value = mock_async_client
    return factory


@pytest.fixture
def auth_provider_config() -> dict[str, Any]:
    """Fixture providing basic AuthProvider configuration."""
    return {
        "zone_id": mock_zone_id,
        "mcp_server_name": "Test Server",
        "mcp_base_url": "http://localhost:8000"
    }
