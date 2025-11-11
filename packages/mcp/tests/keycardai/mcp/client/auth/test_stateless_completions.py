"""Integration tests for stateless completion handling."""

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

from keycardai.mcp.client.auth.handlers import get_default_handler_registry
from keycardai.mcp.client.auth.strategies.oauth import OAuthStrategy
from keycardai.mcp.client.context import Context
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


class MockCoordinator:
    """Mock coordinator for testing stateless completions."""

    def __init__(self):
        self.redirect_uris = ["http://localhost:8080/callback"]
        self.registered_routes = {}
        self.handle_redirect_calls = []
        self.auth_pending = {}
        self.storage = NamespacedStorage(InMemoryBackend(), "auth_coordinator")
        self._requires_synchronous_cleanup = False

    @property
    def requires_synchronous_cleanup(self) -> bool:
        """Mock property for cleanup behavior."""
        return self._requires_synchronous_cleanup

    async def get_redirect_uris(self) -> list[str]:
        """Return mock callback URIs."""
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
        ttl: timedelta | None = None
    ) -> None:
        """Store registered completion route."""
        self.registered_routes[routing_key] = {
            "handler_name": handler_name,
            "storage_namespace": storage_namespace,
            "context_id": context_id,
            "server_name": server_name,
            "routing_param": routing_param,
            "handler_kwargs": handler_kwargs or {},
            "metadata": metadata or {},
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
        ttl: timedelta | None = None
    ) -> None:
        """Store auth pending state."""
        key = f"{context_id}:{server_name}"
        self.auth_pending[key] = auth_metadata

    async def clear_auth_pending(
        self,
        context_id: str,
        server_name: str
    ) -> None:
        """Clear auth pending state."""
        key = f"{context_id}:{server_name}"
        if key in self.auth_pending:
            del self.auth_pending[key]


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    return MockCoordinator()


@pytest.fixture
def test_storage():
    """Create test storage."""
    backend = InMemoryBackend()
    client_storage = NamespacedStorage(backend, "client:test_user")
    server_storage = client_storage.get_namespace("server:test_server")
    connection_storage = server_storage.get_namespace("connection")
    strategy_storage = connection_storage.get_namespace("oauth")
    return backend, connection_storage, strategy_storage


@pytest.fixture
def test_context(mock_coordinator):
    """Create test context."""
    backend = InMemoryBackend()
    client_storage = NamespacedStorage(backend, "client:test_user")
    return Context(id="test_user", storage=client_storage, coordinator=mock_coordinator)


class TestOAuthUnifiedStrategy:
    """Test unified OAuthStrategy with handler registry."""

    @pytest.mark.asyncio
    async def test_initiate_flow_registers_handler_name(
        self, mock_coordinator, test_storage, test_context
    ):
        """Test that initiating OAuth flow registers handler by name, not class path."""
        _, _connection_storage, strategy_storage = test_storage

        # Mock HTTP responses for OAuth discovery and registration
        mock_resource_response = MagicMock(spec=Response)
        mock_resource_response.status_code = 200
        mock_resource_response.raise_for_status = MagicMock()
        mock_resource_response.json = MagicMock(return_value={
            "authorization_servers": ["http://localhost/auth"]
        })

        mock_auth_server_response = MagicMock(spec=Response)
        mock_auth_server_response.status_code = 200
        mock_auth_server_response.raise_for_status = MagicMock()
        mock_auth_server_response.json = MagicMock(return_value={
            "authorization_endpoint": "http://localhost/authorize",
            "token_endpoint": "http://localhost/token",
            "registration_endpoint": "http://localhost/register"
        })

        mock_registration_response = MagicMock(spec=Response)
        mock_registration_response.status_code = 200
        mock_registration_response.raise_for_status = MagicMock()
        mock_registration_response.json = MagicMock(return_value={
            "client_id": "test_client_id",
            "redirect_uris": ["http://localhost:8080/callback"]
        })

        # Create challenge response
        challenge_response = MagicMock(spec=Response)
        challenge_response.status_code = 401
        challenge_response.url = MagicMock()
        challenge_response.url.scheme = "http"
        challenge_response.url.netloc = b"localhost"

        # Create client factory for Phase 1 service-based architecture
        get_call_count = [0]
        post_call_count = [0]

        def mock_client_factory():
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            async def mock_get(url):
                responses = [mock_resource_response, mock_auth_server_response]
                response = responses[get_call_count[0]]
                get_call_count[0] += 1
                return response

            async def mock_post(url, **kwargs):
                responses = [mock_registration_response]
                response = responses[post_call_count[0]]
                post_call_count[0] += 1
                return response

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client.post = AsyncMock(side_effect=mock_post)
            return mock_client

        # Create unified strategy with mock client factory
        strategy = OAuthStrategy(
            server_name="test_server",
            storage=strategy_storage,
            context=test_context,
            coordinator=mock_coordinator,
            client_factory=mock_client_factory
        )

        # Handle challenge
        await strategy.handle_challenge(challenge_response, "http://localhost/resource")

        # Verify completion was registered with handler name, not class path
        assert len(mock_coordinator.registered_routes) == 1
        route_info = list(mock_coordinator.registered_routes.values())[0]

        # Key assertion: unified strategy uses "oauth_completion" name
        assert route_info["handler_name"] == "oauth_completion"
        assert "strategy_class" not in route_info  # We use handler_name, not class path
        assert route_info["context_id"] == "test_user"
        assert route_info["server_name"] == "test_server"


class TestCoordinatorCallbackInvocation:
    """Test coordinator's ability to invoke registered handlers."""

    @pytest.mark.asyncio
    async def test_coordinator_can_invoke_handler_by_name(self):
        """Test that coordinator can retrieve and invoke handler by name."""
        # This simulates what the coordinator does in _route_completion

        # Get the registered handler from the global registry
        registry = get_default_handler_registry()
        completion_handler = registry._handlers["oauth_completion"]

        # Create mock dependencies
        coordinator = MockCoordinator()
        backend = InMemoryBackend()
        storage = NamespacedStorage(backend, "client:test_user:server:test:connection:oauth")

        # Store PKCE state
        await storage.set("_pkce_state:test_state", {
            "code_verifier": "test_verifier",
            "redirect_uri": "http://localhost/callback",
            "client_id": "test_client",
            "token_endpoint": "http://localhost/token",
            "server_name": "test_server"
        })

        # Mock HTTP response
        mock_token_response = MagicMock()
        mock_token_response.status_code = 200
        mock_token_response.json = MagicMock(return_value={
            "access_token": "test_token",
            "expires_in": 3600
        })

        # Create a custom client factory
        def custom_client_factory():
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_token_response)
            return mock_client

        # Invoke callback (as coordinator would) with custom client factory
        result = await completion_handler(
            coordinator=coordinator,
            storage=storage,
            params={"code": "test_code", "state": "test_state"},
            client_factory=custom_client_factory
        )

        # Verify result
        assert result["success"]
        assert result["server_name"] == "test_server"

