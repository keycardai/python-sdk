"""A2A delegation v3 conformance tests."""

import gzip
import json
from inspect import isawaitable

import httpx
import pytest
from keycardai.oauth.exceptions import ResponseBodyTooLargeError
from keycardai.oauth.http._transports import HttpxAsyncTransport, HttpxTransport

from keycardai.a2a.client import ServiceDiscovery
from keycardai.a2a.server import DelegationClient, DelegationClientSync

_PEER = "https://peer.example.test"
_MIB = 1024 * 1024


class _Chunks(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes, exhaust: bool = True):
        self.chunks = chunks
        self.exhaust = exhaust

    def __iter__(self):
        yield from self.chunks
        assert self.exhaust, "the reader consumed data after overflow"

    async def __aiter__(self):
        for chunk in self:
            yield chunk


@pytest.fixture(
    params=[
        (ServiceDiscovery, False),
        (DelegationClient, False),
        (DelegationClient, True),
        (DelegationClientSync, False),
        (DelegationClientSync, True),
    ],
    ids=["standalone", "async-discovery", "async-invocation", "sync-discovery", "sync-invocation"],
)
def peer(request, service_config, monkeypatch):
    client_type, invocation = request.param
    document = (
        {"jsonrpc": "2.0", "result": {"message": {"parts": [{"text": "ok"}]}}} if invocation else {"name": "Peer"}
    )
    expected = {"result": "ok"} if invocation else document

    async def call(make_response, **kwargs):
        client = client_type(service_config, **kwargs)
        transport = httpx.MockTransport(lambda _: make_response())
        monkeypatch.setattr(client.http_client, "_transport_for_url", lambda _: transport)
        try:
            pending = (
                client.invoke_service(_PEER, "hello", token="token") if invocation else client.discover_service(_PEER)
            )
            return await pending if isawaitable(pending) else pending
        finally:
            closed = client.close()
            if isawaitable(closed):
                await closed

    return call, json.dumps(document).encode(), expected, invocation


@pytest.mark.asyncio
async def test_row_9_default_accepts_more_than_one_mib(peer):
    call, body, expected, _ = peer
    assert await call(lambda: httpx.Response(200, stream=_Chunks(body, b" " * _MIB))) == expected


@pytest.mark.asyncio
async def test_row_10_stops_reading_on_decoded_overflow(peer):
    call, body, _, _ = peer
    encoded = gzip.compress(body + b" " * 512)
    assert len(encoded) < 256
    with pytest.raises(ResponseBodyTooLargeError) as caught:
        await call(
            lambda: httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=_Chunks(encoded, exhaust=False),
            ),
            max_peer_response_bytes=256,
        )
    assert caught.value.error == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 429, 503])
async def test_row_10a_preserves_status_and_classifies_retryability(peer, status):
    call, body, _, invocation = peer
    with pytest.raises(ResponseBodyTooLargeError) as caught:
        await call(
            lambda: httpx.Response(status, stream=_Chunks(body)),
            max_peer_response_bytes=len(body) - 1,
        )
    assert caught.value.error == "invalid_response"
    assert caught.value.status_code == status
    assert caught.value.retryable is not (invocation and status == 200)


@pytest.mark.asyncio
async def test_row_11_configures_peer_limit(peer):
    call, body, expected, _ = peer

    def small():
        return httpx.Response(200, stream=_Chunks(body))

    assert await call(small) == expected
    with pytest.raises(ResponseBodyTooLargeError, match="decoded size limit"):
        await call(small, max_peer_response_bytes=len(body) - 1)

    padding = b" " * _MIB

    def large():
        return httpx.Response(200, stream=_Chunks(body, *([padding] * 64)))

    with pytest.raises(ResponseBodyTooLargeError, match="decoded size limit"):
        await call(large)
    assert await call(large, max_peer_response_bytes=64 * _MIB + len(body)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [128, 65 * _MIB])
@pytest.mark.parametrize("client_type", [DelegationClient, DelegationClientSync])
async def test_row_11_token_exchange_keeps_one_mib_cap(client_type, limit, service_config, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/.well-known/"):
            issuer = str(request.url.copy_with(path="/")).rstrip("/")
            return httpx.Response(
                200, json={"issuer": issuer, "token_endpoint": f"{issuer}/oauth/token"}
            )
        return httpx.Response(200, stream=_Chunks(b" " * _MIB, b" "))

    monkeypatch.setattr(
        HttpxAsyncTransport,
        "_create_client",
        lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        HttpxTransport,
        "_create_client",
        lambda _: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    if client_type is DelegationClient:
        async with client_type(service_config, max_peer_response_bytes=limit) as client:
            with pytest.raises(ResponseBodyTooLargeError) as caught:
                await client.get_delegation_token(_PEER, subject_token="caller")
    else:
        with client_type(service_config, max_peer_response_bytes=limit) as client:
            with pytest.raises(ResponseBodyTooLargeError) as caught:
                client.get_delegation_token(_PEER, subject_token="caller")
    assert caught.value.limit == _MIB
    assert seen[-1] == "/oauth/token"
