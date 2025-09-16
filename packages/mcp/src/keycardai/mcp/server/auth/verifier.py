import time
from typing import Any

from mcp.server.auth.provider import AccessToken

from keycardai.oauth.utils.jwt import (
    get_header,
    get_verification_key,
    parse_jwt_access_token,
)

from ._cache import JWKSCache, JWKSKey


class TokenVerifier:
    """Token verifier for KeyCard zone-issued tokens."""

    def __init__(
        self,
        issuer: str,
        required_scopes: list[str] | None = None,
        jwks_uri: str | None = None,
        allowed_algorithms: list[str] = None,
        cache_ttl: int = 300,  # 5 minutes default
    ):
        """Initialize the KeyCard token verifier.

        Args:
            issuer: Expected token issuer (required)
            required_scopes: Required scopes for token validation
            jwks_uri: JWKS endpoint URL for key fetching
            allowed_algorithms: JWT algorithms (default RS256)
            cache_ttl: JWKS cache TTL in seconds (default 300 = 5 minutes)
        """
        if not issuer:
            raise ValueError("Issuer is required for token verification")
        if allowed_algorithms is None:
            allowed_algorithms = ["RS256"]
        self.issuer = issuer
        self.required_scopes = required_scopes or []
        self.jwks_uri = jwks_uri
        self.allowed_algorithms = allowed_algorithms
        self.cache_ttl = cache_ttl

        self._jwks_cache = JWKSCache(ttl=cache_ttl, max_size=10)

    async def _get_verification_key(self, token: str) -> JWKSKey:
        """Get the verification key for the token with caching."""
        if not self.jwks_uri:
            raise ValueError("JWKS URI not configured")

        header = get_header(token)
        kid = header.get("kid")
        algorithm = header.get("alg")
        if algorithm not in self.allowed_algorithms:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

        cached_key = self._jwks_cache.get_key(kid)
        if cached_key is not None:
            return cached_key

        verification_key = await get_verification_key(token, self.jwks_uri)

        self._jwks_cache.set_key(kid, verification_key, algorithm)
        cached_key = self._jwks_cache.get_key(kid)
        if cached_key is None:
            raise ValueError("Failed to cache verification key")
        return cached_key

    def clear_cache(self) -> None:
        """Clear the JWKS key cache."""
        self._jwks_cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics for debugging.

        Returns:
            Dictionary with cache statistics
        """
        return self._jwks_cache.get_stats()

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a JWT token and return AccessToken if valid.

        Performs JWT verification including:
        - Parse token into structured JWTAccessToken model internally
        - Validate token expiration
        - Validate issuer if configured
        - Validate required scopes if configured
        - Convert to AccessToken format for return

        Note: This is a simplified implementation that does not perform
        cryptographic signature verification. For production use, proper
        signature verification should be implemented.

        Args:
            token: JWT token string to verify

        Returns:
            AccessToken object if valid, None if invalid
        """
        try:
            verification_key = await self._get_verification_key(token)

            jwt_access_token = parse_jwt_access_token(
                token, verification_key.key, verification_key.algorithm
            )

            if jwt_access_token.exp < time.time():
                return None

            if jwt_access_token.iss != self.issuer:
                return None

            if self.required_scopes:
                token_scopes = (
                    jwt_access_token.scope.split() if jwt_access_token.scope else []
                )
                token_scopes_set = set(token_scopes)
                required_scopes_set = set(self.required_scopes)
                if not required_scopes_set.issubset(token_scopes_set):
                    return None

            token_scopes = (
                jwt_access_token.scope.split() if jwt_access_token.scope else []
            )

            return AccessToken(
                token=token,
                client_id=jwt_access_token.client_id,
                scopes=token_scopes,
                expires_at=jwt_access_token.exp,
                resource=jwt_access_token.get_custom_claim("resource"),
            )

        except Exception:
            return None

