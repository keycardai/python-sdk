"""OAuth 2.0 response models.

This module contains all the response models used across different
OAuth 2.0 operations like token exchange, introspection, and discovery.
"""

from typing import Any

from pydantic import BaseModel


class TokenExchangeResponse(BaseModel):
    """OAuth 2.0 Token Exchange Response as defined in RFC 8693 Section 2.2.

    Reference: https://datatracker.ietf.org/doc/html/rfc8693#section-2.2
    """

    access_token: str
    issued_token_type: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    scope: str | None = None
    refresh_token: str | None = None


class IntrospectionResponse(BaseModel):
    """OAuth 2.0 Token Introspection Response as defined in RFC 7662 Section 2.2.

    Reference: https://datatracker.ietf.org/doc/html/rfc7662#section-2.2
    """

    active: bool
    scope: str | None = None
    client_id: str | None = None
    username: str | None = None
    token_type: str | None = None
    exp: int | None = None
    iat: int | None = None
    nbf: int | None = None
    sub: str | None = None
    aud: str | list[str] | None = None
    iss: str | None = None
    jti: str | None = None


class AuthorizationServerMetadata(BaseModel):
    """OAuth 2.0 Authorization Server Metadata as defined in RFC 8414 Section 2.

    Reference: https://datatracker.ietf.org/doc/html/rfc8414#section-2
    """

    issuer: str
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    jwks_uri: str | None = None
    registration_endpoint: str | None = None
    scopes_supported: list[str] | None = None
    response_types_supported: list[str] = ["code"]
    response_modes_supported: list[str] | None = None
    grant_types_supported: list[str] = ["authorization_code"]
    token_endpoint_auth_methods_supported: list[str] = ["client_secret_basic"]
    token_endpoint_auth_signing_alg_values_supported: list[str] | None = None
    service_documentation: str | None = None
    ui_locales_supported: list[str] | None = None
    op_policy_uri: str | None = None
    op_tos_uri: str | None = None
    revocation_endpoint: str | None = None
    revocation_endpoint_auth_methods_supported: list[str] | None = None
    revocation_endpoint_auth_signing_alg_values_supported: list[str] | None = None
    introspection_endpoint: str | None = None
    introspection_endpoint_auth_methods_supported: list[str] | None = None
    introspection_endpoint_auth_signing_alg_values_supported: list[str] | None = None
    code_challenge_methods_supported: list[str] | None = None

    # Extension fields for additional metadata
    additional_metadata: dict[str, Any] | None = None
