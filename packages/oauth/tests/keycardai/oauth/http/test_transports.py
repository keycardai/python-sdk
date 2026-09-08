"""Tests for the pooled httpx transports.

No network: the httpx clients are patched at their creation seam with fakes
that record calls and expose ``is_closed`` the way httpx does.
"""

import asyncio

import httpx
import pytest

from keycardai.oauth.exceptions import NetworkError
from keycardai.oauth.http._transports import HttpxAsyncTransport, HttpxTransport
from keycardai.oauth.http._wire import HttpRequest
from keycardai.oauth.types.models import ClientConfig

REQ = HttpRequest(method="POST", url="https://zone.example/token", headers={}, body=b"x")


class FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b"{}"


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.calls: list[dict] = []
        self.is_closed = False
        self.fail_with: Exception | None = None

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        return FakeResponse()

    def close(self):
        self.is_closed = True


class FakeAsyncClient(FakeClient):
    async def request(self, **kwargs):
        return FakeClient.request(self, **kwargs)

    async def aclose(self):
        self.is_closed = True


@pytest.fixture
def created(monkeypatch):
    """Patch both httpx client constructors and collect every instance built."""
    instances: list[FakeClient] = []

    def make_sync(*args, **kwargs):
        c = FakeClient(*args, **kwargs)
        instances.append(c)
        return c

    def make_async(*args, **kwargs):
        c = FakeAsyncClient(*args, **kwargs)
        instances.append(c)
        return c

    monkeypatch.setattr(httpx, "Client", make_sync)
    monkeypatch.setattr(httpx, "AsyncClient", make_async)
    return instances


class TestHttpxTransport:
    def test_two_requests_reuse_one_client(self, created):
        t = HttpxTransport(config=ClientConfig())
        t.request_raw(REQ)
        t.request_raw(REQ)
        assert len(created) == 1
        assert t._client is created[0]
        assert len(created[0].calls) == 2

    def test_client_configured_from_client_config(self, created):
        cfg = ClientConfig(verify_ssl=False, timeout=7.5, user_agent="ua/1")
        HttpxTransport(config=cfg).request_raw(REQ)
        assert created[0].init_kwargs == {
            "verify": False,
            "headers": {"User-Agent": "ua/1"},
            "timeout": 7.5,
        }

    def test_timeout_override_and_fallback(self, created):
        t = HttpxTransport(config=ClientConfig(timeout=30.0))
        t.request_raw(REQ, timeout=2.5)
        t.request_raw(REQ)
        assert created[0].calls[0]["timeout"] == 2.5
        assert created[0].calls[1]["timeout"] == 30.0

    def test_close_closes_client_and_next_use_creates_fresh(self, created):
        t = HttpxTransport(config=ClientConfig())
        t.request_raw(REQ)
        t.close()
        assert created[0].is_closed is True
        t.request_raw(REQ)
        assert len(created) == 2
        assert created[1] is not created[0]
        assert created[1].is_closed is False

    def test_close_without_use_is_noop(self, created):
        HttpxTransport(config=ClientConfig()).close()
        assert created == []

    def test_network_error_wrapping_unchanged(self, created):
        t = HttpxTransport(config=ClientConfig())
        t.request_raw(REQ)
        created[0].fail_with = httpx.ConnectError("refused")
        with pytest.raises(NetworkError) as exc_info:
            t.request_raw(REQ)
        err = exc_info.value
        assert err.operation == "POST https://zone.example/token"
        assert isinstance(err.cause, httpx.ConnectError)
        assert err.retriable is False
        assert err.retryable is True

    def test_concurrent_first_use_creates_one_client(self, monkeypatch, created):
        import threading

        t = HttpxTransport(config=ClientConfig())
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            t.request_raw(REQ)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert len(created) == 1
        assert len(created[0].calls) == 8


class TestHttpxAsyncTransport:
    @pytest.mark.asyncio
    async def test_two_requests_reuse_one_client(self, created):
        t = HttpxAsyncTransport(config=ClientConfig())
        await t.request_raw(REQ)
        await t.request_raw(REQ)
        assert len(created) == 1
        assert t._client is created[0]
        assert len(created[0].calls) == 2

    @pytest.mark.asyncio
    async def test_client_configured_from_client_config(self, created):
        cfg = ClientConfig(verify_ssl=False, timeout=7.5, user_agent="ua/1")
        await HttpxAsyncTransport(config=cfg).request_raw(REQ)
        assert created[0].init_kwargs == {
            "verify": False,
            "headers": {"User-Agent": "ua/1"},
            "timeout": 7.5,
        }

    @pytest.mark.asyncio
    async def test_timeout_override_and_fallback(self, created):
        t = HttpxAsyncTransport(config=ClientConfig(timeout=30.0))
        await t.request_raw(REQ, timeout=2.5)
        await t.request_raw(REQ)
        assert created[0].calls[0]["timeout"] == 2.5
        assert created[0].calls[1]["timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_aclose_closes_client_and_next_use_creates_fresh(self, created):
        t = HttpxAsyncTransport(config=ClientConfig())
        await t.request_raw(REQ)
        await t.aclose()
        assert created[0].is_closed is True
        await t.request_raw(REQ)
        assert len(created) == 2
        assert created[1] is not created[0]

    @pytest.mark.asyncio
    async def test_aclose_without_use_is_noop(self, created):
        await HttpxAsyncTransport(config=ClientConfig()).aclose()
        assert created == []

    @pytest.mark.asyncio
    async def test_network_error_wrapping_unchanged(self, created):
        t = HttpxAsyncTransport(config=ClientConfig())
        await t.request_raw(REQ)
        created[0].fail_with = httpx.ReadTimeout("slow")
        with pytest.raises(NetworkError) as exc_info:
            await t.request_raw(REQ)
        err = exc_info.value
        assert err.operation == "POST https://zone.example/token"
        assert isinstance(err.cause, httpx.ReadTimeout)
        assert err.retriable is False
        assert err.retryable is True

    @pytest.mark.asyncio
    async def test_same_loop_concurrent_first_use_creates_one_client(self, created):
        t = HttpxAsyncTransport(config=ClientConfig())
        await asyncio.gather(*(t.request_raw(REQ) for _ in range(5)))
        assert len(created) == 1
        assert len(created[0].calls) == 5

    def test_new_event_loop_gets_new_client(self, created):
        t = HttpxAsyncTransport(config=ClientConfig())

        async def one():
            await t.request_raw(REQ)
            return t._client

        first = asyncio.run(one())
        second = asyncio.run(one())
        assert first is created[0]
        assert second is created[1]
        assert first is not second
        assert len(first.calls) == 1
        assert len(second.calls) == 1

    def test_aclose_on_foreign_loop_drops_without_awaiting_close(self, created):
        t = HttpxAsyncTransport(config=ClientConfig())

        async def use():
            await t.request_raw(REQ)

        asyncio.run(use())
        asyncio.run(t.aclose())
        assert created[0].is_closed is False
        assert t._client is None
