"""JWT Profile for OAuth 2.0 implementations (RFC 7523, RFC 9068).

This module implements JWT-related OAuth 2.0 profiles for secure token handling,
including JWT client assertions and JWT access token profiles.

RFC 7523: JSON Web Token (JWT) Profile for OAuth 2.0 Client Authentication
and Authorization Grants
https://datatracker.ietf.org/doc/html/rfc7523

RFC 9068: JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens
https://datatracker.ietf.org/doc/html/rfc9068

Key Features:
- JWT client authentication (RFC 7523)
- JWT authorization grants (RFC 7523)
- JWT access token format (RFC 9068)
- Claims validation and verification
- Signature verification and key management

JWT Client Authentication Use Cases:
- High-security environments requiring cryptographic authentication
- Distributed systems with pre-shared keys or certificates
- Service-to-service authentication
- Environments where client secrets are not practical

JWT Access Token Benefits:
- Self-contained tokens with embedded claims
- Cryptographic integrity protection
- Reduced database lookups for token validation
- Standardized claim format across services
"""

import base64
import json
from typing import Any

from authlib.jose import JsonWebKey, JsonWebToken
from pydantic import BaseModel

from ..http._transports import HttpxAsyncTransport
from ..http._wire import HttpRequest
from ..types.models import ClientConfig


def _split_jwt_token(jwt_token: str) -> tuple[str, str, str]:
    """Split JWT token into its three parts.

    Args:
        jwt_token: JWT token string

    Returns:
        Tuple of (header_b64, payload_b64, signature_b64)

    Raises:
        ValueError: If token format is invalid
    """
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise ValueError(
            "Invalid JWT token format - expected 3 parts separated by dots"
        )
    if len([part for part in parts if len(part) > 0]) != 3:
        raise ValueError(
            "Invalid JWT token format - parts cannot be empty"
        )
    return parts[0], parts[1], parts[2]


def _decode_jwt_part(part_b64: str) -> dict[str, Any]:
    """Decode a base64-encoded JWT part (header or payload).

    Args:
        part_b64: Base64-encoded JWT part

    Returns:
        Decoded dictionary

    Raises:
        ValueError: If decoding fails
    """
    padding = len(part_b64) % 4
    if padding:
        part_b64 += "=" * (4 - padding)

    try:
        part_bytes = base64.urlsafe_b64decode(part_b64)
        part_data = json.loads(part_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Failed to decode JWT part: {e}") from e

    return part_data if isinstance(part_data, dict) else {}


def get_claims(jwt_token: str) -> dict[str, Any]:
    """Extract all claims from a JWT token payload without verification.

    This utility extracts all claims from a JWT token's payload without
    performing signature verification. Useful for operational purposes
    like extracting specific claims for token exchange requests.

    Args:
        jwt_token: JWT token string (without Bearer prefix)

    Returns:
        Dictionary of all claims in the JWT payload

    Raises:
        ValueError: If token is malformed or cannot be decoded

    Note:
        This function does NOT verify the token signature. It's intended
        for extracting claims from trusted tokens for operational purposes.

    Example:
        >>> token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJjbGllbnRfaWQiOiJhYmMxMjMiLCJzdWIiOiJ1c2VyMTIzIn0.signature"
        >>> claims = get_claims(token)
        >>> print(claims)  # {"client_id": "abc123", "sub": "user123"}
    """
    try:
        # Split token and extract payload
        _, payload_b64, _ = _split_jwt_token(jwt_token)

        # Decode payload part
        return _decode_jwt_part(payload_b64)

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to extract claims from JWT token: {e}") from e


def get_header(jwt_token: str) -> dict[str, Any]:
    """Extract header information from a JWT token without verification.

    This utility extracts header information from a JWT token including
    algorithm, key ID, and other header claims.

    Args:
        jwt_token: JWT token string (without Bearer prefix)

    Returns:
        Dictionary of all header claims in the JWT

    Raises:
        ValueError: If token is malformed or cannot be decoded

    Note:
        This function does NOT verify the token signature. It's intended
        for extracting header information for operational purposes.

    Example:
        >>> token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImtpZCI6ImtleTEifQ.payload.signature"
        >>> header = get_header(token)
        >>> print(header)  # {"typ": "JWT", "alg": "RS256", "kid": "key1"}
    """
    try:
        header_b64, _, _ = _split_jwt_token(jwt_token)

        header = _decode_jwt_part(header_b64)
        # https://datatracker.ietf.org/doc/html/rfc9068#section-2.1
        if header["alg"] == "none":
            raise ValueError("none algorithm is not supported")

        return _decode_jwt_part(header_b64)

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to extract header from JWT token: {e}") from e


def extract_scopes(claims: dict[str, Any]) -> list[str]:
    """Extract scopes from JWT token claims.

    Supports both 'scope' (space-separated string) and 'scp' (list) claims
    as commonly used in OAuth 2.0 JWT access tokens.

    Args:
        claims: JWT token claims dictionary

    Returns:
        List of scope strings

    Example:
        >>> claims = {"scope": "read write admin"}
        >>> scopes = extract_scopes(claims)
        >>> print(scopes)  # ["read", "write", "admin"]

        >>> claims = {"scp": ["read", "write", "admin"]}
        >>> scopes = extract_scopes(claims)
        >>> print(scopes)  # ["read", "write", "admin"]
    """
    for claim in ["scope", "scp"]:
        if claim in claims:
            if isinstance(claims[claim], str):
                # Split space-separated scope string
                return claims[claim].split()
            elif isinstance(claims[claim], list):
                # Return list of scopes as-is
                return claims[claim]
    return []


class JWTAccessToken(BaseModel):
    """JWT Access Token profile implementing RFC 9068.

    Defines the standard claims and structure for JWT-formatted access tokens
    as specified in RFC 9068. Supports custom claims and preserves the raw token.

    Reference: https://datatracker.ietf.org/doc/html/rfc9068#section-2
    """

    # Standard JWT claims (RFC 7519)
    iss: str  # Issuer
    sub: str  # Subject
    aud: str | list[str]  # Audience
    exp: int  # Expiration time
    iat: int  # Issued at
    jti: str | None = None  # JWT ID

    # OAuth 2.0 specific claims (RFC 9068)
    client_id: str  # OAuth 2.0 client identifier
    scope: str | None = None  # Space-separated scopes

    # Optional authorization details
    authorization_details: list[dict[str, Any]] | None = None

    # Custom claims - stores any additional claims not covered by standard fields
    custom_claims: dict[str, Any] = {}

    # Raw token preservation
    _raw: str  # Original JWT token string

    def get_custom_claim(self, claim_name: str, default: Any = None) -> Any:
        """Get a custom claim value.

        Args:
            claim_name: Name of the custom claim
            default: Default value if claim not found

        Returns:
            Custom claim value or default
        """
        return self.custom_claims.get(claim_name, default)

    def has_custom_claim(self, claim_name: str) -> bool:
        """Check if a custom claim exists.

        Args:
            claim_name: Name of the custom claim

        Returns:
            True if the custom claim exists, False otherwise
        """
        return claim_name in self.custom_claims

    def get_all_claims(self) -> dict[str, Any]:
        """Get all claims (standard + custom) as a dictionary.

        Returns:
            Dictionary containing all token claims
        """
        claims = {
            "iss": self.iss,
            "sub": self.sub,
            "aud": self.aud,
            "exp": self.exp,
            "iat": self.iat,
            "client_id": self.client_id,
        }

        # Add optional standard claims if present
        if self.jti is not None:
            claims["jti"] = self.jti
        if self.scope is not None:
            claims["scope"] = self.scope
        if self.authorization_details is not None:
            claims["authorization_details"] = self.authorization_details

        # Add custom claims
        claims.update(self.custom_claims)

        return claims


def decode_and_verify_jwt(
    jwt_token: str, verification_key: str, algorithm: str = "RS256"
) -> dict:
    """Decode and verify JWT token signature using authlib.

    Args:
        jwt_token: JWT token string (without Bearer prefix)
        verification_key: Public key for verification (PEM format)
        algorithm: JWT algorithm (default RS256)

    Returns:
        Verified JWT claims dictionary

    Raises:
        ValueError: If token is invalid, malformed, or signature verification fails
    """
    try:
        jwt = JsonWebToken([algorithm])
        claims = jwt.decode(jwt_token, verification_key)
        return claims
    except Exception as e:
        raise ValueError(f"JWT verification failed: {e}") from e


def parse_jwt_access_token(
    jwt_token: str, verification_key: str, algorithm: str = "RS256"
) -> JWTAccessToken:
    """Parse a JWT token into a JWTAccessToken model with signature verification.

    This function decodes and verifies the JWT token signature, then creates a
    structured JWTAccessToken model instance. Supports custom claims and
    preserves the original token.

    Args:
        jwt_token: JWT token string (without Bearer prefix)
        verification_key: Public key for verification (PEM format)
        algorithm: JWT algorithm (default RS256)

    Returns:
        JWTAccessToken model instance with verified claims

    Raises:
        ValueError: If token is malformed, signature invalid, or missing required claims

    Example:
        >>> token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9..."
        >>> access_token = parse_jwt_access_token(token, public_key)
        >>> print(f"Client: {access_token.client_id}")
        >>> print(f"Scopes: {access_token.scope}")
        >>> print(f"Custom claims: {access_token.custom_claims}")
    """
    claims = decode_and_verify_jwt(jwt_token, verification_key, algorithm)

    required_claims = ["iss", "sub", "aud", "exp", "iat", "client_id"]
    missing_claims = [claim for claim in required_claims if claim not in claims]
    if missing_claims:
        raise ValueError(f"Missing required claims: {', '.join(missing_claims)}")

    scopes_list = extract_scopes(claims)
    scope_string = " ".join(scopes_list) if scopes_list else None

    standard_claims = {
        "iss",
        "sub",
        "aud",
        "exp",
        "iat",
        "jti",
        "client_id",
        "scope",
        "scp",
        "authorization_details",
    }

    custom_claims = {
        key: value for key, value in claims.items() if key not in standard_claims
    }

    try:
        return JWTAccessToken(
            iss=claims["iss"],
            sub=claims["sub"],
            aud=claims["aud"],
            exp=claims["exp"],
            iat=claims["iat"],
            jti=claims.get("jti"),
            client_id=claims["client_id"],
            scope=scope_string,
            authorization_details=claims.get("authorization_details"),
            custom_claims=custom_claims,
            _raw=jwt_token,
        )
    except Exception as e:
        raise ValueError(f"Failed to create JWTAccessToken model: {e}") from e


async def get_verification_key(token: str, jwks_uri: str) -> str:
    """Get the verification key for a JWT token from JWKS.

    Args:
        token: JWT token string
        jwks_uri: JWKS endpoint URL for key fetching

    Returns:
        Public key for verification (PEM format)

    Raises:
        ValueError: If key cannot be obtained or token is malformed
    """
    # Extract kid from token header
    try:
        header = get_header(token)
        kid = header.get("kid")
        return await get_jwks_key(kid, jwks_uri)

    except Exception as e:
        raise ValueError(f"Failed to extract key ID from token: {e}") from e


async def get_jwks_key(kid: str | None, jwks_uri: str) -> str:
    """Fetch key from JWKS endpoint.

    Args:
        kid: Key ID from JWT header (optional)
        jwks_uri: JWKS endpoint URL

    Returns:
        Public key for verification (PEM format)

    Raises:
        ValueError: If JWKS cannot be fetched or key not found
    """

    try:
        # Use the existing transport infrastructure
        config = ClientConfig()
        transport = HttpxAsyncTransport(config=config)

        request = HttpRequest(method="GET", url=jwks_uri, headers={}, body=b"")

        response = await transport.request_raw(request)

        if response.status != 200:
            raise ValueError(f"JWKS endpoint returned status {response.status}")

        jwks_data = json.loads(response.body.decode("utf-8"))

        keys = jwks_data.get("keys", [])
        if not keys:
            raise ValueError("No keys found in JWKS")

        if kid:
            for key_data in keys:
                if key_data.get("kid") == kid:
                    jwk = JsonWebKey.import_key(key_data)
                    return jwk.get_public_key()  # type: ignore
            raise ValueError(f"Key ID '{kid}' not found")
        else:
            if len(keys) == 1:
                jwk = JsonWebKey.import_key(keys[0])
                return jwk.get_public_key()  # type: ignore
            elif len(keys) > 1:
                raise ValueError("Multiple keys in JWKS but no key ID (kid) in token")
            else:
                raise ValueError("No keys found in JWKS")

    except Exception as e:
        raise ValueError(f"Failed to fetch JWKS: {e}") from e
