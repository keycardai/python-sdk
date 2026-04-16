"""Keycard MCP Server Authentication.

This module provides authentication providers and token verification for MCP servers.

Re-exports from keycardai.oauth.server (canonical location):
    AccessContext, TokenVerifier, AccessToken: Core server auth components
    ApplicationCredential, ClientSecret, WebIdentity, EKSWorkloadIdentity: Credential providers

Local definitions (MCP-specific):
    AuthProvider: MCP authentication provider with @grant decorator

Re-exports from keycardai.oauth:
    AuthStrategy, BasicAuth, BearerAuth, MultiZoneBasicAuth, NoneAuth: HTTP auth strategies
"""

# Re-export auth strategies from keycardai.oauth for convenience
from keycardai.oauth import (
    AuthStrategy,
    BasicAuth,
    BearerAuth,
    MultiZoneBasicAuth,
    NoneAuth,
)

# Re-export from canonical oauth.server location
from keycardai.oauth.server import (
    AccessContext,
    AccessToken,
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    TokenVerifier,
    WebIdentity,
)
from keycardai.oauth.server.exceptions import (
    AuthProviderConfigurationError,
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
    MetadataDiscoveryError,
    MissingAccessContextError,
    ResourceAccessError,
    TokenExchangeError,
)

# MCP-specific
from ..exceptions import MissingContextError
from .provider import AuthProvider

__all__ = [
    # === Core Authentication ===
    "AuthProvider",  # MCP-specific (local)
    "AccessContext",  # re-exported from keycardai.oauth.server
    "AccessToken",  # re-exported from keycardai.oauth.server
    "TokenVerifier",  # re-exported from keycardai.oauth.server
    # === Application Credentials (re-exported from keycardai.oauth.server) ===
    "ApplicationCredential",
    "ClientSecret",
    "EKSWorkloadIdentity",
    "WebIdentity",
    # === HTTP Auth Strategies (re-exported from keycardai.oauth) ===
    "AuthStrategy",
    "BasicAuth",
    "BearerAuth",
    "MultiZoneBasicAuth",
    "NoneAuth",
    # === Exceptions ===
    "AuthProviderConfigurationError",
    "EKSWorkloadIdentityConfigurationError",
    "EKSWorkloadIdentityRuntimeError",
    "TokenExchangeError",
    "ResourceAccessError",
    "MissingAccessContextError",
    "MissingContextError",
    "MetadataDiscoveryError",
]
