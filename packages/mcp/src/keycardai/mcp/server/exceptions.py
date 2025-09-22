"""Exception classes for KeyCard MCP integration.

This module defines all custom exceptions used throughout the mcp package,
providing clear error types and documentation for different failure scenarios.
"""

from __future__ import annotations


class MCPServerError(Exception):
    """Base exception for all KeyCard MCP server errors.

    This is the base class for all exceptions raised by the KeyCard MCP
    server package. It provides a common interface for error handling
    and allows catching all MCP server-related errors with a single except clause.

    Attributes:
        message: Human-readable error message
        details: Optional dictionary with additional error context
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ):
        """Initialize MCP server error.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional error context
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """Return string representation of the error."""
        return self.message


class AuthProviderConfigurationError(MCPServerError):
    """Raised when AuthProvider is misconfigured.

    This exception is raised during AuthProvider initialization when
    the provided configuration is invalid or incomplete.
    """

    def __init__(self, message: str = "Zone URL or Zone ID must be provided"):
        """Initialize configuration error."""
        super().__init__(message)


class OAuthClientConfigurationError(MCPServerError):
    """Raised when OAuth client is misconfigured."""

    def __init__(self):
        """Initialize OAuth client configuration error."""
        super().__init__(
            "OAuth client not available. Ensure the client is properly configured."
        )


class MetadataDiscoveryError(MCPServerError):
    """Raised when KeyCard zone metadata discovery fails."""

    def __init__(self, issuer: str | None = None, zone_id: str | None = None):
        """Initialize zone discovery error."""
        message = "Failed to discover KeyCard zone endpoints"
        if issuer:
            message += f" from issuer: {issuer}"
        if zone_id:
            message += f" (zone: {zone_id})"
        super().__init__(
            message,
        )

class JWKSInitializationError(MCPServerError):
    """Raised when JWKS initialization fails."""

    def __init__(self):
        """Initialize JWKS initialization error."""
        super().__init__(
            "Failed to initialize JWKS",
        )


class JWKSValidationError(MCPServerError):
    """Raised when JWKS URI validation fails."""

    def __init__(self):
        """Initialize JWKS validation error."""
        super().__init__(
            "KeyCard zone does not provide a JWKS URI",
        )


class JWKSDiscoveryError(MCPServerError):
    """JWKS discovery failed, typically due to invalid zone_id or unreachable endpoint."""

    def __init__(self, issuer: str | None = None, zone_id: str | None = None):
        """Initialize JWKS discovery error."""
        if issuer:
            message = f"Failed to discover JWKS from issuer: {issuer}"
            if zone_id:
                message += f" (zone: {zone_id})"
        else:
            message = "Failed to discover JWKS endpoints"
        super().__init__(
            message,
        )


class TokenValidationError(MCPServerError):
    """Token validation failed due to invalid token format, signature, or claims."""

    def __init__(self, message: str = "Token validation failed"):
        """Initialize token validation error."""
        super().__init__(
            message,
        )


class TokenExchangeError(MCPServerError):
    """Raised when OAuth token exchange fails."""

    def __init__(self, message: str = "Token exchange failed"):
        """Initialize token exchange error."""
        super().__init__(message)


class UnsupportedAlgorithmError(MCPServerError):
    """JWT algorithm is not supported by the verifier."""

    def __init__(self, algorithm: str):
        """Initialize unsupported algorithm error."""
        super().__init__(f"Unsupported JWT algorithm: {algorithm}")


class VerifierConfigError(MCPServerError):
    """Token verifier configuration is invalid."""

    def __init__(self, message: str = "Token verifier configuration is invalid"):
        """Initialize verifier config error."""
        super().__init__(message)


class CacheError(MCPServerError):
    """JWKS cache operation failed."""

    def __init__(self, message: str = "JWKS cache operation failed"):
        """Initialize cache error."""
        super().__init__(message)


class MissingContextError(MCPServerError):
    """Raised when grant decorator encounters a missing context error."""

    def __init__(self, message: str = "Missing Context parameter. Ensure the Context parameter is properly annotated."):
        """Initialize missing context error."""
        super().__init__(message)


class MissingAccessContextError(MCPServerError):
    """Raised when grant decorator encounters a missing AccessContext error."""

    def __init__(self, message: str = "Missing AccessContext parameter. Ensure the AccessContext parameter is properly annotated."):
        """Initialize missing access context error."""
        super().__init__(message)


class ResourceAccessError(MCPServerError):
    """Raised when accessing a resource token fails."""

    def __init__(self, message: str = "Resource not granted"):
        """Initialize resource access error."""
        super().__init__(message)


class ClientInitializationError(MCPServerError):
    """Raised when OAuth client initialization fails."""

    def __init__(self, message: str = "Failed to initialize OAuth client"):
        """Initialize client initialization error."""
        super().__init__(message)



# Export all exception classes
__all__ = [
    # Base exception
    "MCPServerError",

    # Specific exceptions
    "AuthProviderConfigurationError",
    "OAuthClientConfigurationError",
    "JWKSInitializationError",
    "MetadataDiscoveryError",
    "JWKSValidationError",
    "JWKSDiscoveryError",
    "TokenValidationError",
    "TokenExchangeError",
    "UnsupportedAlgorithmError",
    "VerifierConfigError",
    "CacheError",
    "MissingContextError",
    "MissingAccessContextError",
    "ResourceAccessError",
    "ClientInitializationError",
]
