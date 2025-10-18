"""Integration tests for AuthProvider interface.

This module tests the AuthProvider class which is one of the core interfaces
in the mcp package. It tests the complete flow from initialization
to JWT verifier creation and related functionality.
"""


import pytest

from keycardai.mcp.server.auth import (
    AuthProvider,
    AuthProviderConfigurationError,
    BasicAuth,
    ClientSecret,
    NoneAuth,
)

from ..fixtures.auth_provider import mock_custom_zone_url, mock_zone_id, mock_zone_url


class TestAuthProviderInitialization:
    """Test AuthProvider initialization and configuration."""

    def test_auth_provider_init_with_zone_id(self, auth_provider_config, mock_client_factory):
        """Test AuthProvider initialization with zone_id."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        assert auth_provider.zone_url == "https://test123.keycard.cloud"
        assert auth_provider.mcp_server_name == "Test Server"
        assert str(auth_provider.mcp_server_url) == "http://localhost:8000/mcp"
        assert auth_provider.required_scopes is None
        assert isinstance(auth_provider.auth, NoneAuth)

    def test_auth_provider_init_with_zone_url(self, mock_client_factory):
        """Test AuthProvider initialization with zone_url."""
        auth_provider = AuthProvider(
            zone_url=mock_zone_url,
            mcp_server_name="Custom Server",
            mcp_server_url="https://api.example.com",
            client_factory=mock_client_factory
        )

        assert auth_provider.zone_url == mock_zone_url
        assert auth_provider.mcp_server_name == "Custom Server"
        assert str(auth_provider.mcp_server_url) == "https://api.example.com"

    def test_auth_provider_init_with_custom_base_url(self, mock_client_factory):
        """Test AuthProvider initialization with custom base_url."""
        auth_provider = AuthProvider(
            zone_id=mock_zone_id,
            base_url=mock_custom_zone_url,
            mcp_server_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        assert auth_provider.zone_url == f"{mock_custom_zone_url.scheme}://{mock_zone_id}.{mock_custom_zone_url.host}"

    def test_auth_provider_init_with_basic_auth(self, mock_client_factory):
        """Test AuthProvider initialization with ClientSecret."""
        app_identity = ClientSecret(("client_id", "client_secret"))
        auth_provider = AuthProvider(
            zone_id=mock_zone_id,
            mcp_server_url="http://localhost:8000",
            application_credential=app_identity,
            client_factory=mock_client_factory
        )

        assert isinstance(auth_provider.auth, BasicAuth)
        assert isinstance(auth_provider.application_credential, ClientSecret)

    def test_auth_provider_init_with_required_scopes(self, mock_client_factory):
        """Test AuthProvider initialization with required scopes."""
        auth_provider = AuthProvider(
            zone_id=mock_zone_id,
            mcp_server_url="http://localhost:8000",
            required_scopes=["read", "write"],
            client_factory=mock_client_factory
        )

        assert auth_provider.required_scopes == ["read", "write"]

    def test_auth_provider_init_missing_zone_info(self):
        """Test AuthProvider initialization fails without zone_id or zone_url."""
        with pytest.raises(AuthProviderConfigurationError):
            AuthProvider(mcp_server_url="http://localhost:8000")
