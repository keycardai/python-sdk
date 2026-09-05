"""JWKS discovery under the event loop (ECO-364) and its caching contract
(spec base.md, "Metadata failures are not sticky").

``verify_token`` is async; jwks_uri discovery uses the synchronous client, so it
must run off the loop. Success is cached for discovery_ttl, a transient failure
caches nothing, a deterministic failure is remembered for at most negative_ttl,
and concurrent cold-cache callers share one fetch that no single caller's
cancellation can poison.
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import pytest
from blockbuster import blockbuster_ctx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc import jwt as jose_jwt
from joserfc.jwk import RSAKey

import keycardai.oauth
from keycardai.oauth.exceptions import NetworkError, OAuthHttpError, OAuthProtocolError
from keycardai.oauth.server.exceptions import JWKSDiscoveryError
from keycardai.oauth.server.verifier import TokenVerifier

KID = "test-key-1"


class _ZoneHandler(BaseHTTPRequestHandler):
    """Serves authorization-server metadata and a JWKS for a local issuer."""

    jwks: dict = {}
    issuer: str = ""
    discovery_hits = 0

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/.well-known/oauth-authorization-server":
            type(self).discovery_hits += 1
            body = {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/oauth2/authorize",
                "token_endpoint": f"{self.issuer}/oauth2/token",
                "jwks_uri": f"{self.issuer}/.well-known/jwks.json",
                "response_types_supported": ["code"],
            }
        elif self.path == "/.well-known/jwks.json":
            body = self.jwks
        else:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def zone():
    """A real local issuer: metadata, JWKS, and a factory for signed tokens."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    rsa_key = RSAKey.import_key(private_pem)
    public_jwk = rsa_key.as_dict(private=False)
    public_jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})

    class Handler(_ZoneHandler):
        pass

    Handler.jwks = {"keys": [public_jwk]}
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Handler.issuer = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def mint(**claims) -> str:
        now = int(time.time())
        payload = {
            "iss": Handler.issuer,
            "sub": "user-1",
            "aud": "https://api.example.com",
            "exp": now + 300,
            "iat": now,
            "client_id": "client-1",
            "scope": "read",
            **claims,
        }
        return jose_jwt.encode({"alg": "RS256", "kid": KID}, payload, rsa_key)

    yield Handler, mint
    server.shutdown()
    server.server_close()


class TestVerifyTokenDoesNotBlockTheLoop:
    """ECO-364: a fresh verifier, cold cache, request under an event loop."""

    @pytest.mark.asyncio
    async def test_cold_cache_verify_under_blockbuster(self, zone):
        handler, mint = zone
        verifier = TokenVerifier(issuer=handler.issuer, audience="https://api.example.com")
        token = mint()

        with blockbuster_ctx(keycardai.oauth):
            access = await verifier.verify_token(token)

        assert access.client_id == "client-1"
        assert access.sub == "user-1"
        assert handler.discovery_hits == 1

    @pytest.mark.asyncio
    async def test_discovery_reused_across_requests(self, zone):
        handler, mint = zone
        verifier = TokenVerifier(issuer=handler.issuer, audience="https://api.example.com")

        with blockbuster_ctx(keycardai.oauth):
            await verifier.verify_token(mint())
            await verifier.verify_token(mint(sub="user-2"))

        assert handler.discovery_hits == 1

    @pytest.mark.asyncio
    async def test_sync_discovery_still_blocks_when_called_directly(self, zone):
        """The sync path is unchanged; only the async call sites moved off-loop."""
        handler, _ = zone
        verifier = TokenVerifier(issuer=handler.issuer)
        assert verifier._discover_jwks_uri() == f"{handler.issuer}/.well-known/jwks.json"


def _verifier_with(side_effect, **knobs) -> tuple[TokenVerifier, Mock]:
    client = Mock()
    client.discover_server_metadata = Mock(side_effect=side_effect)
    factory = Mock()
    factory.create_client = Mock(return_value=client)
    verifier = TokenVerifier(
        issuer="https://example.com", client_factory=factory, **knobs
    )
    return verifier, client.discover_server_metadata


def _metadata(jwks_uri: str | None = "https://example.com/.well-known/jwks.json"):
    metadata = Mock()
    metadata.jwks_uri = jwks_uri
    return metadata


class FakeClock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    fake = FakeClock()
    with patch("keycardai.oauth._metadata_cache._now", fake):
        yield fake


class TestJwksDiscoveryFailureCaching:
    def test_transient_failure_not_cached(self, clock):
        verifier, discover = _verifier_with(
            [NetworkError(ConnectionError("refused")), _metadata()]
        )
        with pytest.raises(JWKSDiscoveryError) as exc_info:
            verifier._discover_jwks_uri()
        assert exc_info.value.retryable is True

        assert verifier._discover_jwks_uri() == "https://example.com/.well-known/jwks.json"
        assert discover.call_count == 2

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_429_and_5xx_are_transient(self, clock, status):
        verifier, discover = _verifier_with([OAuthHttpError(status), _metadata()])
        with pytest.raises(JWKSDiscoveryError) as exc_info:
            verifier._discover_jwks_uri()
        assert exc_info.value.retryable is True
        verifier._discover_jwks_uri()
        assert discover.call_count == 2

    def test_404_remembered_for_negative_ttl_then_retried(self, clock):
        verifier, discover = _verifier_with(
            [OAuthHttpError(404), _metadata()], negative_ttl=60
        )
        for _ in range(3):
            with pytest.raises(JWKSDiscoveryError) as exc_info:
                verifier._discover_jwks_uri()
            assert exc_info.value.retryable is False
        assert discover.call_count == 1

        clock.advance(61)
        assert verifier._discover_jwks_uri() == "https://example.com/.well-known/jwks.json"
        assert discover.call_count == 2

    def test_malformed_metadata_is_deterministic(self, clock):
        verifier, discover = _verifier_with(
            [OAuthProtocolError("invalid_response", "not json"), _metadata()],
            negative_ttl=60,
        )
        with pytest.raises(JWKSDiscoveryError) as exc_info:
            verifier._discover_jwks_uri()
        assert exc_info.value.retryable is False
        with pytest.raises(JWKSDiscoveryError):
            verifier._discover_jwks_uri()
        assert discover.call_count == 1

    def test_missing_jwks_uri_is_deterministic_and_remembered(self, clock):
        verifier, discover = _verifier_with(
            [_metadata(jwks_uri=None), _metadata()], negative_ttl=60
        )
        with pytest.raises(JWKSDiscoveryError) as exc_info:
            verifier._discover_jwks_uri()
        assert exc_info.value.retryable is False
        with pytest.raises(JWKSDiscoveryError):
            verifier._discover_jwks_uri()
        assert discover.call_count == 1

    def test_negative_ttl_zero_disables_negative_caching(self, clock):
        verifier, discover = _verifier_with(
            [OAuthHttpError(404), OAuthHttpError(404), _metadata()], negative_ttl=0
        )
        for _ in range(2):
            with pytest.raises(JWKSDiscoveryError):
                verifier._discover_jwks_uri()
        assert discover.call_count == 2
        verifier._discover_jwks_uri()
        assert discover.call_count == 3

    def test_negative_ttl_never_exceeds_discovery_ttl(self, clock):
        verifier, discover = _verifier_with(
            [OAuthHttpError(404), _metadata()], discovery_ttl=10, negative_ttl=600
        )
        with pytest.raises(JWKSDiscoveryError):
            verifier._discover_jwks_uri()
        clock.advance(11)
        verifier._discover_jwks_uri()
        assert discover.call_count == 2

    def test_negative_cache_is_per_issuer(self, clock):
        verifier, discover = _verifier_with(
            [OAuthHttpError(404), _metadata()], negative_ttl=60
        )
        with pytest.raises(JWKSDiscoveryError):
            verifier._discover_jwks_uri(issuer="https://a.example.com")
        with pytest.raises(JWKSDiscoveryError):
            verifier._discover_jwks_uri(issuer="https://a.example.com")
        verifier._discover_jwks_uri(issuer="https://example.com")
        assert discover.call_count == 2

    def test_default_negative_ttl(self):
        assert TokenVerifier(issuer="https://example.com").negative_ttl == 60.0


class TestJwksDiscoveryConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_cold_verifications_share_one_discovery(self):
        release = threading.Event()

        def slow_discover():
            release.wait(timeout=5)
            return _metadata()

        verifier, discover = _verifier_with(slow_discover)

        with patch(
            "keycardai.oauth.server.verifier.get_header",
            return_value={"alg": "RS256", "kid": "abc"},
        ), patch(
            "keycardai.oauth.server.verifier.get_jwks_key",
            new=Mock(side_effect=lambda *a, **k: asyncio.sleep(0, result="pem")),
        ):
            tasks = [
                asyncio.create_task(verifier._get_verification_key(f"t{i}"))
                for i in range(4)
            ]
            await asyncio.sleep(0.05)
            release.set()
            results = await asyncio.gather(*tasks)

        assert discover.call_count == 1
        assert all(r.key == "pem" for r in results)

    @pytest.mark.asyncio
    async def test_one_callers_cancellation_does_not_poison_the_shared_fetch(self):
        release = threading.Event()

        def slow_discover():
            release.wait(timeout=5)
            return _metadata()

        verifier, discover = _verifier_with(slow_discover)

        with patch(
            "keycardai.oauth.server.verifier.get_header",
            return_value={"alg": "RS256", "kid": "abc"},
        ), patch(
            "keycardai.oauth.server.verifier.get_jwks_key",
            new=Mock(side_effect=lambda *a, **k: asyncio.sleep(0, result="pem")),
        ):
            cancelled = asyncio.create_task(verifier._get_verification_key("t1"))
            survivor = asyncio.create_task(verifier._get_verification_key("t2"))
            await asyncio.sleep(0.05)

            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled

            release.set()
            result = await survivor

        assert result.key == "pem"
        assert discover.call_count == 1
        assert verifier._key_inflight == {}

    @pytest.mark.asyncio
    async def test_failed_resolution_clears_inflight_so_the_next_call_retries(self):
        verifier, discover = _verifier_with(
            [NetworkError(ConnectionError("refused")), _metadata()]
        )

        with patch(
            "keycardai.oauth.server.verifier.get_header",
            return_value={"alg": "RS256", "kid": "abc"},
        ), patch(
            "keycardai.oauth.server.verifier.get_jwks_key",
            new=Mock(side_effect=lambda *a, **k: asyncio.sleep(0, result="pem")),
        ):
            with pytest.raises(JWKSDiscoveryError):
                await verifier._get_verification_key("t1")
            assert verifier._key_inflight == {}
            result = await verifier._get_verification_key("t1")

        assert result.key == "pem"
        assert discover.call_count == 2
