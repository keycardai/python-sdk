"""Unit tests for the OAuthStrategy authentication strategy.

This module tests the OAuthStrategy class, focusing on:
- Initialization with server and client names
- Getting authentication metadata from storage
- Handling 401 challenges and initiating OAuth flows
- OAuth service integration
- Error handling and edge cases

Note: Tests focus on strategy-specific logic and public API.
Phase 1 refactored OAuthStrategy to use service-based architecture.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

from keycardai.mcp.client.auth.handlers import oauth_completion_handler
from keycardai.mcp.client.auth.strategies.oauth import OAuthStrategy
from keycardai.mcp.client.context import Context
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage

# Sentinel to explicitly signal "no callback URIs"
_NO_CALLBACK_URIS = object()


# Helper functions for creating test dependencies
def create_test_storage() -> tuple[InMemoryBackend, NamespacedStorage, NamespacedStorage]:
    """Create backend, connection storage, and strategy storage for testing."""
    backend = InMemoryBackend()
    # Simulate the hierarchy: client:user -> server:name -> connection -> oauth
    client_storage = NamespacedStorage(backend, "client:test_user")
    server_storage = client_storage.get_namespace("server:slack")
    connection_storage = server_storage.get_namespace("connection")
    strategy_storage = connection_storage.get_namespace("oauth")
    return backend, connection_storage, strategy_storage


def create_test_context(coordinator: "MockCoordinator") -> Context:
    """Create test context."""
    backend = InMemoryBackend()
    client_storage = NamespacedStorage(backend, "client:test_user")
    return Context(id="test_user", storage=client_storage, coordinator=coordinator)


# Mock coordinator for testing
class MockCoordinator:
    """Mock coordinator for testing OAuth strategy."""

    def __init__(self, redirect_uris: list[str] | None | object = None):
        if redirect_uris is _NO_CALLBACK_URIS:
            self.redirect_uris = None
        elif redirect_uris is None:
            self.redirect_uris = ["http://localhost:8080/callback"]
        else:
            self.redirect_uris = redirect_uris
        self.registered_routes = {}
        self.handle_redirect_calls = []
        self.auth_pending = {}  # Track auth pending state
        # Mock storage for callback cleanup
        backend = InMemoryBackend()
        self.storage = NamespacedStorage(backend, "auth_coordinator")
        self._requires_synchronous_cleanup = False

    @property
    def requires_synchronous_cleanup(self) -> bool:
        """Mock property for cleanup behavior."""
        return self._requires_synchronous_cleanup

    async def get_redirect_uris(self) -> list[str] | None:
        """Return mock redirect URIs."""
        return self.redirect_uris

    async def register_completion_route(
        self,
        routing_key: str,
        handler_name: str,
        storage_namespace: str,
        context_id: str,
        server_name: str,
        routing_param: str = "state",
        handler_kwargs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ttl: Any | None = None
    ):
        """Store registered completion route."""
        self.registered_routes[routing_key] = {
            "handler_name": handler_name,
            "storage_namespace": storage_namespace,
            "context_id": context_id,
            "server_name": server_name,
            "routing_param": routing_param,
            "handler_kwargs": handler_kwargs or {},
            "metadata": metadata or {},
            "ttl": ttl,
        }

    async def handle_redirect(self, authorization_url: str, metadata: dict[str, Any]):
        """Track redirect calls."""
        self.handle_redirect_calls.append(
            {"url": authorization_url, "metadata": metadata}
        )

    async def set_auth_pending(
        self,
        context_id: str,
        server_name: str,
        auth_metadata: dict[str, Any],
        ttl: Any | None = None
    ) -> None:
        """Store auth pending state."""
        key = f"{context_id}:{server_name}"
        self.auth_pending[key] = auth_metadata

    async def get_auth_pending(
        self,
        context_id: str,
        server_name: str
    ) -> dict[str, Any] | None:
        """Get auth pending state."""
        key = f"{context_id}:{server_name}"
        return self.auth_pending.get(key)

    async def clear_auth_pending(
        self,
        context_id: str,
        server_name: str
    ) -> None:
        """Clear auth pending state."""
        key = f"{context_id}:{server_name}"
        self.auth_pending.pop(key, None)


def create_mock_http_client(
    get_responses: dict[str, Any] | None = None, post_responses: list[Any] | None = None
):
    """
    Create a mock HTTP client factory for testing.

    Args:
        get_responses: Dict mapping URL patterns to mock responses or a callable
        post_responses: List of mock responses for POST requests (returned in order)

    Returns:
        Factory function that returns a mock AsyncClient
    """
    get_responses = get_responses or {}
    post_responses = post_responses or []
    post_call_count = [0]  # Use list to allow mutation in nested function

    def factory():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async def mock_get(url):
            # Try exact match first
            if url in get_responses:
                response = get_responses[url]
                return response() if callable(response) else response

            # Try pattern matching
            for pattern, response in get_responses.items():
                if pattern in url:
                    return response() if callable(response) else response

            # Default error
            raise ValueError(f"Unexpected GET to {url}")

        async def mock_post(url, **kwargs):
            if post_call_count[0] < len(post_responses):
                response = post_responses[post_call_count[0]]
                post_call_count[0] += 1
                return response() if callable(response) else response
            raise ValueError(f"Unexpected POST to {url}")

        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_client.post = AsyncMock(side_effect=mock_post)

        return mock_client

    return factory


class TestOAuthStrategyInitialization:
    """Test OAuthStrategy initialization with various configurations."""

    def test_initialization_with_server_name_only(self):
        """Test that strategy initializes with just server name."""
        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
        )

        assert strategy.server_name == "slack"
        # Services should be initialized
        assert strategy.discovery is not None
        assert strategy.registration is not None
        assert strategy.flow_initiator is not None
        assert strategy.token_exchanger is not None
        assert strategy.oauth_storage is not None

    def test_initialization_with_custom_client_name(self):
        """Test that strategy uses provided client name."""
        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            client_name="Custom Client",
        )

        assert strategy.server_name == "slack"
        # Client name is used by registration service
        assert strategy.registration.client_name == "Custom Client"

    def test_initialization_with_various_server_names(self):
        """Test initialization with different server name formats."""
        test_cases = [
            "slack",
            "github-api",
            "my_server",
            "server.example.com",
            "CamelCaseServer",
        ]

        coordinator = MockCoordinator()
        context = create_test_context(coordinator)

        for server_name in test_cases:
            _, connection_storage, strategy_storage = create_test_storage()
            strategy = OAuthStrategy(
                server_name=server_name,
                storage=strategy_storage,
                context=context,
                coordinator=coordinator,
            )
            assert strategy.server_name == server_name


class TestOAuthStrategyGetAuthMetadata:
    """Test OAuthStrategy.get_auth_metadata method."""

    @pytest.mark.asyncio
    async def test_get_auth_metadata_with_access_token(self):
        """Test that get_auth_metadata returns Bearer token header when available."""
        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
        )

        # Store access token using oauth_storage facade
        await strategy.oauth_storage.save_tokens({"access_token": "test_token_123"})

        metadata = await strategy.get_auth_metadata()

        assert "headers" in metadata
        assert "Authorization" in metadata["headers"]
        assert metadata["headers"]["Authorization"] == "Bearer test_token_123"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tokens_to_store",
        [
            None,  # No tokens stored
            {},  # Empty tokens dict
            {"refresh_token": "refresh_123"},  # Tokens without access_token
        ],
    )
    async def test_get_auth_metadata_without_access_token(self, tokens_to_store):
        """Test that get_auth_metadata returns empty dict when access_token is not available."""
        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
        )

        if tokens_to_store is not None:
            await strategy.oauth_storage.save_tokens(tokens_to_store)

        metadata = await strategy.get_auth_metadata()

        assert metadata == {}

    @pytest.mark.asyncio
    async def test_get_auth_metadata_preserves_token_format(self):
        """Test that get_auth_metadata uses token exactly as stored."""
        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
        )

        test_tokens = [
            "simple_token",
            "token.with.dots",
            "token_with_underscores",
            "VeryLongToken" + "x" * 100,
        ]

        for token in test_tokens:
            await strategy.oauth_storage.save_tokens({"access_token": token})
            metadata = await strategy.get_auth_metadata()
            assert metadata["headers"]["Authorization"] == f"Bearer {token}"


class TestOAuthStrategyHandleChallenge:
    """Test OAuthStrategy.handle_challenge method."""

    @pytest.mark.asyncio
    async def test_handle_challenge_ignores_non_401_responses(self):
        """Test that handle_challenge returns False for non-401 responses."""
        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
        )

        # Create 200 OK response
        response = MagicMock(spec=Response)
        response.status_code = 200

        result = await strategy.handle_challenge(
            response, "http://example.com/resource"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_handle_challenge_ignores_non_response_objects(self):
        """Test that handle_challenge returns False for non-Response objects."""
        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
        )

        # Test with various non-Response objects
        test_cases = [
            {"status": 401},  # dict
            "401 Unauthorized",  # string
            Exception("401"),  # exception
            None,  # None
        ]

        for challenge in test_cases:
            result = await strategy.handle_challenge(
                challenge, "http://example.com/resource"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_handle_challenge_returns_false_on_discovery_error(self):
        """Test that handle_challenge returns False when discovery fails."""

        # Mock client factory that raises error
        def failing_factory():
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get = AsyncMock(side_effect=Exception("Discovery failed"))
            return mock_client

        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            client_factory=failing_factory,
        )

        # Mock 401 response
        response = MagicMock(spec=Response)
        response.status_code = 401
        response.url = MagicMock()
        response.url.scheme = "https"
        response.url.netloc = b"example.com"

        result = await strategy.handle_challenge(
            response, "http://example.com/resource"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_handle_challenge_successful_flow(self):
        """Test handle_challenge completes full OAuth flow successfully."""

        # Mock resource discovery response
        def resource_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={"authorization_servers": ["https://auth.example.com"]}
            )
            return resp

        # Mock auth server discovery response
        def auth_server_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "issuer": "https://auth.example.com",
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                }
            )
            return resp

        # Mock client registration response
        def registration_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "client_id": "client_123",
                    "client_name": "MCP Client - slack",
                    "redirect_uris": ["http://localhost:8080/callback"],
                }
            )
            return resp

        # Create mock client factory
        client_factory = create_mock_http_client(
            get_responses={
                "oauth-protected-resource": resource_response,
                "oauth-authorization-server": auth_server_response,
            },
            post_responses=[registration_response],
        )

        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            client_factory=client_factory,
        )

        # Mock 401 response
        response = MagicMock(spec=Response)
        response.status_code = 401
        response.url = MagicMock()
        response.url.scheme = "https"
        response.url.netloc = b"api.example.com"

        result = await strategy.handle_challenge(
            response, "https://api.example.com/resource"
        )

        assert result is True
        # Verify callback was registered
        assert len(coordinator.registered_routes) == 1
        # Verify redirect was triggered
        assert len(coordinator.handle_redirect_calls) == 1
        # Verify auth challenge stored via coordinator
        challenge = await coordinator.get_auth_pending(context.id, "slack")
        assert challenge is not None
        assert "authorization_url" in challenge
        assert "state" in challenge

    @pytest.mark.asyncio
    async def test_handle_challenge_coordinator_cleanup_behavior(self):
        """Test that handle_challenge respects coordinator cleanup requirements."""

        # Mock responses
        def resource_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={"authorization_servers": ["https://auth.example.com"]}
            )
            return resp

        def auth_server_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                }
            )
            return resp

        def registration_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "client_id": "client_123",
                    "redirect_uris": ["http://localhost:8080/callback"],
                }
            )
            return resp

        client_factory = create_mock_http_client(
            get_responses={
                "oauth-protected-resource": resource_response,
                "oauth-authorization-server": auth_server_response,
            },
            post_responses=[registration_response],
        )

        # Test with coordinator requiring synchronous cleanup
        coordinator = MockCoordinator()
        coordinator._requires_synchronous_cleanup = True
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            client_factory=client_factory,
        )

        response = MagicMock(spec=Response)
        response.status_code = 401
        response.url = MagicMock()
        response.url.scheme = "https"
        response.url.netloc = b"api.example.com"

        result = await strategy.handle_challenge(
            response, "https://api.example.com/resource"
        )

        assert result is True
        # Verify handler_kwargs includes synchronous cleanup flag
        state = list(coordinator.registered_routes.keys())[0]
        handler_kwargs = coordinator.registered_routes[state]["handler_kwargs"]
        assert "run_cleanup_in_background" in handler_kwargs
        assert handler_kwargs["run_cleanup_in_background"] is False


class TestOAuthStrategyCallbackCompletion:
    """Test OAuth flow completion via callback handler."""

    @pytest.mark.asyncio
    async def test_callback_handler_validates_state(self):
        """Test that callback handler validates state parameter."""

        # Setup full flow
        def resource_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={"authorization_servers": ["https://auth.example.com"]}
            )
            return resp

        def auth_server_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                }
            )
            return resp

        def registration_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "client_id": "client_123",
                    "redirect_uris": ["http://localhost:8080/callback"],
                }
            )
            return resp

        client_factory = create_mock_http_client(
            get_responses={
                "oauth-protected-resource": resource_response,
                "oauth-authorization-server": auth_server_response,
            },
            post_responses=[registration_response],
        )

        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            client_factory=client_factory,
        )

        response = MagicMock(spec=Response)
        response.status_code = 401
        response.url = MagicMock()
        response.url.scheme = "https"
        response.url.netloc = b"api.example.com"

        await strategy.handle_challenge(response, "http://example.com/resource")

        # Verify callback route was registered
        challenge = await coordinator.get_auth_pending(context.id, "slack")
        state = challenge["state"]
        callback_info = coordinator.registered_routes[state]

        # Verify callback registration
        assert callback_info["handler_name"] == "oauth_completion"
        assert callback_info["context_id"] == context.id
        assert callback_info["server_name"] == "slack"

        # Verify PKCE state was stored
        pkce_state = await strategy_storage.get(f"_pkce_state:{state}")
        assert pkce_state is not None
        assert "code_verifier" in pkce_state
        assert "code_challenge" in pkce_state
        assert "server_name" in pkce_state
        assert pkce_state["server_name"] == "slack"

    @pytest.mark.asyncio
    async def test_callback_handler_requires_code(self):
        """Test that callback requires authorization code."""
        coordinator = MockCoordinator()
        _, _connection_storage, strategy_storage = create_test_storage()

        # Manually create PKCE state
        state = "test_state_123"
        await strategy_storage.set(
            f"_pkce_state:{state}",
            {
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:8080/callback",
                "client_id": "client_123",
                "token_endpoint": "https://auth.example.com/token",
                "server_name": "slack"
            }
        )

        # Test callback without code
        with pytest.raises(ValueError, match="Missing authorization code"):
            await oauth_completion_handler(
                coordinator=coordinator,
                storage=strategy_storage,
                params={"state": state}
            )

    @pytest.mark.asyncio
    async def test_callback_handler_stores_tokens(self):
        """Test that callback handler stores tokens in storage."""
        # Mock token exchange response
        def mock_token_response():
            resp = MagicMock()
            resp.status_code = 200
            resp.json = MagicMock(
                return_value={
                    "access_token": "access_xyz",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
            return resp

        client_factory = create_mock_http_client(post_responses=[mock_token_response])

        coordinator = MockCoordinator()
        _, _connection_storage, strategy_storage = create_test_storage()

        # Setup PKCE state
        state = "test_state_123"
        await strategy_storage.set(
            f"_pkce_state:{state}",
            {
                "code_verifier": "verifier_xyz",
                "code_challenge": "challenge_xyz",
                "redirect_uri": "http://localhost:8080/callback",
                "client_id": "client_123",
                "token_endpoint": "https://auth.example.com/token",
                "resource_url": "http://example.com/resource",
                "server_name": "slack"
            }
        )

        # Invoke callback handler
        result = await oauth_completion_handler(
            coordinator=coordinator,
            storage=strategy_storage,
            params={"code": "auth_code_123", "state": state},
            client_factory=client_factory,
            run_cleanup_in_background=False
        )

        assert result["success"] is True

        # Verify tokens stored
        tokens = await strategy_storage.get("tokens")
        assert tokens is not None
        assert tokens["access_token"] == "access_xyz"


class TestOAuthStrategyIntegrationScenarios:
    """Test complex integration scenarios with OAuthStrategy."""

    @pytest.mark.asyncio
    async def test_full_oauth_flow_from_challenge_to_token(self):
        """Test complete OAuth flow from 401 challenge to token storage."""

        # Mock all responses
        def resource_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={"authorization_servers": ["https://auth.example.com"]}
            )
            return resp

        def auth_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                }
            )
            return resp

        def registration_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "client_id": "client_123",
                    "redirect_uris": ["http://localhost:8080/callback"],
                }
            )
            return resp

        def token_response():
            resp = MagicMock()
            resp.status_code = 200
            resp.json = MagicMock(
                return_value={
                    "access_token": "access_xyz",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
            return resp

        client_factory = create_mock_http_client(
            get_responses={
                "oauth-protected-resource": resource_response,
                "oauth-authorization-server": auth_response,
            },
            post_responses=[registration_response, token_response],
        )

        coordinator = MockCoordinator()
        _, connection_storage, strategy_storage = create_test_storage()
        context = create_test_context(coordinator)

        strategy = OAuthStrategy(
            server_name="slack",
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            client_factory=client_factory,
        )

        # Mock 401 response
        response = MagicMock(spec=Response)
        response.status_code = 401
        response.url = MagicMock()
        response.url.scheme = "https"
        response.url.netloc = b"api.example.com"

        # Step 1: Handle challenge
        result = await strategy.handle_challenge(
            response, "https://api.example.com/resource"
        )
        assert result is True

        # Step 2: Simulate callback
        challenge = await coordinator.get_auth_pending(context.id, "slack")
        state = challenge["state"]
        await oauth_completion_handler(
            coordinator=coordinator,
            storage=strategy_storage,
            params={"code": "auth_code_123", "state": state},
            client_factory=client_factory,
            run_cleanup_in_background=False
        )

        # Verify final state
        tokens = await strategy_storage.get("tokens")
        assert tokens is not None
        assert tokens["access_token"] == "access_xyz"

        # Verify auth challenge cleared
        assert await coordinator.get_auth_pending(context.id, "slack") is None

        # Verify can get auth metadata
        metadata = await strategy.get_auth_metadata()
        assert metadata["headers"]["Authorization"] == "Bearer access_xyz"

    @pytest.mark.asyncio
    async def test_oauth_storage_isolation_across_servers(self):
        """Test that different servers have isolated storage."""
        # Create two separate storage hierarchies for two different servers
        backend = InMemoryBackend()
        client_storage = NamespacedStorage(backend, "client:test_user")

        # Create storage for slack server
        slack_server_storage = client_storage.get_namespace("server:slack")
        slack_connection_storage = slack_server_storage.get_namespace("connection")
        slack_strategy_storage = slack_connection_storage.get_namespace("oauth")

        # Create storage for github server
        github_server_storage = client_storage.get_namespace("server:github")
        github_connection_storage = github_server_storage.get_namespace("connection")
        github_strategy_storage = github_connection_storage.get_namespace("oauth")

        coordinator1 = MockCoordinator()
        context1 = create_test_context(coordinator1)

        coordinator2 = MockCoordinator()
        context2 = create_test_context(coordinator2)

        strategy1 = OAuthStrategy(
            server_name="slack",
            storage=slack_strategy_storage,
            context=context1,
            coordinator=coordinator1,
        )

        strategy2 = OAuthStrategy(
            server_name="github",
            storage=github_strategy_storage,
            context=context2,
            coordinator=coordinator2,
        )

        # Store tokens in strategy1's storage
        await strategy1.oauth_storage.save_tokens({"access_token": "slack_token"})

        # strategy2 should not have these tokens
        tokens2 = await strategy2.oauth_storage.get_tokens()
        assert tokens2 is None

        # Get auth metadata for each
        metadata1 = await strategy1.get_auth_metadata()
        metadata2 = await strategy2.get_auth_metadata()

        assert metadata1["headers"]["Authorization"] == "Bearer slack_token"
        assert metadata2 == {}
