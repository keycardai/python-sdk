"""Concrete HTTP transport implementations.

This module provides concrete implementations of the HTTP transport protocols
using httpx for both synchronous and asynchronous requests. These operate at the byte level only.
"""

import httpx

from ..exceptions import NetworkError
from ..types.models import ClientConfig
from ._wire import HttpRequest, HttpResponse


class HttpxTransport:
    """Synchronous HTTP transport using the httpx library."""

    def __init__(self, *, config: ClientConfig):
        """Initialize the httpx sync transport.

        Args:
            config: Client configuration
        """
        self.config = config

    def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
        """Execute a raw HTTP request using httpx.

        Args:
            req: The HTTP request to execute
            timeout: Optional timeout in seconds

        Returns:
            The HTTP response

        Raises:
            NetworkError: For network-level failures
        """
        try:
            with httpx.Client(
                verify=self.config.verify_ssl,
                headers={"User-Agent": self.config.user_agent},
                timeout=timeout or self.config.timeout
            ) as client:
                r = client.request(
                    method=req.method,
                    url=req.url,
                    headers=req.headers,
                    content=req.body,  # httpx uses 'content' for raw bytes
                )
            return HttpResponse(status=r.status_code, headers=dict(r.headers), body=r.content)
        except httpx.HTTPError as e:
            raise NetworkError(cause=e, operation=f"{req.method} {req.url}", retriable=False) from e


class HttpxAsyncTransport:
    """Asynchronous HTTP transport using the httpx library."""

    def __init__(self, *, config: ClientConfig):
        """Initialize the httpx async transport.

        Args:
            config: Client configuration
        """
        self.config = config

    async def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
        """Execute a raw HTTP request using httpx.

        Args:
            req: The HTTP request to execute
            timeout: Optional timeout in seconds

        Returns:
            The HTTP response

        Raises:
            NetworkError: For network-level failures
        """
        try:
            async with httpx.AsyncClient(verify=self.config.verify_ssl, headers={"User-Agent": self.config.user_agent}) as c:
                r = await c.request(method=req.method, url=req.url, headers=req.headers, content=req.body, timeout=timeout or self.config.timeout)
            return HttpResponse(status=r.status_code, headers=dict(r.headers), body=r.content)
        except httpx.HTTPError as e:
            raise NetworkError(cause=e, operation=f"{req.method} {req.url}", retriable=False) from e
