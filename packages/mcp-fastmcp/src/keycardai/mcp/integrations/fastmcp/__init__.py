"""Deprecated import path for the FastMCP integration.

The implementation moved to :mod:`keycardai.fastmcp` (PyPI:
``keycardai-fastmcp``). This module re-exports the public API at the old
``keycardai.mcp.integrations.fastmcp`` path so existing callers keep working,
and emits a :class:`DeprecationWarning` on first import. Migrate at your
convenience: rename your imports from
``keycardai.mcp.integrations.fastmcp`` to ``keycardai.fastmcp``.
"""

import warnings as _warnings

_warnings.warn(
    "keycardai.mcp.integrations.fastmcp is deprecated; "
    "import from keycardai.fastmcp instead "
    "(pip install keycardai-fastmcp).",
    DeprecationWarning,
    stacklevel=2,
)

from keycardai.fastmcp import (  # noqa: E402, F401
    AccessContext,
    ApplicationCredential,
    AuthProvider,
    AuthProviderConfigurationError,
    AuthProviderInternalError,
    AuthProviderRemoteError,
    AuthStrategy,
    BasicAuth,
    ClientFactory,
    ClientInitializationError,
    ClientSecret,
    DefaultClientFactory,
    EKSWorkloadIdentity,
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
    JWKSValidationError,
    MCPServerError,
    MetadataDiscoveryError,
    MissingContextError,
    MultiZoneBasicAuth,
    NoneAuth,
    OAuthClientConfigurationError,
    ResourceAccessError,
    TokenExchangeError,
    WebIdentity,
    mock_access_context,
)

__all__ = [
    "AccessContext",
    "ApplicationCredential",
    "AuthProvider",
    "AuthProviderConfigurationError",
    "AuthProviderInternalError",
    "AuthProviderRemoteError",
    "AuthStrategy",
    "BasicAuth",
    "ClientFactory",
    "ClientInitializationError",
    "ClientSecret",
    "DefaultClientFactory",
    "EKSWorkloadIdentity",
    "EKSWorkloadIdentityConfigurationError",
    "EKSWorkloadIdentityRuntimeError",
    "JWKSValidationError",
    "MCPServerError",
    "MetadataDiscoveryError",
    "MissingContextError",
    "MultiZoneBasicAuth",
    "NoneAuth",
    "OAuthClientConfigurationError",
    "ResourceAccessError",
    "TokenExchangeError",
    "WebIdentity",
    "mock_access_context",
]
