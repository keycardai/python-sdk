"""Authentication-specific exceptions for MCP server.

This module defines exception hierarchy for token verification and authentication
with clear distinction between client authentication failures (401) and server
configuration errors (500).
"""


class AuthError(Exception):
    """Base class for all authentication-related errors."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class AuthConfigError(AuthError):
    """Authentication configuration errors that indicate server misconfiguration.

    These errors should result in 500 Internal Server Error responses as they
    indicate server-side issues that need to be fixed by administrators.
    """
    pass


class AuthenticationError(AuthError):
    """Token authentication failures that indicate invalid credentials.

    These errors should result in 401 Unauthorized responses as they indicate
    client-side authentication issues.
    """
    pass


class JWKSDiscoveryError(AuthenticationError):
    """JWKS discovery failed, typically due to invalid zone_id or unreachable endpoint."""

    def __init__(self, issuer: str, zone_id: str | None = None, cause: Exception | None = None):
        self.issuer = issuer
        self.zone_id = zone_id
        message = f"Failed to discover JWKS from issuer: {issuer}"
        if zone_id:
            message += f" (zone: {zone_id})"
        super().__init__(message, cause)


class TokenValidationError(AuthenticationError):
    """Token validation failed due to invalid token format, signature, or claims."""
    pass


class UnsupportedAlgorithmError(AuthenticationError):
    """JWT algorithm is not supported by the verifier."""

    def __init__(self, algorithm: str):
        self.algorithm = algorithm
        super().__init__(f"Unsupported JWT algorithm: {algorithm}")


class VerifierConfigError(AuthConfigError):
    """Token verifier configuration is invalid."""
    pass


class CacheError(AuthConfigError):
    """JWKS cache operation failed."""
    pass
