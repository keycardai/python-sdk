"""Decoded response body size limit (1 MiB) tests.

Implements unit test rows 1, 1a, 1b, and 1c from specs/base.md (spec-version 11)
and verifies the cap across sync and async transports, discovery, JWKS, token,
device authorization, dynamic client registration, and UserInfo operations.
"""

from __future__ import annotations

import gzip
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerDiscoveryError,
    AuthorizationServerMetadata,
    Client,
    ClientConfig,
    DeviceAuthorization,
    DeviceAuthorizationError,
    DevicePollError,
    Endpoints,
    JWKSFetchError,
    OAuthProtocolError,
    ResponseBodyTooLargeError,
)
from keycardai.oauth.clock import Clock
from keycardai.oauth.exceptions import MAX_RESPONSE_BODY_BYTES
from keycardai.oauth.http._transports import HttpxAsyncTransport, HttpxTransport
from keycardai.oauth.http._wire import HttpRequest
from keycardai.oauth.server.exceptions import JWKSDiscoveryError
from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types.models import ClientRegistrationRequest
from keycardai.oauth.utils.jwt import fetch_jwks


class _FakeClock(Clock):
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class _LocalTestServer:
    """In-process HTTP server for testing real httpx transport streaming and gzip."""

    def __init__(self, handler_fn):
        self._handler_fn = handler_fn
        self.request_count = 0

        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.request_count += 1
                outer._handler_fn(self)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    self.rfile.read(length)
                outer.request_count += 1
                outer._handler_fn(self)

            def log_message(self, format, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _oversized_json_bytes(extra: int = 1) -> bytes:
    prefix = b'{"access_token":"'
    suffix = b'","token_type":"Bearer"}'
    pad_len = MAX_RESPONSE_BODY_BYTES - len(prefix) - len(suffix) + extra
    return prefix + (b"a" * pad_len) + suffix


def _exact_limit_json_bytes() -> bytes:
    return _oversized_json_bytes(extra=0)


@pytest.mark.asyncio
async def test_row_1_gzip_token_response_over_1mib_stops_reading_and_raises():
    # Compressed size is tiny (~2 KiB), inflated size is > 1 MiB.
    inflated = _oversized_json_bytes(extra=512 * 1024)
    compressed = gzip.compress(inflated)
    assert len(compressed) < 10_000
    assert len(inflated) > MAX_RESPONSE_BODY_BYTES

    def handle(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Encoding", "gzip")
        req.send_header("Content-Length", str(len(compressed)))
        req.end_headers()
        req.wfile.write(compressed)

    with _LocalTestServer(handle) as server:
        config = ClientConfig(
            enable_metadata_discovery=False, auto_register_client=False
        )
        endpoints = Endpoints(token=f"{server.url}/oauth/token")

        async with AsyncClient(
            server.url, config=config, endpoints=endpoints
        ) as async_client:
            with pytest.raises(OAuthProtocolError) as exc:
                await async_client.exchange_token(
                    subject_token="tok",
                    subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                )
            assert isinstance(exc.value, ResponseBodyTooLargeError)
            assert exc.value.error == "invalid_response"
            assert exc.value.status_code == 200
            assert exc.value.retryable is True

        with Client(server.url, config=config, endpoints=endpoints) as sync_client:
            with pytest.raises(OAuthProtocolError) as exc:
                sync_client.exchange_token(
                    subject_token="tok",
                    subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                )
            assert isinstance(exc.value, ResponseBodyTooLargeError)
            assert exc.value.error == "invalid_response"
            assert exc.value.status_code == 200
            assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_transports_advertise_only_gzip_and_deflate():
    seen: list[str | None] = []
    body = b'{"access_token":"t","token_type":"Bearer"}'

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(req.headers.get("Accept-Encoding"))
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    with _LocalTestServer(handle) as server:
        request = HttpRequest(method="GET", url=f"{server.url}/token", headers={})
        sync_transport = HttpxTransport(config=ClientConfig())
        async_transport = HttpxAsyncTransport(config=ClientConfig())
        try:
            sync_transport.request_raw(request)
            await async_transport.request_raw(request)
        finally:
            sync_transport.close()
            await async_transport.aclose()

    assert len(seen) == 2
    for header in seen:
        assert header is not None
        encodings = {part.strip().lower() for part in header.split(",")}
        assert encodings == {"gzip", "deflate"}


@pytest.mark.asyncio
async def test_row_1_streaming_stops_before_reading_remaining_chunks():
    chunks_yielded = 0

    class _CountingAsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal chunks_yielded
            for _ in range(32):
                chunks_yielded += 1
                yield b"x" * (64 * 1024)

    transport = HttpxAsyncTransport(config=ClientConfig())
    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=_CountingAsyncStream())
        )
    )
    transport._client = mock_client
    transport._loop = None

    # Patch _get_client so the mock client is used directly on the current loop.
    transport._get_client = lambda: mock_client  # type: ignore[method-assign]
    try:
        with pytest.raises(ResponseBodyTooLargeError) as exc:
            await transport.request_raw(
                HttpRequest(method="POST", url="https://zone.example/token", headers={})
            )
        assert exc.value.error == "invalid_response"
        assert exc.value.status_code == 200
        assert exc.value.retryable is True
        # 16 chunks of 64 KiB == 1 MiB; the 17th chunk trips the limit, well before 32.
        assert chunks_yielded == 17
    finally:
        await mock_client.aclose()


def test_exact_1mib_body_succeeds_and_1mib_plus_1_fails():
    exact = _exact_limit_json_bytes()
    assert len(exact) == MAX_RESPONSE_BODY_BYTES
    over = _oversized_json_bytes(extra=1)
    assert len(over) == MAX_RESPONSE_BODY_BYTES + 1

    payload = exact

    def handle(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(payload)))
        req.end_headers()
        req.wfile.write(payload)

    with _LocalTestServer(handle) as server:
        transport = HttpxTransport(config=ClientConfig())
        try:
            res = transport.request_raw(
                HttpRequest(method="GET", url=f"{server.url}/test", headers={})
            )
            assert res.status == 200
            assert len(res.body) == MAX_RESPONSE_BODY_BYTES

            payload = over
            with pytest.raises(ResponseBodyTooLargeError) as exc:
                transport.request_raw(
                    HttpRequest(method="GET", url=f"{server.url}/test", headers={})
                )
            assert exc.value.status_code == 200
            assert exc.value.error == "invalid_response"
            assert exc.value.retryable is True
        finally:
            transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_retryable,expect_cached",
    [
        (200, True, False),
        (429, True, False),
        (503, True, False),
        (400, False, True),
    ],
)
async def test_row_1a_cached_discovery_oversized_body(
    status: int, expected_retryable: bool, expect_cached: bool
):
    oversized = b"x" * (MAX_RESPONSE_BODY_BYTES + 1)
    attempts = 0
    server_url_holder: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            req.send_response(status)
            req.send_header("Content-Type", "application/json")
            req.send_header("Content-Length", str(len(oversized)))
            req.end_headers()
            try:
                req.wfile.write(oversized)
            except BrokenPipeError:
                pass
            return
        body = json.dumps(
            {
                "issuer": server_url_holder[0],
                "token_endpoint": f"{server_url_holder[0]}/oauth/token",
                "jwks_uri": f"{server_url_holder[0]}/jwks.json",
            }
        ).encode()
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    with _LocalTestServer(handle) as server:
        server_url_holder.append(server.url)
        client = AsyncClient(
            server.url,
            config=ClientConfig(
                enable_metadata_discovery=True,
                auto_register_client=False,
                negative_ttl=60.0,
            ),
        )
        try:
            with pytest.raises(AuthorizationServerDiscoveryError) as exc:
                await client._resolve_metadata()
            assert exc.value.status_code == status
            assert exc.value.retryable is expected_retryable
            assert isinstance(exc.value.cause, ResponseBodyTooLargeError)

            if expect_cached:
                with pytest.raises(AuthorizationServerDiscoveryError) as exc2:
                    await client._resolve_metadata()
                assert exc2.value.status_code == status
                assert exc2.value.retryable is False
                assert attempts == 1
            else:
                meta = await client._resolve_metadata()
                assert meta.issuer == server.url
                assert attempts == 2
        finally:
            await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_retryable,expect_cached",
    [
        (200, True, False),
        (429, True, False),
        (503, True, False),
        (400, False, True),
    ],
)
async def test_row_1a_jwks_discovery_and_fetch_oversized_body(
    status: int, expected_retryable: bool, expect_cached: bool
):
    oversized = b"x" * (MAX_RESPONSE_BODY_BYTES + 1)
    discovery_attempts = 0
    jwks_attempts = 0
    server_url_holder: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        nonlocal discovery_attempts, jwks_attempts
        if req.path == "/.well-known/oauth-authorization-server":
            discovery_attempts += 1
            if discovery_attempts == 1:
                req.send_response(status)
                req.send_header("Content-Length", str(len(oversized)))
                req.end_headers()
                try:
                    req.wfile.write(oversized)
                except BrokenPipeError:
                    pass
                return
            body = json.dumps(
                {
                    "issuer": server_url_holder[0],
                    "jwks_uri": f"{server_url_holder[0]}/jwks.json",
                }
            ).encode()
            req.send_response(200)
            req.send_header("Content-Type", "application/json")
            req.send_header("Content-Length", str(len(body)))
            req.end_headers()
            req.wfile.write(body)
            return

        jwks_attempts += 1
        if jwks_attempts == 1:
            req.send_response(status)
            req.send_header("Content-Length", str(len(oversized)))
            req.end_headers()
            try:
                req.wfile.write(oversized)
            except BrokenPipeError:
                pass
            return
        body = json.dumps({"keys": []}).encode()
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    with _LocalTestServer(handle) as server:
        server_url_holder.append(server.url)
        verifier = TokenVerifier(issuer=server.url, negative_ttl=60.0)

        with pytest.raises(JWKSDiscoveryError) as disc_exc:
            verifier._discover_jwks_uri(issuer=server.url)
        assert disc_exc.value.status_code == status
        assert disc_exc.value.retryable is expected_retryable

        if expect_cached:
            with pytest.raises(JWKSDiscoveryError) as disc_exc2:
                verifier._discover_jwks_uri(issuer=server.url)
            assert disc_exc2.value.status_code == status
            assert discovery_attempts == 1
        else:
            jwks_uri = verifier._discover_jwks_uri(issuer=server.url)
            assert jwks_uri == f"{server.url}/jwks.json"
            assert discovery_attempts == 2

        with pytest.raises(JWKSFetchError) as fetch_exc:
            await fetch_jwks(f"{server.url}/jwks.json")
        assert fetch_exc.value.status_code == status
        assert fetch_exc.value.retryable is expected_retryable

        keys = await fetch_jwks(f"{server.url}/jwks.json")
        assert keys == []
        assert jwks_attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_row_1b_device_poll_oversized_429_and_5xx(status: int):
    oversized = b"x" * (MAX_RESPONSE_BODY_BYTES + 1)
    poll_count = 0

    def handle(req: BaseHTTPRequestHandler) -> None:
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            # First request: poll_once gets oversized directly.
            req.send_response(status)
            req.send_header("Content-Length", str(len(oversized)))
            req.end_headers()
            try:
                req.wfile.write(oversized)
            except BrokenPipeError:
                pass
            return
        if poll_count == 2:
            body = json.dumps({"error": "slow_down"}).encode()
            req.send_response(400)
            req.send_header("Content-Type", "application/json")
            req.send_header("Content-Length", str(len(body)))
            req.end_headers()
            req.wfile.write(body)
            return
        req.send_response(status)
        req.send_header("Content-Length", str(len(oversized)))
        req.end_headers()
        try:
            req.wfile.write(oversized)
        except BrokenPipeError:
            pass

    with _LocalTestServer(handle) as server:
        clock = _FakeClock()
        auth = DeviceAuthorization(
            device_code="dev-code",
            user_code="ABCD-1234",
            verification_uri=f"{server.url}/verify",
            expires_in=600,
            interval=5,
            expires_at=clock.now() + timedelta(seconds=600),
        )
        meta = AuthorizationServerMetadata(
            issuer=server.url,
            token_endpoint=f"{server.url}/oauth/token",
            device_authorization_endpoint=f"{server.url}/oauth/device",
        )
        async with AsyncClient(
            server.url,
            clock=clock,
            config=ClientConfig(
                enable_metadata_discovery=False, auto_register_client=False
            ),
        ) as client:
            with pytest.raises(DeviceAuthorizationError) as once_exc:
                await client.poll_device_token(auth, client_id="cid", metadata=meta)
            assert once_exc.value.error == "invalid_response"
            assert once_exc.value.status_code == status
            assert once_exc.value.retryable is True

            with pytest.raises(DevicePollError) as wait_exc:
                await client.wait_for_device_token(auth, client_id="cid", metadata=meta)
            assert wait_exc.value.retryable is True
            assert wait_exc.value.interval == 10
            assert isinstance(wait_exc.value.cause, DeviceAuthorizationError)
            assert wait_exc.value.cause.error == "invalid_response"
            assert wait_exc.value.cause.status_code == status
            assert wait_exc.value.cause.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 400])
async def test_row_36_wait_for_device_token_oversized_body_fails_directly(status: int):
    oversized = b"x" * (MAX_RESPONSE_BODY_BYTES + 1)

    def handle(req: BaseHTTPRequestHandler) -> None:
        req.send_response(status)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(oversized)))
        req.end_headers()
        try:
            req.wfile.write(oversized)
        except BrokenPipeError:
            pass

    with _LocalTestServer(handle) as server:
        clock = _FakeClock()
        auth = DeviceAuthorization(
            device_code="dev-code",
            user_code="ABCD-1234",
            verification_uri=f"{server.url}/verify",
            expires_in=600,
            interval=5,
            expires_at=clock.now() + timedelta(seconds=600),
        )
        meta = AuthorizationServerMetadata(
            issuer=server.url,
            token_endpoint=f"{server.url}/oauth/token",
            device_authorization_endpoint=f"{server.url}/oauth/device",
        )
        async with AsyncClient(
            server.url,
            clock=clock,
            config=ClientConfig(
                enable_metadata_discovery=False, auto_register_client=False
            ),
        ) as client:
            # DeviceAuthorizationError, not DevicePollError: the loop ends
            # with the error itself and carries no interval to resume with.
            with pytest.raises(DeviceAuthorizationError) as exc:
                await client.wait_for_device_token(auth, client_id="cid", metadata=meta)

        assert exc.value.error == "invalid_response"
        assert exc.value.status_code == status
        assert not hasattr(exc.value, "interval")
        assert server.request_count == 1
        assert clock.sleeps == [5]


@pytest.mark.asyncio
async def test_row_1c_token_endpoint_400_oversized_body_not_retryable():
    # Even if the body starts with a valid OAuth error prefix, reading stops
    # before buffering or parsing any OAuth error body.
    prefix = b'{"error":"invalid_request","error_description":"'
    oversized = prefix + (b"z" * (MAX_RESPONSE_BODY_BYTES + 100)) + b'"}'

    def handle(req: BaseHTTPRequestHandler) -> None:
        req.send_response(400)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(oversized)))
        req.end_headers()
        try:
            req.wfile.write(oversized)
        except BrokenPipeError:
            pass

    with _LocalTestServer(handle) as server:
        config = ClientConfig(
            enable_metadata_discovery=False, auto_register_client=False
        )
        endpoints = Endpoints(token=f"{server.url}/oauth/token")
        async with AsyncClient(
            server.url, config=config, endpoints=endpoints
        ) as client:
            with pytest.raises(OAuthProtocolError) as exc:
                await client.exchange_token(
                    subject_token="tok",
                    subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                )
            assert exc.value.error == "invalid_response"
            assert exc.value.status_code == 400
            assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_dcr_and_userinfo_oversized_body():
    oversized = b"x" * (MAX_RESPONSE_BODY_BYTES + 1)

    def handle(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(oversized)))
        req.end_headers()
        try:
            req.wfile.write(oversized)
        except BrokenPipeError:
            pass

    with _LocalTestServer(handle) as server:
        config = ClientConfig(
            enable_metadata_discovery=False, auto_register_client=False
        )
        endpoints = Endpoints(register=f"{server.url}/oauth/register")
        meta = AuthorizationServerMetadata(
            issuer=server.url,
            userinfo_endpoint=f"{server.url}/oauth/userinfo",
        )
        async with AsyncClient(
            server.url, config=config, endpoints=endpoints
        ) as client:
            with pytest.raises(OAuthProtocolError) as dcr_exc:
                await client.register_client(
                    ClientRegistrationRequest(client_name="test")
                )
            assert dcr_exc.value.error == "invalid_response"
            assert dcr_exc.value.status_code == 200
            assert dcr_exc.value.retryable is True

            with pytest.raises(OAuthProtocolError) as ui_exc:
                await client.userinfo("access-tok", metadata=meta)
            assert ui_exc.value.error == "invalid_response"
            assert ui_exc.value.status_code == 200
            assert ui_exc.value.retryable is True
