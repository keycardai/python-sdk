"""Unit tests for AuthProvider class.

This module contains unit tests for individual methods and components
of the AuthProvider class, testing them in isolation.
"""

from unittest.mock import Mock

import pytest

from keycardai.mcp.integrations.fastmcp.provider import AuthProvider, ClientFactory


@pytest.fixture
def mock_metadata():
    """Fixture providing mock OAuth server metadata."""
    metadata = Mock()
    metadata.jwks_uri = "https://test123.keycard.cloud/.well-known/jwks.json"
    return metadata


@pytest.fixture
def mock_client(mock_metadata):
    """Fixture providing a mock synchronous OAuth client."""
    client = Mock()
    client.discover_server_metadata.return_value = mock_metadata
    return client


@pytest.fixture
def mock_async_client():
    """Fixture providing a mock asynchronous OAuth client."""
    return Mock()


@pytest.fixture
def mock_client_factory(mock_client, mock_async_client):
    """Fixture providing a mock client factory."""
    factory = Mock(spec=ClientFactory)
    factory.create_client.return_value = mock_client
    factory.create_async_client.return_value = mock_async_client
    return factory


@pytest.fixture
def auth_provider_for_url_testing(mock_client_factory):
    """Fixture providing an AuthProvider instance for URL testing."""
    return AuthProvider(
        zone_id="test123",
        mcp_base_url="http://localhost:8000",
        client_factory=mock_client_factory
    )


class TestAuthProviderUrlBuilding:
    """Unit tests for AuthProvider URL building logic."""

    def test_build_zone_url_method_directly(self, auth_provider_for_url_testing):
        """Test the _build_zone_url method directly with various parameter combinations."""
        test_cases = [
            {
                "name": "explicit_zone_url",
                "zone_url": "https://explicit.keycard.cloud",
                "zone_id": None,
                "base_url": None,
                "expected": "https://explicit.keycard.cloud"
            },
            {
                "name": "zone_id_default_domain",
                "zone_url": None,
                "zone_id": "test123",
                "base_url": None,
                "expected": "https://test123.keycard.cloud"
            },
            {
                "name": "zone_id_custom_base_url",
                "zone_url": None,
                "zone_id": "test123",
                "base_url": "https://custom.domain.com",
                "expected": "https://test123.custom.domain.com"
            },
            {
                "name": "zone_url_with_trailing_slash",
                "zone_url": "https://explicit.keycard.cloud/",
                "zone_id": None,
                "base_url": None,
                "expected": "https://explicit.keycard.cloud/"
            },
            {
                "name": "custom_base_url_with_port",
                "zone_url": None,
                "zone_id": "dev123",
                "base_url": "https://staging.example.com:8443",
                "expected": "https://dev123.staging.example.com:8443"
            },
            {
                "name": "custom_base_url_http_scheme",
                "zone_url": None,
                "zone_id": "local123",
                "base_url": "http://localhost:3000",
                "expected": "http://local123.localhost:3000"
            },
        ]

        for case in test_cases:
            result = auth_provider_for_url_testing._build_zone_url(
                zone_url=case["zone_url"],
                zone_id=case["zone_id"],
                base_url=case["base_url"]
            )
            assert result == case["expected"], f"Test case '{case['name']}' failed: expected {case['expected']}, got {result}"

    def test_build_zone_url_priority_explicit_zone_url_wins(self, auth_provider_for_url_testing):
        """Test that explicit zone_url takes priority over zone_id and base_url."""
        # When zone_url is provided, it should take priority over zone_id and base_url
        result = auth_provider_for_url_testing._build_zone_url(
            zone_url="https://explicit.keycard.cloud",
            zone_id="ignored_zone_id",
            base_url="https://ignored.base.com"
        )

        assert result == "https://explicit.keycard.cloud"
