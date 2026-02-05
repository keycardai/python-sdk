"""End-to-end tests for MCP server AuthProvider.

Tests: AuthProvider init -> JWT verifier creation -> tool invocation.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import Context

from keycardai.mcp.server.auth import (
    AccessContext,
    AuthProvider,
    AuthProviderConfigurationError,
)
from keycardai.oauth.types.models import TokenResponse


def create_mock_context_with_auth_info(
    access_token: str = "user_jwt_token",
    zone_id: str = "e2e-test",
    resource_client_id: str = "",
    resource_server_url: str = "http://localhost:8000/",
):
    """Helper function to create a mock Context with authentication info."""
    mock_context = Mock(spec=Context)
    mock_context.request_context = Mock()
    mock_context.request_context.request = Mock()
    mock_context.request_context.request.state.keycardai_auth_info = {
        "access_token": access_token,
        "zone_id": zone_id,
        "resource_client_id": resource_client_id,
        "resource_server_url": resource_server_url,
    }
    return mock_context


class TestAuthProviderE2EFlow:
    """End-to-end tests for AuthProvider initialization to tool execution."""

    @pytest.mark.asyncio
    async def test_authprovider_init_to_tool_invocation(
        self, e2e_client_factory, e2e_auth_provider_config
    ):
        """Test complete flow from AuthProvider init to successful tool invocation."""
        factory, mock_async_client = e2e_client_factory

        # Step 1: Initialize AuthProvider
        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        # Step 2: Verify JWT verifier can be created
        verifier = auth_provider.get_token_verifier()
        assert verifier is not None
        # Note: AuthProvider.required_scopes defaults to None, verifier converts to empty list
        assert verifier.required_scopes == (auth_provider.required_scopes or [])

        # Step 3: Create a tool with grant decorator
        @auth_provider.grant("https://api.e2e-test.com")
        def e2e_tool(access_ctx: AccessContext, ctx: Context, query: str) -> str:
            if access_ctx.has_errors():
                return f"Error: {access_ctx.get_errors()}"
            token = access_ctx.access("https://api.e2e-test.com").access_token
            return f"Success with token: {token}"

        # Step 4: Create mock context with auth info
        mock_context = create_mock_context_with_auth_info()

        # Step 5: Execute the tool
        result = await e2e_tool(ctx=mock_context, query="test query")

        # Step 6: Verify complete flow succeeded
        assert "Success with token" in result
        assert "e2e_token_for_api_e2e-test_com" in result

    @pytest.mark.asyncio
    async def test_authprovider_multi_resource_grant(
        self, e2e_client_factory, e2e_auth_provider_config
    ):
        """Test AuthProvider with multiple resource grants."""
        factory, mock_async_client = e2e_client_factory

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant(["https://api1.e2e-test.com", "https://api2.e2e-test.com"])
        def multi_resource_tool(access_ctx: AccessContext, ctx: Context) -> dict:
            results = {}
            for resource in ["https://api1.e2e-test.com", "https://api2.e2e-test.com"]:
                if access_ctx.has_resource_error(resource):
                    results[resource] = "error"
                else:
                    results[resource] = access_ctx.access(resource).access_token
            return results

        mock_context = create_mock_context_with_auth_info()

        result = await multi_resource_tool(ctx=mock_context)

        assert "e2e_token_for_api1_e2e-test_com" in result["https://api1.e2e-test.com"]
        assert "e2e_token_for_api2_e2e-test_com" in result["https://api2.e2e-test.com"]

    @pytest.mark.asyncio
    async def test_authprovider_async_tool(self, e2e_client_factory, e2e_auth_provider_config):
        """Test AuthProvider with async tool function."""
        factory, mock_async_client = e2e_client_factory

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant("https://api.e2e-test.com")
        async def async_e2e_tool(access_ctx: AccessContext, ctx: Context, data: str) -> str:
            if access_ctx.has_errors():
                return f"Error: {access_ctx.get_errors()}"
            token = access_ctx.access("https://api.e2e-test.com").access_token
            return f"Async success: {data}, token: {token}"

        mock_context = create_mock_context_with_auth_info()

        result = await async_e2e_tool(ctx=mock_context, data="test_data")

        assert "Async success: test_data" in result
        assert "e2e_token_for_api_e2e-test_com" in result

    def test_authprovider_missing_zone_configuration(self):
        """Test AuthProvider raises appropriate errors for missing zone config."""
        with pytest.raises(AuthProviderConfigurationError):
            AuthProvider(
                mcp_server_name="Test Server", mcp_server_url="http://localhost:8000/"
            )

    def test_authprovider_with_explicit_zone_url(self):
        """Test AuthProvider accepts explicit zone_url instead of zone_id."""
        # Should not raise - using zone_url directly
        auth_provider = AuthProvider(
            zone_url="https://custom.keycard.cloud",
            mcp_server_name="Test Server",
            mcp_server_url="http://localhost:8000/",
        )

        assert auth_provider.zone_url == "https://custom.keycard.cloud"

    @pytest.mark.asyncio
    async def test_authprovider_token_exchange_failure(self, e2e_auth_provider_config):
        """Test AuthProvider handles token exchange failures gracefully."""
        # Create factory with failing async client
        factory = Mock()
        mock_sync_client = Mock()
        mock_sync_client.discover_server_metadata.return_value = Mock(
            issuer="https://e2e-test.keycard.cloud",
            authorization_endpoint="https://e2e-test.keycard.cloud/auth",
            token_endpoint="https://e2e-test.keycard.cloud/token",
            jwks_uri="https://e2e-test.keycard.cloud/.well-known/jwks.json",
        )
        factory.create_client.return_value = mock_sync_client

        mock_async_client = AsyncMock()
        mock_async_client.exchange_token.side_effect = Exception("Token exchange failed")
        factory.create_async_client.return_value = mock_async_client

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant("https://api.e2e-test.com")
        def failing_tool(access_ctx: AccessContext, ctx: Context) -> str:
            if access_ctx.has_errors():
                errors = access_ctx.get_errors()
                return f"Error occurred: {errors}"
            return "Should not reach here"

        mock_context = create_mock_context_with_auth_info()

        result = await failing_tool(ctx=mock_context)

        assert "Error occurred" in result
        assert "Token exchange failed" in str(result)

    @pytest.mark.asyncio
    async def test_authprovider_partial_token_exchange_failure(
        self, e2e_auth_provider_config
    ):
        """Test AuthProvider handles partial failures with multiple resources."""
        # Create factory where one resource fails
        factory = Mock()
        mock_sync_client = Mock()
        mock_sync_client.discover_server_metadata.return_value = Mock(
            issuer="https://e2e-test.keycard.cloud",
            authorization_endpoint="https://e2e-test.keycard.cloud/auth",
            token_endpoint="https://e2e-test.keycard.cloud/token",
            jwks_uri="https://e2e-test.keycard.cloud/.well-known/jwks.json",
        )
        factory.create_client.return_value = mock_sync_client

        mock_async_client = AsyncMock()
        call_count = [0]

        async def mock_exchange(request):
            call_count[0] += 1
            resource = request.resource if hasattr(request, "resource") else str(request)
            if "api1" in resource:
                return TokenResponse(
                    access_token="token_for_api1", token_type="Bearer", expires_in=3600
                )
            else:
                raise Exception("API2 exchange failed")

        mock_async_client.exchange_token.side_effect = mock_exchange
        factory.create_async_client.return_value = mock_async_client

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant(["https://api1.e2e-test.com", "https://api2.e2e-test.com"])
        def partial_tool(access_ctx: AccessContext, ctx: Context) -> dict:
            return {
                "status": access_ctx.get_status(),
                "has_errors": access_ctx.has_errors(),
                "successful": access_ctx.get_successful_resources(),
                "failed": access_ctx.get_failed_resources(),
            }

        mock_context = create_mock_context_with_auth_info()

        result = await partial_tool(ctx=mock_context)

        # Verify partial success/failure handling
        assert result["has_errors"] is True
        assert "https://api1.e2e-test.com" in result["successful"]
        assert "https://api2.e2e-test.com" in result["failed"]


class TestAuthProviderVerifier:
    """Tests for AuthProvider JWT verifier creation."""

    def test_verifier_creation_with_default_scopes(self, e2e_auth_provider_config):
        """Test verifier is created with default empty scopes."""
        factory = Mock()
        mock_sync_client = Mock()
        mock_sync_client.discover_server_metadata.return_value = Mock(
            issuer="https://e2e-test.keycard.cloud",
            jwks_uri="https://e2e-test.keycard.cloud/.well-known/jwks.json",
        )
        factory.create_client.return_value = mock_sync_client

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        verifier = auth_provider.get_token_verifier()

        assert verifier is not None
        assert verifier.required_scopes == []

    def test_verifier_creation_with_custom_scopes(self, e2e_auth_provider_config):
        """Test verifier is created with custom required scopes."""
        factory = Mock()
        mock_sync_client = Mock()
        mock_sync_client.discover_server_metadata.return_value = Mock(
            issuer="https://e2e-test.keycard.cloud",
            jwks_uri="https://e2e-test.keycard.cloud/.well-known/jwks.json",
        )
        factory.create_client.return_value = mock_sync_client

        config = {**e2e_auth_provider_config, "required_scopes": ["read", "write"]}
        auth_provider = AuthProvider(**config, client_factory=factory)

        verifier = auth_provider.get_token_verifier()

        assert verifier is not None
        assert verifier.required_scopes == ["read", "write"]
