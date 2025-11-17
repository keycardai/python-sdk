"""Namespaced storage wrapper."""

from datetime import timedelta
from typing import Any

from .backend import StorageBackend


class NamespacedStorage:
    """
    Wraps a backend with namespace isolation.

    Provides:
    - Automatic key prefixing for isolation
    - Hierarchical namespaces (user:alice:server:slack)
    - Easy bulk cleanup by namespace

    Usage:
        backend = InMemoryBackend()
        root = NamespacedStorage(backend, "mcp")
        user = root.get_namespace("user:alice")
        server = user.get_namespace("server:slack")

        # Physical key: "mcp:user:alice:server:slack:tokens"
        await server.set("tokens", {...})
    """

    def __init__(
        self,
        backend: StorageBackend,
        namespace: str,
        separator: str = ":"
    ):
        self._backend = backend
        self._namespace = namespace
        self._separator = separator

    def _make_key(self, key: str) -> str:
        """Convert logical key to physical key with namespace prefix."""
        return f"{self._namespace}{self._separator}{key}"

    def _strip_key(self, physical_key: str) -> str:
        """Strip namespace prefix from physical key."""
        prefix = f"{self._namespace}{self._separator}"
        if physical_key.startswith(prefix):
            return physical_key[len(prefix):]
        return physical_key

    # Core operations (with automatic prefixing)

    async def get(self, key: str) -> Any | None:
        """Get value from this namespace."""
        return await self._backend.get(self._make_key(key))

    async def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None
    ) -> None:
        """Set value in this namespace."""
        await self._backend.set(self._make_key(key), value, ttl)

    async def delete(self, key: str) -> bool:
        """Delete key from this namespace."""
        return await self._backend.delete(self._make_key(key))

    async def exists(self, key: str) -> bool:
        """Check if key exists in this namespace."""
        return await self._backend.exists(self._make_key(key))

    # Batch operations

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values (returns logical keys)."""
        physical_keys = [self._make_key(k) for k in keys]
        physical_result = await self._backend.get_many(physical_keys)

        # Convert back to logical keys
        return {
            self._strip_key(k): v
            for k, v in physical_result.items()
        }

    async def set_many(
        self,
        items: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """Set multiple values."""
        physical_items = {
            self._make_key(k): v
            for k, v in items.items()
        }
        await self._backend.set_many(physical_items, ttl)

    async def delete_many(self, keys: list[str]) -> int:
        """Delete multiple keys from this namespace."""
        physical_keys = [self._make_key(k) for k in keys]
        return await self._backend.delete_many(physical_keys)

    # Atomic operations

    async def increment(
        self,
        key: str,
        amount: int = 1,
        ttl: timedelta | None = None
    ) -> int:
        """Atomically increment counter."""
        return await self._backend.increment(
            self._make_key(key),
            amount,
            ttl
        )

    # Namespace operations

    async def list_keys(self, limit: int | None = None) -> list[str]:
        """List all keys in this namespace (without prefix)."""
        prefix = f"{self._namespace}{self._separator}"
        physical_keys = await self._backend.list_keys(prefix, limit)
        return [self._strip_key(k) for k in physical_keys]

    async def clear(self) -> int:
        """Delete all keys in this namespace. Returns count."""
        prefix = f"{self._namespace}{self._separator}"
        return await self._backend.delete_prefix(prefix)

    def get_namespace(self, sub_namespace: str) -> "NamespacedStorage":
        """
        Create a sub-namespace.

        Example:
            root = NamespacedStorage(backend, "mcp")
            user = root.get_namespace("user:alice")
            server = user.get_namespace("server:slack")
            # Full namespace: "mcp:user:alice:server:slack"
        """
        full_namespace = f"{self._namespace}{self._separator}{sub_namespace}"
        return NamespacedStorage(
            self._backend,
            full_namespace,
            self._separator
        )

