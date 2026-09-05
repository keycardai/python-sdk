"""Metadata cache with non-sticky failure semantics.

A metadata cache (authorization-server metadata, token endpoint, jwks_uri)
records a success for ``discovery_ttl``. A transient failure (network error,
timeout, HTTP 5xx, HTTP 429) is never recorded; the next call fetches again.
A deterministic failure may be recorded for at most ``negative_ttl``, never
longer than ``discovery_ttl``; ``negative_ttl`` of 0 disables that.
"""

from __future__ import annotations

import time
from typing import Generic, TypeVar

DEFAULT_DISCOVERY_TTL = 3600.0
DEFAULT_NEGATIVE_TTL = 60.0


T = TypeVar("T")
E = TypeVar("E", bound=Exception)


def _now() -> float:
    return time.time()


class MetadataCache(Generic[T, E]):
    """Single-slot cache of a discovery outcome.

    ``lookup`` returns the cached value when it is fresh, raises the cached
    deterministic failure while it is remembered, and returns ``None`` on a
    cold or expired slot.
    """

    def __init__(
        self,
        discovery_ttl: float = DEFAULT_DISCOVERY_TTL,
        negative_ttl: float = DEFAULT_NEGATIVE_TTL,
    ):
        self.discovery_ttl = float(discovery_ttl)
        self.negative_ttl = float(negative_ttl)
        self._value: T | None = None
        self._error: E | None = None
        self._expires_at = 0.0

    def lookup(self) -> T | None:
        if _now() >= self._expires_at:
            self._value = None
            self._error = None
            return None
        if self._error is not None:
            raise self._error
        return self._value

    def store_success(self, value: T) -> T:
        self._value = value
        self._error = None
        self._expires_at = _now() + self.discovery_ttl
        return value

    def store_failure(self, error: E, *, retryable: bool) -> E:
        """Remember ``error`` when it is deterministic; return it for raising."""
        if retryable or self.negative_ttl <= 0:
            return error
        self._value = None
        self._error = error
        self._expires_at = _now() + min(self.negative_ttl, self.discovery_ttl)
        return error
