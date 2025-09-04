"""KeyCard AI OAuth SDK

A unified, developer-friendly Python SDK for OAuth 2.0 functionality implementing
multiple OAuth 2.0 standards with enterprise-ready features.

Supported OAuth 2.0 Standards:
- RFC 8693: OAuth 2.0 Token Exchange
- RFC 7662: OAuth 2.0 Token Introspection
- RFC 7009: OAuth 2.0 Token Revocation
- RFC 7591: OAuth 2.0 Dynamic Client Registration
- RFC 7523: JWT Profile for OAuth 2.0 Client Authentication
- RFC 9068: JWT Profile for OAuth 2.0 Access Tokens
- RFC 6750: OAuth 2.0 Bearer Token Usage
- RFC 8414: OAuth 2.0 Authorization Server Metadata
- RFC 8705: OAuth 2.0 Mutual-TLS Client Authentication
- RFC 7636: Proof Key for Code Exchange (PKCE)
- RFC 9126: OAuth 2.0 Pushed Authorization Requests

Example:
    # Simple usage
    from keycardai.oauth import AsyncClient, Client

    # Async client (primary implementation)
    async with AsyncClient("https://api.keycard.ai") as client:
        response = await client.introspect_token("token_to_validate")

    # Sync client (wrapper)
    with Client("https://api.keycard.ai") as client:
        response = client.introspect_token("token_to_validate")
"""

from .client import AsyncClient, Client
from .exceptions import (
    AuthenticationError,
    ConfigError,
    NetworkError,
    OAuthError,
    OAuthHttpError,
    OAuthProtocolError,
    TokenExchangeError,
)
from .http import AuthStrategy, BasicAuth, BearerAuth, NoneAuth
from .types.models import (
    PKCE,
    AuthorizationServerMetadata,
    ClientConfig,
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    Endpoints,
    TokenExchangeRequest,
    TokenResponse,
)
from .types.oauth import (
    GrantType,
    PKCECodeChallengeMethod,
    ResponseType,
    TokenEndpointAuthMethod,
    TokenType,
    TokenTypeHint,
    WellKnownEndpoint,
)
from .utils import (
    create_auth_header,
    create_jwt_assertion,
    extract_bearer_token,
    generate_cert_thumbprint,
    generate_pkce_challenge,
    validate_bearer_format,
    verify_pkce_challenge,
)

__version__ = "0.0.1"

__all__ = [
    "__version__",
    "AsyncClient",
    "Client",
    "OAuthError",
    "OAuthHttpError",
    "OAuthProtocolError",
    "NetworkError",
    "ConfigError",
    "AuthenticationError",
    "TokenExchangeError",

    "TokenResponse",
    "ClientRegistrationResponse",
    "PKCE",
    "Endpoints",
    "ClientConfig",
    "GrantType",
    "ResponseType",
    "TokenEndpointAuthMethod",
    "TokenType",
    "TokenTypeHint",
    "PKCECodeChallengeMethod",
    "ClientRegistrationRequest",
    "TokenExchangeRequest",
    "AuthorizationServerMetadata",
    "WellKnownEndpoint",
    "AuthStrategy",
    "BasicAuth",
    "BearerAuth",
    "NoneAuth",
    "extract_bearer_token",
    "validate_bearer_format",
    "create_auth_header",
    "generate_pkce_challenge",
    "verify_pkce_challenge",
    "create_jwt_assertion",
    "generate_cert_thumbprint",
]
