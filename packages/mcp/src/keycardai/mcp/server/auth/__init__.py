"""Keycard MCP Server Authentication.

This module provides authentication providers and token verification for MCP servers.

Local Definitions:
    AuthProvider, AccessContext, TokenVerifier: Core server auth components
    ApplicationCredential, ClientSecret, WebIdentity, EKSWorkloadIdentity: Credential providers

Re-exports (from keycardai.oauth):
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

from ..exceptions import (
    AuthProviderConfigurationError,
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
    MetadataDiscoveryError,
    MissingAccessContextError,
    MissingContextError,
    ResourceAccessError,
    TokenExchangeError,
)
from .application_credentials import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from .provider import AccessContext, AuthProvider
from .verifier import TokenVerifier

__all__ = [
    # === Core Authentication (Local) ===
    "AuthProvider",
    "AccessContext",
    "TokenVerifier",
    # === Application Credentials (Local) ===
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
    # === Exceptions (re-exported from ..exceptions) ===
    # Configuration errors
    "AuthProviderConfigurationError",
    "EKSWorkloadIdentityConfigurationError",
    # Runtime errors
    "EKSWorkloadIdentityRuntimeError",
    "TokenExchangeError",
    "ResourceAccessError",
    # Context errors - MissingContextError is for FastMCP Context parameter,
    # MissingAccessContextError is for Keycard AccessContext parameter
    "MissingAccessContextError",
    "MissingContextError",
    "MetadataDiscoveryError",
]
