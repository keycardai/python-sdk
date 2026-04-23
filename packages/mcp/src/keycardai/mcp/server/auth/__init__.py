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

from keycardai.oauth import (
    AuthStrategy,
    BasicAuth,
    BearerAuth,
    MultiZoneBasicAuth,
    NoneAuth,
)
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

from ..exceptions import MissingContextError
from .provider import AuthProvider

__all__ = [
    "AuthProvider",
    "AccessContext",
    "AccessToken",
    "TokenVerifier",
    "ApplicationCredential",
    "ClientSecret",
    "EKSWorkloadIdentity",
    "WebIdentity",
    "AuthStrategy",
    "BasicAuth",
    "BearerAuth",
    "MultiZoneBasicAuth",
    "NoneAuth",
    "AuthProviderConfigurationError",
    "EKSWorkloadIdentityConfigurationError",
    "EKSWorkloadIdentityRuntimeError",
    "TokenExchangeError",
    "ResourceAccessError",
    "MissingAccessContextError",
    "MissingContextError",
    "MetadataDiscoveryError",
]
