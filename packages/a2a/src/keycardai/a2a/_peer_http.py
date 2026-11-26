"""Bounded default HTTP clients for peer replies."""

import zlib
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from keycardai.oauth.exceptions import ResponseBodyTooLargeError
from keycardai.oauth.http._transports import (
    ResponseBodyDecodeError,
    _BoundedZlibDecoder,
    _read_bounded_async,
    _read_bounded_sync,
)

DEFAULT_PEER_RESPONSE_BYTES = 64 * 1024 * 1024
_ACCEPT_ENCODING = {"Accept-Encoding": "gzip, deflate"}


class _InvocationResponseTooLargeError(ResponseBodyTooLargeError):
    @property
    def retryable(self) -> bool:
        return False


class _PeerZlibDecoder(_BoundedZlibDecoder):
    def finish(self) -> bytes:
        remaining = self._limit - len(self._buf)
        tail = self._decompressor.decompress(b"", remaining + 1)
        if len(tail) > remaining:
            raise ResponseBodyTooLargeError(
                status_code=self._status_code,
                limit=self._limit,
                operation=self._operation,
            )
        self._buf.extend(tail)
        return bytes(self._buf)


def _validate_limit(limit: int) -> int:
    if type(limit) is not int or limit <= 0:
        raise ValueError("max_peer_response_bytes must be a positive integer")
    return limit


def _decoded_response(response: httpx.Response, content: bytes) -> httpx.Response:
    headers = response.headers.copy()
    headers.pop("content-encoding", None)
    if "content-length" in headers:
        headers["content-length"] = str(len(content))
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=content,
        request=response.request,
        history=response.history,
        extensions=response.extensions,
    )


@contextmanager
def _peer_decoder(
    response: httpx.Response, operation: str, limit: int
) -> Iterator[_PeerZlibDecoder | None]:
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        yield None
        return
    if encoding not in ("gzip", "x-gzip", "deflate"):
        raise httpx.DecodingError(f"Unsupported peer content encoding: {encoding}", request=response.request)
    try:
        yield _PeerZlibDecoder(encoding, limit, response.status_code, operation)
    except (zlib.error, httpx.DecodingError) as error:
        cause = error if isinstance(error, httpx.DecodingError) else httpx.DecodingError(str(error))
        raise ResponseBodyDecodeError(
            status=response.status_code,
            headers=response.headers,
            cause=cause,
            operation=operation,
        ) from error


class PeerClient(httpx.Client):
    def __init__(self, limit: int):
        self._limit = _validate_limit(limit)
        super().__init__(timeout=30.0, headers=_ACCEPT_ENCODING)

    def send(self, request: httpx.Request, *, stream: bool = False, **kwargs) -> httpx.Response:
        response = super().send(request, stream=True, **kwargs)
        operation = "a2a_invocation" if request.method == "POST" else "a2a_discovery"
        try:
            with _peer_decoder(response, operation, self._limit) as decoder:
                if decoder is None:
                    content = _read_bounded_sync(response, operation, self._limit)
                else:
                    for chunk in response.iter_raw():
                        decoder.feed(chunk)
                    content = decoder.finish()
            return _decoded_response(response, content)
        except ResponseBodyTooLargeError as error:
            if request.method == "POST" and response.is_success:
                raise _InvocationResponseTooLargeError(
                    status_code=response.status_code,
                    limit=self._limit,
                    operation=operation,
                ) from error
            raise
        finally:
            response.close()


class AsyncPeerClient(httpx.AsyncClient):
    def __init__(self, limit: int):
        self._limit = _validate_limit(limit)
        super().__init__(timeout=30.0, headers=_ACCEPT_ENCODING)

    async def send(self, request: httpx.Request, *, stream: bool = False, **kwargs) -> httpx.Response:
        response = await super().send(request, stream=True, **kwargs)
        operation = "a2a_invocation" if request.method == "POST" else "a2a_discovery"
        try:
            with _peer_decoder(response, operation, self._limit) as decoder:
                if decoder is None:
                    content = await _read_bounded_async(response, operation, self._limit)
                else:
                    async for chunk in response.aiter_raw():
                        decoder.feed(chunk)
                    content = decoder.finish()
            return _decoded_response(response, content)
        except ResponseBodyTooLargeError as error:
            if request.method == "POST" and response.is_success:
                raise _InvocationResponseTooLargeError(
                    status_code=response.status_code,
                    limit=self._limit,
                    operation=operation,
                ) from error
            raise
        finally:
            await response.aclose()
