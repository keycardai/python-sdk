"""Shared test fixtures for auth subsystem tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from keycardai.mcp.client.auth.handlers import CompletionHandlerRegistry


@pytest.fixture
def isolated_completion_registry():
    """
    Create an isolated completion handler registry for testing.

    This registry is completely separate from the global default registry,
    preventing test pollution and enabling parallel test execution.

    Example:
        def test_my_feature(isolated_completion_registry):
            @isolated_completion_registry.register("my_handler")
            async def my_handler(coordinator, storage, params):
                return {"success": True}

            # Test using isolated registry...
    """
    registry = CompletionHandlerRegistry()

    # Register a test OAuth completion handler
    @registry.register("oauth_completion")
    async def test_oauth_completion(coordinator, storage, params):
        """Test OAuth completion handler."""
        code = params.get("code")
        state = params.get("state")

        if not code:
            raise ValueError("Missing authorization code in completion")
        if not state:
            raise ValueError("Missing state in completion")

        # Get PKCE state from storage
        pkce_state = await storage.get(f"_pkce_state:{state}")
        if not pkce_state:
            raise ValueError("PKCE state not found or expired")

        # Mock token storage
        await storage.set("tokens", {
            "access_token": "test_token",
            "token_type": "Bearer",
            "expires_in": 3600
        })

        # Clean up PKCE state
        await storage.delete(f"_pkce_state:{state}")

        # Clear auth pending
        server_name = pkce_state.get("server_name", "unknown")
        namespace_parts = storage._namespace.split(":")
        context_id = namespace_parts[1] if len(namespace_parts) > 1 else "unknown"
        await coordinator.clear_auth_pending(context_id=context_id, server_name=server_name)

        return {"success": True, "server_name": server_name}

    # Set up a default mock client factory
    def mock_client_factory():
        """Mock HTTP client factory for tests."""
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # Mock successful token exchange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "access_token": "test_token",
            "token_type": "Bearer",
            "expires_in": 3600
        })

        mock_client.post = AsyncMock(return_value=mock_response)
        return mock_client

    registry.set_client_factory(mock_client_factory)

    return registry

