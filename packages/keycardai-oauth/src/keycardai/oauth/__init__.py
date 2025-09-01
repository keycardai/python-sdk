"""KeyCard AI OAuth SDK

A Python SDK for OAuth 2.0 functionality implementing
multiple OAuth 2.0 standards for comprehensive token management.

Supported OAuth 2.0 Standards:
- RFC 8693: OAuth 2.0 Token Exchange
- RFC 7662: OAuth 2.0 Token Introspection
- RFC 7009: OAuth 2.0 Token Revocation
- RFC 7523: JWT Profile for OAuth 2.0 Client Authentication
- RFC 9068: JWT Profile for OAuth 2.0 Access Tokens
- RFC 6750: OAuth 2.0 Bearer Token Usage
- RFC 8414: OAuth 2.0 Authorization Server Metadata
- RFC 8705: OAuth 2.0 Mutual-TLS Client Authentication
- RFC 7636: Proof Key for Code Exchange (PKCE)
- RFC 9126: OAuth 2.0 Pushed Authorization Requests
"""

# Main client interface
from .client import (
    Client,
    DiscoveryClient,
    IntrospectionClient,
    PushedAuthorizationClient,
    RevocationClient,
    TokenExchangeClient,
)

# Core exceptions
from .exceptions import (
    OAuthError,
    OAuthInvalidClientError,
    OAuthInvalidScopeError,
    OAuthInvalidTokenError,
    OAuthRequestError,
    OAuthTokenExpiredError,
    OAuthTokenInactiveError,
    OAuthUnsupportedGrantTypeError,
)

# Types and models
from .types import (
    AuthorizationServerMetadata,
    IntrospectionRequest,
    IntrospectionResponse,
    PushedAuthorizationRequest,
    RevocationRequest,
    RevocationTokenTypeHints,
    TokenExchangeRequest,
    TokenExchangeResponse,
    TokenTypeHints,
    TokenTypes,
)

# Utilities
from .utils import (
    BearerToken,
    BearerTokenError,
    BearerTokenErrors,
    BearerTokenValidator,
    CertificateBoundToken,
    JWTAccessToken,
    JWTAccessTokenHandler,
    JWTAccessTokenValidator,
    JWTClientAssertion,
    MutualTLSClientAuth,
    PKCEChallenge,
    PKCEGenerator,
    PKCEMethods,
)

__version__ = "0.0.1"

__all__ = [
    # Version
    "__version__",

    # Main client interface
    "Client",

    # Individual client classes
    "TokenExchangeClient",
    "IntrospectionClient",
    "RevocationClient",
    "PushedAuthorizationClient",
    "DiscoveryClient",

    # Core Exceptions
    "OAuthError",
    "OAuthInvalidTokenError",
    "OAuthTokenExpiredError",
    "OAuthTokenInactiveError",
    "OAuthRequestError",
    "OAuthUnsupportedGrantTypeError",
    "OAuthInvalidScopeError",
    "OAuthInvalidClientError",

    # Types and constants
    "TokenTypes",
    "TokenTypeHints",
    "RevocationTokenTypeHints",

    # Request/Response models
    "TokenExchangeRequest",
    "TokenExchangeResponse",
    "IntrospectionRequest",
    "IntrospectionResponse",
    "RevocationRequest",
    "PushedAuthorizationRequest",
    "AuthorizationServerMetadata",

    # Utility classes
    "BearerToken",
    "BearerTokenValidator",
    "BearerTokenError",
    "BearerTokenErrors",
    "JWTClientAssertion",
    "JWTAccessToken",
    "JWTAccessTokenHandler",
    "JWTAccessTokenValidator",
    "PKCEGenerator",
    "PKCEChallenge",
    "PKCEMethods",
    "MutualTLSClientAuth",
    "CertificateBoundToken",
]
