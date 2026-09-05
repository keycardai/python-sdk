"""Metadata failures are not sticky (spec base.md; token-exchange and
client-credentials unit rows on discovery caching).

The client never substitutes a convention-derived token endpoint for a failed
discovery. Success is cached for discovery_ttl, a transient failure caches
nothing, a deterministic failure is remembered for at most negative_ttl, and
concurrent cold-cache callers share one fetch.
"""

import asyncio
import dataclasses
import threading
from unittest.mock import AsyncMock, Mock, patch

import pytest

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.exceptions import (
    AuthorizationServerDiscoveryError,
    NetworkError,
    OAuthHttpError,
    OAuthProtocolError,
)
from keycardai.oauth.types.models import AuthorizationServerMetadata

ISSUER = "https://test.example.com"
CONVENTION_TOKEN_ENDPOINT = f"{ISSUER}/oauth2/token"
DISCOVERED_TOKEN_ENDPOINT = f"{ISSUER}/discovered/token"

METADATA = AuthorizationServerMetadata(
    issuer=ISSUER,
    authorization_endpoint=f"{ISSUER}/authorize",
    token_endpoint=DISCOVERED_TOKEN_ENDPOINT,
)
METADATA_WITHOUT_TOKEN_ENDPOINT = AuthorizationServerMetadata(
    issuer=ISSUER,
    authorization_endpoint=f"{ISSUER}/authorize",
)


def _transient() -> NetworkError:
    return NetworkError(ConnectionError("connection refused"), "GET metadata")


def _not_found() -> OAuthHttpError:
    return OAuthHttpError(404, operation="GET metadata")


def _server_error() -> OAuthHttpError:
    return OAuthHttpError(503, operation="GET metadata")


def _config(**overrides) -> ClientConfig:
    return ClientConfig(enable_metadata_discovery=True, **overrides)


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


class TestSyncClientDiscoveryCache:
    def _client(self, side_effect, **config) -> tuple[Client, Mock]:
        client = Client(ISSUER, config=_config(**config))
        discover = Mock(side_effect=side_effect)
        client.discover_server_metadata = discover
        return client, discover

    def test_transient_failure_surfaces_typed_and_is_not_cached(self, clock):
        client, discover = self._client([_transient(), METADATA])

        with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
            client._get_current_endpoints()
        assert exc_info.value.retryable is True
        assert isinstance(exc_info.value.cause, NetworkError)

        assert client._get_current_endpoints().token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 2

    def test_5xx_and_429_are_transient(self, clock):
        client, discover = self._client(
            [_server_error(), OAuthHttpError(429), METADATA]
        )
        for _ in range(2):
            with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
                client._get_current_endpoints()
            assert exc_info.value.retryable is True
        assert client._get_current_endpoints().token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 3

    def test_failed_discovery_never_yields_convention_endpoint(self, clock):
        client, _ = self._client([_transient()])
        with pytest.raises(AuthorizationServerDiscoveryError):
            client._get_current_endpoints()
        assert client._discovered_endpoints is None
        assert client._initialized is False

    def test_404_remembered_for_negative_ttl_then_rediscovered(self, clock):
        client, discover = self._client([_not_found(), METADATA], negative_ttl=60)

        for _ in range(3):
            with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
                client._get_current_endpoints()
            assert exc_info.value.retryable is False
        assert discover.call_count == 1

        clock.advance(61)
        assert client._get_current_endpoints().token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 2

    def test_negative_ttl_zero_disables_negative_caching(self, clock):
        client, discover = self._client(
            [_not_found(), _not_found(), METADATA], negative_ttl=0
        )
        for _ in range(2):
            with pytest.raises(AuthorizationServerDiscoveryError):
                client._get_current_endpoints()
        assert discover.call_count == 2
        assert client._get_current_endpoints().token == DISCOVERED_TOKEN_ENDPOINT

    def test_negative_ttl_never_exceeds_discovery_ttl(self, clock):
        client, discover = self._client(
            [_not_found(), METADATA], discovery_ttl=10, negative_ttl=600
        )
        with pytest.raises(AuthorizationServerDiscoveryError):
            client._get_current_endpoints()
        clock.advance(11)
        assert client._get_current_endpoints().token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 2

    def test_missing_token_endpoint_is_typed_and_remembered(self, clock):
        client, discover = self._client(
            [METADATA_WITHOUT_TOKEN_ENDPOINT, METADATA], negative_ttl=60
        )
        for _ in range(2):
            with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
                client._get_current_endpoints()
            assert exc_info.value.retryable is False
            assert "token_endpoint" in str(exc_info.value)
        assert discover.call_count == 1

        clock.advance(61)
        assert client._get_current_endpoints().token == DISCOVERED_TOKEN_ENDPOINT

    def test_malformed_metadata_is_deterministic(self, clock):
        client, _ = self._client([OAuthProtocolError("invalid_response", "bad json")])
        with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
            client._get_current_endpoints()
        assert exc_info.value.retryable is False

    def test_success_cached_until_discovery_ttl(self, clock):
        second = dataclasses.replace(METADATA, token_endpoint=f"{ISSUER}/rotated")
        client, discover = self._client([METADATA, second], discovery_ttl=3600)

        for _ in range(3):
            assert client._get_current_endpoints().token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 1

        clock.advance(3601)
        assert client._get_current_endpoints().token == f"{ISSUER}/rotated"
        assert discover.call_count == 2

    def test_concurrent_cold_callers_share_one_fetch(self, clock):
        release = threading.Event()
        started = threading.Barrier(5)

        def slow_discover(*args, **kwargs):
            release.wait(timeout=5)
            return METADATA

        client, discover = self._client(slow_discover)
        results: list = []

        def worker():
            started.wait(timeout=5)
            results.append(client._get_current_endpoints().token)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        started.wait(timeout=5)
        release.set()
        for t in threads:
            t.join(timeout=5)

        assert results == [DISCOVERED_TOKEN_ENDPOINT] * 4
        assert discover.call_count == 1

    def test_discovery_disabled_uses_convention_endpoint(self):
        client = Client(ISSUER, config=ClientConfig(enable_metadata_discovery=False))
        assert client._get_current_endpoints().token == CONVENTION_TOKEN_ENDPOINT


class TestAsyncClientDiscoveryCache:
    def _client(self, side_effect, **config) -> tuple[AsyncClient, AsyncMock]:
        client = AsyncClient(ISSUER, config=_config(**config))
        discover = AsyncMock(side_effect=side_effect)
        client.discover_server_metadata = discover
        return client, discover

    @pytest.mark.asyncio
    async def test_transient_failure_surfaces_typed_and_is_not_cached(self, clock):
        client, discover = self._client([_transient(), METADATA])

        with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
            await client._get_current_endpoints()
        assert exc_info.value.retryable is True

        endpoints = await client._get_current_endpoints()
        assert endpoints.token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 2

    @pytest.mark.asyncio
    async def test_failed_discovery_never_yields_convention_endpoint(self, clock):
        client, _ = self._client([_server_error()])
        with pytest.raises(AuthorizationServerDiscoveryError):
            await client._get_current_endpoints()
        assert client._discovered_endpoints is None
        assert client._initialized is False

    @pytest.mark.asyncio
    async def test_404_remembered_for_negative_ttl_then_rediscovered(self, clock):
        client, discover = self._client([_not_found(), METADATA], negative_ttl=60)

        for _ in range(3):
            with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
                await client._get_current_endpoints()
            assert exc_info.value.retryable is False
        assert discover.call_count == 1

        clock.advance(61)
        endpoints = await client._get_current_endpoints()
        assert endpoints.token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 2

    @pytest.mark.asyncio
    async def test_negative_ttl_zero_disables_negative_caching(self, clock):
        client, discover = self._client(
            [_not_found(), _not_found(), METADATA], negative_ttl=0
        )
        for _ in range(2):
            with pytest.raises(AuthorizationServerDiscoveryError):
                await client._get_current_endpoints()
        assert discover.call_count == 2
        endpoints = await client._get_current_endpoints()
        assert endpoints.token == DISCOVERED_TOKEN_ENDPOINT

    @pytest.mark.asyncio
    async def test_missing_token_endpoint_is_typed_and_remembered(self, clock):
        client, discover = self._client(
            [METADATA_WITHOUT_TOKEN_ENDPOINT, METADATA], negative_ttl=60
        )
        for _ in range(2):
            with pytest.raises(AuthorizationServerDiscoveryError) as exc_info:
                await client._get_current_endpoints()
            assert exc_info.value.retryable is False
        assert discover.call_count == 1

        clock.advance(61)
        endpoints = await client._get_current_endpoints()
        assert endpoints.token == DISCOVERED_TOKEN_ENDPOINT

    @pytest.mark.asyncio
    async def test_success_cached_until_discovery_ttl(self, clock):
        second = dataclasses.replace(METADATA, token_endpoint=f"{ISSUER}/rotated")
        client, discover = self._client([METADATA, second], discovery_ttl=3600)

        for _ in range(3):
            endpoints = await client._get_current_endpoints()
            assert endpoints.token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 1

        clock.advance(3601)
        endpoints = await client._get_current_endpoints()
        assert endpoints.token == f"{ISSUER}/rotated"
        assert discover.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_cold_callers_share_one_fetch(self, clock):
        release = asyncio.Event()

        async def slow_discover(*args, **kwargs):
            await release.wait()
            return METADATA

        client, discover = self._client(slow_discover)
        tasks = [
            asyncio.create_task(client._get_current_endpoints()) for _ in range(4)
        ]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*tasks)

        assert [e.token for e in results] == [DISCOVERED_TOKEN_ENDPOINT] * 4
        assert discover.call_count == 1

    @pytest.mark.asyncio
    async def test_one_callers_cancellation_does_not_poison_shared_fetch(self, clock):
        release = asyncio.Event()

        async def slow_discover(*args, **kwargs):
            await release.wait()
            return METADATA

        client, discover = self._client(slow_discover)
        cancelled = asyncio.create_task(client._get_current_endpoints())
        survivor = asyncio.create_task(client._get_current_endpoints())
        await asyncio.sleep(0)

        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        release.set()
        endpoints = await survivor
        assert endpoints.token == DISCOVERED_TOKEN_ENDPOINT
        assert discover.call_count == 1

    @pytest.mark.asyncio
    async def test_discovery_disabled_uses_convention_endpoint(self):
        client = AsyncClient(
            ISSUER, config=ClientConfig(enable_metadata_discovery=False)
        )
        endpoints = await client._get_current_endpoints()
        assert endpoints.token == CONVENTION_TOKEN_ENDPOINT


class TestClientConfigDiscoveryKnobs:
    def test_defaults(self):
        config = ClientConfig()
        assert config.discovery_ttl == 3600.0
        assert config.negative_ttl == 60.0

    def test_client_credentials_grant_propagates_discovery_error(self):
        client = Client(ISSUER, config=_config())
        client.discover_server_metadata = Mock(side_effect=_not_found())
        with pytest.raises(AuthorizationServerDiscoveryError):
            client.client_credentials_grant(client_id="id", client_secret="secret")
