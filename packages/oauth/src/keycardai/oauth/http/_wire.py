"""Core wire types for HTTP operations.

These are internal types for representing HTTP requests and responses
at the byte level. No JSON handling here - just raw HTTP data.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HttpRequest:
    """Raw HTTP request representation."""

    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes | None = field(default=None, repr=False)  # None for GET etc.


@dataclass(frozen=True)
class HttpResponse:
    """Raw HTTP response representation."""

    status: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
