"""Unit tests for ApplicationCredential providers.

This module tests the ApplicationCredential protocol implementations including
NoneIdentityProvider and WebIdentity.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from keycardai.mcp.server.auth.application_credentials import (
    KeycardZone,
    NoneIdentity,
    WebIdentity,
)
from keycardai.oauth import BasicAuth, MultiZoneBasicAuth
from keycardai.oauth.types.models import (
    AuthorizationServerMetadata,
    TokenExchangeRequest,
)


@pytest.fixture
def mock_metadata():
    """Fixture providing mock OAuth server metadata."""
    return AuthorizationServerMetadata(
        issuer="https://test.keycard.cloud",
        authorization_endpoint="https://test.keycard.cloud/auth",
        token_endpoint="https://test.keycard.cloud/token",
        jwks_uri="https://test.keycard.cloud/.well-known/jwks.json"
    )


@pytest.fixture
def mock_client(mock_metadata):
    """Fixture providing a mock async OAuth client."""
    client = AsyncMock()

    async def mock_discover_server_metadata():
        return mock_metadata

    client.discover_server_metadata.side_effect = mock_discover_server_metadata

    # Set up _initialized and _discovered_endpoints for audience lookup
    client._initialized = True
    client._discovered_endpoints = AsyncMock()
    client._discovered_endpoints.token = mock_metadata.token_endpoint

    return client


class TestNoneIdentity:
    """Test NoneIdentity for basic token exchange."""

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request(self, mock_client):
        """Test basic token exchange request preparation."""
        provider = NoneIdentity()

        request = await provider.prepare_token_exchange_request(
            client=mock_client,
            subject_token="test_access_token",
            resource="https://api.example.com",
        )

        assert isinstance(request, TokenExchangeRequest)
        assert request.subject_token == "test_access_token"
        assert request.resource == "https://api.example.com"
        assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
        assert request.client_assertion is None
        assert request.client_assertion_type is None

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request_with_auth_info(self, mock_client):
        """Test that auth_info is ignored for NoneIdentityProvider."""
        provider = NoneIdentity()

        request = await provider.prepare_token_exchange_request(
            client=mock_client,
            subject_token="test_access_token",
            resource="https://api.example.com",
            auth_info={"resource_client_id": "https://mcp.example.com"}
        )

        # Should work fine even with auth_info provided
        assert request.subject_token == "test_access_token"
        assert request.client_assertion is None


class TestKeycardZone:
    """Test KeycardZone for Keycard Zone credential-based authentication."""

    @pytest.mark.asyncio
    async def test_initialization_with_basic_auth(self):
        """Test KeycardZone initialization with BasicAuth."""
        auth = BasicAuth(client_id="test_client_id", client_secret="test_client_secret")
        provider = KeycardZone(auth=auth)

        assert provider.auth == auth

    @pytest.mark.asyncio
    async def test_initialization_with_multi_zone_auth(self):
        """Test KeycardZone initialization with MultiZoneBasicAuth."""
        multi_auth = MultiZoneBasicAuth({
            "zone1": ("client_id_1", "client_secret_1"),
            "zone2": ("client_id_2", "client_secret_2"),
        })

        provider = KeycardZone(auth=multi_auth)

        assert provider.auth == multi_auth

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request(self, mock_client):
        """Test token exchange request preparation with Keycard Zone credentials."""
        auth = BasicAuth(client_id="test_client_id", client_secret="test_client_secret")
        provider = KeycardZone(auth=auth)

        request = await provider.prepare_token_exchange_request(
            client=mock_client,
            subject_token="test_access_token",
            resource="https://api.example.com",
        )

        assert isinstance(request, TokenExchangeRequest)
        assert request.subject_token == "test_access_token"
        assert request.resource == "https://api.example.com"
        assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
        # Client authentication is handled at HTTP level via AuthStrategy
        assert request.client_assertion is None
        assert request.client_assertion_type is None

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request_with_auth_info(self, mock_client):
        """Test that auth_info is passed but unused (authentication is via AuthStrategy)."""
        auth = BasicAuth(client_id="test_client_id", client_secret="test_client_secret")
        provider = KeycardZone(auth=auth)

        request = await provider.prepare_token_exchange_request(
            client=mock_client,
            subject_token="test_access_token",
            resource="https://api.example.com",
            auth_info={"zone_id": "zone1", "resource_client_id": "https://mcp.example.com"}
        )

        # Request is prepared successfully
        assert request.subject_token == "test_access_token"
        assert request.resource == "https://api.example.com"
        # Authentication happens at HTTP level, not in the request
        assert request.client_assertion is None


class TestWebIdentity:
    """Test WebIdentity for private key JWT authentication."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test WebIdentity initialization creates keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            # Verify keys were created
            key_dir = Path(tmpdir)
            pem_files = list(key_dir.glob("*.pem"))
            json_files = list(key_dir.glob("*.json"))

            assert len(pem_files) == 1
            assert len(json_files) == 1

            # Verify JWKS is available
            jwks = provider.get_jwks()
            assert jwks is not None
            assert len(jwks.keys) == 1

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request(self, mock_client):
        """Test JWT client assertion generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            request = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
                auth_info={"resource_client_id": "https://mcp.example.com"}
            )

            assert isinstance(request, TokenExchangeRequest)
            assert request.subject_token == "test_access_token"
            assert request.resource == "https://api.example.com"
            assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
            assert request.client_assertion is not None
            assert request.client_assertion_type == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

            # Verify JWT structure (should have 3 parts separated by dots)
            jwt_parts = request.client_assertion.split(".")
            assert len(jwt_parts) == 3

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request_without_auth_info(self, mock_client):
        """Test that missing auth_info raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            with pytest.raises(ValueError, match="auth_info with 'resource_client_id' is required"):
                await provider.prepare_token_exchange_request(
                    client=mock_client,
                    subject_token="test_access_token",
                    resource="https://api.example.com",
                )

    @pytest.mark.asyncio
    async def test_key_persistence(self):
        """Test that keys persist across provider instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create first provider
            provider1 = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                key_id="stable-key-id"
            )
            jwks1 = provider1.get_jwks()

            # Create second provider with same storage
            provider2 = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                key_id="stable-key-id"
            )
            jwks2 = provider2.get_jwks()

            # Should have the same public keys
            assert jwks1.keys[0].kid == jwks2.keys[0].kid
            assert jwks1.keys[0].n == jwks2.keys[0].n
            assert jwks1.keys[0].e == jwks2.keys[0].e

    @pytest.mark.asyncio
    async def test_custom_key_id(self):
        """Test WebIdentity with custom key ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                key_id="custom-stable-id"
            )

            jwks = provider.get_jwks()
            assert jwks.keys[0].kid == "custom-stable-id"

    @pytest.mark.asyncio
    async def test_audience_config(self, mock_client):
        """Test WebIdentity with audience configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                audience_config="https://custom-audience.example.com"
            )

            request = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
                auth_info={"resource_client_id": "https://mcp.example.com"}
            )

            # JWT should be created successfully
            assert request.client_assertion is not None

