"""OAuth 2.0 utility functions and helpers.

This module contains standalone utilities that don't require a client instance,
including bearer token utilities, JWT helpers, PKCE generators, and crypto utilities.
"""

from .bearer import (
    BearerToken,
    BearerTokenError,
    BearerTokenErrors,
    BearerTokenValidator,
    extract_bearer_token,
    validate_bearer_format,
)
from .crypto import CertificateBoundToken, MutualTLSClientAuth
from .jwt import (
    JWTAccessToken,
    JWTAccessTokenHandler,
    JWTAccessTokenValidator,
    JWTClientAssertion,
    extract_jwt_client_id,
)
from .pkce import PKCEChallenge, PKCEGenerator, PKCEMethods

__all__ = [
    # Bearer token utilities (RFC 6750)
    "BearerToken",
    "BearerTokenValidator",
    "BearerTokenError",
    "BearerTokenErrors",
    # JWT utilities (RFC 7523, RFC 9068)
    "JWTAccessToken",
    "JWTAccessTokenHandler",
    "JWTAccessTokenValidator",
    "JWTClientAssertion",
    "extract_jwt_client_id",
    # PKCE utilities (RFC 7636)
    "PKCEGenerator",
    "PKCEChallenge",
    "PKCEMethods",
    # Crypto utilities (RFC 8705)
    "MutualTLSClientAuth",
    "CertificateBoundToken",
    # Standalone utility functions
    "extract_bearer_token",
    "validate_bearer_format",
    "verify_pkce_challenge",
]
