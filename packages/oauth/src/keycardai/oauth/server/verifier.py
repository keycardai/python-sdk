"""Token verification for Keycard zone-issued tokens.

This module provides JWT token verification with JWKS caching, multi-zone support,
and audience/scope validation. It replaces the MCP-dependent verifier with a
framework-free implementation.
"""

import asyncio
import time
import warnings
from typing import Any

from pydantic import AnyHttpUrl, BaseModel

from keycardai.oauth.exceptions import InvalidTokenError
from keycardai.oauth.types.models import ClientConfig
from keycardai.oauth.utils.jwt import (
    get_claims,
    get_header,
    get_jwks_key,
    parse_jwt_access_token,
)

from ._cache import JWKSCache, JWKSKey
from .client_factory import ClientFactory, DefaultClientFactory
from .exceptions import (
    CacheError,
    JWKSDiscoveryError,
    JWKSUriValidationError,
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
        issuer: str | list[str],
        required_scopes: list[str] | None = None,
        jwks_uri: str | None = None,
        allowed_algorithms: list[str] = None,
        key_ttl: int = 300,
        enable_multi_zone: bool = False,
        audience: str | dict[str, str] | None = None,
        client_factory: ClientFactory | None = None,
        *,
        discovery_ttl: int = 3600,
        fetch_timeout: float = 10.0,
        cache_ttl: int | None = None,
    ):
        if not issuer:
            raise VerifierConfigError("Issuer is required for token verification")
        if allowed_algorithms is None:
            allowed_algorithms = ["RS256"]
        if cache_ttl is not None:
            warnings.warn(
                "cache_ttl is deprecated; use key_ttl instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            key_ttl = cache_ttl
        # A single issuer or an allowlist of trusted issuers. The verify surface
        # accepts a token whose `iss` is any member of the allowlist. `self.issuer`
        # is the primary issuer, used for multi-zone zone-scoped URL derivation.
        if isinstance(issuer, str):
            self._trusted_issuers = {issuer}
            self.issuer = issuer
        else:
            self._trusted_issuers = set(issuer)
            self.issuer = issuer[0]
        self.required_scopes = required_scopes or []
        self.jwks_uri = jwks_uri
        self.allowed_algorithms = allowed_algorithms
        self.key_ttl = key_ttl
        self.discovery_ttl = discovery_ttl
        self.fetch_timeout = fetch_timeout

        self._jwks_cache = JWKSCache(ttl=key_ttl, max_size=256)
        # Discovered jwks_uri per zone, with a discovery_ttl expiry:
        # cache_key -> (jwks_uri, cached_at).
        self._discovered_jwks_uris: dict[str, tuple[str, float]] = {}
        # De-duplicate concurrent cold-cache key fetches (asyncio, one event loop).
        self._key_inflight: dict[str, asyncio.Future] = {}

        self.enable_multi_zone = enable_multi_zone
        self.audience = audience
        self.client_factory = client_factory or DefaultClientFactory()

    @property
    def cache_ttl(self) -> int:
        """Deprecated alias for ``key_ttl``."""
        return self.key_ttl

    def _discover_jwks_uri(
        self, issuer: str | None = None, zone_id: str | None = None
    ) -> str:
        # An explicitly configured jwks_uri is static and bypasses discovery.
        if self.jwks_uri:
            return self.jwks_uri

        if issuer is not None:
            cache_key = f"issuer:{issuer}"
            discovery_issuer = issuer
        else:
            cache_key = f"{zone_id or 'default'}"
            discovery_issuer = self.issuer
            if self.enable_multi_zone and zone_id:
                discovery_issuer = self._create_zone_scoped_url(self.issuer, zone_id)

        cached = self._discovered_jwks_uris.get(cache_key)
        if cached is not None:
            cached_uri, cached_at = cached
            if time.time() - cached_at < self.discovery_ttl:
                return cached_uri

        try:
            client = self.client_factory.create_client(
                discovery_issuer,
                config=ClientConfig(
                    enable_metadata_discovery=True,
                    auto_register_client=False,
                    timeout=self.fetch_timeout,
                ),
            )
            server_metadata = client.discover_server_metadata()
            discovered_uri = server_metadata.jwks_uri
        except Exception as e:
            raise JWKSDiscoveryError(discovery_issuer, zone_id, cause=e) from e

        if not discovered_uri:
            raise JWKSDiscoveryError(discovery_issuer, zone_id)

        # Security: a discovered jwks_uri must share the issuer's origin, so a
        # tampered discovery document cannot point key resolution elsewhere.
        self._assert_same_origin(discovery_issuer, discovered_uri)

        self._discovered_jwks_uris[cache_key] = (discovered_uri, time.time())
        return discovered_uri

    def _assert_same_origin(self, issuer: str, jwks_uri: str) -> None:
        issuer_url = AnyHttpUrl(issuer)
        jwks_url = AnyHttpUrl(jwks_uri)
        if (
            issuer_url.scheme != jwks_url.scheme
            or issuer_url.host != jwks_url.host
            or issuer_url.port != jwks_url.port
        ):
            raise JWKSUriValidationError(issuer, jwks_uri)

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
        try:
            header = get_header(token)
        except ValueError as e:
            raise InvalidTokenError("Malformed JWT header") from e
        algorithm = header.get("alg")
        if algorithm not in self.allowed_algorithms:
            raise InvalidTokenError(f"Unsupported JWT algorithm: {algorithm}")
        kid = header.get("kid")
        if not kid:
            raise InvalidTokenError("JWT missing key id (kid) header")
        return (kid, algorithm)

    def _get_zone_jwks_uri(self, jwks_uri: str, zone_id: str) -> str:
        jwks_url = AnyHttpUrl(jwks_uri)
        jwks_zone_host = jwks_url.host.replace(
            jwks_url.host, f"{zone_id}.{jwks_url.host}"
        )
        jwks_url.host = jwks_zone_host
        return jwks_url.to_string()

    async def _get_verification_key(
        self, token: str, zone_id: str | None = None, issuer: str | None = None
    ) -> JWKSKey:
        """Get the verification key for the token, with caching and de-dup.

        Keys are cached per ``(issuer, kid)`` so two issuers that happen to
        share a ``kid`` cannot collide. Concurrent cold-cache lookups for the
        same key share a single in-flight resolution, so a burst of requests
        triggers one discovery and one JWKS fetch rather than a thundering herd.
        """
        kid, algorithm = self._get_kid_and_algorithm(token)

        cache_key = f"{issuer}::{kid}" if issuer else kid
        cached_key = self._jwks_cache.get_key(cache_key)
        if cached_key is not None:
            return cached_key

        inflight_key = f"{issuer or 'default'}:{zone_id or 'default'}:{kid}"
        existing = self._key_inflight.get(inflight_key)
        if existing is not None:
            return await existing

        future = asyncio.ensure_future(
            self._resolve_and_cache_key(kid, algorithm, zone_id, issuer)
        )
        self._key_inflight[inflight_key] = future
        try:
            return await future
        finally:
            self._key_inflight.pop(inflight_key, None)

    async def _resolve_and_cache_key(
        self,
        kid: str,
        algorithm: str,
        zone_id: str | None = None,
        issuer: str | None = None,
    ) -> JWKSKey:
        """Discover the jwks_uri, fetch the key for ``kid``, and cache it."""
        if issuer is not None:
            jwks_uri = self._discover_jwks_uri(issuer=issuer)
        elif self.enable_multi_zone and zone_id:
            jwks_uri = self._discover_jwks_uri(zone_id=zone_id)
        else:
            jwks_uri = self._discover_jwks_uri()
            if zone_id:
                jwks_uri = self._get_zone_jwks_uri(jwks_uri, zone_id)

        verification_key = await get_jwks_key(
            kid, jwks_uri, timeout=self.fetch_timeout
        )

        cache_key = f"{issuer}::{kid}" if issuer else kid
        self._jwks_cache.set_key(cache_key, verification_key, algorithm)
        cached_key = self._jwks_cache.get_key(cache_key)
        if cached_key is None:
            raise CacheError("Failed to cache verification key")
        return cached_key

    def clear_cache(self) -> None:
        """Clear the JWKS key cache."""
        self._jwks_cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics for debugging."""
        return self._jwks_cache.get_stats()

    def _unverified_claims(self, token: str) -> dict[str, Any]:
        """Decode the JWT payload without verifying the signature.

        Used for the cheap policy checks that gate network key resolution.
        """
        try:
            return get_claims(token)
        except ValueError as e:
            raise InvalidTokenError("Malformed JWT") from e

    def _validate_issuer(self, iss: str | None) -> str:
        """Return the issuer if it is trusted, else raise.

        Enforced against the configured allowlist before any key resolution.
        """
        if not iss:
            raise InvalidTokenError("JWT missing issuer (iss) claim")
        if iss not in self._trusted_issuers:
            raise InvalidTokenError("Untrusted issuer")
        return iss

    def _check_not_expired(self, exp: Any) -> None:
        if exp is None:
            raise InvalidTokenError("JWT missing expiration (exp) claim")
        try:
            expired = float(exp) < time.time()
        except (TypeError, ValueError) as e:
            raise InvalidTokenError("JWT has invalid expiration (exp) claim") from e
        if expired:
            raise InvalidTokenError("Token expired")

    async def verify_token(self, token: str) -> AccessToken:
        """Verify a JWT token and return its ``AccessToken``.

        Cheap policy checks (trusted issuer, expiration, algorithm, ``kid``)
        run before any network I/O, so a token with an untrusted ``iss`` is
        rejected without triggering key resolution. The verification key is
        then resolved for the validated issuer, the signature checked, and the
        verified claims (issuer, expiration, audience, scopes) confirmed.

        Raises:
            InvalidTokenError: If the token fails any verification step.
        """
        claims = self._unverified_claims(token)
        issuer = self._validate_issuer(claims.get("iss"))
        self._check_not_expired(claims.get("exp"))

        key = await self._get_verification_key(token, issuer=issuer)
        return self._verify_token(token, key, expected_issuer=issuer)

    async def verify_token_for_zone(self, token: str, zone_id: str) -> AccessToken:
        """Verify a JWT token for a specific zone and return its ``AccessToken``.

        Raises:
            InvalidTokenError: If the token fails any verification step.
        """
        claims = self._unverified_claims(token)
        expected_issuer = self.issuer
        if self.enable_multi_zone and zone_id:
            expected_issuer = self._create_zone_scoped_url(self.issuer, zone_id)
        if claims.get("iss") != expected_issuer:
            raise InvalidTokenError("Untrusted issuer")
        self._check_not_expired(claims.get("exp"))

        key = await self._get_verification_key(token, zone_id)
        return self._verify_token(
            token, key, expected_issuer=expected_issuer, zone_id=zone_id
        )

    def _verify_token(
        self,
        token: str,
        key: JWKSKey,
        expected_issuer: str,
        zone_id: str | None = None,
    ) -> AccessToken:
        try:
            jwt_access_token = parse_jwt_access_token(token, key.key, key.algorithm)
        except ValueError as e:
            raise InvalidTokenError("Token signature or claims invalid") from e

        if jwt_access_token.exp < time.time():
            raise InvalidTokenError("Token expired")

        if jwt_access_token.iss != expected_issuer:
            raise InvalidTokenError("Untrusted issuer")

        if not jwt_access_token.validate_audience(self.audience, zone_id):
            raise InvalidTokenError("Invalid audience")

        if not jwt_access_token.validate_scopes(self.required_scopes):
            raise InvalidTokenError("Insufficient scope")

        return AccessToken(
            token=token,
            client_id=jwt_access_token.client_id,
            scopes=jwt_access_token.get_scopes(),
            expires_at=jwt_access_token.exp,
            resource=jwt_access_token.get_custom_claim("resource"),
        )
