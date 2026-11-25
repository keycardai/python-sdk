"""Per-entry caching and retry pacing for authorization-server metadata."""

from __future__ import annotations

import random
import re
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Generic, TypeVar

from .exceptions import OAuthError, OAuthHttpError

DEFAULT_DISCOVERY_TTL = 3600.0
DEFAULT_NEGATIVE_TTL = 60.0


T = TypeVar("T")
E = TypeVar("E", bound=Exception)


def _now() -> float:
    return time.monotonic()


_MONTHS: Sequence[str] = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_WEEKDAYS = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()
_HTTP_DATES = (
    re.compile(
        r"(?P<weekday>[A-Z][a-z]{2}), (?P<day>[0-9]{2}) (?P<month>[A-Z][a-z]{2}) "
        r"(?P<year>[0-9]{4}) (?P<time>[0-9]{2}:[0-9]{2}:[0-9]{2}) GMT"
    ),
    re.compile(
        r"(?P<weekday>[A-Z][a-z]+), (?P<day>[0-9]{2})-(?P<month>[A-Z][a-z]{2})-"
        r"(?P<year>[0-9]{2}) (?P<time>[0-9]{2}:[0-9]{2}:[0-9]{2}) GMT"
    ),
    re.compile(
        r"(?P<weekday>[A-Z][a-z]{2}) (?P<month>[A-Z][a-z]{2}) (?P<day>[0-9]{2}| [1-9]) "
        r"(?P<time>[0-9]{2}:[0-9]{2}:[0-9]{2}) (?P<year>[0-9]{4})"
    ),
)
MAX_RETRY_CACHE_ENTRIES = 128


def _http_date(value: str, now: float) -> float | None:
    for index, pattern in enumerate(_HTTP_DATES):
        match = pattern.fullmatch(value)
        if match is None:
            continue
        fields = match.groupdict()
        hour, minute, second = map(int, fields["time"].split(":"))
        year = int(fields["year"])
        month = _MONTHS.index(fields["month"]) + 1
        day = int(fields["day"])
        if index == 1:
            current = datetime.fromtimestamp(now, timezone.utc)
            limit = current.year + 50
            year += (limit - year) // 100 * 100
            if (year, month, day, hour, minute, second) > (
                limit, current.month, current.day, current.hour, current.minute, current.second
            ):
                year -= 100
        date = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        weekday = _WEEKDAYS[date.weekday()]
        if fields["weekday"] == (weekday if index == 1 else weekday[:3]):
            return date.timestamp()
    return None


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
        now = time.time()
        date = _http_date(value, now)
        if date is not None:
            return min(60.0, max(0.0, date - now))
    except (ValueError, OverflowError, OSError):
        pass
    return None


class MetadataRetryDelay(Generic[E]):
    def __init__(self) -> None:
        self._failure: tuple[E, float] | None = None
        self._next = 1.0
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            if self._failure is not None:
                error, until = self._failure
                if _now() < until:
                    raise error.with_traceback(None)

    def record(self, error: E, cause: Exception | None) -> bool:
        delay = None
        if isinstance(cause, OAuthHttpError) and cause.status_code in (429, 503):
            delay = _retry_after(cause)
        # An oversized 429 carries no readable Retry-After, so like any 429
        # without a valid one it starts no delay.
        status_code = getattr(cause, "status_code", None)
        if isinstance(cause, OAuthError) and status_code == 429 and delay is None:
            return False
        with self._lock:
            if delay is None:
                delay = min(30.0, self._next * random.uniform(0.8, 1.2))
                self._next = min(32.0, self._next * 2)
            if delay <= 0:
                return False
            self._failure = error, _now() + delay
            return True

    def reset(self) -> None:
        with self._lock:
            self._failure = None
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
        self._lock = threading.Lock()

    def lookup(self, *, refresh: bool = False) -> T | None:
        with self._lock:
            if not refresh and _now() < self._expires_at:
                if self._error is not None:
                    raise self._error.with_traceback(None)
                if self._value is not None:
                    return self._value
            self.retry_delay.check()
            return None

    def store_success(self, value: T) -> T:
        with self._lock:
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
        with self._lock:
            now = _now()
            if self._value is None or now >= self._expires_at:
                return None
            return now - self._stored_at

    def store_failure(
        self, error: E, *, retryable: bool, cause: Exception | None = None
    ) -> E:
        if retryable:
            self.retry_delay.record(error, cause)
            return error
        if self.negative_ttl <= 0:
            return error
        with self._lock:
            self._value = None
            self._error = error
            self._expires_at = _now() + min(self.negative_ttl, self.discovery_ttl)
        return error
