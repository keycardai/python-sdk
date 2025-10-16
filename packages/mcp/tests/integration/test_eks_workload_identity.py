"""Integration tests for EKSWorkloadIdentity.

This module tests that AuthProvider correctly configures the OAuth client
when using EKSWorkloadIdentity, particularly around:
- Token file reading and validation
- Client configuration behavior
- Token exchange request preparation
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import Context

from keycardai.mcp.server.auth import AccessContext, AuthProvider, EKSWorkloadIdentity
from keycardai.mcp.server.exceptions import (
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
)
from keycardai.oauth.types.models import AuthorizationServerMetadata, TokenResponse


class TestEKSWorkloadIdentity:
    """Test OAuth client configuration and behavior with EKSWorkloadIdentity."""

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
    async def test_eks_workload_identity_initialization_with_token_file(self):
        """Test that EKSWorkloadIdentity initializes with valid token file."""
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("eks-test-token")

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

            # Create EKSWorkloadIdentity
            eks_identity = EKSWorkloadIdentity(token_file_path=str(token_file))

            # Create AuthProvider
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                application_credential=eks_identity,
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

    @pytest.mark.asyncio
    async def test_eks_workload_identity_with_env_var(self):
        """Test that EKSWorkloadIdentity works with environment variable."""
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("eks-test-token")

            # Set environment variable
            os.environ["AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE"] = str(token_file)
            try:
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

                # Create EKSWorkloadIdentity without explicit path
                eks_identity = EKSWorkloadIdentity()

                # Create AuthProvider
                auth_provider = AuthProvider(
                    zone_id=zone_id,
                    mcp_server_name="Test Server",
                    mcp_server_url="https://mcp.example.com",
                    base_url="https://keycard.cloud",
                    enable_multi_zone=False,
                    application_credential=eks_identity,
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

            finally:
                os.environ.pop("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", None)

    @pytest.mark.asyncio
    async def test_eks_workload_identity_fails_when_token_missing(self):
        """Test that EKSWorkloadIdentity fails early when token cannot be read."""
        # Ensure env var is not set
        os.environ.pop("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", None)

        with pytest.raises(EKSWorkloadIdentityConfigurationError) as exc_info:
            EKSWorkloadIdentity()

        assert "Failed to initialize EKS workload identity" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_eks_workload_identity_does_not_attempt_dynamic_registration(self):
        """Test that EKSWorkloadIdentity does not enable dynamic registration by default."""
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("eks-test-token")

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

            # Create EKSWorkloadIdentity
            eks_identity = EKSWorkloadIdentity(token_file_path=str(token_file))

            # Create AuthProvider WITHOUT specifying enable_dynamic_client_registration
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                # enable_dynamic_client_registration NOT specified
                application_credential=eks_identity,
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

            # EKSWorkloadIdentity should NOT enable registration by default
            # It follows the same pattern as WebIdentity - the client should already
            # be registered with the authorization server
            assert client_config.auto_register_client is False

    @pytest.mark.asyncio
    async def test_eks_workload_identity_respects_disabled_registration(self):
        """Test that EKSWorkloadIdentity respects enable_dynamic_client_registration=False."""
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("eks-test-token")

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

            # Create EKSWorkloadIdentity
            eks_identity = EKSWorkloadIdentity(token_file_path=str(token_file))

            # Create AuthProvider with explicit enable_dynamic_client_registration=False
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                enable_dynamic_client_registration=False,  # Explicitly disabled
                application_credential=eks_identity,
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
            assert client_config.auto_register_client is False

    @pytest.mark.asyncio
    async def test_eks_workload_identity_token_exchange_has_assertion(self):
        """Test that token exchange request includes the EKS token as client_assertion."""
        zone_id = "test123"
        expected_zone_url = f"https://{zone_id}.keycard.cloud"

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            test_token = "eks-workload-identity-token-12345"
            token_file.write_text(test_token)

            # Create mock client that captures the exchange request
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

            captured_requests = []

            def mock_exchange_token(request=None, **kwargs):
                if request:
                    captured_requests.append(request)
                return TokenResponse(
                    access_token="exchanged_token",
                    token_type="Bearer",
                    expires_in=3600
                )

            mock_async_client.exchange_token.side_effect = mock_exchange_token
            mock_async_client._initialized = True
            mock_async_client._discovered_endpoints = Mock()
            mock_async_client._discovered_endpoints.token = mock_metadata.token_endpoint

            # Mock client factory
            mock_factory = Mock()
            mock_factory.create_async_client.return_value = mock_async_client

            # Create EKSWorkloadIdentity
            eks_identity = EKSWorkloadIdentity(token_file_path=str(token_file))

            # Create AuthProvider
            auth_provider = AuthProvider(
                zone_id=zone_id,
                mcp_server_name="Test Server",
                mcp_server_url="https://mcp.example.com",
                base_url="https://keycard.cloud",
                enable_multi_zone=False,
                application_credential=eks_identity,
                client_factory=mock_factory
            )

            # Trigger client creation and token exchange
            @auth_provider.grant("https://api.example.com")
            def test_function(access_ctx: AccessContext, ctx: Context):
                return {"success": True}

            mock_context = self.create_mock_context_with_auth(zone_id)
            await test_function(ctx=mock_context)

            # Verify the token exchange request included the EKS token
            assert len(captured_requests) > 0
            request = captured_requests[0]
            assert request.client_assertion == test_token
            assert request.client_assertion_type == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

    @pytest.mark.asyncio
    async def test_eks_workload_identity_runtime_error_after_init(self):
        """Test that runtime error is raised when token is deleted after initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            test_token = "eks-workload-identity-token-12345"
            token_file.write_text(test_token)

            # Create mock client
            mock_async_client = AsyncMock()
            mock_async_client._initialized = True
            mock_async_client._discovered_endpoints = Mock()
            mock_async_client._discovered_endpoints.token = "https://test.keycard.cloud/token"

            # Create EKSWorkloadIdentity (successfully initializes)
            eks_identity = EKSWorkloadIdentity(token_file_path=str(token_file))

            # Delete the token file after initialization
            token_file.unlink()

            # Should raise runtime error when trying to prepare token exchange request
            with pytest.raises(EKSWorkloadIdentityRuntimeError) as exc_info:
                await eks_identity.prepare_token_exchange_request(
                    client=mock_async_client,
                    subject_token="test_access_token",
                    resource="https://api.example.com",
                )

            assert "Failed to read EKS workload identity token at runtime" in str(exc_info.value)

