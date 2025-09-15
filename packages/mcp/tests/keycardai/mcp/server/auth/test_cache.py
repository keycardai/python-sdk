"""Tests for JWKS caching components."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from keycardai.mcp.server.auth._cache import JWKSCache


class TestJWKSCache:
    """Test JWKS cache functionality."""

    def test_cache_initialization(self):
        """Test cache initialization with default and custom parameters."""
        # Default initialization
        cache = JWKSCache()
        assert cache.ttl == 300
        assert cache.max_size == 10
        assert cache.size() == 0

        # Custom initialization
        cache = JWKSCache(ttl=600, max_size=5)
        assert cache.ttl == 600
        assert cache.max_size == 5
        assert cache.size() == 0

    def test_basic_set_and_get_with_kid(self):
        """Test basic cache set and get operations with key IDs."""
        cache = JWKSCache()

        key_pem = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBg...\n-----END PUBLIC KEY-----"

        # Set and get a key with specific kid
        cache.set_key("key1", key_pem, "RS256")
        cached_key = cache.get_key("key1")
        assert cached_key is not None
        assert cached_key.key == key_pem
        assert cached_key.algorithm == "RS256"
        assert cache.size() == 1

        # Get non-existent key
        assert cache.get_key("nonexistent") is None

    def test_default_key_handling(self):
        """Test setting and getting default key (kid=None)."""
        cache = JWKSCache()

        key_pem = "-----BEGIN PUBLIC KEY-----\ndefault_key...\n-----END PUBLIC KEY-----"

        # Set default key (kid=None)
        cache.set_key(None, key_pem, "RS256")
        cached_key = cache.get_key(None)
        assert cached_key is not None
        assert cached_key.key == key_pem
        assert cached_key.algorithm == "RS256"

        # Update default key
        cache.set_key(None, "updated_key", "ES256")
        cached_key = cache.get_key(None)
        assert cached_key is not None
        assert cached_key.key == "updated_key"
        assert cached_key.algorithm == "ES256"

    def test_cache_expiration(self):
        """Test that cached keys expire after TTL."""
        cache = JWKSCache(ttl=1)  # 1 second TTL

        cache.set_key("key1", "test_key", "RS256")
        cached_key = cache.get_key("key1")
        assert cached_key is not None
        assert cached_key.key == "test_key"

        # Wait for expiration
        time.sleep(1.1)
        assert cache.get_key("key1") is None
        assert cache.size() == 0  # Expired entry should be removed

    @patch("time.time")
    def test_cache_expiration_mocked(self, mock_time):
        """Test cache expiration using mocked time for deterministic testing."""
        cache = JWKSCache(ttl=300)

        # Set initial time
        mock_time.return_value = 1000.0
        cache.set_key("key1", "test_key", "RS256")
        cached_key = cache.get_key("key1")
        assert cached_key is not None
        assert cached_key.key == "test_key"

        # Advance time by 200 seconds (still within TTL)
        mock_time.return_value = 1200.0
        cached_key = cache.get_key("key1")
        assert cached_key is not None
        assert cached_key.key == "test_key"

        # Advance time beyond TTL
        mock_time.return_value = 1301.0
        assert cache.get_key("key1") is None

    def test_cache_size_limit(self):
        """Test that cache clears when max size is reached."""
        cache = JWKSCache(max_size=3)

        # Fill cache to max size
        cache.set_key("key1", "value1", "RS256")
        cache.set_key("key2", "value2", "ES256")
        cache.set_key("key3", "value3", "PS256")
        assert cache.size() == 3

        # Adding another item should clear the cache first
        cache.set_key("key4", "value4", "RS256")
        assert cache.size() == 1
        cached_key = cache.get_key("key4")
        assert cached_key is not None
        assert cached_key.key == "value4"
        # Previous keys should be gone
        assert cache.get_key("key1") is None
        assert cache.get_key("key2") is None
        assert cache.get_key("key3") is None

    def test_cache_size_limit_existing_key(self):
        """Test that updating existing key doesn't trigger size limit."""
        cache = JWKSCache(max_size=2)

        cache.set_key("key1", "value1", "RS256")
        cache.set_key("key2", "value2", "ES256")
        assert cache.size() == 2

        # Update existing key - should not trigger clear
        cache.set_key("key1", "updated_value1", "PS256")
        assert cache.size() == 2
        cached_key1 = cache.get_key("key1")
        cached_key2 = cache.get_key("key2")
        assert cached_key1 is not None
        assert cached_key1.key == "updated_value1"
        assert cached_key1.algorithm == "PS256"
        assert cached_key2 is not None
        assert cached_key2.key == "value2"

    def test_clear_cache(self):
        """Test clearing the entire cache."""
        cache = JWKSCache()

        cache.set_key("key1", "value1", "RS256")
        cache.set_key("key2", "value2", "ES256")
        cache.set_key(None, "default_value", "PS256")
        assert cache.size() == 3

        cache.clear()
        assert cache.size() == 0
        assert cache.get_key("key1") is None
        assert cache.get_key("key2") is None
        assert cache.get_key(None) is None

    def test_remove_specific_key(self):
        """Test removing specific keys from cache."""
        cache = JWKSCache()

        cache.set_key("key1", "value1", "RS256")
        cache.set_key("key2", "value2", "ES256")
        cache.set_key(None, "default_value", "PS256")
        assert cache.size() == 3

        # Remove existing key
        assert cache.remove_key("key1") is True
        assert cache.size() == 2
        assert cache.get_key("key1") is None
        cached_key2 = cache.get_key("key2")
        assert cached_key2 is not None
        assert cached_key2.key == "value2"

        # Remove default key
        assert cache.remove_key(None) is True
        assert cache.size() == 1
        assert cache.get_key(None) is None

        # Remove non-existent key
        assert cache.remove_key("nonexistent") is False
        assert cache.size() == 1

    def test_cached_kids_method(self):
        """Test getting all cached key IDs."""
        cache = JWKSCache()

        assert cache.cached_kids() == []

        cache.set_key("key1", "value1", "RS256")
        cache.set_key("key2", "value2", "ES256")
        cache.set_key(None, "default_value", "PS256")

        cached_kids = cache.cached_kids()
        assert len(cached_kids) == 3
        assert "key1" in cached_kids
        assert "key2" in cached_kids
        assert "_default" in cached_kids

    @patch("time.time")
    def test_get_stats(self, mock_time):
        """Test cache statistics reporting."""
        cache = JWKSCache(ttl=300, max_size=5)
        mock_time.return_value = 1000.0

        # Empty cache stats
        stats = cache.get_stats()
        assert stats["cache_size"] == 0
        assert stats["max_size"] == 5
        assert stats["ttl_seconds"] == 300
        assert stats["expired_entries"] == 0
        assert stats["cached_keys"] == []

        # Add some entries
        cache.set_key("key1", "value1", "RS256")
        mock_time.return_value = 1100.0  # 100 seconds later
        cache.set_key("key2", "value2", "ES256")

        # Check stats
        mock_time.return_value = 1200.0  # 200 seconds from start
        stats = cache.get_stats()
        assert stats["cache_size"] == 2
        assert stats["expired_entries"] == 0
        assert len(stats["cached_keys"]) == 2
        assert "key1" in stats["cached_keys"]
        assert "key2" in stats["cached_keys"]

        # Check individual key details
        key1_details = stats["cache_details"]["key1"]
        assert key1_details["age_seconds"] == 200.0
        assert key1_details["expired"] is False

        key2_details = stats["cache_details"]["key2"]
        assert key2_details["age_seconds"] == 100.0
        assert key2_details["expired"] is False

        # Advance time to make key1 expired
        mock_time.return_value = 1350.0  # 350 seconds from start
        stats = cache.get_stats()
        assert stats["expired_entries"] == 1
        key1_details = stats["cache_details"]["key1"]
        assert key1_details["expired"] is True

    @patch("time.time")
    def test_cleanup_expired(self, mock_time):
        """Test cleaning up expired entries."""
        cache = JWKSCache(ttl=300)
        mock_time.return_value = 1000.0

        # Add entries at different times
        cache.set_key("key1", "value1", "RS256")
        mock_time.return_value = 1100.0
        cache.set_key("key2", "value2", "ES256")
        mock_time.return_value = 1200.0
        cache.set_key("key3", "value3", "PS256")

        assert cache.size() == 3

        # Advance time to expire key1 and key2
        mock_time.return_value = 1450.0
        removed_count = cache.cleanup_expired()

        assert removed_count == 2  # key1 and key2 expired
        assert cache.size() == 1
        assert cache.get_key("key1") is None
        assert cache.get_key("key2") is None
        cached_key3 = cache.get_key("key3")
        assert cached_key3 is not None
        assert cached_key3.key == "value3"

    @patch("time.time")
    def test_kid_none_mapping(self, mock_time):
        """Test that kid=None maps to '_default' internally."""
        cache = JWKSCache()
        mock_time.return_value = 1000.0

        # Set keys with None and string kid
        cache.set_key(None, "default_key", "RS256")
        cache.set_key("specific_kid", "specific_key", "ES256")

        stats = cache.get_stats()
        assert stats["cache_size"] == 2

        # Check that both keys are accessible
        cached_default = cache.get_key(None)
        cached_specific = cache.get_key("specific_kid")
        assert cached_default is not None
        assert cached_default.key == "default_key"
        assert cached_specific is not None
        assert cached_specific.key == "specific_key"

        # Verify internal key mapping in stats
        cached_keys = stats["cached_keys"]
        assert "_default" in cached_keys
        assert "specific_kid" in cached_keys

    def test_real_pem_keys(self):
        """Test with realistic PEM key formats."""
        cache = JWKSCache()

        # Realistic PEM key examples
        rsa_key = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4f5wg5l2hKsTeNem/V41
fGnJm6gOdrj8ym3rFkEjWT2btf0hNSqGo6RWqwZyT9QHGdBGqhCdM4GlVOe5NB4d
-----END PUBLIC KEY-----"""

        ec_key = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEYbfkjgLbJK2Bg8gn7dxH8bhxJsLm
6HjOCZuKyXkYkF8dKf5rY9K7wjKJnFk8GlVOe5NB4dR8gn7dxH8bhxJsLm6Hj
-----END PUBLIC KEY-----"""

        cache.set_key("rsa_key_1", rsa_key, "RS256")
        cache.set_key("ec_key_1", ec_key, "ES256")

        cached_rsa = cache.get_key("rsa_key_1")
        cached_ec = cache.get_key("ec_key_1")
        assert cached_rsa is not None
        assert cached_rsa.key == rsa_key
        assert cached_rsa.algorithm == "RS256"
        assert cached_ec is not None
        assert cached_ec.key == ec_key
        assert cached_ec.algorithm == "ES256"

        stats = cache.get_stats()
        assert stats["cache_size"] == 2

    def test_algorithm_storage(self):
        """Test that algorithms are properly stored and retrieved."""
        cache = JWKSCache()

        # Test different algorithms
        algorithms = ["RS256", "ES256", "PS256", "HS256", "ES384", "RS512"]
        for i, alg in enumerate(algorithms):
            cache.set_key(f"key_{i}", f"key_value_{i}", alg)

        # Verify all algorithms are stored correctly
        for i, alg in enumerate(algorithms):
            cached_key = cache.get_key(f"key_{i}")
            assert cached_key is not None
            assert cached_key.algorithm == alg
            assert cached_key.key == f"key_value_{i}"

        assert cache.size() == len(algorithms)

    def test_thread_safety_concurrent_access(self):
        """Test that concurrent read/write operations are thread-safe."""
        cache = JWKSCache(ttl=10, max_size=100)
        num_threads = 10
        operations_per_thread = 50

        def worker(thread_id: int) -> list[str]:
            """Worker function that performs mixed read/write operations."""
            results = []
            for i in range(operations_per_thread):
                key_id = f"thread_{thread_id}_key_{i}"

                cache.set_key(key_id, f"value_{thread_id}_{i}", "RS256")
                results.append(f"set_{key_id}")

                cached_key = cache.get_key(key_id)
                if cached_key:
                    results.append(f"get_{key_id}_success")
                else:
                    results.append(f"get_{key_id}_failed")

                if i % 10 == 0:
                    cache.remove_key(key_id)
                    results.append(f"remove_{key_id}")

            return results

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]

            all_results = []
            for future in as_completed(futures):
                all_results.extend(future.result())

        assert len(all_results) > 0

        final_size = cache.size()
        assert final_size >= 0  # Should not crash or have negative size

        cache.set_key("final_test", "final_value", "ES256")
        final_key = cache.get_key("final_test")
        assert final_key is not None
        assert final_key.key == "final_value"

    def test_thread_safety_stats_during_modifications(self):
        """Test that get_stats() is safe during concurrent modifications."""
        cache = JWKSCache(ttl=5, max_size=50)

        def modifier():
            """Continuously modify the cache."""
            for i in range(100):
                cache.set_key(f"key_{i}", f"value_{i}", "RS256")
                time.sleep(0.001)  # Small delay to allow interleaving
                if i % 10 == 0:
                    cache.remove_key(f"key_{i-5}")

        def stats_reader():
            """Continuously read stats."""
            stats_results = []
            for _ in range(50):
                try:
                    stats = cache.get_stats()
                    stats_results.append(stats["cache_size"])
                    time.sleep(0.002)
                except Exception as e:
                    stats_results.append(f"error: {e}")
            return stats_results

        with ThreadPoolExecutor(max_workers=3) as executor:
            modifier_future = executor.submit(modifier)
            stats_future1 = executor.submit(stats_reader)
            stats_future2 = executor.submit(stats_reader)

            modifier_future.result()
            stats1 = stats_future1.result()
            stats2 = stats_future2.result()

        for result in stats1 + stats2:
            assert not isinstance(result, str) or not result.startswith("error:")

    def test_thread_safety_cleanup_during_access(self):
        """Test that cleanup_expired is safe during concurrent access."""
        cache = JWKSCache(ttl=1, max_size=20)  # Short TTL for testing

        for i in range(10):
            cache.set_key(f"key_{i}", f"value_{i}", "RS256")

        time.sleep(1.2)

        def accessor():
            """Try to access keys while cleanup is running."""
            results = []
            for _i in range(20):
                for j in range(10):
                    key = cache.get_key(f"key_{j}")
                    results.append(key is not None)
                time.sleep(0.001)
            return results

        def cleaner():
            """Run cleanup periodically."""
            cleanup_results = []
            for _ in range(10):
                removed = cache.cleanup_expired()
                cleanup_results.append(removed)
                time.sleep(0.005)
            return cleanup_results

        with ThreadPoolExecutor(max_workers=3) as executor:
            accessor_future1 = executor.submit(accessor)
            accessor_future2 = executor.submit(accessor)
            cleaner_future = executor.submit(cleaner)

            access_results1 = accessor_future1.result()
            access_results2 = accessor_future2.result()
            cleanup_results = cleaner_future.result()

        assert len(access_results1) > 0
        assert len(access_results2) > 0
        assert len(cleanup_results) > 0

        cache.set_key("post_test", "post_value", "RS256")
        assert cache.get_key("post_test") is not None
