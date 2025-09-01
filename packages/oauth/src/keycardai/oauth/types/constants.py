"""Constants, enums, and token types for OAuth 2.0.

This module contains all the constants, token types, and enumerations
used across the OAuth 2.0 implementation.
"""


class TokenTypes:
    """OAuth 2.0 token types as defined in RFC 8693 Section 3.

    Reference: https://datatracker.ietf.org/doc/html/rfc8693#section-3
    """

    ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
    REFRESH_TOKEN = "urn:ietf:params:oauth:token-type:refresh_token"
    ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"
    SAML1 = "urn:ietf:params:oauth:token-type:saml1"
    SAML2 = "urn:ietf:params:oauth:token-type:saml2"
    JWT = "urn:ietf:params:oauth:token-type:jwt"


class TokenTypeHints:
    """Token type hints for introspection as defined in RFC 7662 Section 2.1.

    Reference: https://datatracker.ietf.org/doc/html/rfc7662#section-2.1
    """

    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"


class RevocationTokenTypeHints:
    """Token type hints for revocation as defined in RFC 7009 Section 2.1.

    Reference: https://datatracker.ietf.org/doc/html/rfc7009#section-2.1
    """

    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"


class GrantTypes:
    """OAuth 2.0 grant types."""

    TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
    AUTHORIZATION_CODE = "authorization_code"
    CLIENT_CREDENTIALS = "client_credentials"
    REFRESH_TOKEN = "refresh_token"


# PKCEMethods is defined in utils.pkce module to avoid duplication
