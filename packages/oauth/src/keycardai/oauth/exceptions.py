"""OAuth 2.0 exception hierarchy with retriable classification.

This module provides structured exception classes with deterministic retry guidance
for OAuth 2.0 operations as defined across multiple RFCs.

References:
- RFC 6749: The OAuth 2.0 Authorization Framework
- RFC 8693: OAuth 2.0 Token Exchange
- RFC 7662: OAuth 2.0 Token Introspection
- RFC 7009: OAuth 2.0 Token Revocation
"""

from dataclasses import dataclass


class OAuthError(Exception):
    """Base class for all OAuth 2.0 errors."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


@dataclass
class OAuthHttpError(OAuthError):
    """HTTP-level errors with retry guidance.

    Raised for HTTP status codes indicating server or client errors.
    Includes deterministic retriability classification.
    """

    status_code: int
    response_body: str
    headers: dict[str, str]
    operation: str
    retriable: bool

    def __init__(
        self,
        status_code: int,
        response_body: str = "",
        headers: dict[str, str] | None = None,
        operation: str = "",
    ):
        self.status_code = status_code
        self.response_body = response_body
        self.headers = headers or {}
        self.operation = operation

        # Deterministic retriability classification
        # 429 (rate limit) and 5xx (server errors) are retriable
        # 4xx (client errors except 429) are not retriable
        self.retriable = status_code == 429 or (500 <= status_code < 600)

        message = (
            f"HTTP {status_code} during {operation}"
            if operation
            else f"HTTP {status_code}"
        )
        super().__init__(message)

    def __str__(self) -> str:
        return f"HTTP {self.status_code} during {self.operation} (retriable: {self.retriable})"


@dataclass
class OAuthProtocolError(OAuthError):
    """RFC 6749 error responses from OAuth servers.

    Represents structured error responses as defined in OAuth 2.0 specifications.
    Protocol errors are never retriable as they indicate client-side issues.
    """

    error: str
    error_description: str | None = None
    error_uri: str | None = None
    operation: str = ""
    retriable: bool = False  # Protocol errors are never retriable

    def __init__(
        self,
        error: str,
        error_description: str | None = None,
        error_uri: str | None = None,
        operation: str = "",
    ):
        self.error = error
        self.error_description = error_description
        self.error_uri = error_uri
        self.operation = operation
        self.retriable = False

        message = f"OAuth error: {error}"
        if error_description:
            message += f" - {error_description}"

        super().__init__(message)


@dataclass
class NetworkError(OAuthError):
    """Transport/network failures with retry guidance.

    Covers connection failures, timeouts, and other network-level issues.
    """

    cause: Exception
    retriable: bool
    operation: str = ""

    def __init__(
        self,
        cause: Exception,
        operation: str = "",
        retriable: bool = True,  # Network errors are generally retriable
    ):
        self.cause = cause
        self.operation = operation
        self.retriable = retriable

        message = (
            f"Network error during {operation}: {cause}"
            if operation
            else f"Network error: {cause}"
        )
        super().__init__(message, cause)


class ConfigError(OAuthError):
    """Client configuration errors.

    Raised when client is misconfigured (missing endpoints, invalid parameters, etc.).
    These are never retriable as they require code changes.
    """

    def __init__(self, message: str):
        super().__init__(message)