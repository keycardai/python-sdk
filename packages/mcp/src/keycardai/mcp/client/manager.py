from typing import Any

from .auth.coordinators import AuthCoordinator, LocalAuthCoordinator
from .client import Client
from .storage import InMemoryBackend, StorageBackend


class ClientManager:
    """
    Factory for creating multiple clients with shared coordinator.

    Use when you need multi-context/multi-user scenarios.
    All clients share the same coordinator (e.g., one local callback server for all users).

    Usage:
        # With default local coordinator:
        async with ClientManager(servers) as manager:
            client_alice = await manager.get_client("user:alice")
            client_bob = await manager.get_client("user:bob")
            # Both use same local callback server

        # With remote coordinator (for web apps):
        coordinator = StarletteAuthCoordinator(...)
        async with ClientManager(servers, auth_coordinator=coordinator) as manager:
            client = await manager.get_client("user:alice")
            # ... use client
    """

    def __init__(
        self,
        servers: dict[str, Any],
        storage_backend: StorageBackend | None = None,
        auth_coordinator: AuthCoordinator | None = None
    ):
        """
        Initialize client manager.

        Args:
            servers: Server configurations (shared across all clients)
            storage_backend: Storage backend (defaults to InMemoryBackend)
            auth_coordinator: Optional coordinator (creates LocalAuthCoordinator if not provided)
        """
        self.servers = servers

        if storage_backend is None:
            storage_backend = InMemoryBackend()

        if auth_coordinator is None:
            auth_coordinator = LocalAuthCoordinator(backend=storage_backend)

        self.auth_coordinator = auth_coordinator
        self.clients = {}

    async def get_client(self, context_id: str, metadata: dict[str, Any] | None = None) -> Client:
        """
        Get or create a client for a specific context.

        All clients share the same coordinator but have isolated storage.

        Args:
            context_id: Identifier for the client context (e.g., "user:alice", "task:123")
            metadata: Optional metadata dict to attach to context (e.g., user info, session data)

        Returns:
            Client instance for the given context
        """
        if context_id in self.clients:
            return self.clients[context_id]

        client = Client(
            servers=self.servers,
            context_id=context_id,
            auth_coordinator=self.auth_coordinator,
            metadata=metadata
        )

        self.clients[context_id] = client
        return client

    async def __aenter__(self) -> "ClientManager":
        """Context manager entry - returns self."""
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """
        Context manager exit - disconnects all clients.
        """
        for client in self.clients.values():
            await client.disconnect()
