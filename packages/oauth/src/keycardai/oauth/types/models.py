"""OAuth 2.0 Request and Response Models.

This module contains all OAuth 2.0 request and response models organized in pairs.

The models preserve complete RFC information plus vendor extensions for all
OAuth 2.0 operations, providing comprehensive support for:
- Token Exchange (RFC 8693)
- Dynamic Client Registration (RFC 7591)
- Authorization Server Metadata Discovery (RFC 8414)
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .oauth import (
    GrantType,
    PKCECodeChallengeMethod,
    ResponseType,
    TokenEndpointAuthMethod,
    TokenType,
    TokenTypeHint,
)

# =============================================================================
# Token Exchange (RFC 8693)
# =============================================================================

class TokenExchangeRequest(BaseModel):
    """OAuth 2.0 Token Exchange Request as defined in RFC 8693 Section 2.1.

    Reference: https://datatracker.ietf.org/doc/html/rfc8693#section-2.1
    """

    grant_type: str = "urn:ietf:params:oauth:grant-type:token-exchange"
    resource: str | None = None
    audience: str | None = None
    scope: str | None = None
    requested_token_type: TokenType | None = None
    subject_token: str | None = Field(default=None, min_length=1, description="The token to exchange.")
    subject_token_type: TokenType | None = Field(default=TokenType.ACCESS_TOKEN, description="The type of the token to exchange.")
    actor_token: str | None = None
    actor_token_type: TokenType | None = None
    timeout: float | None = None
    client_id: str | None = None

    client_assertion_type: str | None = None
    client_assertion: str | None = None


@dataclass
class TokenResponse:
    """RFC 8693 Token Exchange Response + RFC 6749 Token Response.

    Comprehensive token response supporting both token exchange and traditional
    OAuth 2.0 token responses with vendor extension preservation.
    """

    # Required fields
    access_token: str
    token_type: TokenType = TokenType.BEARER

    # Optional RFC fields
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: list[str] | None = None

    # RFC 8693 specific fields
    issued_token_type: TokenType | None = None
    subject_issuer: str | None = None

    # Vendor extensions and debugging
    raw: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


# =============================================================================
# Token Revocation (RFC 7009)
# =============================================================================

class RevocationRequest(BaseModel):
    """OAuth 2.0 Token Revocation Request as defined in RFC 7009 Section 2.1.

    Reference: https://datatracker.ietf.org/doc/html/rfc7009#section-2.1
    """

    token: str
    token_type_hint: TokenTypeHint | None = None
    timeout: float | None = None


# Note: Token revocation typically has no response body (RFC 7009 Section 2.2)
# The operation either succeeds (HTTP 200) or fails with an error response


# =============================================================================
# Dynamic Client Registration (RFC 7591)
# =============================================================================

class ClientRegistrationRequest(BaseModel):
    """Dynamic Client Registration Request as defined in RFC 7591 Section 2.

    Reference: https://datatracker.ietf.org/doc/html/rfc7591#section-2
    """
    client_id: str | None = None
    client_name: str = Field(..., min_length=1, description="Human-readable name of the client application.")
    jwks_uri: str | None = None
    jwks: dict | None = None
    token_endpoint_auth_method: TokenEndpointAuthMethod = (
        TokenEndpointAuthMethod.CLIENT_SECRET_BASIC
    )
    redirect_uris: list[str] | None = Field(default_factory=lambda: ["http://localhost:8080/callback"])
    grant_types: list[GrantType] | None = Field(default_factory=lambda: [GrantType.TOKEN_EXCHANGE, GrantType.CLIENT_CREDENTIALS])
    response_types: list[ResponseType] | None = Field(default_factory=lambda: [ResponseType.CODE])
    scope: str | None = "read write"
    timeout: float | None = None
    additional_metadata: dict[str, Any] | None = None

    # Direct client metadata fields (alternatives to additional_metadata)
    client_uri: str | None = None
    logo_uri: str | None = None
    tos_uri: str | None = None
    policy_uri: str | None = None
    software_id: str | None = None
    software_version: str | None = None


@dataclass
class ClientRegistrationResponse:
    """RFC 7591 Dynamic Client Registration Response.

    Preserves all RFC 7591 fields plus vendor extensions and response metadata.
    Reference: https://datatracker.ietf.org/doc/html/rfc7591#section-3.2.1
    """

    # Server-generated required fields
    client_id: str
    client_secret: str | None = None
    client_id_issued_at: int | None = None
    client_secret_expires_at: int | None = None

    # Echoed request fields
    client_name: str | None = None
    jwks_uri: str | None = None
    jwks: dict | None = None
    token_endpoint_auth_method: TokenEndpointAuthMethod | None = None
    redirect_uris: list[str] | None = None
    grant_types: list[GrantType] | None = None
    response_types: list[ResponseType] | None = None
    scope: list[str] | None = None

    # Additional server-provided metadata
    registration_access_token: str | None = None
    registration_client_uri: str | None = None

    # Additional client metadata (vendor extensions)
    client_uri: str | None = None
    logo_uri: str | None = None
    tos_uri: str | None = None
    policy_uri: str | None = None
    software_id: str | None = None
    software_version: str | None = None

    # Vendor extensions and debugging
    raw: dict[str, Any] | None = None
    headers: dict[str, str] | None = None

# =============================================================================
# Pushed Authorization Requests (RFC 9126)
# =============================================================================

class PushedAuthorizationRequest(BaseModel):
    """Pushed Authorization Request as defined in RFC 9126 Section 2.

    Reference: https://datatracker.ietf.org/doc/html/rfc9126#section-2
    """

    client_id: str
    response_type: str = "code"
    redirect_uri: str
    scope: str | None = None
    state: str | None = None
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    timeout: float | None = None
    additional_params: dict[str, str] | None = None


# Note: Pushed Authorization Response would go here when implemented
# It typically contains request_uri and expires_in fields


# =============================================================================
# Authorization Server Metadata Discovery (RFC 8414)
# =============================================================================

class ServerMetadataRequest(BaseModel):
    """OAuth 2.0 Authorization Server Metadata discovery request as defined in RFC 8414.

    Reference: https://datatracker.ietf.org/doc/html/rfc8414#section-3
    """

    base_url: str = Field(..., min_length=1, description="Base URL of the OAuth 2.0 authorization server.")


@dataclass
class AuthorizationServerMetadata:
    """OAuth 2.0 Authorization Server Metadata (RFC 8414).

    Complete implementation of RFC 8414 authorization server metadata
    with support for all standard and optional fields.

    Reference: https://datatracker.ietf.org/doc/html/rfc8414#section-2
    """

    # Required fields (RFC 8414 Section 2)
    issuer: str

    # Common endpoint URLs
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None

    # RFC 7662 Token Introspection
    introspection_endpoint: str | None = None

    # RFC 7009 Token Revocation
    revocation_endpoint: str | None = None

    # RFC 7591 Dynamic Client Registration
    registration_endpoint: str | None = None

    # RFC 9126 Pushed Authorization Requests
    pushed_authorization_request_endpoint: str | None = None

    # JWKS endpoint for server public keys
    jwks_uri: str | None = None

    # Supported capabilities
    response_types_supported: list[str] | None = None
    response_modes_supported: list[str] | None = None
    grant_types_supported: list[str] | None = None
    subject_types_supported: list[str] | None = None
    scopes_supported: list[str] | None = None

    # Token endpoint authentication
    token_endpoint_auth_methods_supported: list[str] | None = None
    token_endpoint_auth_signing_alg_values_supported: list[str] | None = None

    # Introspection endpoint authentication (RFC 7662)
    introspection_endpoint_auth_methods_supported: list[str] | None = None
    introspection_endpoint_auth_signing_alg_values_supported: list[str] | None = None

    # Revocation endpoint authentication (RFC 7009)
    revocation_endpoint_auth_methods_supported: list[str] | None = None
    revocation_endpoint_auth_signing_alg_values_supported: list[str] | None = None

    # Code challenge methods for PKCE (RFC 7636)
    code_challenge_methods_supported: list[str] | None = None

    # Additional optional fields
    service_documentation: str | None = None
    ui_locales_supported: list[str] | None = None
    op_policy_uri: str | None = None
    op_tos_uri: str | None = None

    # Vendor extensions and debugging
    raw: dict[str, Any] | None = None
    headers: dict[str, str] | None = None

# =============================================================================
# JSON Web Key Set (RFC 7517)
# =============================================================================

class JsonWebKey(BaseModel):
    """JSON Web Key (JWK) as defined in RFC 7517 Section 4.

    Reference: https://datatracker.ietf.org/doc/html/rfc7517#section-4
    """

    # Required fields
    kty: str = Field(..., description="Key type (e.g., 'RSA', 'EC', 'oct')")

    # Optional standard fields
    use: str | None = Field(None, description="Intended use of the key ('sig' or 'enc')")
    key_ops: list[str] | None = Field(None, description="Key operations")
    alg: str | None = Field(None, description="Algorithm intended for use with the key")
    kid: str | None = Field(None, description="Key ID")

    # RSA key parameters
    n: str | None = Field(None, description="RSA modulus")
    e: str | None = Field(None, description="RSA exponent")

    # EC key parameters
    crv: str | None = Field(None, description="Curve name for elliptic curve keys")
    x: str | None = Field(None, description="X coordinate for elliptic curve keys")
    y: str | None = Field(None, description="Y coordinate for elliptic curve keys")

    # Symmetric key parameters
    k: str | None = Field(None, description="Key value for symmetric keys")

    # Certificate chain
    x5c: list[str] | None = Field(None, description="X.509 certificate chain")
    x5t: str | None = Field(None, description="X.509 certificate SHA-1 thumbprint")
    x5t_s256: str | None = Field(None, alias="x5t#S256", description="X.509 certificate SHA-256 thumbprint")
    x5u: str | None = Field(None, description="X.509 certificate URL")


class JsonWebKeySet(BaseModel):
    """JSON Web Key Set (JWKS) as defined in RFC 7517 Section 5.

    Reference: https://datatracker.ietf.org/doc/html/rfc7517#section-5
    """

    keys: list[JsonWebKey] = Field(..., description="Array of JSON Web Key objects")


# =============================================================================
# Utility Models
# =============================================================================

@dataclass
class PKCE:
    """RFC 7636 PKCE Challenge with S256 method support."""

    code_verifier: str
    code_challenge: str
    code_challenge_method: PKCECodeChallengeMethod = PKCECodeChallengeMethod.S256


@dataclass
class Endpoints:
    """Type-safe endpoint configuration for unified client."""

    token: str | None = None
    introspect: str | None = None
    revoke: str | None = None
    register: str | None = None
    par: str | None = None
    authorize: str | None = None


@dataclass
class ClientConfig:
    """Comprehensive client configuration with enterprise defaults."""

    timeout: float = 30.0
    max_retries: int = 3
    verify_ssl: bool = True
    user_agent: str = "KeyCardAI-OAuth/0.0.1"
    custom_headers: dict[str, str] | None = None

    enable_metadata_discovery: bool = True
    auto_register_client: bool = False

    client_id: str | None = None
    client_name: str = "KeycardAI OAuth Client"
    client_redirect_uris: list[str] = field(default_factory=lambda: ["http://localhost:8080/callback"])
    client_grant_types: list[GrantType] = field(default_factory=lambda: [GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN, GrantType.TOKEN_EXCHANGE])
    client_token_endpoint_auth_method: TokenEndpointAuthMethod = field(default_factory=lambda: TokenEndpointAuthMethod.NONE)

    client_jwks_url: str | None = None
