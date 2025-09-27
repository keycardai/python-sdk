"""Integration tests for grant decorator interface.

This module tests the grant decorator which is one of the core interfaces
in the mcp-fastmcp package. It tests the complete flow of token exchange
and context injection for both sync and async functions.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastmcp import Context
from fastmcp.server.dependencies import AccessToken

from keycardai.mcp.integrations.fastmcp import (
    AccessContext,
    AuthProvider,
    ResourceAccessError,
)
from keycardai.mcp.server.exceptions import MissingContextError
from keycardai.oauth.types.models import TokenResponse


def check_access_context_for_errors(ctx: Context, resource: str = None):
    """Helper function to check AccessContext for errors and return error dict if found.

    Args:
        ctx: The FastMCP Context object
        resource: Optional specific resource to check for errors

    Returns:
        dict: Error dictionary if error found, None otherwise
    """
    access_ctx = ctx.get_state("keycardai")

    # Check for global error first
    if access_ctx.has_error():
        error = access_ctx.get_error()
        return {"error": error["error"], "isError": True}

    # Check for resource-specific error if resource specified
    if resource and access_ctx.has_resource_error(resource):
        error = access_ctx.get_resource_error(resource)
        return {"error": error["error"], "isError": True}

    return None


def create_mock_context():
    """Helper function to create a mock Context with proper state management."""
    mock_context = Mock(spec=Context)

    # Create a state storage for the mock context
    context_state = {}

    def mock_set_state(key: str, value):
        context_state[key] = value

    def mock_get_state(key: str):
        return context_state.get(key)

    mock_context.set_state = mock_set_state
    mock_context.get_state = mock_get_state

    return mock_context


class TestGrantDecoratorExecution:
    """Test grant decorator execution and token exchange."""

    @pytest.mark.asyncio
    async def test_grant_decorator_missing_context(self, auth_provider_config, mock_client_factory):
        """Test grant decorator handles missing Context parameter."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        with pytest.raises(MissingContextError):
            @auth_provider.grant("https://api.example.com")
            def test_function(user_id: str) -> str:  # No Context parameter
                return f"Hello {user_id}"

    @pytest.mark.asyncio
    @patch('keycardai.mcp.integrations.fastmcp.provider.get_access_token')
    async def test_grant_decorator_missing_auth_token(self, mock_get_token, auth_provider_config, mock_client_factory):
        """Test grant decorator handles missing authentication token."""
        mock_get_token.return_value = None

        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(ctx: Context, user_id: str):
            # Check if there's an error in the context
            access_ctx = ctx.get_state("keycardai")
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"], "isError": True}
            return f"Hello {user_id}"

        mock_context = create_mock_context()

        result = await test_function(mock_context, "user123")

        assert result["isError"] is True
        assert "No authentication token available" in result["error"]

    @pytest.mark.asyncio
    @patch('keycardai.mcp.integrations.fastmcp.provider.get_access_token')
    async def test_grant_decorator_token_exchange_failure_with_injected_client(self, mock_get_token, auth_provider_config, mock_client_factory):
        """Test grant decorator handles token exchange failure with injected client."""
        mock_get_token.return_value = AccessToken(token="test_token", client_id="test_client", scopes=["test_scope"])

        # Mock client with failing exchange_token
        mock_client = AsyncMock()
        mock_client.exchange_token.side_effect = Exception("Exchange failed")

        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )
        # Override with failing client
        auth_provider.client = mock_client

        @auth_provider.grant("https://api.example.com")
        def test_function(ctx: Context, user_id: str):
            # Check if there's a resource error
            access_ctx = ctx.get_state("keycardai")
            if access_ctx.has_resource_error("https://api.example.com"):
                error = access_ctx.get_resource_errors("https://api.example.com")
                return {"error": error["error"], "isError": True}
            return {"error": "No error", "isError": False, "access_ctx": access_ctx}

        mock_context = create_mock_context()

        result = await test_function(mock_context, "user123")

        assert result["error"] == "Token exchange failed for https://api.example.com: Exchange failed"
        assert result["isError"] is True

    @pytest.mark.asyncio
    @patch('keycardai.mcp.integrations.fastmcp.provider.get_access_token')
    async def test_grant_decorator_successful_sync_function_with_injected_client(self, mock_get_token, auth_provider_config, mock_client_factory):
        """Test grant decorator with successful token exchange for sync function using injected client."""
        mock_get_token.return_value = AccessToken(token="test_token", client_id="test_client", scopes=["test_scope"])

        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(ctx: Context, user_id: str):
            # Access the token through context
            token = ctx.get_state("keycardai").access("https://api.example.com").access_token
            return f"Hello {user_id}, token: {token}"

        mock_context = create_mock_context()

        result = await test_function(mock_context, "user123")

        # Verify context was set with AccessContext
        keycardai_context = mock_context.get_state("keycardai")
        assert keycardai_context is not None
        assert isinstance(keycardai_context, AccessContext)

        # Verify function executed successfully
        assert result == "Hello user123, token: exchanged_token_123"

    @pytest.mark.asyncio
    @patch('keycardai.mcp.integrations.fastmcp.provider.get_access_token')
    async def test_grant_decorator_successful_async_function_with_injected_client(self, mock_get_token, auth_provider_config, mock_client_factory):
        """Test grant decorator with successful token exchange for async function using injected client."""
        mock_get_token.return_value = AccessToken(token="test_token", client_id="test_client", scopes=["test_scope"])

        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        async def test_async_function(ctx: Context, user_id: str):
            # Access the token through context
            token = ctx.get_state("keycardai").access("https://api.example.com").access_token
            return f"Async Hello {user_id}, token: {token}"

        mock_context = create_mock_context()

        result = await test_async_function(mock_context, "user123")

        # Verify function executed successfully
        assert result == "Async Hello user123, token: exchanged_token_123"

    @pytest.mark.asyncio
    @patch('keycardai.mcp.integrations.fastmcp.provider.get_access_token')
    async def test_grant_decorator_multiple_resources_success_with_injected_client(self, mock_get_token, auth_provider_config, mock_client_factory):
        """Test grant decorator with multiple resources successful token exchange using injected client."""
        mock_get_token.return_value = AccessToken(token="test_token", client_id="test_client", scopes=["test_scope"])

        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant(["https://api1.example.com", "https://api2.example.com"])
        def test_function(ctx: Context, user_id: str):
            # Access tokens for both resources
            token1 = ctx.get_state("keycardai").access("https://api1.example.com").access_token
            token2 = ctx.get_state("keycardai").access("https://api2.example.com").access_token
            return f"Hello {user_id}, token1: {token1}, token2: {token2}"

        mock_context = create_mock_context()

        result = await test_function(mock_context, "user123")

        # Verify function executed successfully with both tokens
        assert result == "Hello user123, token1: token_api1_123, token2: token_api2_456"


class TestAccessContext:
    """Test AccessContext functionality used by grant decorator."""

    def test_access_context_single_token(self):
        """Test AccessContext with single token."""
        token_response = TokenResponse(
            access_token="test_token_123",
            token_type="Bearer",
            expires_in=3600
        )

        access_context = AccessContext({
            "https://api.example.com": token_response
        })

        retrieved_token = access_context.access("https://api.example.com")
        assert retrieved_token == token_response
        assert retrieved_token.access_token == "test_token_123"

    def test_access_context_multiple_tokens(self):
        """Test AccessContext with multiple tokens."""
        token_response_1 = TokenResponse(
            access_token="token_1",
            token_type="Bearer",
            expires_in=3600
        )
        token_response_2 = TokenResponse(
            access_token="token_2",
            token_type="Bearer",
            expires_in=7200
        )

        access_context = AccessContext({
            "https://api1.example.com": token_response_1,
            "https://api2.example.com": token_response_2
        })

        assert access_context.access("https://api1.example.com") == token_response_1
        assert access_context.access("https://api2.example.com") == token_response_2

    def test_access_context_missing_resource(self):
        """Test AccessContext raises ResourceAccessError for missing resource."""
        token_response = TokenResponse(
            access_token="test_token_123",
            token_type="Bearer",
            expires_in=3600
        )

        access_context = AccessContext({
            "https://api.example.com": token_response
        })

        with pytest.raises(ResourceAccessError):
            access_context.access("https://missing.com")

    def test_access_context_error_states(self):
        """Test AccessContext error state management."""
        access_context = AccessContext()

        # Test global error
        access_context.set_error({"error": "Global failure"})
        assert access_context.has_error()
        assert access_context.get_status() == "error"
        assert access_context.get_error()["error"] == "Global failure"

        # Test resource error
        access_context = AccessContext()
        access_context.set_resource_error("https://api1.com", {
            "error": "Resource failed",
        })
        assert access_context.has_resource_error("https://api1.com")
        assert access_context.get_status() == "partial_error"
        assert access_context.get_resource_errors("https://api1.com")["error"] == "Resource failed"

    def test_access_context_partial_success(self):
        """Test AccessContext with partial success scenario."""
        token_response = TokenResponse(
            access_token="success_token",
            token_type="Bearer",
            expires_in=3600
        )

        access_context = AccessContext()
        access_context.set_token("https://api1.com", token_response)
        access_context.set_resource_error("https://api2.com", {
            "error": "Failed to get token",
        })

        # Check status
        assert access_context.get_status() == "partial_error"
        assert access_context.has_errors()
        assert not access_context.has_error()  # No global error
        assert access_context.has_resource_error("https://api2.com")

        # Check successful resources
        successful = access_context.get_successful_resources()
        failed = access_context.get_failed_resources()
        assert "https://api1.com" in successful
        assert "https://api2.com" in failed

        # Access successful resource
        token = access_context.access("https://api1.com")
        assert token.access_token == "success_token"

        # Access failed resource should raise error
        with pytest.raises(ResourceAccessError):
            access_context.access("https://api2.com")


class TestGrantDecoratorIntegration:
    """Integration tests for grant decorator end-to-end functionality."""

    @pytest.mark.asyncio
    @patch('keycardai.mcp.integrations.fastmcp.provider.get_access_token')
    async def test_full_grant_decorator_flow_with_injected_client(self, mock_get_token, auth_provider_config, mock_client_factory):
        """Test complete grant decorator flow from decoration to execution using injected client."""
        # Setup mocks
        mock_get_token.return_value = AccessToken(token="user_jwt_token", client_id="test_client", scopes=["test_scope"])

        # Create AuthProvider and apply decorator
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.integration.com")
        def integration_tool(ctx: Context, query: str):
            """Integration test tool that uses delegated token."""
            token = ctx.get_state("keycardai").access("https://api.integration.com").access_token
            return {
                "query": query,
                "token": token,
                "status": "success"
            }

        # Create mock context
        mock_context = create_mock_context()

        # Execute the decorated function
        result = await integration_tool(mock_context, "test query")

        # Verify complete flow
        assert result["query"] == "test query"
        assert result["token"] == "delegated_access_token"
        assert result["status"] == "success"

        # Verify the integration flow worked correctly

        # Verify context state was set correctly
        keycardai_context = mock_context.get_state("keycardai")
        assert keycardai_context is not None
        assert isinstance(keycardai_context, AccessContext)
