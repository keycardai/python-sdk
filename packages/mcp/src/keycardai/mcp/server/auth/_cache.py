"""Time-based cache implementation for JWKS verification keys."""

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class JWKSKey:
    """JWKS verification key with timestamp."""
    key: str
    timestamp: float
    algorithm: str

class JWKSCache:
    """Thread-safe time-to-live cache for JWKS verification keys."""

    def __init__(self, ttl: int = 300, max_size: int = 10):
        """Initialize the JWKS cache.

        Args:
            ttl: Time-to-live in seconds (default 300 = 5 minutes)
            max_size: Maximum cache size before clearing (default 10)
        """
        self.ttl = ttl
        self.max_size = max_size
        self._cache: dict[str, JWKSKey] = {}  # key -> (key, timestamp)
        self._lock = threading.RLock()  # Reentrant lock for nested locking

    def get_key(self, kid: str | None) -> JWKSKey | None:
        """Get a verification key from the cache if it exists and hasn't expired.

        Args:
            kid: Key ID from JWT header (None for default key)

        Returns:
            JWKSKey if found and not expired, None otherwise
        """
        cache_key = kid or "_default"

        with self._lock:
            if cache_key not in self._cache:
                return None

            jwks_key = self._cache[cache_key]
            current_time = time.time()
            age = current_time - jwks_key.timestamp

            if age >= self.ttl:
                # Key has expired, remove it
                del self._cache[cache_key]
                return None

            return jwks_key

    def set_key(self, kid: str | None, key: str, algorithm: str) -> None:
        """Set a verification key in the cache with current timestamp.

        Args:
            kid: Key ID from JWT header (None for default key)
            key: PEM-formatted verification key
            algorithm: JWT algorithm for this key
        """
        cache_key = kid or "_default"
        current_time = time.time()

        with self._lock:
            if len(self._cache) >= self.max_size and cache_key not in self._cache:
                self._cache.clear()  # Use direct clear to avoid nested locking

            self._cache[cache_key] = JWKSKey(key, current_time, algorithm)

    def clear(self) -> None:
        """Clear all cached keys."""
        with self._lock:
            self._cache.clear()

    def remove_key(self, kid: str | None) -> bool:
        """Remove a specific key from the cache.

        Args:
            kid: Key ID to remove (None for default key)

        Returns:
            True if key was removed, False if it didn't exist
        """
        cache_key = kid or "_default"
        with self._lock:
            if cache_key in self._cache:
                del self._cache[cache_key]
                return True
            return False

    def size(self) -> int:
        """Get the current cache size."""
        with self._lock:
            return len(self._cache)

    def cached_kids(self) -> list[str]:
        """Get all cached key IDs."""
        with self._lock:
            return list(self._cache.keys())

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics for debugging.

        Returns:
            Dictionary with cache statistics including per-key details
        """
        with self._lock:
            current_time = time.time()

            cache_details = {}
            expired_count = 0

            cache_snapshot = dict(self._cache)

        for cache_key, jwks_key in cache_snapshot.items():
            age = current_time - jwks_key.timestamp
            is_expired = age >= self.ttl
            if is_expired:
                expired_count += 1

            cache_details[cache_key] = {
                "age_seconds": age,
                "expired": is_expired,
            }

        return {
            "cache_size": len(cache_snapshot),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl,
            "expired_entries": expired_count,
            "cached_keys": list(cache_snapshot.keys()),
            "cache_details": cache_details,
        }

    def cleanup_expired(self) -> int:
        """Remove all expired keys from the cache.

        Returns:
            Number of entries removed
        """
        with self._lock:
            current_time = time.time()
            expired_keys = []

            for cache_key, jwks_key in self._cache.items():
                age = current_time - jwks_key.timestamp
                if age >= self.ttl:
                    expired_keys.append(cache_key)

            for cache_key in expired_keys:
                del self._cache[cache_key]

            return len(expired_keys)
