"""Concrete HTTP transport implementations.

This module provides concrete implementations of the HTTP transport protocols
using httpx for both synchronous and asynchronous requests. These operate at the
byte level only.

Each transport owns one pooled httpx client, created lazily on first use and
reused for every subsequent request, so a keycardai client that lives across
many calls reuses TCP and TLS connections to the zone instead of paying a fresh
handshake per request.
"""

import asyncio
import threading
import zlib
from collections.abc import Mapping

import httpx

from ..exceptions import (
    MAX_RESPONSE_BODY_BYTES,
    NetworkError,
    ResponseBodyTooLargeError,
)
from ..types.models import ClientConfig
from ._wire import HttpRequest, HttpResponse


class _BoundedZlibDecoder:
    """Incrementally decompresses gzip or deflate bytes with a hard output cap.

    Uses ``zlib.decompressobj().decompress(chunk, max_length=...)`` so a
    compressed response (e.g. a gzip bomb) never allocates more than
    ``limit + 1`` decoded bytes before raising ``ResponseBodyTooLargeError``.
    """

    def __init__(self, encoding: str, limit: int, status_code: int, operation: str):
        self._encoding = encoding
        self._limit = limit
        self._status_code = status_code
        self._operation = operation
        self._buf = bytearray()
        self._first_chunk = True
        wbits = zlib.MAX_WBITS | 16 if encoding in ("gzip", "x-gzip") else zlib.MAX_WBITS
        self._decompressor = zlib.decompressobj(wbits)

    @property
    def eof(self) -> bool:
        return self._decompressor.eof

    def feed(self, chunk: bytes) -> None:
        if self._decompressor.eof:
            return
        data = chunk
        while data and not self._decompressor.eof:
            remaining = self._limit - len(self._buf)
            try:
                decoded = self._decompressor.decompress(data, remaining + 1)
            except zlib.error:
                if self._first_chunk and self._encoding == "deflate":
                    self._first_chunk = False
                    self._decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
                    decoded = self._decompressor.decompress(data, remaining + 1)
                else:
                    raise
            self._first_chunk = False
            if len(self._buf) + len(decoded) > self._limit:
                raise ResponseBodyTooLargeError(
                    status_code=self._status_code,
                    limit=self._limit,
                    operation=self._operation,
                )
            self._buf.extend(decoded)
            data = self._decompressor.unconsumed_tail

    def finish(self) -> bytes:
        remaining = self._limit - len(self._buf)
        tail = self._decompressor.flush(remaining + 1)
        if len(self._buf) + len(tail) > self._limit:
            raise ResponseBodyTooLargeError(
                status_code=self._status_code,
                limit=self._limit,
                operation=self._operation,
            )
        self._buf.extend(tail)
        return bytes(self._buf)


def _zlib_encoding(r: httpx.Response) -> str | None:
    enc = r.headers.get("content-encoding", "").strip().lower()
    if enc in ("gzip", "x-gzip", "deflate"):
        return enc
    return None


def _read_bounded_sync(
    r: httpx.Response,
    operation: str,
    limit: int = MAX_RESPONSE_BODY_BYTES,
) -> bytes:
    enc = _zlib_encoding(r)
    if enc is not None and hasattr(r, "iter_raw"):
        decoder = _BoundedZlibDecoder(enc, limit, r.status_code, operation)
        try:
            for raw_chunk in r.iter_raw():
                decoder.feed(raw_chunk)
                if decoder.eof:
                    break
            return decoder.finish()
        except (zlib.error, httpx.DecodingError) as e:
            cause = e if isinstance(e, httpx.DecodingError) else httpx.DecodingError(str(e))
            raise ResponseBodyDecodeError(
                status=r.status_code,
                headers=r.headers,
                cause=cause,
                operation=operation,
            ) from e

    buf = bytearray()
    try:
        for chunk in r.iter_bytes():
            if len(buf) + len(chunk) > limit:
                raise ResponseBodyTooLargeError(
                    status_code=r.status_code,
                    limit=limit,
                    operation=operation,
                )
            buf.extend(chunk)
    except httpx.DecodingError as e:
        raise ResponseBodyDecodeError(
            status=r.status_code,
            headers=r.headers,
            cause=e,
            operation=operation,
        ) from e
    return bytes(buf)


async def _read_bounded_async(
    r: httpx.Response,
    operation: str,
    limit: int = MAX_RESPONSE_BODY_BYTES,
) -> bytes:
    enc = _zlib_encoding(r)
    if enc is not None and hasattr(r, "aiter_raw"):
        decoder = _BoundedZlibDecoder(enc, limit, r.status_code, operation)
        try:
            async for raw_chunk in r.aiter_raw():
                decoder.feed(raw_chunk)
                if decoder.eof:
                    break
            return decoder.finish()
        except (zlib.error, httpx.DecodingError) as e:
            cause = e if isinstance(e, httpx.DecodingError) else httpx.DecodingError(str(e))
            raise ResponseBodyDecodeError(
                status=r.status_code,
                headers=r.headers,
                cause=cause,
                operation=operation,
            ) from e

    buf = bytearray()
    try:
        async for chunk in r.aiter_bytes():
            if len(buf) + len(chunk) > limit:
                raise ResponseBodyTooLargeError(
                    status_code=r.status_code,
                    limit=limit,
                    operation=operation,
                )
            buf.extend(chunk)
    except httpx.DecodingError as e:
        raise ResponseBodyDecodeError(
            status=r.status_code,
            headers=r.headers,
            cause=e,
            operation=operation,
        ) from e
    return bytes(buf)


class ResponseBodyDecodeError(NetworkError):
    """The response arrived, but its body did not decode.

    httpx raises ``httpx.DecodingError`` for a body that does not match its
    ``Content-Encoding``, such as a gzip body that is not gzip. ``status``
    and ``headers`` are the response's. It subclasses NetworkError, so
    other operations handle it as a network error. The device
    authorization grant handles it as a malformed response.
    """

    def __init__(
        self,
        *,
        status: int,
        headers: Mapping[str, str],
        cause: Exception,
        operation: str = "",
    ):
        super().__init__(cause=cause, operation=operation, retriable=False)
        self.status = status
        self.headers = dict(headers)


class HttpxTransport:
    """Synchronous HTTP transport using the httpx library.

    The underlying ``httpx.Client`` is created on the first request and reused
    until :meth:`close`. Creation is guarded by a lock so concurrent first
    requests from several threads share one client.
    """

    def __init__(self, *, config: ClientConfig):
        """Initialize the httpx sync transport.

        Args:
            config: Client configuration
        """
        self.config = config
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _create_client(self) -> httpx.Client:
        return httpx.Client(
            verify=self.config.verify_ssl,
            headers={"User-Agent": self.config.user_agent},
            timeout=self.config.timeout,
        )

    def _get_client(self) -> httpx.Client:
        client = self._client
        if client is not None and not client.is_closed:
            return client
        with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = self._create_client()
            return self._client

    def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
        """Execute a raw HTTP request using httpx.

        Args:
            req: The HTTP request to execute
            timeout: Optional timeout in seconds; defaults to ``config.timeout``

        Returns:
            The HTTP response

        Raises:
            ResponseBodyTooLargeError: The decoded body exceeded 1 MiB.
            ResponseBodyDecodeError: The body did not decode.
            NetworkError: For network-level failures
        """
        operation = f"{req.method} {req.url}"
        try:
            client = self._get_client()
            with client.stream(
                method=req.method,
                url=req.url,
                headers=req.headers,
                content=req.body,  # httpx uses 'content' for raw bytes
                timeout=timeout if timeout is not None else self.config.timeout,
            ) as r:
                body = _read_bounded_sync(r, operation)
                return HttpResponse(
                    status=r.status_code, headers=dict(r.headers), body=body
                )
        except (ResponseBodyDecodeError, ResponseBodyTooLargeError):
            raise
        except httpx.HTTPError as e:
            raise NetworkError(cause=e, operation=operation, retriable=False) from e

    def close(self) -> None:
        """Close the pooled httpx client, if one was created.

        The next request after ``close()`` creates a fresh client. Calling
        ``close()`` is optional: an unclosed httpx client releases its
        connections when it is garbage collected.
        """
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()


class HttpxAsyncTransport:
    """Asynchronous HTTP transport using the httpx library.

    The underlying ``httpx.AsyncClient`` is created on the first request and
    reused until :meth:`aclose`. An ``httpx.AsyncClient`` is bound to the event
    loop it was created on, so the transport remembers that loop and builds a
    fresh client if a request arrives on a different one.
    """

    def __init__(self, *, config: ClientConfig):
        """Initialize the httpx async transport.

        Args:
            config: Client configuration
        """
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _create_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self.config.verify_ssl,
            headers={"User-Agent": self.config.user_agent},
            timeout=self.config.timeout,
        )

    def _get_client(self) -> httpx.AsyncClient:
        # No await between the check and the assignment, so two coroutines on
        # the same loop cannot both observe a missing client and double-create.
        loop = asyncio.get_running_loop()
        client = self._client
        if client is not None and not client.is_closed and self._loop is loop:
            return client
        # A client created on another loop cannot be reused here, and its
        # aclose() cannot be awaited from this loop either. Dropping the
        # reference leaves it to garbage collection, which httpx handles.
        self._client = self._create_client()
        self._loop = loop
        return self._client

    async def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
        """Execute a raw HTTP request using httpx.

        Args:
            req: The HTTP request to execute
            timeout: Optional timeout in seconds; defaults to ``config.timeout``

        Returns:
            The HTTP response

        Raises:
            ResponseBodyTooLargeError: The decoded body exceeded 1 MiB.
            ResponseBodyDecodeError: The body did not decode.
            NetworkError: For network-level failures
        """
        operation = f"{req.method} {req.url}"
        try:
            client = self._get_client()
            request = client.build_request(
                method=req.method,
                url=req.url,
                headers=req.headers,
                content=req.body,
                timeout=timeout if timeout is not None else self.config.timeout,
            )
            r = await client.send(request, stream=True)
            try:
                body = await _read_bounded_async(r, operation)
            finally:
                await r.aclose()
            return HttpResponse(status=r.status_code, headers=dict(r.headers), body=body)
        except (ResponseBodyDecodeError, ResponseBodyTooLargeError):
            raise
        except httpx.HTTPError as e:
            raise NetworkError(cause=e, operation=operation, retriable=False) from e

    async def aclose(self) -> None:
        """Close the pooled httpx client, if one was created on this loop.

        The next request after ``aclose()`` creates a fresh client. Calling
        ``aclose()`` is optional: an unclosed httpx client releases its
        connections when it is garbage collected. A client owned by a
        different event loop is dropped without awaiting its close, since
        that cannot be done safely from a foreign loop.
        """
        client, loop = self._client, self._loop
        self._client = None
        self._loop = None
        if client is None or client.is_closed:
            return
        if loop is asyncio.get_running_loop():
            await client.aclose()
