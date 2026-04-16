"""Exception classes for Keycard MCP integration.

Framework-free exceptions are re-exported from keycardai.oauth.server.exceptions.
MCP-specific exceptions (MissingContextError) remain defined here.

Backward compatibility: ``MCPServerError`` is an alias for ``OAuthServerError``.
"""

from __future__ import annotations

# Re-export all framework-free exceptions from oauth.server
from keycardai.oauth.server.exceptions import (
    AuthProviderConfigurationError,
    AuthProviderInternalError,
    AuthProviderRemoteError,
    CacheError,
    ClientInitializationError,
    ClientSecretConfigurationError,
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
    JWKSDiscoveryError,
    JWKSInitializationError,
    JWKSValidationError,
    MetadataDiscoveryError,
    MissingAccessContextError,
    OAuthClientConfigurationError,
    OAuthServerError,
    ResourceAccessError,
    TokenExchangeError,
    TokenValidationError,
    UnsupportedAlgorithmError,
    VerifierConfigError,
)

# Backward-compatible alias: existing code using MCPServerError continues to work
MCPServerError = OAuthServerError


# ---------------------------------------------------------------------------
# MCP-specific exceptions (not moved to oauth.server)
# ---------------------------------------------------------------------------


class MissingContextError(OAuthServerError):
    """Raised when grant decorator encounters a missing context error.

    This exception is MCP-specific because it references FastMCP ``Context``
    and ``RequestContext`` types in its guidance messages.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        function_name: str | None = None,
        parameters: list[str] | None = None,
        runtime_context: bool = False,
    ):
        if message is None:
            func_info = f"'{function_name}'" if function_name else "function"

            if runtime_context:
                message = (
                    f"Context parameter not found in {func_info} arguments.\n\n"
                    "This error occurs when:\n"
                    "1. Context parameter is not properly annotated with type hint\n"
                    "2. Context is not passed when calling the function\n\n"
                    "Ensure your function signature looks like:\n"
                    f"  def {function_name or 'your_function'}(ctx: Context, ...):  # <- Context must be type-hinted\n\n"
                    "And Context is passed when calling the function."
                )
            else:
                message = (
                    f"Function {func_info} must have a Context parameter to use @grant decorator.\n\n"
                    "The @grant decorator requires access to Context to store access tokens.\n\n"
                    "Fix by adding Context parameter:\n"
                    "  from fastmcp import Context\n\n"
                    "  @auth_provider.grant('https://api.example.com')\n"
                    f"  async def {function_name or 'your_function'}(ctx: Context, ...):  # <- Add 'ctx: Context' parameter\n"
                    "      access_context = await ctx.get_state('keycardai')\n"
                    "      # ... rest of function"
                )

        details = {
            "function_name": function_name or "unknown",
            "current_parameters": parameters or [],
            "runtime_context": runtime_context,
            "solution": (
                "Add 'ctx: Context' parameter to function signature"
                if not runtime_context
                else "Ensure Context parameter is properly type-hinted and passed"
            ),
        }

        super().__init__(message, details=details)


# Export all exception classes
__all__ = [
    # Base exception (alias)
    "MCPServerError",
    "OAuthServerError",
    # MCP-specific exceptions
    "MissingContextError",
    # Re-exported from keycardai.oauth.server.exceptions
    "AuthProviderConfigurationError",
    "AuthProviderInternalError",
    "AuthProviderRemoteError",
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
    "MissingAccessContextError",
    "ResourceAccessError",
    "ClientInitializationError",
    "ClientSecretConfigurationError",
    "EKSWorkloadIdentityConfigurationError",
    "EKSWorkloadIdentityRuntimeError",
]
