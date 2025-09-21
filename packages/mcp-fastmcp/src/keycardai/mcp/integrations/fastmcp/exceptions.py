"""Exception classes for KeyCard FastMCP integration.

This module defines all custom exceptions used throughout the mcp-fastmcp package,
providing clear error types and documentation for different failure scenarios.
"""

from __future__ import annotations


class FastMCPIntegrationError(Exception):
    """Base exception for all KeyCard FastMCP integration errors.

    This is the base class for all exceptions raised by the KeyCard FastMCP
    integration package. It provides a common interface for error handling
    and allows catching all integration-related errors with a single except clause.

    Attributes:
        message: Human-readable error message
        error_code: Optional error code for programmatic handling
        details: Optional dictionary with additional error context
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        details: dict[str, str] | None = None,
    ):
        """Initialize FastMCP integration error.

        Args:
            message: Human-readable error message
            error_code: Optional error code for programmatic handling
            details: Optional dictionary with additional error context
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}

    def __str__(self) -> str:
        """Return string representation of the error."""
        if self.error_code:
            return f"[{self.error_code}] {self.message}"
        return self.message


class AuthProviderConfigurationError(FastMCPIntegrationError):
    """Raised when AuthProvider is misconfigured.

    This exception is raised during AuthProvider initialization when
    the provided configuration is invalid or incomplete.
    """

    def __init__(self):
        """Initialize configuration error."""
        super().__init__(
            "Zone ID or Zone URL must be provided",
            error_code="CONFIGURATION_ERROR",
        )

class OAuthClientConfigurationError(FastMCPIntegrationError):
    """Raised when OAuth client is misconfigured."""

    def __init__(self):
        """Initialize OAuth client configuration error."""
        super().__init__(
            "OAuth client not available. Ensure the client is properly configured.",
            error_code="CONFIGURATION_ERROR",
        )


class ZoneDiscoveryError(FastMCPIntegrationError):
    """Raised when KeyCard zone metadata discovery fails."""

    def __init__(self):
        """Initialize zone discovery error."""
        super().__init__(
            "Failed to discover KeyCard zone endpoints",
            error_code="ZONE_DISCOVERY_ERROR",
        )


class JWKSValidationError(FastMCPIntegrationError):
    """Raised when JWKS URI validation fails."""

    def __init__(self):
        """Initialize JWKS validation error."""
        super().__init__(
            "KeyCard zone does not provide a JWKS URI",
            error_code="JWKS_VALIDATION_ERROR",
        )


class TokenExchangeError(FastMCPIntegrationError):
    """Raised when OAuth token exchange fails."""

    def __init__(self):
        """Initialize token exchange error."""
        super().__init__(
            "Token exchange failed",
            error_code="TOKEN_EXCHANGE_ERROR",
        )


class MissingContextError(FastMCPIntegrationError):
    """Raised when grant decorator encounters a missing context error."""

    def __init__(self):
        """Initialize missing context error."""
        super().__init__(
            "Missing Context paramete. Ensure the Context parameter is properly annotated.",
            error_code="GRANT_DECORATOR_ERROR",
        )


class ResourceAccessError(FastMCPIntegrationError):
    """Raised when accessing a resource token fails."""

    def __init__(self):
        """Initialize resource access error."""
        super().__init__(
            "Resource not granted",
            error_code="RESOURCE_ACCESS_ERROR",
        )


class ClientInitializationError(FastMCPIntegrationError):
    """Raised when OAuth client initialization fails."""

    def __init__(self):
        """Initialize client initialization error."""
        super().__init__(
            "Failed to initialize OAuth client",
            error_code="CLIENT_INITIALIZATION_ERROR",
        )


# Convenience aliases for backward compatibility and ease of use
ConfigurationError = AuthProviderConfigurationError
DiscoveryError = ZoneDiscoveryError
ExchangeError = TokenExchangeError
DecoratorError = MissingContextError
AccessError = ResourceAccessError
InitializationError = ClientInitializationError

# Export all exception classes
__all__ = [
    # Base exception
    "FastMCPIntegrationError",

    # Specific exceptions
    "AuthProviderConfigurationError",
    "ZoneDiscoveryError",
    "JWKSValidationError",
    "TokenExchangeError",
    "ResourceAccessError",
    "ClientInitializationError",

    # Convenience aliases
    "ConfigurationError",
    "DiscoveryError",
    "ExchangeError",
    "DecoratorError",
    "AccessError",
    "InitializationError",
]
