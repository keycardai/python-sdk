"""Storage backend interface."""

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any


class StorageBackend(ABC):
    """
    Low-level storage backend interface.

    Provides generic key-value operations with TTL support.
    All backends must implement these core operations.
    """

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """
        Get value by key.

        Returns None if key doesn't exist or has expired.
        """
        pass

    @abstractmethod
    async def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None
    ) -> None:
        """
        Set value with optional TTL.

        Args:
            key: Storage key
            value: Value to store (must be JSON-serializable)
            ttl: Time to live (None = no expiration)
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        Delete key.

        Returns True if deleted, False if key didn't exist.
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        pass

    # Batch operations (can have default implementations)

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """
        Get multiple values.

        Default implementation calls get() for each key.
        Backends can override for efficiency.
        """
        result = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value
        return result

    async def set_many(
        self,
        items: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """
        Set multiple values.

        Default implementation calls set() for each item.
        Backends can override for efficiency.
        """
        for key, value in items.items():
            await self.set(key, value, ttl)

    async def delete_many(self, keys: list[str]) -> int:
        """
        Delete multiple keys.

        Returns count of deleted keys.
        """
        count = 0
        for key in keys:
            if await self.delete(key):
                count += 1
        return count

    # Atomic operations (optional but useful)

    async def increment(
        self,
        key: str,
        amount: int = 1,
        ttl: timedelta | None = None
    ) -> int:
        """
        Atomically increment counter.

        Default implementation is not atomic.
        Backends with native atomic support should override.

        Returns new value.
        """
        current = await self.get(key)
        new_value = (int(current) if current else 0) + amount
        await self.set(key, new_value, ttl)
        return new_value

    # Prefix operations (for namespace cleanup)

    @abstractmethod
    async def list_keys(
        self,
        prefix: str | None = None,
        limit: int | None = None
    ) -> list[str]:
        """
        List keys, optionally filtered by prefix.

        Args:
            prefix: Key prefix filter
            limit: Maximum number of keys to return
        """
        pass

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """
        Delete all keys matching prefix.

        Returns count of deleted keys.
        """
        pass

    # Lifecycle
    @abstractmethod
    async def close(self) -> None:
        """Close backend and release resources."""
        pass

