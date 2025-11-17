"""SQLite file-based storage backend."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from ..backend import StorageBackend


class SQLiteBackend(StorageBackend):
    """
    SQLite file-based storage with TTL support.

    Stores data persistently in a SQLite database file.
    Suitable for:
    - Local development with persistence
    - Single-instance applications
    - Testing with persistent state

    For distributed systems, use RedisBackend or DynamoDBBackend.

    Schema:
        key TEXT PRIMARY KEY
        value TEXT (JSON-serialized)
        expires_at REAL (timestamp, NULL for no expiration)
    """

    def __init__(self, db_path: str | Path = "keycard_storage.db"):
        """
        Initialize SQLite backend.

        Args:
            db_path: Path to SQLite database file (created if doesn't exist)
        """
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Ensure database is connected and table exists."""
        if self._initialized:
            return

        async with self._lock:
            # Double-check after acquiring lock
            if self._initialized:
                return

            # Create parent directory if needed
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            # Connect to database
            self._db = await aiosqlite.connect(str(self.db_path))

            # Create table
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS storage (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at REAL
                )
            """)

            # Create index for prefix queries
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_key_prefix
                ON storage(key)
            """)

            # Create index for expiration cleanup
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires_at
                ON storage(expires_at)
                WHERE expires_at IS NOT NULL
            """)

            await self._db.commit()
            self._initialized = True

    def _serialize(self, value: Any) -> str:
        """Serialize value to JSON string."""
        return json.dumps(value)

    def _deserialize(self, data: str) -> Any:
        """Deserialize JSON string to value."""
        return json.loads(data)

    def _is_expired(self, expires_at: float | None) -> bool:
        """Check if entry is expired."""
        if expires_at is None:
            return False
        return datetime.now(timezone.utc).timestamp() >= expires_at

    async def get(self, key: str) -> Any | None:
        await self._ensure_initialized()
        assert self._db is not None

        cursor = await self._db.execute(
            "SELECT value, expires_at FROM storage WHERE key = ?",
            (key,)
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        value_json, expires_at = row

        # Check expiration
        if self._is_expired(expires_at):
            # Auto-cleanup expired entry
            await self._db.execute("DELETE FROM storage WHERE key = ?", (key,))
            await self._db.commit()
            return None

        return self._deserialize(value_json)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: timedelta | None = None
    ) -> None:
        await self._ensure_initialized()
        assert self._db is not None

        expires_at = None
        if ttl:
            expires_at = (datetime.now(timezone.utc) + ttl).timestamp()

        value_json = self._serialize(value)

        await self._db.execute(
            """
            INSERT INTO storage (key, value, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                expires_at = excluded.expires_at
            """,
            (key, value_json, expires_at)
        )
        await self._db.commit()

    async def delete(self, key: str) -> bool:
        await self._ensure_initialized()
        assert self._db is not None

        cursor = await self._db.execute(
            "DELETE FROM storage WHERE key = ?",
            (key,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Optimized batch get using SQL IN clause."""
        if not keys:
            return {}

        await self._ensure_initialized()
        assert self._db is not None

        # Build placeholders for SQL IN clause
        placeholders = ",".join("?" * len(keys))
        cursor = await self._db.execute(
            f"SELECT key, value, expires_at FROM storage WHERE key IN ({placeholders})",
            keys
        )
        rows = await cursor.fetchall()

        result = {}
        expired_keys = []

        for key, value_json, expires_at in rows:
            if self._is_expired(expires_at):
                expired_keys.append(key)
                continue

            result[key] = self._deserialize(value_json)

        # Cleanup expired entries
        if expired_keys:
            placeholders = ",".join("?" * len(expired_keys))
            await self._db.execute(
                f"DELETE FROM storage WHERE key IN ({placeholders})",
                expired_keys
            )
            await self._db.commit()

        return result

    async def set_many(
        self,
        items: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """Optimized batch set using transaction."""
        if not items:
            return

        await self._ensure_initialized()
        assert self._db is not None

        expires_at = None
        if ttl:
            expires_at = (datetime.now(timezone.utc) + ttl).timestamp()

        # Use transaction for batch insert
        async with self._db.execute("BEGIN"):
            for key, value in items.items():
                value_json = self._serialize(value)
                await self._db.execute(
                    """
                    INSERT INTO storage (key, value, expires_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        expires_at = excluded.expires_at
                    """,
                    (key, value_json, expires_at)
                )
        await self._db.commit()

    async def delete_many(self, keys: list[str]) -> int:
        """Optimized batch delete using SQL IN clause."""
        if not keys:
            return 0

        await self._ensure_initialized()
        assert self._db is not None

        placeholders = ",".join("?" * len(keys))
        cursor = await self._db.execute(
            f"DELETE FROM storage WHERE key IN ({placeholders})",
            keys
        )
        await self._db.commit()
        return cursor.rowcount

    async def increment(
        self,
        key: str,
        amount: int = 1,
        ttl: timedelta | None = None
    ) -> int:
        """Atomic increment using database transaction."""
        await self._ensure_initialized()
        assert self._db is not None

        async with self._lock:
            # Get current value
            current = 0
            cursor = await self._db.execute(
                "SELECT value, expires_at FROM storage WHERE key = ?",
                (key,)
            )
            row = await cursor.fetchone()

            if row is not None:
                value_json, expires_at = row
                if not self._is_expired(expires_at):
                    current = int(self._deserialize(value_json))

            # Calculate new value
            new_value = current + amount

            # Store new value
            expires_at = None
            if ttl:
                expires_at = (datetime.now(timezone.utc) + ttl).timestamp()

            value_json = self._serialize(new_value)
            await self._db.execute(
                """
                INSERT INTO storage (key, value, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    expires_at = excluded.expires_at
                """,
                (key, value_json, expires_at)
            )
            await self._db.commit()

            return new_value

    async def list_keys(
        self,
        prefix: str | None = None,
        limit: int | None = None
    ) -> list[str]:
        await self._ensure_initialized()
        assert self._db is not None

        # Build query
        if prefix:
            query = "SELECT key, expires_at FROM storage WHERE key LIKE ?"
            params = (f"{prefix}%",)
        else:
            query = "SELECT key, expires_at FROM storage"
            params = ()

        if limit:
            query += f" LIMIT {limit}"

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        keys = []
        expired_keys = []

        for key, expires_at in rows:
            if self._is_expired(expires_at):
                expired_keys.append(key)
                continue
            keys.append(key)

        # Cleanup expired entries
        if expired_keys:
            placeholders = ",".join("?" * len(expired_keys))
            await self._db.execute(
                f"DELETE FROM storage WHERE key IN ({placeholders})",
                expired_keys
            )
            await self._db.commit()

        return keys

    async def delete_prefix(self, prefix: str) -> int:
        """Optimized prefix delete using SQL LIKE."""
        await self._ensure_initialized()
        assert self._db is not None

        cursor = await self._db.execute(
            "DELETE FROM storage WHERE key LIKE ?",
            (f"{prefix}%",)
        )
        await self._db.commit()
        return cursor.rowcount

    async def cleanup_expired(self) -> int:
        """
        Remove all expired entries.

        This is a maintenance operation that can be called periodically.
        Returns count of deleted entries.
        """
        await self._ensure_initialized()
        assert self._db is not None

        now = datetime.now(timezone.utc).timestamp()
        cursor = await self._db.execute(
            "DELETE FROM storage WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def close(self) -> None:
        """Close database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            self._initialized = False

