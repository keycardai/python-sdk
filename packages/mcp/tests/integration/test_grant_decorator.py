"""Integration tests for grant decorator interface.

This module tests the grant decorator which is one of the core interfaces
in the mcp package. It tests the complete flow of token exchange
and context injection for both sync and async functions.
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


def check_access_context_for_errors(access_ctx: AccessContext, resource: str = None):
    """Helper function to check AccessContext for errors and return error dict if found.

    Args:
        access_ctx: The AccessContext object
        resource: Optional specific resource to check for errors

    Returns:
        dict: Error dictionary if error found, None otherwise
    """
    # Check for global error first
    if access_ctx.has_error():
        error = access_ctx.get_error()
        return {"error": error["error"], "isError": True}

    # Check for resource-specific error if resource specified
    if resource and access_ctx.has_resource_error(resource):
        error = access_ctx.get_resource_error(resource)
        return {"error": error["error"], "isError": True}

    return None

def create_missing_auth_info_context():
    """Helper function to create a mock Context with missing auth info."""
    mock_context = Mock(spec=Context)
    mock_context.request_context = Mock()
    mock_context.request_context.request = Mock()
    mock_context.request_context.request.state = {}
    return mock_context

def create_mock_context():
    """Helper function to create a mock Context with proper state management."""
    mock_context = Mock(spec=Context)

    # Create a state storage for the mock context
    context_state = {
        "access_token": "test_token",
        "zone_id": "test123",
        "resource_client_id": "https://api.example.com",
        "resource_server_url": "https://api.example.com"
    }

    mock_context.request_context = Mock()
    mock_context.request_context.request = Mock()
    mock_context.request_context.request.state.keycardai_auth_info = context_state

    return mock_context


class TestGrantDecoratorExecution:
    """Test grant decorator execution and token exchange."""

    @pytest.mark.asyncio
    async def test_grant_decorator_missing_access_context(self, auth_provider_config, mock_client_factory):
        """Test grant decorator handles missing AccessContext parameter."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        with pytest.raises(MissingAccessContextError):
            @auth_provider.grant("https://api.example.com")
            def test_function(ctx: Context, user_id: str) -> str:  # No AccessContext parameter
                return f"Hello {user_id}"

    @pytest.mark.asyncio
    async def test_grant_decorator_missing_context(self, auth_provider_config, mock_client_factory):
        """Test grant decorator handles missing Context parameter."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        with pytest.raises(MissingContextError):
            @auth_provider.grant("https://api.example.com")
            def test_function(access_ctx: AccessContext, user_id: str) -> str:  # No Context parameter
                return f"Hello {user_id}"

    @pytest.mark.asyncio
    async def test_grant_decorator_missing_auth_token(self, auth_provider_config, mock_client_factory):
        """Test grant decorator handles missing authentication token."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str):
            # Check if there's an error in the access context
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"], "isError": True}
            return f"Hello {user_id}"

        mock_context = create_missing_auth_info_context()

        result = await test_function(ctx=mock_context, user_id="user123")

        assert result["isError"] is True
        assert "No request authentication information available" in result["error"]

    @pytest.mark.asyncio
    async def test_grant_decorator_token_exchange_failure_with_injected_client(self, auth_provider_config):
        """Test grant decorator handles token exchange failure with injected client."""
        # Mock client with failing exchange_token
        mock_client = AsyncMock()
        mock_client.exchange_token.side_effect = Exception("Exchange failed")
        mock_client_factory = Mock()
        mock_client_factory.create_async_client.return_value = mock_client

        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str):
            # Check if there's a resource error
            if access_ctx.has_resource_error("https://api.example.com"):
                error = access_ctx.get_resource_errors("https://api.example.com")
                return {"error": error["error"], "isError": True}
            return {"error": "No error", "isError": False, "access_ctx": access_ctx}

        mock_context = create_mock_context()

        result = await test_function(ctx=mock_context, user_id="user123")

        assert result["error"] == "Token exchange failed for https://api.example.com: Exchange failed"
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_grant_decorator_successful_sync_function_with_injected_client(self, auth_provider_config, mock_client_factory):
        """Test grant decorator with successful token exchange for sync function using injected client."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api1.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str):
            # Access the token through AccessContext
            token = access_ctx.access("https://api1.example.com").access_token
            return f"Hello {user_id}, token: {token}"

        mock_context = create_mock_context()

        result = await test_function(ctx=mock_context, user_id="user123")

        # Verify function executed successfully
        assert result == "Hello user123, token: token_api1_123"

    @pytest.mark.asyncio
    async def test_grant_decorator_successful_async_function_with_injected_client(self, auth_provider_config, mock_client_factory):
        """Test grant decorator with successful token exchange for async function using injected client."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api1.example.com")
        async def test_async_function(access_ctx: AccessContext, ctx: Context, user_id: str):
            # Access the token through AccessContext
            token = access_ctx.access("https://api1.example.com").access_token
            return f"Async Hello {user_id}, token: {token}"

        mock_context = create_mock_context()

        result = await test_async_function(ctx=mock_context, user_id="user123")

        # Verify function executed successfully
        assert result == "Async Hello user123, token: token_api1_123"

    @pytest.mark.asyncio
    async def test_grant_decorator_multiple_resources_success_with_injected_client(self, auth_provider_config, mock_client_factory):
        """Test grant decorator with multiple resources successful token exchange using injected client."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant(["https://api1.example.com", "https://api2.example.com"])
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str):
            # Access tokens for both resources
            token1 = access_ctx.access("https://api1.example.com").access_token
            token2 = access_ctx.access("https://api2.example.com").access_token
            return f"Hello {user_id}, token1: {token1}, token2: {token2}"

        mock_context = create_mock_context()

        result = await test_function(ctx=mock_context, user_id="user123")

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
