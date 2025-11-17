"""Tests for SQLite storage backend."""

import asyncio
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import pytest_asyncio

from keycardai.mcp.client.storage.backends.sqlite import SQLiteBackend


class TestSQLiteBackend:
    """Test SQLite storage backend."""

    @pytest_asyncio.fixture
    async def backend(self):
        """Create temporary SQLite backend."""
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            backend = SQLiteBackend(db_path)
            yield backend
            await backend.close()

    @pytest.mark.asyncio
    async def test_basic_operations(self, backend):
        """Test basic get/set/delete operations."""
        # Set a value
        await backend.set("test_key", "test_value")

        # Get the value
        value = await backend.get("test_key")
        assert value == "test_value"

        # Check exists
        assert await backend.exists("test_key")

        # Delete the value
        deleted = await backend.delete("test_key")
        assert deleted is True

        # Verify deleted
        assert await backend.get("test_key") is None
        assert not await backend.exists("test_key")

        # Delete non-existent key
        deleted = await backend.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_json_serialization(self, backend):
        """Test storing complex Python objects."""
        # Dictionary
        await backend.set("dict", {"name": "John", "age": 30})
        assert await backend.get("dict") == {"name": "John", "age": 30}

        # List
        await backend.set("list", [1, 2, 3, 4, 5])
        assert await backend.get("list") == [1, 2, 3, 4, 5]

        # Nested structure
        data = {
            "users": [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"}
            ],
            "meta": {"count": 2}
        }
        await backend.set("nested", data)
        assert await backend.get("nested") == data

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, backend):
        """Test TTL expiration."""
        # Set value with very short TTL
        await backend.set("temp_key", "temp_value", ttl=timedelta(milliseconds=100))

        # Should exist immediately
        assert await backend.get("temp_key") == "temp_value"

        # Wait for expiration
        await asyncio.sleep(0.2)

        # Should be expired
        assert await backend.get("temp_key") is None
        assert not await backend.exists("temp_key")

    @pytest.mark.asyncio
    async def test_batch_operations(self, backend):
        """Test batch get/set/delete operations."""
        # Batch set
        items = {
            "key1": "value1",
            "key2": "value2",
            "key3": "value3"
        }
        await backend.set_many(items)

        # Batch get
        values = await backend.get_many(["key1", "key2", "key3"])
        assert values == items

        # Partial batch get
        values = await backend.get_many(["key1", "nonexistent"])
        assert values == {"key1": "value1"}

        # Batch delete
        count = await backend.delete_many(["key1", "key2"])
        assert count == 2

        # Verify deleted
        assert await backend.get("key1") is None
        assert await backend.get("key2") is None
        assert await backend.get("key3") == "value3"

    @pytest.mark.asyncio
    async def test_increment(self, backend):
        """Test atomic increment operation."""
        # Increment non-existent key
        value = await backend.increment("counter")
        assert value == 1

        # Increment by default (1)
        value = await backend.increment("counter")
        assert value == 2

        # Increment by custom amount
        value = await backend.increment("counter", amount=5)
        assert value == 7

        # Verify stored value
        assert await backend.get("counter") == 7

    @pytest.mark.asyncio
    async def test_list_keys(self, backend):
        """Test listing keys with prefix."""
        # Set up test data
        await backend.set("user:1:name", "Alice")
        await backend.set("user:2:name", "Bob")
        await backend.set("user:3:name", "Charlie")
        await backend.set("session:1", "data1")
        await backend.set("session:2", "data2")

        # List all keys
        all_keys = await backend.list_keys()
        assert len(all_keys) == 5

        # List with prefix
        user_keys = await backend.list_keys(prefix="user:")
        assert len(user_keys) == 3
        assert all(key.startswith("user:") for key in user_keys)

        # List with limit
        limited_keys = await backend.list_keys(limit=2)
        assert len(limited_keys) == 2

        # List with prefix and limit
        limited_user_keys = await backend.list_keys(prefix="user:", limit=2)
        assert len(limited_user_keys) == 2
        assert all(key.startswith("user:") for key in limited_user_keys)

    @pytest.mark.asyncio
    async def test_delete_prefix(self, backend):
        """Test deleting keys by prefix."""
        # Set up test data
        await backend.set("user:1:name", "Alice")
        await backend.set("user:2:name", "Bob")
        await backend.set("session:1", "data1")

        # Delete by prefix
        count = await backend.delete_prefix("user:")
        assert count == 2

        # Verify deleted
        assert await backend.get("user:1:name") is None
        assert await backend.get("user:2:name") is None
        assert await backend.get("session:1") == "data1"

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, backend):
        """Test cleanup of expired entries."""
        # Set values with different TTLs
        await backend.set("perm", "permanent")
        await backend.set("short", "short_lived", ttl=timedelta(milliseconds=100))
        await backend.set("medium", "medium_lived", ttl=timedelta(seconds=10))

        # Wait for short TTL to expire
        await asyncio.sleep(0.2)

        # Cleanup expired
        count = await backend.cleanup_expired()
        assert count == 1

        # Verify state
        assert await backend.get("perm") == "permanent"
        assert await backend.get("short") is None
        assert await backend.get("medium") == "medium_lived"

    @pytest.mark.asyncio
    async def test_persistence(self):
        """Test that data persists across connections."""
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "persist.db"

            # Create first backend and store data
            backend1 = SQLiteBackend(db_path)
            await backend1.set("persist_key", "persist_value")
            await backend1.close()

            # Create second backend pointing to same file
            backend2 = SQLiteBackend(db_path)
            value = await backend2.get("persist_key")
            assert value == "persist_value"
            await backend2.close()

    @pytest.mark.asyncio
    async def test_concurrent_operations(self, backend):
        """Test thread-safe concurrent operations."""
        # Perform concurrent increments
        tasks = [backend.increment("concurrent_counter") for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All operations should succeed
        assert len(results) == 10

        # Final value should be 10
        final_value = await backend.get("concurrent_counter")
        assert final_value == 10

    @pytest.mark.asyncio
    async def test_update_existing_key(self, backend):
        """Test updating existing key."""
        # Set initial value
        await backend.set("update_key", "initial")
        assert await backend.get("update_key") == "initial"

        # Update value
        await backend.set("update_key", "updated")
        assert await backend.get("update_key") == "updated"

        # Update with TTL
        await backend.set("update_key", "with_ttl", ttl=timedelta(hours=1))
        assert await backend.get("update_key") == "with_ttl"

