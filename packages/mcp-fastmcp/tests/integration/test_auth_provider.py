"""Integration tests for AuthProvider interface.

This module tests the AuthProvider class which is one of the core interfaces
in the mcp-fastmcp package. It tests the complete flow from initialization
to JWT verifier creation and RemoteAuthProvider creation.
"""

from unittest.mock import Mock

import pytest
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fixtures.auth_provider import mock_custom_zone_url, mock_zone_id, mock_zone_url

from keycardai.mcp.integrations.fastmcp import (
    AuthProvider,
    AuthProviderConfigurationError,
    BasicAuth,
    MetadataDiscoveryError,
    NoneAuth,
)
from keycardai.oauth.types.models import AuthorizationServerMetadata


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
        assert auth_provider.mcp_base_url == "http://localhost:8000/"
        assert auth_provider.required_scopes == []
        assert isinstance(auth_provider.auth, NoneAuth)

    def test_auth_provider_init_with_zone_url(self, mock_client_factory):
        """Test AuthProvider initialization with zone_url."""
        auth_provider = AuthProvider(
            zone_url=mock_zone_url,
            mcp_server_name="Custom Server",
            mcp_base_url="https://api.example.com",
            client_factory=mock_client_factory
        )

        assert auth_provider.zone_url == mock_zone_url
        assert auth_provider.mcp_server_name == "Custom Server"
        assert auth_provider.mcp_base_url == "https://api.example.com/"

    def test_auth_provider_init_with_custom_base_url(self, mock_client_factory):
        """Test AuthProvider initialization with custom base_url."""
        auth_provider = AuthProvider(
            zone_id=mock_zone_id,
            base_url=mock_custom_zone_url,
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        assert auth_provider.zone_url == f"{mock_custom_zone_url.scheme}://{mock_zone_id}.{mock_custom_zone_url.host}"

    def test_auth_provider_init_with_basic_auth(self, mock_client_factory):
        """Test AuthProvider initialization with BasicAuth."""
        basic_auth = BasicAuth("client_id", "client_secret")
        auth_provider = AuthProvider(
            zone_id=mock_zone_id,
            mcp_base_url="http://localhost:8000",
            auth=basic_auth,
            client_factory=mock_client_factory
        )

        assert auth_provider.auth == basic_auth

    def test_auth_provider_init_with_required_scopes(self, mock_client_factory):
        """Test AuthProvider initialization with required scopes."""
        auth_provider = AuthProvider(
            zone_id=mock_zone_id,
            mcp_base_url="http://localhost:8000",
            required_scopes=["read", "write"],
            client_factory=mock_client_factory
        )

        assert auth_provider.required_scopes == ["read", "write"]

    def test_auth_provider_init_missing_zone_info(self):
        """Test AuthProvider initialization fails without zone_id or zone_url."""
        with pytest.raises(AuthProviderConfigurationError):
            AuthProvider(mcp_base_url="http://localhost:8000")

    def test_auth_provider_init_with_client_factory(self):
        """Test AuthProvider initialization with custom client factory."""
        mock_client_factory = Mock()
        mock_sync_client = Mock()
        mock_async_client = Mock()

        # Mock the factory methods
        mock_client_factory.create_client.return_value = mock_sync_client
        mock_client_factory.create_async_client.return_value = mock_async_client

        # Mock the discovery to avoid actual HTTP calls
        mock_metadata = AuthorizationServerMetadata(
            issuer="https://customissuer.keycard.cloud",
            authorization_endpoint="https://customissuer.keycard.cloud/auth",
            token_endpoint="https://customissuer.keycard.cloud/token",
            jwks_uri="https://customissuer.keycard.cloud/.well-known/jwks.json"
        )
        mock_sync_client.discover_server_metadata.return_value = mock_metadata

        auth_provider = AuthProvider(
            zone_id="customzone",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        assert auth_provider.client_factory == mock_client_factory
        assert auth_provider.client == mock_async_client
        mock_client_factory.create_client.assert_called_once()
        mock_client_factory.create_async_client.assert_called_once()

class TestAuthProviderJWTVerifier:
    """Test AuthProvider JWT verifier creation."""

    def test_get_jwt_token_verifier_success_with_client_factory(self, auth_provider_config, mock_client_factory):
        """Test successful JWT token verifier creation with client factory."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            required_scopes=["read", "write"],
            client_factory=mock_client_factory
        )

        verifier = auth_provider.get_jwt_token_verifier()

        assert isinstance(verifier, JWTVerifier)
        # Verify the JWKS URI was discovered and stored
        assert auth_provider.jwks_uri == "https://test123.keycard.cloud/.well-known/jwks.json"

    def test_get_jwt_token_verifier_discovery_failure(self, mock_client_factory):
        """Test JWT token verifier creation when discovery fails during initialization."""
        with pytest.raises(MetadataDiscoveryError):
            AuthProvider(
                zone_id="nonexistentzone",
                mcp_base_url="http://localhost:8000",
                client_factory=mock_client_factory
            )



class TestAuthProviderRemoteAuthProvider:
    """Test AuthProvider RemoteAuthProvider creation."""

    def test_get_remote_auth_provider_success(self, auth_provider_config, mock_client_factory):
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        remote_auth = auth_provider.get_remote_auth_provider()

        assert isinstance(remote_auth, RemoteAuthProvider)
        assert isinstance(remote_auth.token_verifier, JWTVerifier)

    def test_get_remote_auth_provider_with_custom_settings(self, mock_client_factory):
        """Test RemoteAuthProvider creation with custom settings."""
        auth_provider = AuthProvider(
            zone_url=mock_zone_url,
            mcp_server_name="Custom Server",
            mcp_base_url="https://api.example.com",
            required_scopes=["admin"],
            client_factory=mock_client_factory
        )

        remote_auth = auth_provider.get_remote_auth_provider()

        assert isinstance(remote_auth, RemoteAuthProvider)
        assert isinstance(remote_auth.token_verifier, JWTVerifier)



