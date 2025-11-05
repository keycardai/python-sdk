"""Unit tests for AuthProvider class.

This module contains unit tests for individual methods and components
of the AuthProvider class, testing them in isolation.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from keycardai.mcp.integrations.fastmcp.provider import AuthProvider, ClientFactory
from keycardai.mcp.server.auth import ClientSecret, EKSWorkloadIdentity, WebIdentity
from keycardai.mcp.server.exceptions import AuthProviderConfigurationError


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
def temp_key_storage():
    """Fixture providing a temporary directory for WebIdentity key storage.

    Creates a temporary directory before tests and cleans it up after.
    """
    temp_dir = tempfile.mkdtemp(prefix="test_webidentity_keys_")
    yield temp_dir
    # Cleanup: remove the temporary directory and all its contents
    if Path(temp_dir).exists():
        shutil.rmtree(temp_dir)


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


class TestAuthProviderCredentialDiscovery:
    """Unit tests for AuthProvider application credential discovery logic."""

    def test_discover_returns_provided_credential(self, mock_client_factory):
        """Test that provided application credential is returned as-is."""
        # Create a specific credential
        provided_credential = ClientSecret(("test_client_id", "test_secret"))

        # Create provider with the credential
        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            application_credential=provided_credential,
            client_factory=mock_client_factory
        )

        # Should return the same credential
        result = auth_provider._discover_application_credential(provided_credential)
        assert result is provided_credential

    @patch.dict(os.environ, {
        "KEYCARD_CLIENT_ID": "test_client_id",
        "KEYCARD_CLIENT_SECRET": "test_secret"
    }, clear=True)
    def test_discover_from_client_id_secret_env_vars(self, mock_client_factory):
        """Test discovery of ClientSecret from environment variables."""
        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return ClientSecret with correct credentials
        assert isinstance(result, ClientSecret)

    @patch.dict(os.environ, {"KEYCARD_CLIENT_ID": "test_id"}, clear=True)
    def test_discover_ignores_partial_client_credentials(self, mock_client_factory):
        """Test that only KEYCARD_CLIENT_ID without SECRET is ignored."""
        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return None when only client_id is present
        assert result is None

    @patch.dict(os.environ, {"KEYCARD_CLIENT_SECRET": "test_secret"}, clear=True)
    def test_discover_ignores_secret_without_client_id(self, mock_client_factory):
        """Test that only KEYCARD_CLIENT_SECRET without ID is ignored."""
        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return None when only client_secret is present
        assert result is None

    @patch.dict(os.environ, {
        "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "eks_workload_identity",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"
    }, clear=True)
    @patch("builtins.open", create=True)
    def test_discover_eks_workload_identity_from_type_env(self, mock_open, mock_client_factory):
        """Test discovery of EKSWorkloadIdentity from credential type env var."""
        # Mock the token file read
        mock_open.return_value.__enter__.return_value.read.return_value = "test_token"

        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return EKSWorkloadIdentity instance
        assert isinstance(result, EKSWorkloadIdentity)

    def test_discover_web_identity_from_type_env(self, mock_client_factory, temp_key_storage):
        """Test discovery of WebIdentity from credential type env var."""
        with patch.dict(os.environ, {
            "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "web_identity",
            "KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR": temp_key_storage
        }, clear=True):
            auth_provider = AuthProvider(
                zone_id="test123",
                mcp_base_url="http://localhost:8000",
                mcp_server_name="test_mcp_server",
                client_factory=mock_client_factory
            )

            result = auth_provider._discover_application_credential(None)

            # Should return WebIdentity instance
            assert isinstance(result, WebIdentity)

            # Verify key storage directory was used
            assert Path(temp_key_storage).exists()

    @patch.dict(os.environ, {"KEYCARD_APPLICATION_CREDENTIAL_TYPE": "unknown_type"}, clear=True)
    def test_discover_raises_error_for_unknown_credential_type(self, mock_client_factory):
        """Test that unknown credential type raises AuthProviderConfigurationError."""
        # Should raise error with helpful message during initialization
        with pytest.raises(AuthProviderConfigurationError) as exc_info:
            AuthProvider(
                zone_id="test123",
                mcp_base_url="http://localhost:8000",
                client_factory=mock_client_factory
            )

        # Check error message contains useful information
        assert "Unknown application credential type: unknown_type" in str(exc_info.value)
        assert "eks_workload_identity" in str(exc_info.value)
        assert "web_identity" in str(exc_info.value)

    @patch.dict(os.environ, {"AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"}, clear=True)
    @patch("builtins.open", create=True)
    def test_discover_eks_workload_identity_from_token_file_env(self, mock_open, mock_client_factory):
        """Test discovery of EKSWorkloadIdentity from AWS token file env var."""
        # Mock the token file read
        mock_open.return_value.__enter__.return_value.read.return_value = "test_token"

        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should detect and return EKSWorkloadIdentity
        assert isinstance(result, EKSWorkloadIdentity)

    @patch.dict(os.environ, {}, clear=True)
    def test_discover_returns_none_when_no_credentials_found(self, mock_client_factory):
        """Test that None is returned when no credentials are discoverable."""
        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return None when nothing is configured
        assert result is None

    @patch.dict(os.environ, {
        "KEYCARD_CLIENT_ID": "env_client_id",
        "KEYCARD_CLIENT_SECRET": "env_secret",
        "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "web_identity"
    }, clear=True)
    def test_discover_priority_client_credentials_over_type(self, mock_client_factory):
        """Test that KEYCARD_CLIENT_ID/SECRET take priority over credential type."""
        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return ClientSecret, not WebIdentity
        assert isinstance(result, ClientSecret)

    @patch.dict(os.environ, {
        "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "eks_workload_identity",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"
    }, clear=True)
    @patch("builtins.open", create=True)
    def test_discover_priority_explicit_type_over_detected(self, mock_open, mock_client_factory):
        """Test that explicit credential type takes priority over auto-detected."""
        # Mock the token file read
        mock_open.return_value.__enter__.return_value.read.return_value = "test_token"

        auth_provider = AuthProvider(
            zone_id="test123",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return EKSWorkloadIdentity (though both paths lead to same result)
        assert isinstance(result, EKSWorkloadIdentity)

    def test_discover_provided_credential_ignores_env_vars(self, mock_client_factory, temp_key_storage):
        """Test that provided credential takes absolute priority over env vars."""
        provided_credential = WebIdentity(
            mcp_server_name="provided_server",
            storage_dir=temp_key_storage
        )

        with patch.dict(os.environ, {
            "KEYCARD_CLIENT_ID": "env_client_id",
            "KEYCARD_CLIENT_SECRET": "env_secret",
            "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "eks_workload_identity",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"
        }, clear=True):
            auth_provider = AuthProvider(
                zone_id="test123",
                mcp_base_url="http://localhost:8000",
                application_credential=provided_credential,
                client_factory=mock_client_factory
            )

            result = auth_provider._discover_application_credential(provided_credential)

            # Should return the provided credential, not anything from env
            assert result is provided_credential
            assert isinstance(result, WebIdentity)


class TestAuthProviderZoneConfigurationDiscovery:
    """Unit tests for AuthProvider zone configuration discovery logic."""

    @patch.dict(os.environ, {}, clear=True)
    def test_zone_id_from_explicit_parameter(self, mock_client_factory):
        """Test that explicit zone_id parameter takes priority."""
        auth_provider = AuthProvider(
            zone_id="explicit_zone",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should use the explicit zone_id to construct zone_url
        assert "explicit_zone" in auth_provider.zone_url
        assert auth_provider.zone_url == "https://explicit_zone.keycard.cloud"

    @patch.dict(os.environ, {"KEYCARD_ZONE_ID": "env_zone"}, clear=True)
    def test_zone_id_from_environment_variable(self, mock_client_factory):
        """Test discovery of zone_id from KEYCARD_ZONE_ID env var."""
        auth_provider = AuthProvider(
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should discover zone_id from environment
        assert "env_zone" in auth_provider.zone_url
        assert auth_provider.zone_url == "https://env_zone.keycard.cloud"

    @patch.dict(os.environ, {"KEYCARD_ZONE_ID": "env_zone"}, clear=True)
    def test_explicit_zone_id_takes_priority_over_env(self, mock_client_factory):
        """Test that explicit zone_id parameter takes priority over env var."""
        auth_provider = AuthProvider(
            zone_id="explicit_zone",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should use explicit zone_id, not env var
        assert "explicit_zone" in auth_provider.zone_url
        assert "env_zone" not in auth_provider.zone_url

    @patch.dict(os.environ, {}, clear=True)
    def test_zone_url_from_explicit_parameter(self, mock_client_factory):
        """Test that explicit zone_url parameter is used directly."""
        auth_provider = AuthProvider(
            zone_url="https://custom.zone.example.com",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should use the explicit zone_url
        assert auth_provider.zone_url == "https://custom.zone.example.com"

    @patch.dict(os.environ, {"KEYCARD_ZONE_URL": "https://env.zone.example.com"}, clear=True)
    def test_zone_url_from_environment_variable(self, mock_client_factory):
        """Test discovery of zone_url from KEYCARD_ZONE_URL env var."""
        auth_provider = AuthProvider(
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should discover zone_url from environment
        assert auth_provider.zone_url == "https://env.zone.example.com"

    @patch.dict(os.environ, {"KEYCARD_ZONE_URL": "https://env.zone.example.com"}, clear=True)
    def test_explicit_zone_url_takes_priority_over_env(self, mock_client_factory):
        """Test that explicit zone_url parameter takes priority over env var."""
        auth_provider = AuthProvider(
            zone_url="https://explicit.zone.example.com",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should use explicit zone_url, not env var
        assert auth_provider.zone_url == "https://explicit.zone.example.com"

    @patch.dict(os.environ, {}, clear=True)
    def test_base_url_from_explicit_parameter(self, mock_client_factory):
        """Test that explicit base_url parameter is used for zone construction."""
        auth_provider = AuthProvider(
            zone_id="test_zone",
            mcp_base_url="http://localhost:8000",
            base_url="https://custom.keycard.example.com",
            client_factory=mock_client_factory
        )

        # Should use custom base_url to construct zone_url
        assert auth_provider.zone_url == "https://test_zone.custom.keycard.example.com"

    @patch.dict(os.environ, {"KEYCARD_BASE_URL": "https://env.keycard.example.com"}, clear=True)
    def test_base_url_from_environment_variable(self, mock_client_factory):
        """Test discovery of base_url from KEYCARD_BASE_URL env var."""
        auth_provider = AuthProvider(
            zone_id="test_zone",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should discover base_url from environment and use it for zone construction
        assert auth_provider.zone_url == "https://test_zone.env.keycard.example.com"

    @patch.dict(os.environ, {"KEYCARD_BASE_URL": "https://env.keycard.example.com"}, clear=True)
    def test_explicit_base_url_takes_priority_over_env(self, mock_client_factory):
        """Test that explicit base_url parameter takes priority over env var."""
        auth_provider = AuthProvider(
            zone_id="test_zone",
            mcp_base_url="http://localhost:8000",
            base_url="https://explicit.keycard.example.com",
            client_factory=mock_client_factory
        )

        # Should use explicit base_url, not env var
        assert auth_provider.zone_url == "https://test_zone.explicit.keycard.example.com"

    @patch.dict(os.environ, {
        "KEYCARD_ZONE_ID": "env_zone",
        "KEYCARD_ZONE_URL": "https://env.zone.example.com",
        "KEYCARD_BASE_URL": "https://env.keycard.example.com"
    }, clear=True)
    def test_zone_url_takes_priority_over_zone_id(self, mock_client_factory):
        """Test that zone_url (explicit or env) takes priority over zone_id in construction."""
        auth_provider = AuthProvider(
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should use zone_url from env, ignoring zone_id and base_url
        assert auth_provider.zone_url == "https://env.zone.example.com"

    @patch.dict(os.environ, {
        "KEYCARD_ZONE_ID": "env_zone",
        "KEYCARD_ZONE_URL": "https://env.zone.example.com"
    }, clear=True)
    def test_explicit_zone_url_takes_priority_over_all(self, mock_client_factory):
        """Test that explicit zone_url takes priority over all env vars."""
        auth_provider = AuthProvider(
            zone_url="https://explicit.zone.example.com",
            mcp_base_url="http://localhost:8000",
            client_factory=mock_client_factory
        )

        # Should use explicit zone_url, ignoring all env vars
        assert auth_provider.zone_url == "https://explicit.zone.example.com"

    @patch.dict(os.environ, {}, clear=True)
    def test_raises_error_when_no_zone_configuration(self, mock_client_factory):
        """Test that AuthProviderConfigurationError is raised when no zone configuration is provided."""
        with pytest.raises(AuthProviderConfigurationError):
            AuthProvider(
                mcp_base_url="http://localhost:8000",
                client_factory=mock_client_factory
            )
