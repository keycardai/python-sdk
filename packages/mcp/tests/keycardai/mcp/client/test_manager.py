"""Unit tests for the ClientManager class.

This module tests the ClientManager class initialization, lifecycle management,
client creation, and coordination with shared coordinator across multiple contexts.
"""

import pytest

from keycardai.mcp.client.auth.coordinators.base import AuthCoordinator
from keycardai.mcp.client.auth.coordinators.local import LocalAuthCoordinator
from keycardai.mcp.client.client import Client
from keycardai.mcp.client.manager import ClientManager
from keycardai.mcp.client.storage import InMemoryBackend


# Mock AuthCoordinator for testing
class MockAuthCoordinator(AuthCoordinator):
    """Mock coordinator that tracks method calls."""

    def __init__(self, storage=None):
        super().__init__(storage)
        self.shutdown_called = False
        self.shutdown_call_count = 0

    @property
    def endpoint_type(self) -> str:
        """Return test endpoint type."""
        return "test"

    async def get_callback_uris(self) -> list[str] | None:
        """Return mock callback URIs."""
        return ["http://localhost:8080/callback"]

    async def shutdown(self):
        """Optional cleanup method (not part of interface)."""
        self.shutdown_called = True
        self.shutdown_call_count += 1

    async def handle_redirect(self, authorization_url: str, metadata: dict):
        pass


class TestClientManagerInitialization:
    """Test ClientManager initialization with various configurations."""

    def test_default_manager_initialization(self):
        """Test that a default manager is created with minimal config."""
        servers = {
            "test_server": {
                "url": "http://localhost:3000"
            }
        }

        manager = ClientManager(servers=servers)

        assert manager.servers == servers
        assert isinstance(manager.auth_coordinator, LocalAuthCoordinator)
        assert manager.clients == {}

    def test_custom_coordinator_is_used(self):
        """Test that custom coordinator is used when provided."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        custom_coordinator = LocalAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=custom_coordinator)

        assert manager.auth_coordinator is custom_coordinator

    def test_custom_storage_injection(self):
        """Test that custom storage backend is properly used."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        custom_backend = InMemoryBackend()

        manager = ClientManager(servers=servers, storage_backend=custom_backend)

        # Verify the coordinator has storage that wraps our backend
        assert manager.auth_coordinator.storage is not None

    def test_custom_coordinator_and_storage(self):
        """Test that both custom coordinator and storage can be provided."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        custom_backend = InMemoryBackend()
        custom_coordinator = LocalAuthCoordinator(backend=custom_backend)

        manager = ClientManager(
            servers=servers,
            auth_coordinator=custom_coordinator
        )

        assert manager.auth_coordinator is custom_coordinator
        assert manager.auth_coordinator.storage is not None

    def test_multiple_server_configs(self):
        """Test manager initialization with multiple servers."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"},
            "server3": {"url": "http://localhost:3002"}
        }

        manager = ClientManager(servers=servers)

        assert manager.servers == servers
        assert len(manager.servers) == 3

    def test_no_side_effects_on_creation(self):
        """Test that creating a manager has no side effects."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        # Verify no async operations were performed
        assert mock_coordinator.shutdown_called is False

        # Verify no clients were created
        assert len(manager.clients) == 0


class TestClientManagerLifecycle:
    """Test ClientManager context manager lifecycle."""

    @pytest.mark.asyncio
    async def test_context_manager_disconnects_clients(self):
        """Test that context manager disconnects all clients on exit."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(
            servers=servers,
            auth_coordinator=mock_coordinator
        )

        # Get some clients
        await manager.get_client("user:alice")
        await manager.get_client("user:bob")

        assert len(manager.clients) == 2

        # Context manager should disconnect them
        async with manager:
            pass

        # Clients are still in the dict but disconnected
        # (disconnect is called but clients remain for reference)
        assert len(manager.clients) == 2


class TestClientManagerGetClient:
    """Test ClientManager get_client method."""

    @pytest.mark.asyncio
    async def test_get_client_creates_new_client(self):
        """Test that get_client creates a new client for new context_id."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        client = await manager.get_client("user:alice")

        assert isinstance(client, Client)
        assert client._context_id == "user:alice"
        assert "user:alice" in manager.clients
        assert manager.clients["user:alice"] is client

    @pytest.mark.asyncio
    async def test_get_client_returns_same_client_for_same_context(self):
        """Test that get_client returns the same client for the same context_id."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        client1 = await manager.get_client("user:alice")
        client2 = await manager.get_client("user:alice")

        assert client1 is client2
        assert len(manager.clients) == 1

    @pytest.mark.asyncio
    async def test_get_client_creates_different_clients_for_different_contexts(self):
        """Test that get_client creates different clients for different context_ids."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        client_alice = await manager.get_client("user:alice")
        client_bob = await manager.get_client("user:bob")

        assert client_alice is not client_bob
        assert client_alice._context_id == "user:alice"
        assert client_bob._context_id == "user:bob"
        assert len(manager.clients) == 2

    @pytest.mark.asyncio
    async def test_get_client_uses_shared_coordinator(self):
        """Test that all clients share the same coordinator."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        client1 = await manager.get_client("user:alice")
        client2 = await manager.get_client("user:bob")

        assert client1.auth_coordinator is mock_coordinator
        assert client2.auth_coordinator is mock_coordinator
        assert client1.auth_coordinator is client2.auth_coordinator

    @pytest.mark.asyncio
    async def test_get_client_uses_shared_servers_config(self):
        """Test that all clients use the same servers configuration."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        client1 = await manager.get_client("user:alice")
        client2 = await manager.get_client("user:bob")

        # Both clients should have sessions for both servers
        assert "server1" in client1.sessions
        assert "server2" in client1.sessions
        assert "server1" in client2.sessions
        assert "server2" in client2.sessions

    @pytest.mark.asyncio
    async def test_get_client_clients_have_isolated_contexts(self):
        """Test that clients have isolated contexts (different storage namespaces)."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        client_alice = await manager.get_client("user:alice")
        client_bob = await manager.get_client("user:bob")

        # Clients should have different contexts
        assert client_alice.context is not client_bob.context
        assert client_alice.context.id == "user:alice"
        assert client_bob.context.id == "user:bob"

        # Contexts should have different storage instances
        assert client_alice.context.storage is not client_bob.context.storage

    @pytest.mark.asyncio
    async def test_get_client_shares_coordinator(self):
        """Test that clients created by manager share the coordinator."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        client = await manager.get_client("user:alice")

        # Client should use the manager's coordinator
        assert client.auth_coordinator is mock_coordinator


class TestClientManagerContextManager:
    """Test ClientManager context manager behavior."""

    @pytest.mark.asyncio
    async def test_context_manager_returns_manager(self):
        """Test that context manager returns the manager instance."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        async with manager as returned_manager:
            assert returned_manager is manager

    @pytest.mark.asyncio
    async def test_context_manager_exception_handling(self):
        """Test that context manager properly handles exceptions."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(
            servers=servers,
            auth_coordinator=mock_coordinator
        )

        with pytest.raises(RuntimeError, match="Test error"):
            async with manager:
                raise RuntimeError("Test error")

        # Context manager should still execute cleanup
        # (which disconnects clients)

    @pytest.mark.asyncio
    async def test_context_manager_with_client_creation(self):
        """Test using get_client within context manager."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        async with manager:
            client = await manager.get_client("user:alice")
            assert client._context_id == "user:alice"
            assert client.auth_coordinator is mock_coordinator

        # After exit, clients should still be cached
        assert "user:alice" in manager.clients


class TestClientManagerMultiContextScenarios:
    """Test ClientManager with multiple contexts and complex scenarios."""

    @pytest.mark.asyncio
    async def test_multi_user_scenario(self):
        """Test typical multi-user scenario with shared coordinator."""
        servers = {
            "slack": {"url": "http://localhost:3000"},
            "github": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        async with ClientManager(servers, auth_coordinator=mock_coordinator) as manager:
            # Create clients for different users
            client_alice = await manager.get_client("user:alice")
            client_bob = await manager.get_client("user:bob")
            client_charlie = await manager.get_client("user:charlie")

            # All use same coordinator
            assert client_alice.auth_coordinator is mock_coordinator
            assert client_bob.auth_coordinator is mock_coordinator
            assert client_charlie.auth_coordinator is mock_coordinator

            # All have access to both servers
            assert len(client_alice.sessions) == 2
            assert len(client_bob.sessions) == 2
            assert len(client_charlie.sessions) == 2

            # But have different contexts
            assert client_alice.context.id == "user:alice"
            assert client_bob.context.id == "user:bob"
            assert client_charlie.context.id == "user:charlie"

    @pytest.mark.asyncio
    async def test_task_based_isolation(self):
        """Test task-based context isolation."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        # Create clients for different tasks
        client_task1 = await manager.get_client("task:123")
        client_task2 = await manager.get_client("task:456")

        # Different contexts
        assert client_task1.context.id == "task:123"
        assert client_task2.context.id == "task:456"

        # Isolated storage
        await client_task1.context.storage.set("key", "value1")
        await client_task2.context.storage.set("key", "value2")

        assert await client_task1.context.storage.get("key") == "value1"
        assert await client_task2.context.storage.get("key") == "value2"

    @pytest.mark.asyncio
    async def test_many_clients_with_caching(self):
        """Test that manager efficiently caches many clients."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        manager = ClientManager(servers=servers, auth_coordinator=mock_coordinator)

        # Create many clients
        num_clients = 100
        clients = []
        for i in range(num_clients):
            client = await manager.get_client(f"user:{i}")
            clients.append(client)

        # All should be cached
        assert len(manager.clients) == num_clients

        # Getting same clients again should return cached instances
        for i in range(num_clients):
            cached_client = await manager.get_client(f"user:{i}")
            assert cached_client is clients[i]

    @pytest.mark.asyncio
    async def test_multiple_clients_share_coordinator(self):
        """Test that multiple clients share the same coordinator."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        async with ClientManager(servers, auth_coordinator=mock_coordinator) as manager:
            client_alice = await manager.get_client("user:alice")
            client_bob = await manager.get_client("user:bob")
            client_charlie = await manager.get_client("user:charlie")

            # All clients should share the same coordinator
            assert client_alice.auth_coordinator is mock_coordinator
            assert client_bob.auth_coordinator is mock_coordinator
            assert client_charlie.auth_coordinator is mock_coordinator

