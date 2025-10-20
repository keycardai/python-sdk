"""Unit tests for OAuth client creation in AuthProvider.

This module tests the client creation logic to ensure that clients are created
with the correct base URLs for both single-zone and multi-zone configurations.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import Context

from keycardai.mcp.server.auth import AccessContext, AuthProvider, ClientSecret
from keycardai.oauth.types.models import AuthorizationServerMetadata, TokenResponse


class TestClientCreation:
    """Test OAuth client creation with correct URLs."""

    def create_mock_context_with_auth(self, zone_id: str = "test123"):
        """Helper to create a mock Context with authentication info."""
        mock_context = Mock(spec=Context)
        mock_context.request_context = Mock()
        mock_context.request_context.request = Mock()
        mock_context.request_context.request.state.keycardai_auth_info = {
            "access_token": "test_token",
            "zone_id": zone_id,
            "resource_client_id": "https://api.example.com",
            "resource_server_url": "https://api.example.com"
        }
        return mock_context

    @pytest.mark.asyncio
    async def test_single_zone_client_creation_uses_zone_scoped_url(self):
        """Test that single-zone mode creates client with zone-scoped URL.

        This test catches the bug where base_url doesn't include the zone_id
        in single-zone mode. The client should be created with:
        https://test123.keycard.cloud
        NOT with:
        https://keycard.cloud
        """
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        # Create mock client factory that tracks what URL is used
        mock_async_client = AsyncMock()

        # Mock metadata
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_get_metadata():
            return mock_metadata

        mock_async_client.get_metadata.side_effect = mock_get_metadata

        # Mock token exchange
        def mock_exchange_token(request=None, **kwargs):
            return TokenResponse(
                access_token="exchanged_token",
                token_type="Bearer",
                expires_in=3600
            )

        mock_async_client.exchange_token.side_effect = mock_exchange_token

        # Mock client factory
        mock_factory = Mock()
        mock_factory.create_async_client.return_value = mock_async_client

        # Create AuthProvider in single-zone mode
        auth_provider = AuthProvider(
            zone_id=zone_id,
            mcp_server_name="Test Server",
            mcp_server_url="http://localhost:8000/mcp",
            base_url="https://keycard.cloud",
            enable_multi_zone=False,  # Single zone mode
            client_factory=mock_factory
        )

        # Trigger client creation by using the grant decorator
        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context):
            return {"success": True}

        mock_context = self.create_mock_context_with_auth(zone_id)

        # Execute the function to trigger client creation
        await test_function(ctx=mock_context)

        # Verify the client factory was called
        assert mock_factory.create_async_client.called, "Client factory should have been called"

        # Get the actual call arguments
        call_args = mock_factory.create_async_client.call_args
        actual_base_url = call_args.kwargs['base_url']

        # CRITICAL ASSERTION: The base_url should include the zone_id
        assert actual_base_url == expected_zone_url, (
            f"Client should be created with zone-scoped URL. "
            f"Expected: {expected_zone_url}, Got: {actual_base_url}"
        )

    @pytest.mark.asyncio
    async def test_multi_zone_client_creation_uses_zone_from_request(self):
        """Test that multi-zone mode creates client with zone from request context."""
        base_url = "https://keycard.cloud"
        zone_id_from_request = "zone456"
        expected_zone_url = f"https://{zone_id_from_request}.keycard.cloud"

        # Create mock client factory
        mock_async_client = AsyncMock()

        # Mock metadata
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_get_metadata():
            return mock_metadata

        mock_async_client.get_metadata.side_effect = mock_get_metadata

        # Mock token exchange
        def mock_exchange_token(request=None, **kwargs):
            return TokenResponse(
                access_token="exchanged_token",
                token_type="Bearer",
                expires_in=3600
            )

        mock_async_client.exchange_token.side_effect = mock_exchange_token

        # Mock client factory
        mock_factory = Mock()
        mock_factory.create_async_client.return_value = mock_async_client

        # Create AuthProvider in multi-zone mode
        auth_provider = AuthProvider(
            zone_url=base_url,  # Top-level domain for multi-zone
            mcp_server_name="Test Server",
            mcp_server_url="http://localhost:8000/mcp",
            base_url=base_url,
            enable_multi_zone=True,  # Multi-zone mode
            client_factory=mock_factory
        )

        # Trigger client creation
        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context):
            return {"success": True}

        mock_context = self.create_mock_context_with_auth(zone_id_from_request)

        # Execute the function
        await test_function(ctx=mock_context)

        # Verify the client factory was called with correct URL
        assert mock_factory.create_async_client.called
        call_args = mock_factory.create_async_client.call_args
        actual_base_url = call_args.kwargs['base_url']

        assert actual_base_url == expected_zone_url, (
            f"Multi-zone client should use zone from request. "
            f"Expected: {expected_zone_url}, Got: {actual_base_url}"
        )

    @pytest.mark.asyncio
    async def test_single_zone_with_client_secret_identity(self):
        """Test single-zone with ClientSecret creates client with correct URL."""
        zone_id = "prod"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        # Create mock client
        mock_async_client = AsyncMock()
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_get_metadata():
            return mock_metadata

        mock_async_client.get_metadata.side_effect = mock_get_metadata

        def mock_exchange_token(request=None, **kwargs):
            return TokenResponse(
                access_token="exchanged_token",
                token_type="Bearer",
                expires_in=3600
            )

        mock_async_client.exchange_token.side_effect = mock_exchange_token

        # Mock client factory
        mock_factory = Mock()
        mock_factory.create_async_client.return_value = mock_async_client

        # Create identity with credentials
        app_identity = ClientSecret(("client_id", "client_secret"))

        # Create AuthProvider
        auth_provider = AuthProvider(
            zone_id=zone_id,
            mcp_server_name="Test Server",
            mcp_server_url="http://localhost:8000/mcp",
            base_url="https://keycard.cloud",
            enable_multi_zone=False,
            application_credential=app_identity,
            client_factory=mock_factory
        )

        # Trigger client creation
        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context):
            return {"success": True}

        mock_context = self.create_mock_context_with_auth(zone_id)
        await test_function(ctx=mock_context)

        # Verify correct URL
        assert mock_factory.create_async_client.called
        call_args = mock_factory.create_async_client.call_args
        actual_base_url = call_args.kwargs['base_url']

        assert actual_base_url == expected_zone_url, (
            f"Single-zone with ClientSecret should use zone-scoped URL. "
            f"Expected: {expected_zone_url}, Got: {actual_base_url}"
        )

