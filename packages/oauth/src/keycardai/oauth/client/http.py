"""HTTP client implementations for both sync and async OAuth 2.0 operations.

This module provides HTTP client abstractions and implementations for enterprise flexibility
with both synchronous and asynchronous support.
"""

import json as json_module
from typing import Any, Protocol

import httpx
import requests

from ..exceptions import NetworkError, OAuthHttpError, OAuthProtocolError

# Protocol Definition


class HTTPClientProtocol(Protocol):
    """Protocol for HTTP client implementations.

    Enables bring-your-own HTTP client for enterprise customization
    (custom SSL, proxies, monitoring, etc.).
    """

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute HTTP request and return parsed JSON response.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            headers: Optional request headers
            data: Optional form data for POST requests
            json: Optional JSON data for POST requests
            auth: Optional basic authentication tuple
            timeout: Optional request timeout

        Returns:
            Parsed JSON response as dictionary

        Raises:
            OAuthProtocolError: If server returns OAuth error response
            OAuthHttpError: If HTTP request fails with error status
            NetworkError: If network request fails
        """
        ...


# Async HTTP Client


class AsyncHTTPClient:
    """Asynchronous HTTP client implementation using httpx.

    Provides enterprise-ready defaults with SSL verification, timeouts,
    and proper error handling for async operations.
    """

    def __init__(
        self,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        max_retries: int = 3,
        user_agent: str = "KeyCardAI-OAuth/0.0.1",
    ):
        """Initialize async HTTP client.

        Args:
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
            max_retries: Maximum number of retry attempts
            user_agent: User agent string for requests
        """
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.user_agent = user_agent

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute async HTTP request with proper OAuth 2.0 error handling.

        Implements RFC-compliant error handling for OAuth 2.0 operations.
        """
        request_headers = headers or {}

        if data is not None:
            request_headers.setdefault(
                "Content-Type", "application/x-www-form-urlencoded"
            )

        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    data=data,
                    json=json,
                    auth=auth,
                    timeout=timeout or self.timeout,
                )

            if response.status_code >= 400:
                await self._handle_http_error(response, method, url)

            try:
                response_data = response.json()
            except json_module.JSONDecodeError as e:
                raise OAuthHttpError(
                    status_code=response.status_code,
                    response_body=response.text,
                    headers=dict(response.headers),
                    operation=f"{method} {url}",
                ) from e

            if isinstance(response_data, dict) and "error" in response_data:
                raise OAuthProtocolError(
                    error=response_data["error"],
                    error_description=response_data.get("error_description"),
                    error_uri=response_data.get("error_uri"),
                    operation=f"{method} {url}",
                )

            return response_data

        except httpx.HTTPError as e:
            raise NetworkError(
                cause=e,
                operation=f"{method} {url}",
                retriable=False,
            ) from e

    async def _handle_http_error(
        self, response: httpx.Response, method: str, url: str
    ) -> None:
        """Handle HTTP error responses with proper classification."""
        response_headers = dict(response.headers)
        response_body = response.text

        oauth_error = None
        try:
            error_data = response.json()
            if isinstance(error_data, dict) and "error" in error_data:
                oauth_error = OAuthProtocolError(
                    error=error_data["error"],
                    error_description=error_data.get("error_description"),
                    error_uri=error_data.get("error_uri"),
                    operation=f"{method} {url}",
                )
        except json_module.JSONDecodeError:
            pass

        if oauth_error:
            raise oauth_error
        raise OAuthHttpError(
            status_code=response.status_code,
            response_body=response_body,
            headers=response_headers,
            operation=f"{method} {url}",
        )

    async def aclose(self):
        """Close the HTTP client (async context manager support)."""
        pass

    async def __aenter__(self):
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context manager."""
        await self.aclose()


# Sync HTTP Client


class HTTPClient:
    """Synchronous HTTP client implementation using requests.

    Provides enterprise-ready defaults with SSL verification, timeouts,
    and proper error handling for synchronous operations.
    """

    def __init__(
        self,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        max_retries: int = 3,
        user_agent: str = "KeyCardAI-OAuth/0.0.1",
    ):
        """Initialize synchronous HTTP client.

        Args:
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
            max_retries: Maximum number of retry attempts
            user_agent: User agent string for requests
        """
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.user_agent = user_agent

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute sync HTTP request with proper OAuth 2.0 error handling.

        Implements RFC-compliant error handling for OAuth 2.0 operations.
        """
        request_headers = headers or {}
        if data is not None:
            request_headers.setdefault(
                "Content-Type", "application/x-www-form-urlencoded"
            )

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=request_headers,
                data=data,
                json=json,
                auth=auth,
                timeout=timeout or self.timeout,
                verify=self.verify_ssl,
            )

            if response.status_code >= 400:
                self._handle_http_error(response, method, url)

            try:
                response_data = response.json()
            except json_module.JSONDecodeError as e:
                raise OAuthHttpError(
                    status_code=response.status_code,
                    response_body=response.text,
                    headers=dict(response.headers),
                    operation=f"{method} {url}",
                ) from e

            if isinstance(response_data, dict) and "error" in response_data:
                raise OAuthProtocolError(
                    error=response_data["error"],
                    error_description=response_data.get("error_description"),
                    error_uri=response_data.get("error_uri"),
                    operation=f"{method} {url}",
                )

            return response_data

        except requests.exceptions.RequestException as e:
            raise NetworkError(
                cause=e,
                operation=f"{method} {url}",
                retriable=False,
            ) from e

    def _handle_http_error(
        self, response: requests.Response, method: str, url: str
    ) -> None:
        """Handle HTTP error responses with proper classification."""
        response_headers = dict(response.headers)
        response_body = response.text

        oauth_error = None
        try:
            error_data = response.json()
            if isinstance(error_data, dict) and "error" in error_data:
                oauth_error = OAuthProtocolError(
                    error=error_data["error"],
                    error_description=error_data.get("error_description"),
                    error_uri=error_data.get("error_uri"),
                    operation=f"{method} {url}",
                )
        except json_module.JSONDecodeError:
            pass

        if oauth_error:
            raise oauth_error
        raise OAuthHttpError(
            status_code=response.status_code,
            response_body=response_body,
            headers=response_headers,
            operation=f"{method} {url}",
        )

    def close(self):
        """Close the HTTP client (context manager support)."""
        pass

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        self.close()
