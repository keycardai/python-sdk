"""Core wire types for HTTP operations.

These are internal types for representing HTTP requests and responses
at the byte level. No JSON handling here - just raw HTTP data.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class HttpRequest:
    """Raw HTTP request representation."""

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None  # None for GET etc.


@dataclass(frozen=True)
class HttpResponse:
    """Raw HTTP response representation."""

    status: int
    headers: Mapping[str, str]
    body: bytes
