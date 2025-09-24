"""Keycard OAuth SDK

A unified, developer-friendly Python SDK for OAuth 2.0 functionality implementing
multiple OAuth 2.0 standards with enterprise-ready features.

Supported OAuth 2.0 Standards:
- RFC 8693: OAuth 2.0 Token Exchange
- RFC 7591: OAuth 2.0 Dynamic Client Registration
- RFC 6750: OAuth 2.0 Bearer Token Usage
- RFC 8414: OAuth 2.0 Authorization Server Metadata

Example:
    # Simple usage
    from keycardai.oauth import AsyncClient, Client

    # Async client (primary implementation)
    async with AsyncClient("https://api.keycard.ai") as client:
        response = await client.exchange_token(
            subject_token="original_access_token",
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            audience="target-service.company.com"
        )

    # Sync client (wrapper)
    with Client("https://api.keycard.ai") as client:
        response = client.exchange_token(
            subject_token="original_access_token",
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            audience="target-service.company.com"
        )
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
from .http.auth import AuthStrategy, BasicAuth, BearerAuth, MultiZoneBasicAuth, NoneAuth
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
from .utils.bearer import extract_bearer_token, validate_bearer_format

__all__ = [
    # Core clients
    "AsyncClient",
    "Client",
    # Exceptions
    "OAuthError",
    "OAuthHttpError",
    "OAuthProtocolError",
    "NetworkError",
    "ConfigError",
    "AuthenticationError",
    "TokenExchangeError",
    # Models and types
    "TokenResponse",
    "ClientRegistrationResponse",
    "PKCE",
    "Endpoints",
    "ClientConfig",
    "ClientRegistrationRequest",
    "TokenExchangeRequest",
    "AuthorizationServerMetadata",
    # Enums
    "GrantType",
    "ResponseType",
    "TokenEndpointAuthMethod",
    "TokenType",
    "TokenTypeHint",
    "PKCECodeChallengeMethod",
    "WellKnownEndpoint",
    # Auth strategies
    "AuthStrategy",
    "BasicAuth",
    "BearerAuth",
    "NoneAuth",
    "MultiZoneBasicAuth",
    # Utility functions
    "extract_bearer_token",
    "validate_bearer_format",
]
