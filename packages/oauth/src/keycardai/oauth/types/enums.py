"""OAuth 2.0 standard enums with RFC documentation.

This module contains all standardized enum values used across OAuth 2.0 specifications,
properly documented with their RFC sources for compliance and reference.
"""

from enum import Enum


class TokenEndpointAuthMethod(str, Enum):
    """Token endpoint authentication methods as defined in multiple RFCs.

    References:
    - RFC 6749 Section 2.3: Client Authentication
    - RFC 7591 Section 2: Client Registration Parameters
    - RFC 7523: JWT Profile for OAuth 2.0 Client Authentication
    - RFC 8705: OAuth 2.0 Mutual-TLS Client Authentication
    """

    # RFC 6749 Section 2.3.1 - HTTP Basic Authentication
    CLIENT_SECRET_BASIC = "client_secret_basic"

    # RFC 6749 Section 2.3.1 - Form-encoded Body Authentication
    CLIENT_SECRET_POST = "client_secret_post"

    # RFC 7523 - JWT Profile for Client Authentication
    PRIVATE_KEY_JWT = "private_key_jwt"
    CLIENT_SECRET_JWT = "client_secret_jwt"

    # RFC 8705 - Mutual TLS Client Authentication
    TLS_CLIENT_AUTH = "tls_client_auth"
    SELF_SIGNED_TLS_CLIENT_AUTH = "self_signed_tls_client_auth"

    # RFC 7591 Section 2 - No authentication (for Dynamic Client Registration)
    NONE = "none"


class GrantType(str, Enum):
    """OAuth 2.0 grant types as defined in multiple RFCs.

    References:
    - RFC 6749: The OAuth 2.0 Authorization Framework
    - RFC 8693: OAuth 2.0 Token Exchange
    - RFC 7523: JWT Profile for OAuth 2.0 Client Authentication
    - RFC 7522: SAML 2.0 Profile for OAuth 2.0 Client Authentication
    - RFC 8628: OAuth 2.0 Device Authorization Grant
    """

    # RFC 6749 Section 4.1 - Authorization Code Grant
    AUTHORIZATION_CODE = "authorization_code"

    # RFC 6749 Section 4.2 - Implicit Grant (deprecated in OAuth 2.1)
    IMPLICIT = "implicit"

    # RFC 6749 Section 4.3 - Resource Owner Password Credentials Grant
    PASSWORD = "password"

    # RFC 6749 Section 4.4 - Client Credentials Grant
    CLIENT_CREDENTIALS = "client_credentials"

    # RFC 6749 Section 6 - Refresh Token Grant
    REFRESH_TOKEN = "refresh_token"

    # RFC 8693 - Token Exchange Grant
    TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"

    # RFC 7523 - JWT Bearer Grant
    JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"

    # RFC 7522 - SAML 2.0 Bearer Grant
    SAML2_BEARER = "urn:ietf:params:oauth:grant-type:saml2-bearer"

    # RFC 8628 - Device Authorization Grant
    DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"


class ResponseType(str, Enum):
    """OAuth 2.0 response types as defined in RFC 6749.

    References:
    - RFC 6749 Section 3.1.1: Response Type
    - RFC 6749 Section 4.1: Authorization Code Grant
    - RFC 6749 Section 4.2: Implicit Grant
    """

    # RFC 6749 Section 4.1 - Authorization Code Flow
    CODE = "code"

    # RFC 6749 Section 4.2 - Implicit Flow (deprecated in OAuth 2.1)
    TOKEN = "token"

    # OpenID Connect - Hybrid flows
    ID_TOKEN = "id_token"
    CODE_ID_TOKEN = "code id_token"
    CODE_TOKEN = "code token"
    CODE_ID_TOKEN_TOKEN = "code id_token token"


class TokenType(str, Enum):
    """OAuth 2.0 token types as defined in multiple RFCs.

    References:
    - RFC 6749 Section 7.1: Access Token Types
    - RFC 6750: Bearer Token Usage
    - RFC 8693 Section 3: Token Exchange Response
    - RFC 9068: JWT Profile for OAuth 2.0 Access Tokens
    """

    # RFC 6750 - Bearer Token Usage
    BEARER = "Bearer"

    # RFC 8693 Section 3 - Token Exchange token types
    ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
    REFRESH_TOKEN = "urn:ietf:params:oauth:token-type:refresh_token"
    ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"

    # RFC 9068 - JWT Profile for Access Tokens
    JWT = "urn:ietf:params:oauth:token-type:jwt"


class TokenTypeHint(str, Enum):
    """Token type hints for introspection and revocation as defined in RFCs.

    References:
    - RFC 7662 Section 2.1: Introspection Request
    - RFC 7009 Section 2.1: Revocation Request
    """

    # RFC 7662/7009 - Standard token type hints
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"

    # OpenID Connect
    ID_TOKEN = "id_token"


class PKCECodeChallengeMethod(str, Enum):
    """PKCE code challenge methods as defined in RFC 7636.

    References:
    - RFC 7636 Section 4.2: Client Creates the Code Challenge
    """

    # RFC 7636 - Plain text (not recommended for production)
    PLAIN = "plain"

    # RFC 7636 - SHA256 hash (recommended)
    S256 = "S256"
