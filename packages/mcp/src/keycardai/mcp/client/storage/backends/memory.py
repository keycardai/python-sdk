"""In-memory storage backend."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from ..backend import StorageBackend


class InMemoryBackend(StorageBackend):
    """
    In-memory storage with TTL support.

    ⚠️ WARNING: State is lost when process ends!
    Only use for:
    - Local development
    - Testing
    - Single long-running processes

    For stateless environments, use RedisBackend or DynamoDBBackend.
    """

    def __init__(self):
        self._data: dict[str, tuple[Any, datetime | None]] = {}
        self._lock = asyncio.Lock()

    def _is_expired(self, expires_at: datetime | None) -> bool:
        """Check if entry is expired."""
        if expires_at is None:
            return False
        return datetime.now(timezone.utc) >= expires_at

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            if key not in self._data:
                return None

            value, expires_at = self._data[key]

            # Auto-cleanup expired entries
            if self._is_expired(expires_at):
                del self._data[key]
                return None

            return value

    async def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None
    ) -> None:
        expires_at = None
        if ttl:
            expires_at = datetime.now(timezone.utc) + ttl

        async with self._lock:
            self._data[key] = (value, expires_at)

    async def delete(self, key: str) -> bool:
        async with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def increment(
        self,
        key: str,
        amount: int = 1,
        ttl: timedelta | None = None
    ) -> int:
        """Atomic increment using lock."""
        async with self._lock:
            current = 0
            if key in self._data:
                value, expires_at = self._data[key]
                if not self._is_expired(expires_at):
                    current = int(value)

            new_value = current + amount

            expires_at = None
            if ttl:
                expires_at = datetime.now(timezone.utc) + ttl

            self._data[key] = (new_value, expires_at)
            return new_value

    async def list_keys(
        self,
        prefix: str | None = None,
        limit: int | None = None
    ) -> list[str]:
        async with self._lock:
            keys = []
            for key in self._data.keys():
                if prefix and not key.startswith(prefix):
                    continue

                # Skip expired
                _, expires_at = self._data[key]
                if self._is_expired(expires_at):
                    continue

                keys.append(key)

                if limit and len(keys) >= limit:
                    break

            return keys

    async def delete_prefix(self, prefix: str) -> int:
        keys = await self.list_keys(prefix=prefix)
        return await self.delete_many(keys)

    async def close(self) -> None:
        """Clear all data."""
        async with self._lock:
            self._data.clear()

