"""OAuth 2.0 Constants, Enums, and Well-Known Endpoints.

This module consolidates all OAuth 2.0 related constants, enumerations, and
well-known endpoints into a single location for easier imports and better organization.

References:
- RFC 6749: The OAuth 2.0 Authorization Framework
- RFC 7009: OAuth 2.0 Token Revocation
- RFC 7033: WebFinger
- RFC 7517: JSON Web Key (JWK) Set
- RFC 7522: SAML 2.0 Profile for OAuth 2.0 Client Authentication
- RFC 7523: JWT Profile for OAuth 2.0 Client Authentication
- RFC 7591: OAuth 2.0 Dynamic Client Registration Protocol
- RFC 7636: Proof Key for Code Exchange by OAuth Public Clients
- RFC 7662: OAuth 2.0 Token Introspection
- RFC 8414: OAuth 2.0 Authorization Server Metadata
- RFC 8628: OAuth 2.0 Device Authorization Grant
- RFC 8693: OAuth 2.0 Token Exchange
- RFC 8705: OAuth 2.0 Mutual-TLS Client Authentication
- RFC 9068: JWT Profile for OAuth 2.0 Access Tokens
- OpenID Connect Discovery 1.0
"""

from enum import Enum

# =============================================================================
# OAuth 2.0 Enums
# =============================================================================

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
    JWT_BEARER_CLIENT_ASSERTION = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
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
    """OAuth 2.0 token types"""

    # RFC 6750 - Bearer Token Usage
    BEARER = "Bearer"

    # RFC 8693 Section 3 - Token Exchange token types
    ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
    REFRESH_TOKEN = "urn:ietf:params:oauth:token-type:refresh_token"
    ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"

    # RFC 8693 Section 3 - SAML token types
    SAML1 = "urn:ietf:params:oauth:token-type:saml1"
    SAML2 = "urn:ietf:params:oauth:token-type:saml2"

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


# =============================================================================
# Well-Known Endpoints
# =============================================================================

class WellKnownEndpoint(str, Enum):
    """Well-known OAuth 2.0 and OpenID Connect discovery endpoints.

    These endpoints are standardized across the OAuth 2.0 ecosystem for
    automatic discovery of server capabilities and configuration.

    Usage:
        # Simple string formatting - the enum values are strings
        discovery_url = f"https://auth.example.com{WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER}"
        # Results in: https://auth.example.com/.well-known/oauth-authorization-server

        # Or use the class method for proper URL handling (handles trailing slashes)
        discovery_url = WellKnownEndpoint.construct_url("https://auth.example.com", WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER)
    """

    # OAuth 2.0 Authorization Server Metadata Discovery (RFC 8414)
    OAUTH_AUTHORIZATION_SERVER = "/.well-known/oauth-authorization-server"
    """OAuth 2.0 Authorization Server Metadata endpoint.

    Used to discover OAuth 2.0 authorization server capabilities including:
    - Supported grant types and response types
    - Token, authorization, introspection, and revocation endpoints
    - Supported authentication methods and scopes
    - JWKS URI and other security parameters

    Reference: RFC 8414 Section 3
    URL: https://datatracker.ietf.org/doc/html/rfc8414#section-3
    """

    # OpenID Connect Discovery (OpenID Connect Discovery 1.0)
    OPENID_CONFIGURATION = "/.well-known/openid-configuration"
    """OpenID Connect Provider Configuration endpoint.

    Used to discover OpenID Connect Provider (OP) capabilities including:
    - All OAuth 2.0 authorization server metadata (extends RFC 8414)
    - OpenID Connect specific endpoints (userinfo, end_session)
    - Supported claims, ID token signing algorithms
    - Subject types and response modes

    Reference: OpenID Connect Discovery 1.0 Section 4
    URL: https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfig
    """

    # JSON Web Key Set (RFC 7517)
    JWKS = "/.well-known/jwks.json"
    """JSON Web Key Set (JWKS) endpoint.

    Contains the public keys used by the authorization server to sign tokens.
    Clients use this endpoint to retrieve keys for token signature verification.

    Reference: RFC 7517 Section 5
    URL: https://datatracker.ietf.org/doc/html/rfc7517#section-5
    """

    # WebFinger (RFC 7033) - used for OpenID Connect Issuer Discovery
    WEBFINGER = "/.well-known/webfinger"
    """WebFinger endpoint for resource discovery.

    Used in OpenID Connect for issuer discovery when only a user identifier
    is known. Helps determine which OpenID Connect Provider serves a user.

    Reference: RFC 7033, OpenID Connect Discovery 1.0 Section 2
    URL: https://datatracker.ietf.org/doc/html/rfc7033
    """

    # OAuth 2.0 Protected Resource Metadata Discovery (RFC 8707)
    OAUTH_PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource"
    """OAuth 2.0 Protected Resource Metadata endpoint.

    Used to discover protected resource (resource server) capabilities including:
    - Resource registration endpoint for dynamic resource registration
    - Supported token types and authentication methods
    - Scopes and permissions supported by the resource server
    - Security requirements and policy information

    Reference: RFC 8707 Section 3
    URL: https://datatracker.ietf.org/doc/html/rfc8707#section-3
    """

    # Alternative OAuth 2.0 discovery paths used by some providers
    OAUTH2_AUTHORIZATION_SERVER = "/.well-known/oauth2-authorization-server"
    """Alternative OAuth 2.0 discovery path used by some providers.

    Some OAuth 2.0 implementations use this alternative path instead of
    the standard RFC 8414 path. Always try the standard path first.
    """

    @classmethod
    def construct_url(cls, base_url: str, endpoint: "WellKnownEndpoint") -> str:
        """Construct a complete discovery URL from base URL and well-known endpoint.

        Args:
            base_url: Base URL of the authorization server (e.g., "https://auth.example.com")
            endpoint: Well-known endpoint to append

        Returns:
            Complete discovery URL

        Example:
            >>> WellKnownEndpoint.construct_url("https://auth.example.com", WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER)
            'https://auth.example.com/.well-known/oauth-authorization-server'

            >>> WellKnownEndpoint.construct_url("https://auth.example.com/", WellKnownEndpoint.JWKS)
            'https://auth.example.com/.well-known/jwks.json'
        """
        return f"{base_url.rstrip('/')}{endpoint.value}"

# =============================================================================
# OAuth 2.0 Endpoints
# =============================================================================

class OAuth2DefaultEndpoints:
    """Standard OAuth 2.0 endpoint paths as defined in various RFCs.

    These are the conventional default paths used by many OAuth 2.0 implementations.
    Actual endpoint URLs should be discovered through RFC 8414 authorization server
    metadata discovery when possible.

    Usage:
        # These are path constants - combine with your server's base URL
        auth_url = f"https://auth.example.com{OAuth2DefaultEndpoints.AUTHORIZATION}"
        # Results in: https://auth.example.com/oauth2/authorize
    """

    # RFC 6749 Section 3.1 - Authorization Endpoint
    AUTHORIZATION = "/oauth2/authorize"
    """Authorization endpoint for initiating OAuth 2.0 authorization flows.

    This endpoint is used to:
    - Initiate authorization code flow (RFC 6749 Section 4.1)
    - Initiate implicit flow (RFC 6749 Section 4.2, deprecated in OAuth 2.1)
    - Handle authorization requests with optional PKCE (RFC 7636)
    - Process pushed authorization requests when supported (RFC 9126)

    Reference: RFC 6749 Section 3.1
    URL: https://datatracker.ietf.org/doc/html/rfc6749#section-3.1
    """

    # RFC 6749 Section 3.2 - Token Endpoint
    TOKEN = "/oauth2/token"
    """Token endpoint for exchanging authorization grants for access tokens.

    This endpoint handles:
    - Authorization code exchange (RFC 6749 Section 4.1.3)
    - Client credentials grant (RFC 6749 Section 4.4.2)
    - Resource owner password credentials grant (RFC 6749 Section 4.3.2)
    - Refresh token grant (RFC 6749 Section 6)
    - Token exchange (RFC 8693)
    - JWT bearer token grant (RFC 7523)
    - SAML 2.0 bearer token grant (RFC 7522)
    - Device authorization grant (RFC 8628)

    Reference: RFC 6749 Section 3.2
    URL: https://datatracker.ietf.org/doc/html/rfc6749#section-3.2
    """

    # RFC 7662 - Token Introspection Endpoint
    INTROSPECTION = "/oauth2/introspect"
    """Token introspection endpoint for validating and getting metadata about tokens.

    This endpoint allows authorized parties to query information about tokens:
    - Whether a token is active/valid
    - Token type, scope, expiration time
    - Client ID and subject associated with the token
    - Custom claims and metadata

    Commonly used by resource servers to validate access tokens.

    Reference: RFC 7662 Section 2
    URL: https://datatracker.ietf.org/doc/html/rfc7662#section-2
    """

    # RFC 7009 - Token Revocation Endpoint
    REVOCATION = "/oauth2/revoke"
    """Token revocation endpoint for invalidating access and refresh tokens.

    This endpoint allows clients to notify the authorization server that
    a token is no longer needed:
    - Revoke access tokens to limit their lifetime
    - Revoke refresh tokens to prevent future token generation
    - Bulk revocation when refresh token is revoked

    Important for security when tokens may be compromised or sessions end.

    Reference: RFC 7009 Section 2
    URL: https://datatracker.ietf.org/doc/html/rfc7009#section-2
    """

    # RFC 7591 - Dynamic Client Registration Endpoint
    REGISTRATION = "/oauth2/register"
    """Dynamic client registration endpoint for registering OAuth 2.0 clients.

    This endpoint allows clients to dynamically register with the authorization server:
    - Submit client metadata (redirect URIs, grant types, etc.)
    - Receive client credentials or client ID
    - Update or delete client registrations
    - Handle client authentication methods

    Enables automatic client onboarding without manual administrator intervention.

    Reference: RFC 7591 Section 3
    URL: https://datatracker.ietf.org/doc/html/rfc7591#section-3
    """

    # RFC 9126 - Pushed Authorization Requests (PAR) Endpoint
    PUSHED_AUTHORIZATION = "/oauth2/par"
    """Pushed Authorization Requests (PAR) endpoint for pre-registering authorization requests.

    This endpoint allows clients to push authorization request parameters directly
    to the authorization server before redirecting the user:
    - Submit authorization parameters via secure back-channel
    - Receive a request URI to use in the authorization request
    - Improve security by reducing parameter exposure in front-channel
    - Enable large request payloads that exceed URL length limits

    Particularly useful for complex authorization requests and enhanced security.

    Reference: RFC 9126 Section 3
    URL: https://datatracker.ietf.org/doc/html/rfc9126#section-3
    """

    @classmethod
    def construct_url(cls, base_url: str, endpoint: "OAuth2DefaultEndpoints") -> str:
        """Construct a complete OAuth 2.0 endpoint URL from base URL and endpoint path.

        Args:
            base_url: Base URL of the authorization server (e.g., "https://auth.example.com")
            endpoint: OAuth 2.0 endpoint path to append

        Returns:
            Complete endpoint URL

        Example:
            >>> OAuth2DefaultEndpoints.construct_url("https://auth.example.com", OAuth2DefaultEndpoints.AUTHORIZATION)
            'https://auth.example.com/oauth2/authorize'

            >>> OAuth2DefaultEndpoints.construct_url("https://auth.example.com/", OAuth2DefaultEndpoints.TOKEN)
            'https://auth.example.com/oauth2/token'
        """
        return f"{base_url.rstrip('/')}{endpoint}"
