"""Per-entry caching and retry pacing for authorization-server metadata."""

from __future__ import annotations

import random
import time
from email.utils import parsedate_to_datetime
from typing import Generic, TypeVar

from .exceptions import OAuthHttpError

DEFAULT_DISCOVERY_TTL = 3600.0
DEFAULT_NEGATIVE_TTL = 60.0


T = TypeVar("T")
E = TypeVar("E", bound=Exception)


def _now() -> float:
    return time.time()


def _retry_after(error: OAuthHttpError) -> float | None:
    value = next(
        (v.strip() for k, v in error.headers.items() if k.lower() == "retry-after"),
        None,
    )
    if not value:
        return None
    if value.isascii() and value.isdecimal():
        return float(min(int(value.lstrip("0")[:3] or "0"), 60))
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is not None:
            return min(60.0, max(0.0, date.timestamp() - time.time()))
    except (ValueError, OverflowError, OSError):
        pass
    return None


class MetadataRetryDelay(Generic[E]):
    def __init__(self) -> None:
        self._error: E | None = None
        self._until = 0.0
        self._next = 1.0

    def check(self) -> None:
        if self._error is not None and _now() < self._until:
            raise self._error
        self._error = None

    def record(self, error: E, cause: Exception | None) -> None:
        delay = None
        if isinstance(cause, OAuthHttpError):
            if cause.status_code in (429, 503):
                delay = _retry_after(cause)
            if cause.status_code == 429 and delay is None:
                return
        if delay is None:
            delay = min(30.0, self._next * random.uniform(0.8, 1.2))
            self._next = min(32.0, self._next * 2)
        if delay > 0:
            self._error = error
            self._until = _now() + delay

    def reset(self) -> None:
        self._error = None
        self._next = 1.0


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
        self._stored_at = 0.0
        self.retry_delay: MetadataRetryDelay[E] = MetadataRetryDelay()

    def lookup(self, *, refresh: bool = False) -> T | None:
        if _now() >= self._expires_at:
            self._value = None
            self._error = None
        if not refresh:
            if self._error is not None:
                raise self._error
            if self._value is not None:
                return self._value
        self.retry_delay.check()
        return None

    def store_success(self, value: T) -> T:
        self._value = value
        self._error = None
        self.retry_delay.reset()
        self._stored_at = _now()
        self._expires_at = self._stored_at + self.discovery_ttl
        return value

    def success_age(self) -> float | None:
        """Return the seconds since the cached success was stored.

        None when the slot does not hold a fresh success.
        """
        if self._value is None or _now() >= self._expires_at:
            return None
        return _now() - self._stored_at

    def store_failure(
        self, error: E, *, retryable: bool, cause: Exception | None = None
    ) -> E:
        if retryable:
            self.retry_delay.record(error, cause)
            return error
        if self.negative_ttl <= 0:
            return error
        self._value = None
        self._error = error
        self._expires_at = _now() + min(self.negative_ttl, self.discovery_ttl)
        return error
