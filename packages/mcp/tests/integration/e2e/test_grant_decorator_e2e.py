"""End-to-end tests for @grant decorator.

Tests: token exchange -> AccessContext population -> error handling.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import Context

from keycardai.mcp.server.auth import (
    AccessContext,
    AuthProvider,
    MissingAccessContextError,
    MissingContextError,
    ResourceAccessError,
)
from keycardai.oauth.types.models import TokenResponse


def create_mock_context_with_auth():
    """Create mock context with authentication info for E2E tests."""
    mock_context = Mock(spec=Context)
    mock_context.request_context = Mock()
    mock_context.request_context.request = Mock()
    mock_context.request_context.request.state.keycardai_auth_info = {
        "access_token": "user_token",
        "zone_id": "e2e-test",
        "resource_client_id": "",
        "resource_server_url": "http://localhost:8000/",
    }
    return mock_context


class TestGrantDecoratorE2E:
    """End-to-end tests for grant decorator functionality."""

    @pytest.mark.asyncio
    async def test_grant_decorator_token_exchange_success(
        self, e2e_client_factory, e2e_auth_provider_config
    ):
        """Test successful token exchange through grant decorator."""
        factory, mock_async_client = e2e_client_factory

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant("https://api.example.com")
        def test_tool(access_ctx: AccessContext, ctx: Context, data: str) -> str:
            token = access_ctx.access("https://api.example.com").access_token
            return f"Got token: {token}, data: {data}"

        mock_context = create_mock_context_with_auth()

        result = await test_tool(ctx=mock_context, data="test_input")

        assert "Got token:" in result
        assert "data: test_input" in result

    @pytest.mark.asyncio
    async def test_grant_decorator_token_exchange_failure(self, e2e_auth_provider_config):
        """Test error handling when token exchange fails."""
        # Create factory with failing exchange
        factory = Mock()
        mock_sync_client = Mock()
        mock_sync_client.discover_server_metadata.return_value = Mock(
            issuer="https://e2e-test.keycard.cloud",
            jwks_uri="https://e2e-test.keycard.cloud/.well-known/jwks.json",
        )
        factory.create_client.return_value = mock_sync_client

        mock_async_client = AsyncMock()
        mock_async_client.exchange_token.side_effect = Exception("Token exchange failed")
        factory.create_async_client.return_value = mock_async_client

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant("https://api.example.com")
        def test_tool(access_ctx: AccessContext, ctx: Context) -> str:
            if access_ctx.has_errors():
                return f"Error: {access_ctx.get_errors()}"
            return "Should not reach here"

        mock_context = create_mock_context_with_auth()

        result = await test_tool(ctx=mock_context)

        assert "Error" in result
        assert "Token exchange failed" in str(result)

    @pytest.mark.asyncio
    async def test_grant_decorator_partial_success(self, e2e_auth_provider_config):
        """Test partial success scenario with multiple resources."""
        factory = Mock()
        mock_sync_client = Mock()
        mock_sync_client.discover_server_metadata.return_value = Mock(
            issuer="https://e2e-test.keycard.cloud",
            jwks_uri="https://e2e-test.keycard.cloud/.well-known/jwks.json",
        )
        factory.create_client.return_value = mock_sync_client

        mock_async_client = AsyncMock()

        async def mock_exchange(request):
            resource = request.resource if hasattr(request, "resource") else str(request)
            if "api1" in resource:
                return TokenResponse(
                    access_token="token_api1", token_type="Bearer", expires_in=3600
                )
            else:
                raise Exception("API2 token exchange failed")

        mock_async_client.exchange_token.side_effect = mock_exchange
        factory.create_async_client.return_value = mock_async_client

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant(["https://api1.example.com", "https://api2.example.com"])
        def test_tool(access_ctx: AccessContext, ctx: Context) -> dict:
            status = access_ctx.get_status()
            successful = access_ctx.get_successful_resources()
            failed = access_ctx.get_failed_resources()
            return {"status": status, "successful": successful, "failed": failed}

        mock_context = create_mock_context_with_auth()

        result = await test_tool(ctx=mock_context)

        # Verify partial success behavior
        assert result["status"] == "partial_error"
        assert "https://api1.example.com" in result["successful"]
        assert "https://api2.example.com" in result["failed"]

    def test_grant_decorator_missing_access_context(
        self, e2e_client_factory, e2e_auth_provider_config
    ):
        """Test that missing AccessContext parameter raises error."""
        factory, _ = e2e_client_factory

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        with pytest.raises(MissingAccessContextError):

            @auth_provider.grant("https://api.example.com")
            def bad_tool(ctx: Context, data: str) -> str:  # Missing AccessContext
                return data

    def test_grant_decorator_missing_context(
        self, e2e_client_factory, e2e_auth_provider_config
    ):
        """Test that missing Context parameter raises error."""
        factory, _ = e2e_client_factory

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        with pytest.raises(MissingContextError):

            @auth_provider.grant("https://api.example.com")
            def bad_tool(access_ctx: AccessContext, data: str) -> str:  # Missing Context
                return data

    @pytest.mark.asyncio
    async def test_grant_decorator_preserves_function_metadata(
        self, e2e_client_factory, e2e_auth_provider_config
    ):
        """Test that grant decorator preserves function name and docstring."""
        factory, _ = e2e_client_factory

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant("https://api.example.com")
        def my_documented_tool(access_ctx: AccessContext, ctx: Context, data: str) -> str:
            """This is my tool documentation."""
            return data

        # The wrapper should preserve the function name and docstring
        assert my_documented_tool.__name__ == "my_documented_tool"
        assert "documentation" in my_documented_tool.__doc__

    @pytest.mark.asyncio
    async def test_grant_decorator_with_no_auth_info(
        self, e2e_client_factory, e2e_auth_provider_config
    ):
        """Test grant decorator handles missing authentication info gracefully."""
        factory, _ = e2e_client_factory

        auth_provider = AuthProvider(**e2e_auth_provider_config, client_factory=factory)

        @auth_provider.grant("https://api.example.com")
        def test_tool(access_ctx: AccessContext, ctx: Context) -> str:
            if access_ctx.has_error():
                return f"Auth error: {access_ctx.get_error()}"
            return "Success"

        # Create context without auth info
        mock_context = Mock(spec=Context)
        mock_context.request_context = Mock()
        mock_context.request_context.request = Mock()
        mock_context.request_context.request.state = {}

        result = await test_tool(ctx=mock_context)

        assert "Auth error" in result
        assert "No request authentication information" in str(result)


class TestAccessContextE2E:
    """End-to-end tests for AccessContext behavior."""

    def test_access_context_token_retrieval(self):
        """Test token retrieval from AccessContext."""
        token_response = TokenResponse(
            access_token="test_token_abc", token_type="Bearer", expires_in=3600
        )

        ctx = AccessContext({"https://api.example.com": token_response})

        retrieved = ctx.access("https://api.example.com")
        assert retrieved.access_token == "test_token_abc"

    def test_access_context_missing_resource_error(self):
        """Test ResourceAccessError for missing resource."""
        ctx = AccessContext(
            {
                "https://api.example.com": TokenResponse(
                    access_token="token", token_type="Bearer"
                )
            }
        )

        with pytest.raises(ResourceAccessError):
            ctx.access("https://other.api.com")

    def test_access_context_error_states(self):
        """Test error state management in AccessContext."""
        ctx = AccessContext()

        # Initially no errors
        assert not ctx.has_errors()
        assert ctx.get_status() == "success"

        # Set resource error
        ctx.set_resource_error("https://api1.com", {"error": "Failed"})
        assert ctx.has_errors()
        assert ctx.has_resource_error("https://api1.com")
        assert ctx.get_status() == "partial_error"

        # Set global error
        ctx.set_error({"error": "Global failure"})
        assert ctx.has_error()
        assert ctx.get_status() == "error"

    def test_access_context_set_and_retrieve_token(self):
        """Test setting and retrieving tokens dynamically."""
        ctx = AccessContext()

        # Initially empty
        assert ctx.get_successful_resources() == []

        # Set token
        token = TokenResponse(access_token="dynamic_token", token_type="Bearer")
        ctx.set_token("https://api.test.com", token)

        # Verify retrieval
        assert "https://api.test.com" in ctx.get_successful_resources()
        retrieved = ctx.access("https://api.test.com")
        assert retrieved.access_token == "dynamic_token"

    def test_access_context_error_overrides_token(self):
        """Test that setting error clears previous token for resource."""
        ctx = AccessContext()

        # Set token first
        token = TokenResponse(access_token="original_token", token_type="Bearer")
        ctx.set_token("https://api.test.com", token)

        # Set error for same resource
        ctx.set_resource_error("https://api.test.com", {"error": "Now failed"})

        # Token should no longer be accessible
        with pytest.raises(ResourceAccessError):
            ctx.access("https://api.test.com")

        # Resource should be in failed list
        assert "https://api.test.com" in ctx.get_failed_resources()
        assert "https://api.test.com" not in ctx.get_successful_resources()

    def test_access_context_bulk_tokens(self):
        """Test setting multiple tokens at once."""
        token1 = TokenResponse(access_token="token1", token_type="Bearer")
        token2 = TokenResponse(access_token="token2", token_type="Bearer")

        ctx = AccessContext()
        ctx.set_bulk_tokens(
            {"https://api1.com": token1, "https://api2.com": token2}
        )

        assert ctx.access("https://api1.com").access_token == "token1"
        assert ctx.access("https://api2.com").access_token == "token2"
        assert len(ctx.get_successful_resources()) == 2
