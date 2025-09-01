"""OAuth 2.0 request models.

This module contains all the request models used across different
OAuth 2.0 operations like token exchange, introspection, and revocation.
"""

from pydantic import BaseModel

from .enums import GrantType, ResponseType, TokenEndpointAuthMethod, TokenTypeHint


class TokenExchangeRequest(BaseModel):
    """OAuth 2.0 Token Exchange Request as defined in RFC 8693 Section 2.1.

    Reference: https://datatracker.ietf.org/doc/html/rfc8693#section-2.1
    """

    grant_type: str = "urn:ietf:params:oauth:grant-type:token-exchange"
    resource: str | None = None
    audience: str | None = None
    scope: str | None = None
    requested_token_type: str | None = None
    subject_token: str
    subject_token_type: str
    actor_token: str | None = None
    actor_token_type: str | None = None


class IntrospectionRequest(BaseModel):
    """OAuth 2.0 Token Introspection Request as defined in RFC 7662 Section 2.1.

    Reference: https://datatracker.ietf.org/doc/html/rfc7662#section-2.1
    """

    token: str
    token_type_hint: TokenTypeHint | None = None


class RevocationRequest(BaseModel):
    """OAuth 2.0 Token Revocation Request as defined in RFC 7009 Section 2.1.

    Reference: https://datatracker.ietf.org/doc/html/rfc7009#section-2.1
    """

    token: str
    token_type_hint: TokenTypeHint | None = None


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


class ClientRegistrationRequest(BaseModel):
    """Dynamic Client Registration Request as defined in RFC 7591 Section 2.

    Reference: https://datatracker.ietf.org/doc/html/rfc7591#section-2
    """

    client_name: str
    jwks_uri: str | None = None
    jwks: dict | None = None
    token_endpoint_auth_method: TokenEndpointAuthMethod = (
        TokenEndpointAuthMethod.CLIENT_SECRET_BASIC
    )
    redirect_uris: list[str] | None = None
    grant_types: list[GrantType] | None = None
    response_types: list[ResponseType] | None = None
    scope: str | None = None
    client_uri: str | None = None
    logo_uri: str | None = None
    tos_uri: str | None = None
    policy_uri: str | None = None
    software_id: str | None = None
    software_version: str | None = None
