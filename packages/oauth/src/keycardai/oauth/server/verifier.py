"""Token verification for Keycard zone-issued tokens.

This module provides JWT token verification with JWKS caching, multi-zone support,
and audience/scope validation. It replaces the MCP-dependent verifier with a
framework-free implementation.
"""

import time
from typing import Any

from pydantic import AnyHttpUrl, BaseModel

from keycardai.oauth.utils.jwt import (
    get_header,
    get_jwks_key,
    parse_jwt_access_token,
)

from ._cache import JWKSCache, JWKSKey
from .client_factory import ClientFactory, DefaultClientFactory
from .exceptions import (
    CacheError,
    JWKSDiscoveryError,
    UnsupportedAlgorithmError,
    VerifierConfigError,
)


class AccessToken(BaseModel):
    """Verified access token representation.

    This is a local model replacing ``mcp.server.auth.provider.AccessToken``
    so that the verifier has no MCP dependency.  The fields are identical
    to the MCP model for drop-in compatibility.
    """

    token: str
    client_id: str
    scopes: list[str]
    expires_at: int | None = None
    resource: str | None = None  # RFC 8707 resource indicator


class TokenVerifier:
    """Token verifier for Keycard zone-issued tokens."""

    def __init__(
        self,
        issuer: str,
        required_scopes: list[str] | None = None,
        jwks_uri: str | None = None,
        allowed_algorithms: list[str] = None,
        cache_ttl: int = 300,
        enable_multi_zone: bool = False,
        audience: str | dict[str, str] | None = None,
        client_factory: ClientFactory | None = None,
    ):
        if not issuer:
            raise VerifierConfigError("Issuer is required for token verification")
        if allowed_algorithms is None:
            allowed_algorithms = ["RS256"]
        self.issuer = issuer
        self.required_scopes = required_scopes or []
        self.jwks_uri = jwks_uri
        self.allowed_algorithms = allowed_algorithms
        self.cache_ttl = cache_ttl

        self._jwks_cache = JWKSCache(ttl=cache_ttl, max_size=10)
        self._discovered_jwks_uri: str | None = None
        self._discovered_jwks_uris: dict[str, str] = {}

        self.enable_multi_zone = enable_multi_zone
        self.audience = audience
        self.client_factory = client_factory or DefaultClientFactory()

    def _discover_jwks_uri(self, zone_id: str | None = None) -> str:
        cache_key = f"{zone_id or 'default'}"
        cached_uri = self._discovered_jwks_uris.get(cache_key)
        if cached_uri is not None:
            return cached_uri

        if self.jwks_uri:
            self._discovered_jwks_uris[cache_key] = self.jwks_uri
            return self.jwks_uri

        discovery_issuer = self.issuer
        if self.enable_multi_zone and zone_id:
            discovery_issuer = self._create_zone_scoped_url(self.issuer, zone_id)

        try:
            client = self.client_factory.create_client(discovery_issuer)
            server_metadata = client.discover_server_metadata()
            discovered_uri = server_metadata.jwks_uri

            if not discovered_uri:
                raise JWKSDiscoveryError(discovery_issuer, zone_id)

            self._discovered_jwks_uris[cache_key] = discovered_uri
            return discovered_uri

        except Exception as e:
            raise JWKSDiscoveryError(discovery_issuer, zone_id, cause=e) from e

    def _create_zone_scoped_url(self, base_url: str, zone_id: str) -> str:
        """Create zone-scoped URL by prepending zone_id to the host."""
        base_url_obj = AnyHttpUrl(base_url)

        port_part = ""
        if base_url_obj.port and not (
            (base_url_obj.scheme == "https" and base_url_obj.port == 443)
            or (base_url_obj.scheme == "http" and base_url_obj.port == 80)
        ):
            port_part = f":{base_url_obj.port}"

        zone_url = (
            f"{base_url_obj.scheme}://{zone_id}.{base_url_obj.host}{port_part}"
        )
        return zone_url

    def _get_kid_and_algorithm(self, token: str) -> tuple[str, str]:
        header = get_header(token)
        kid = header.get("kid")
        algorithm = header.get("alg")
        if algorithm not in self.allowed_algorithms:
            raise UnsupportedAlgorithmError(algorithm)
        return (kid, algorithm)

    def _get_zone_jwks_uri(self, jwks_uri: str, zone_id: str) -> str:
        jwks_url = AnyHttpUrl(jwks_uri)
        jwks_zone_host = jwks_url.host.replace(
            jwks_url.host, f"{zone_id}.{jwks_url.host}"
        )
        jwks_url.host = jwks_zone_host
        return jwks_url.to_string()

    async def _get_verification_key(
        self, token: str, zone_id: str | None = None
    ) -> JWKSKey:
        """Get the verification key for the token with caching."""
        kid, algorithm = self._get_kid_and_algorithm(token)

        cached_key = self._jwks_cache.get_key(kid)
        if cached_key is not None:
            return cached_key

        if self.enable_multi_zone and zone_id:
            jwks_uri = self._discover_jwks_uri(zone_id)
        else:
            jwks_uri = self._discover_jwks_uri()
            if zone_id:
                jwks_uri = self._get_zone_jwks_uri(jwks_uri, zone_id)

        verification_key = await get_jwks_key(kid, jwks_uri)

        self._jwks_cache.set_key(kid, verification_key, algorithm)
        cached_key = self._jwks_cache.get_key(kid)
        if cached_key is None:
            raise CacheError("Failed to cache verification key")
        return cached_key

    def clear_cache(self) -> None:
        """Clear the JWKS key cache."""
        self._jwks_cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics for debugging."""
        return self._jwks_cache.get_stats()

    async def verify_token_for_zone(
        self, token: str, zone_id: str
    ) -> AccessToken | None:
        """Verify a JWT token for a specific zone and return AccessToken if valid."""
        try:
            key = await self._get_verification_key(token, zone_id)
            return self._verify_token(token, key, zone_id)
        except Exception:
            return None

    def _verify_token(
        self, token: str, key: JWKSKey, zone_id: str | None = None
    ) -> AccessToken | None:
        jwt_access_token = parse_jwt_access_token(token, key.key, key.algorithm)

        if jwt_access_token.exp < time.time():
            return None

        expected_issuer = self.issuer
        if self.enable_multi_zone and zone_id:
            expected_issuer = self._create_zone_scoped_url(self.issuer, zone_id)

        if jwt_access_token.iss != expected_issuer:
            return None

        if not jwt_access_token.validate_audience(self.audience, zone_id):
            return None

        if not jwt_access_token.validate_scopes(self.required_scopes):
            return None

        token_scopes = jwt_access_token.get_scopes()

        return AccessToken(
            token=token,
            client_id=jwt_access_token.client_id,
            scopes=token_scopes,
            expires_at=jwt_access_token.exp,
            resource=jwt_access_token.get_custom_claim("resource"),
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a JWT token and return AccessToken if valid.

        Performs JWT verification including:
        - Parse token into structured JWTAccessToken model internally
        - Validate token expiration
        - Validate issuer if configured
        - Validate required scopes if configured
        - Convert to AccessToken format for return
        """
        try:
            key = await self._get_verification_key(token)
            return self._verify_token(token, key)
        except Exception:
            return None
