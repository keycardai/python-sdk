"""OAuth 2.0 utility functions and helpers.

This module contains standalone utilities that don't require a client instance,
including bearer token utilities, JWT helpers, PKCE generators, and crypto utilities.
"""

from .bearer import (
    BearerToken,
    BearerTokenError,
    BearerTokenErrors,
    BearerTokenValidator,
)
from .crypto import CertificateBoundToken, MutualTLSClientAuth
from .jwt import (
    JWTAccessToken,
    JWTAccessTokenHandler,
    JWTAccessTokenValidator,
    JWTClientAssertion,
)
from .pkce import PKCEChallenge, PKCEGenerator, PKCEMethods


# Standalone utility functions for interface proposal compliance
def extract_bearer_token(authorization_header: str | None) -> str | None:
    """Extract bearer token from Authorization header.

    Args:
        authorization_header: Authorization header value

    Returns:
        Bearer token or None if not found/invalid
    """
    if not authorization_header:
        return None

    parts = authorization_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def validate_bearer_format(token: str) -> bool:
    """Validate bearer token format per RFC 6750.

    Args:
        token: Token to validate

    Returns:
        True if format is valid
    """
    if not token or not isinstance(token, str):
        return False
    # Basic format validation - token should not contain whitespace
    return (
        " " not in token
        and "\t" not in token
        and "\n" not in token
        and "\r" not in token
    )


def create_auth_header(token: str) -> str:
    """Create Authorization header for bearer token.

    Args:
        token: Bearer token

    Returns:
        Authorization header value
    """
    return f"Bearer {token}"


def generate_pkce_challenge():
    """Generate PKCE challenge.

    This is a placeholder - will be implemented in Phase 2.
    """
    raise NotImplementedError("PKCE generation will be implemented in Phase 2")


def verify_pkce_challenge(verifier: str, challenge: str) -> bool:
    """Verify PKCE challenge.

    This is a placeholder - will be implemented in Phase 2.
    """
    raise NotImplementedError("PKCE verification will be implemented in Phase 2")


def create_jwt_assertion(client_id: str, audience: str, private_key: str) -> str:
    """Create JWT client assertion.

    This is a placeholder - will be implemented in Phase 2.
    """
    raise NotImplementedError("JWT assertion creation will be implemented in Phase 2")


def generate_cert_thumbprint(cert_data: bytes) -> str:
    """Generate certificate thumbprint.

    This is a placeholder - will be implemented in Phase 2.
    """
    raise NotImplementedError(
        "Certificate thumbprint generation will be implemented in Phase 2"
    )


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
    "create_auth_header",
    "generate_pkce_challenge",
    "verify_pkce_challenge",
    "create_jwt_assertion",
    "generate_cert_thumbprint",
]
