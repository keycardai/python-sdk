"""OAuth 2.0 response models with vendor extension support.

This module contains comprehensive response models that preserve complete RFC information
plus vendor extensions for all OAuth 2.0 operations.
"""

from dataclasses import dataclass
from typing import Any, Literal

from .enums import GrantType, ResponseType, TokenEndpointAuthMethod


@dataclass
class IntrospectionResponse:
    """RFC 7662 Token Introspection Response with complete field support.

    Preserves all RFC 7662 fields plus vendor extensions and response metadata.
    Reference: https://datatracker.ietf.org/doc/html/rfc7662#section-2.2
    """

    active: bool
    scope: list[str] | None = None
    client_id: str | None = None
    username: str | None = None
    token_type: str | None = None
    sub: str | None = None
    aud: list[str] | None = None
    iss: str | None = None
    nbf: int | None = None
    iat: int | None = None
    exp: int | None = None
    jti: str | None = None

    # Vendor extensions and debugging
    raw: dict[str, Any] | None = None
    headers: dict[str, str] | None = None

    @classmethod
    def from_response(
        cls, data: dict[str, Any], headers: dict[str, str] | None = None
    ) -> "IntrospectionResponse":
        """Create response from raw server data.

        Handles parameter normalization and vendor extension preservation.
        """
        # Normalize scope from space-delimited string to list
        scope = data.get("scope")
        if isinstance(scope, str):
            scope = scope.split() if scope else None
        elif isinstance(scope, list):
            scope = scope if scope else None

        # Normalize audience from string or array to list
        aud = data.get("aud")
        if isinstance(aud, str):
            aud = [aud]
        elif not isinstance(aud, list):
            aud = None

        return cls(
            active=bool(data.get("active", False)),
            scope=scope,
            client_id=data.get("client_id"),
            username=data.get("username"),
            token_type=data.get("token_type"),
            sub=data.get("sub"),
            aud=aud,
            iss=data.get("iss"),
            nbf=data.get("nbf"),
            iat=data.get("iat"),
            exp=data.get("exp"),
            jti=data.get("jti"),
            raw=data,
            headers=headers,
        )


@dataclass
class TokenResponse:
    """RFC 8693 Token Exchange Response + RFC 6749 Token Response.

    Comprehensive token response supporting both token exchange and traditional
    OAuth 2.0 token responses with vendor extension preservation.
    """

    # Required fields
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"

    # Optional RFC fields
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: list[str] | None = None

    # RFC 8693 specific fields
    issued_token_type: str | None = None
    subject_issuer: str | None = None

    # Vendor extensions and debugging
    raw: dict[str, Any] | None = None
    headers: dict[str, str] | None = None

    @classmethod
    def from_response(
        cls, data: dict[str, Any], headers: dict[str, str] | None = None
    ) -> "TokenResponse":
        """Create response from raw server data."""
        # Normalize scope from space-delimited string to list
        scope = data.get("scope")
        if isinstance(scope, str):
            scope = scope.split() if scope else None
        elif isinstance(scope, list):
            scope = scope if scope else None

        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in"),
            refresh_token=data.get("refresh_token"),
            scope=scope,
            issued_token_type=data.get("issued_token_type"),
            subject_issuer=data.get("subject_issuer"),
            raw=data,
            headers=headers,
        )


@dataclass
class PKCE:
    """RFC 7636 PKCE Challenge with S256 method support."""

    code_verifier: str
    code_challenge: str
    code_challenge_method: Literal["S256"] = "S256"


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

    @classmethod
    def from_response(
        cls, data: dict[str, Any], headers: dict[str, str] | None = None
    ) -> "ClientRegistrationResponse":
        """Create response from raw server data.

        Handles parameter normalization and vendor extension preservation.
        """
        scope = data.get("scope")
        if isinstance(scope, str):
            scope = scope.split() if scope else None
        elif isinstance(scope, list):
            scope = scope if scope else None

        redirect_uris = data.get("redirect_uris")
        if isinstance(redirect_uris, str):
            redirect_uris = [redirect_uris]
        elif not isinstance(redirect_uris, list):
            redirect_uris = None

        grant_types = data.get("grant_types")
        if isinstance(grant_types, str):
            grant_types = [grant_types]
        elif not isinstance(grant_types, list):
            grant_types = None

        response_types = data.get("response_types")
        if isinstance(response_types, str):
            response_types = [response_types]
        elif not isinstance(response_types, list):
            response_types = None

        return cls(
            client_id=data["client_id"],
            client_secret=data.get("client_secret"),
            client_id_issued_at=data.get("client_id_issued_at"),
            client_secret_expires_at=data.get("client_secret_expires_at"),
            client_name=data.get("client_name"),
            jwks_uri=data.get("jwks_uri"),
            jwks=data.get("jwks"),
            token_endpoint_auth_method=data.get("token_endpoint_auth_method"),
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            response_types=response_types,
            scope=scope,
            registration_access_token=data.get("registration_access_token"),
            registration_client_uri=data.get("registration_client_uri"),
            client_uri=data.get("client_uri"),
            logo_uri=data.get("logo_uri"),
            tos_uri=data.get("tos_uri"),
            policy_uri=data.get("policy_uri"),
            software_id=data.get("software_id"),
            software_version=data.get("software_version"),
            raw=data,
            headers=headers,
        )


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


@dataclass
class AuthorizationServerMetadata:
    """OAuth 2.0 Authorization Server Metadata (RFC 8414).

    Reference: https://datatracker.ietf.org/doc/html/rfc8414#section-2
    """

    issuer: str
    token_endpoint: str | None = None
    introspection_endpoint: str | None = None
    revocation_endpoint: str | None = None
    authorization_endpoint: str | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> "AuthorizationServerMetadata":
        """Create metadata from server discovery response."""
        return cls(
            issuer=data["issuer"],
            token_endpoint=data.get("token_endpoint"),
            introspection_endpoint=data.get("introspection_endpoint"),
            revocation_endpoint=data.get("revocation_endpoint"),
            authorization_endpoint=data.get("authorization_endpoint"),
            raw=data,
        )
