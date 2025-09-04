"""Transport protocol interfaces for HTTP operations.

This module defines the protocol interfaces that all transport implementations
must follow. Operations call these methods exclusively.
"""

from typing import Protocol

from ._wire import HttpRequest, HttpResponse


class HTTPTransport(Protocol):
    """Protocol for synchronous HTTP transport implementations."""

    def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
        """Execute a raw HTTP request synchronously.

        Args:
            req: The HTTP request to execute
            timeout: Optional timeout in seconds

        Returns:
            The HTTP response

        Raises:
            NetworkError: For network-level failures
        """
        ...


class AsyncHTTPTransport(Protocol):
    """Protocol for asynchronous HTTP transport implementations."""

    async def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
        """Execute a raw HTTP request asynchronously.

        Args:
            req: The HTTP request to execute
            timeout: Optional timeout in seconds

        Returns:
            The HTTP response

        Raises:
            NetworkError: For network-level failures
        """
        ...
