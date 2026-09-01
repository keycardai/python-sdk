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

# OAuth error codes where a retry cannot help: the authorization server said
# no on policy grounds, the caller lacks the delegated authorization it asked
# for, or the client credentials themselves are wrong. Every other exchange
# failure (transport faults, rate limits, 5xx, malformed responses) is treated
# as retryable.
PERMANENT_ERROR_CODES: frozenset[str] = frozenset(
    {"access_denied", "insufficient_authorization", "invalid_client"}
)


class OAuthError(Exception):
    """Base class for all OAuth 2.0 errors."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause

    @property
    def retryable(self) -> bool:
        """Whether repeating the failed operation unchanged could succeed.

        False by default: configuration, authentication, and other
        client-side faults require a code or config change. Subclasses
        raised on the token exchange paths (OAuthProtocolError,
        OAuthHttpError, NetworkError) derive the value from the failure.
        """
        return False


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

    @property
    def retryable(self) -> bool:
        """True for 429 and 5xx responses, False for other 4xx responses."""
        return self.retriable


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

    @property
    def retryable(self) -> bool:
        """Whether the OAuth error code leaves room for a retry to succeed.

        False for the permanent denials in PERMANENT_ERROR_CODES, where no
        retry can change the outcome:

        - ``access_denied``: zone policy denied the request.
        - ``insufficient_authorization``: the caller lacks the delegated
          authorization the exchange requires.
        - ``invalid_client``: client authentication failed.

        True for every other code, including ``invalid_response`` for
        malformed server responses, so transport-shaped failures stay
        retryable. Independent of the legacy ``retriable`` attribute, which
        is always False for protocol errors.
        """
        return self.error not in PERMANENT_ERROR_CODES


class AuthorizationDeniedError(OAuthProtocolError):
    """Authorization endpoint denial returned in a redirect callback.

    Carries the OAuth ``error`` and optional ``error_description`` values
    returned by the authorization server.
    """

    def __init__(
        self,
        error: str,
        error_description: str | None = None,
        error_uri: str | None = None,
        operation: str = "",
    ):
        super().__init__(error, error_description, error_uri, operation)


class StateMismatchError(OAuthError):
    """The authorization callback state is missing or does not match."""

    def __init__(self, message: str = "Authorization callback state mismatch"):
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

    @property
    def retryable(self) -> bool:
        """Mirrors ``retriable``: True unless the transport marked the fault permanent."""
        return self.retriable


class ConfigError(OAuthError):
    """Client configuration errors.

    Raised when client is misconfigured (missing endpoints, invalid parameters, etc.).
    These are never retriable as they require code changes.
    """

    def __init__(self, message: str):
        super().__init__(message)


class AuthenticationError(OAuthError):
    """Authentication failures with OAuth 2.0 servers.

    Raised when client authentication fails, typically due to invalid
    credentials or authentication method issues.
    """

    def __init__(self, message: str):
        super().__init__(message)


class TokenExchangeError(OAuthProtocolError):
    """OAuth 2.0 Token Exchange specific errors (RFC 8693).

    Raised for token exchange protocol violations and error responses.
    Inherits from OAuthProtocolError with token exchange semantics.
    """

    def __init__(
        self,
        error: str,
        error_description: str | None = None,
        error_uri: str | None = None,
        operation: str = "",
    ):
        super().__init__(error, error_description, error_uri, operation)


class JWKSError(OAuthError):
    """Base class for JWKS key-resolution failures (fetch and key lookup).

    Raised by the low-level JWKS helper. Catch this to handle any JWKS
    resolution failure, or a subclass for a single category.
    """


class JWKSFetchError(JWKSError):
    """The JWKS endpoint could not be fetched or returned a non-2xx response."""


class JWKSKeyNotFoundError(JWKSError):
    """The requested key (`kid`) was not present in the fetched JWKS."""


class InvalidTokenError(OAuthError):
    """A presented token failed verification or was rejected by a server.

    Raised by the verify surface for any token-validity failure: an
    unsupported algorithm, a missing ``kid`` header, an untrusted or missing
    issuer, an expired token, an audience or scope mismatch, or a bad
    signature. Also raised when a resource server rejects the token with
    HTTP 401, as the UserInfo endpoint does.

    Carries the RFC 6750 error code, ``invalid_token`` unless a server
    challenge named a different one.
    """

    error_code = "invalid_token"

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
