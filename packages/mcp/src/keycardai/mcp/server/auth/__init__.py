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
    "AuthProvider",
    "AccessContext",
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
    "MissingAccessContextError",
    "MissingContextError",
    "ResourceAccessError",
    "TokenExchangeError",
    "MetadataDiscoveryError",
]
