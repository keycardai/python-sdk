"""Integration tests for WebIdentity.

This module tests that AuthProvider correctly configures the OAuth client
when using WebIdentity, particularly around:
- Dynamic client registration behavior
- Client configuration with private key JWT authentication
- Correct handling of auto_register_client flag across different code paths
"""

import tempfile
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import Context

from keycardai.mcp.server.auth import AccessContext, AuthProvider, WebIdentity
from keycardai.oauth.types.models import AuthorizationServerMetadata, TokenResponse


class TestWebIdentity:
    """Test OAuth client configuration and behavior with WebIdentity."""

    def create_mock_context_with_auth(self, zone_id: str = "test123"):
        """Helper to create a mock Context with authentication info."""
        mock_context = Mock(spec=Context)
        mock_context.request_context = Mock()
        mock_context.request_context.request = Mock()
        mock_context.request_context.request.state.keycardai_auth_info = {
            "access_token": "test_token",
            "zone_id": zone_id,
            "resource_client_id": "https://mcp.example.com",
            "resource_server_url": "https://mcp.example.com"
        }
        return mock_context

    @pytest.mark.asyncio
    async def test_web_identity_does_not_attempt_dynamic_registration_when_disabled(self):
        """Test that WebIdentity respects enable_dynamic_client_registration=False.

        When enable_dynamic_client_registration is explicitly set to False,
        the client should NOT attempt dynamic registration, even with WebIdentity.
        """
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        # Create mock client
        mock_async_client = AsyncMock()
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_discover():
            return mock_metadata

        mock_async_client.discover_server_metadata.side_effect = mock_discover

        def mock_exchange_token(request=None, **kwargs):
            return TokenResponse(
                access_token="exchanged_token",
                token_type="Bearer",
                expires_in=3600
            )

        mock_async_client.exchange_token.side_effect = mock_exchange_token

        # Mock client factory that tracks configuration
        mock_factory = Mock()
        mock_factory.create_async_client.return_value = mock_async_client

        # Create WebIdentity
        with tempfile.TemporaryDirectory() as tmpdir:
            web_identity = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            # Create AuthProvider with dynamic registration DISABLED
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                enable_dynamic_client_registration=False,  # Explicitly disabled
                application_credential=web_identity,
                client_factory=mock_factory
            )

            # Trigger client creation
            @auth_provider.grant("https://api.example.com")
            def test_function(access_ctx: AccessContext, ctx: Context):
                return {"success": True}

            mock_context = self.create_mock_context_with_auth(zone_id)
            await test_function(ctx=mock_context)

            # Verify client was created
            assert mock_factory.create_async_client.called

            # Get the client config that was used
            call_args = mock_factory.create_async_client.call_args
            client_config = call_args.kwargs['config']

            # CRITICAL: auto_register_client should be False
            assert client_config.auto_register_client is False, (
                "When enable_dynamic_client_registration=False, "
                "auto_register_client should be False even with WebIdentity"
            )

    @pytest.mark.asyncio
    async def test_web_identity_disables_registration_by_default(self):
        """Test that WebIdentity DISABLES dynamic registration by default.

        WebIdentity uses private key JWT authentication where the client
        should already be registered with the authorization server with its JWKS URL.
        Dynamic registration should NOT happen automatically.
        """
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        # Create mock client
        mock_async_client = AsyncMock()
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_discover():
            return mock_metadata

        mock_async_client.discover_server_metadata.side_effect = mock_discover

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

        # Create WebIdentity
        with tempfile.TemporaryDirectory() as tmpdir:
            web_identity = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            # Create AuthProvider WITHOUT specifying enable_dynamic_client_registration
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                # enable_dynamic_client_registration NOT specified
                application_credential=web_identity,
                client_factory=mock_factory
            )

            # Trigger client creation
            @auth_provider.grant("https://api.example.com")
            def test_function(access_ctx: AccessContext, ctx: Context):
                return {"success": True}

            mock_context = self.create_mock_context_with_auth(zone_id)
            await test_function(ctx=mock_context)

            # Verify client config
            assert mock_factory.create_async_client.called
            call_args = mock_factory.create_async_client.call_args
            client_config = call_args.kwargs['config']

            # CRITICAL: WebIdentity should NOT auto-register by default
            # The client should already be registered with its JWKS URL
            assert client_config.auto_register_client is False, (
                "WebIdentity should NOT enable auto_register_client by default. "
                "The client should already be registered with the authorization server "
                "with its JWKS URL configured."
            )

            # Verify JWT-specific configuration is still set
            assert client_config.client_token_endpoint_auth_method == "private_key_jwt"
            assert client_config.client_jwks_url is not None
            assert client_config.client_id == "https://mcp.example.com"

    @pytest.mark.asyncio
    async def test_web_identity_explicit_registration_enabled(self):
        """Test WebIdentity with explicit enable_dynamic_client_registration=True."""
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        # Create mock client
        mock_async_client = AsyncMock()
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_discover():
            return mock_metadata

        mock_async_client.discover_server_metadata.side_effect = mock_discover

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

        # Create WebIdentity
        with tempfile.TemporaryDirectory() as tmpdir:
            web_identity = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            # Create AuthProvider with explicit enable_dynamic_client_registration=True
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                enable_dynamic_client_registration=True,  # Explicitly enabled
                application_credential=web_identity,
                client_factory=mock_factory
            )

            # Trigger client creation
            @auth_provider.grant("https://api.example.com")
            def test_function(access_ctx: AccessContext, ctx: Context):
                return {"success": True}

            mock_context = self.create_mock_context_with_auth(zone_id)
            await test_function(ctx=mock_context)

            # Verify client config
            assert mock_factory.create_async_client.called
            call_args = mock_factory.create_async_client.call_args
            client_config = call_args.kwargs['config']

            # Should respect the explicit setting
            assert client_config.auto_register_client is True
            assert client_config.client_token_endpoint_auth_method == "private_key_jwt"

    @pytest.mark.asyncio
    async def test_web_identity_auto_register_not_overridden_by_none_auth(self):
        """Test that WebIdentity's auto_register setting is not overridden.

        This catches the bug where:
        1. WebIdentity sets auto_register_client = False (correct)
        2. But then lines 336-337 check if auth_strategy is NoneAuth
        3. Since WebIdentity uses NoneAuth, it overrides to True (BUG!)

        The bug occurs because the logic is:
        ```python
        if self.enable_private_key_identity:
            client_config.auto_register_client = False  # Set correctly

        # ... later ...

        elif isinstance(auth_strategy, NoneAuth):
            client_config.auto_register_client = True  # BUG: Overrides it!
        ```

        Since WebIdentity doesn't need client credentials, auth_strategy
        is NoneAuth, which triggers the override.
        """
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        # Create mock client
        mock_async_client = AsyncMock()
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_discover():
            return mock_metadata

        mock_async_client.discover_server_metadata.side_effect = mock_discover

        def mock_exchange_token(request=None, **kwargs):
            return TokenResponse(
                access_token="exchanged_token",
                token_type="Bearer",
                expires_in=3600
            )

        mock_async_client.exchange_token.side_effect = mock_exchange_token

        # Mock client factory - THIS IS KEY: we inspect the ACTUAL config passed
        actual_configs = []

        def track_config(*args, **kwargs):
            # Store the config so we can inspect it
            if 'config' in kwargs:
                actual_configs.append(kwargs['config'])
            return mock_async_client

        mock_factory = Mock()
        mock_factory.create_async_client.side_effect = track_config

        # Create WebIdentity
        with tempfile.TemporaryDirectory() as tmpdir:
            web_identity = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            # Create AuthProvider WITHOUT explicit enable_dynamic_client_registration
            # This tests the DEFAULT behavior
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                # enable_dynamic_client_registration NOT specified - testing default
                application_credential=web_identity,
                client_factory=mock_factory
            )

            # Verify initial state
            # WebIdentity uses NoneAuth
            from keycardai.oauth.http.auth import NoneAuth
            assert isinstance(auth_provider.auth, NoneAuth), (
                "WebIdentity should use NoneAuth"
            )

            # Trigger client creation
            @auth_provider.grant("https://api.example.com")
            def test_function(access_ctx: AccessContext, ctx: Context):
                return {"success": True}

            mock_context = self.create_mock_context_with_auth(zone_id)
            await test_function(ctx=mock_context)

            # Verify client was created
            assert len(actual_configs) == 1, "Client should have been created once"

            client_config = actual_configs[0]

            # CRITICAL ASSERTION: Despite auth_strategy being NoneAuth,
            # auto_register_client should still be False for WebIdentity
            assert client_config.auto_register_client is False, (
                f"BUG DETECTED: WebIdentity should have auto_register_client=False, "
                f"but got {client_config.auto_register_client}. "
                f"The setting was likely overridden by the 'isinstance(auth_strategy, NoneAuth)' "
                f"check in the code, even though WebIdentity correctly set it to False initially."
            )

            # Also verify JWT config is still correct
            assert client_config.client_token_endpoint_auth_method == "private_key_jwt"
            assert client_config.client_jwks_url is not None

    @pytest.mark.asyncio
    async def test_no_application_credential_does_enable_registration(self):
        """Test that None application_credential (without WebIdentity) DOES enable registration.

        This verifies that the NoneAuth check is correct when no application credential is provided,
        just not for WebIdentity which also uses NoneAuth.
        """
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        # Create mock client
        mock_async_client = AsyncMock()
        mock_metadata = AuthorizationServerMetadata(
            issuer=expected_zone_url,
            authorization_endpoint=f"{expected_zone_url}/auth",
            token_endpoint=f"{expected_zone_url}/token",
            jwks_uri=f"{expected_zone_url}/.well-known/jwks.json"
        )

        async def mock_discover():
            return mock_metadata

        mock_async_client.discover_server_metadata.side_effect = mock_discover

        def mock_exchange_token(request=None, **kwargs):
            return TokenResponse(
                access_token="exchanged_token",
                token_type="Bearer",
                expires_in=3600
            )

        mock_async_client.exchange_token.side_effect = mock_exchange_token

        # Mock client factory
        actual_configs = []

        def track_config(*args, **kwargs):
            if 'config' in kwargs:
                actual_configs.append(kwargs['config'])
            return mock_async_client

        mock_factory = Mock()
        mock_factory.create_async_client.side_effect = track_config

        # Create AuthProvider without application_credential (defaults to None)
        auth_provider = AuthProvider(
            zone_id=zone_id,
            mcp_server_name="Test Server",
            mcp_server_url="https://mcp.example.com",
            base_url="https://keycard.cloud",
            enable_multi_zone=False,
            # application_credential NOT specified - defaults to None
            client_factory=mock_factory
        )

        # Trigger client creation
        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context):
            return {"success": True}

        mock_context = self.create_mock_context_with_auth(zone_id)
        await test_function(ctx=mock_context)

        # Verify
        assert len(actual_configs) == 1
        client_config = actual_configs[0]

        # None application_credential SHOULD enable registration
        assert client_config.auto_register_client is True, (
            "None application_credential should enable auto_register_client by default"
        )

