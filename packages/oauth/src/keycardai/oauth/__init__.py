"""Keycard OAuth SDK

A unified, developer-friendly Python SDK for OAuth 2.0 functionality implementing
multiple OAuth 2.0 standards with enterprise-ready features.

Supported OAuth 2.0 Standards:
- RFC 8693: OAuth 2.0 Token Exchange
- RFC 7591: OAuth 2.0 Dynamic Client Registration
- RFC 6750: OAuth 2.0 Bearer Token Usage
- RFC 8414: OAuth 2.0 Authorization Server Metadata
- OpenID Connect Core 1.0 Section 5.3: UserInfo

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
    PERMANENT_ERROR_CODES,
    AuthenticationError,
    AuthorizationDeniedError,
    AuthorizationServerDiscoveryError,
    ConfigError,
    InvalidTokenError,
    JWKSError,
    JWKSFetchError,
    JWKSKeyNotFoundError,
    NetworkError,
    OAuthError,
    OAuthHttpError,
    OAuthProtocolError,
    StateMismatchError,
    TokenExchangeError,
)
from .http.auth import AuthStrategy, BasicAuth, BearerAuth, MultiZoneBasicAuth, NoneAuth
from .operations._authorize import build_authorize_url
from .types.models import (
    PKCE,
    AuthorizationServerMetadata,
    ClientConfig,
    ClientCredentialsRequest,
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    Endpoints,
    TokenExchangeRequest,
    TokenResponse,
    UserInfoRequest,
    UserInfoResponse,
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
    # === Core Clients ===
    "AsyncClient",
    "Client",
    # === Exceptions ===
    "OAuthError",  # Base exception for all OAuth errors
    "OAuthHttpError",
    "OAuthProtocolError",
    "NetworkError",
    "ConfigError",
    "AuthenticationError",
    "AuthorizationDeniedError",
    "AuthorizationServerDiscoveryError",
    "StateMismatchError",
    "TokenExchangeError",
    "JWKSError",
    "JWKSFetchError",
    "JWKSKeyNotFoundError",
    "InvalidTokenError",
    "PERMANENT_ERROR_CODES",
    # === Data Models ===
    "TokenResponse",
    "ClientRegistrationResponse",
    "PKCE",
    "Endpoints",
    "ClientConfig",
    "ClientRegistrationRequest",
    "ClientCredentialsRequest",
    "TokenExchangeRequest",
    "AuthorizationServerMetadata",
    "UserInfoRequest",
    "UserInfoResponse",
    # === Authorization ===
    "build_authorize_url",
    # === OAuth Enums ===
    "GrantType",
    "ResponseType",
    "TokenEndpointAuthMethod",
    "TokenType",
    "TokenTypeHint",
    "PKCECodeChallengeMethod",
    "WellKnownEndpoint",
    # === HTTP Auth Strategies ===
    "AuthStrategy",
    "BasicAuth",
    "BearerAuth",
    "NoneAuth",
    "MultiZoneBasicAuth",
    # === Utility Functions ===
    "extract_bearer_token",
    "validate_bearer_format",
]
