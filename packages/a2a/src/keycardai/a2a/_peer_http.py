"""Bounded default HTTP clients for peer replies."""

import zlib
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Generic, TypeVar

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
_BOUNDED_CODINGS = {"gzip", "x-gzip", "deflate"}
_UNSUPPORTED_CODINGS = {"br", "zstd", "compress"}


class _InvocationResponseTooLargeError(ResponseBodyTooLargeError):
    @property
    def retryable(self) -> bool:
        return False


class _PeerZlibDecoder(_BoundedZlibDecoder):
    def take_chunk(self) -> bytes:
        chunk = bytes(self._buf)
        self._limit -= len(chunk)
        self._buf.clear()
        return chunk

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


def _peer_coding(response: httpx.Response, operation: str) -> str | None:
    tokens = [value.strip().lower() for value in response.headers.get_list("content-encoding", split_commas=True)]
    codings = [token for token in tokens if token in _BOUNDED_CODINGS or token in _UNSUPPORTED_CODINGS]
    if not codings:
        return None
    if len(codings) != 1 or codings[0] in _UNSUPPORTED_CODINGS:
        cause = httpx.DecodingError(
            f"Unsupported peer content encoding: {', '.join(codings)}",
            request=response.request,
        )
        raise _decode_error(response, operation, cause, response.headers) from cause
    return codings[0]


def _decode_error(
    response: httpx.Response, operation: str, cause: httpx.DecodingError, headers: httpx.Headers
) -> ResponseBodyDecodeError:
    return ResponseBodyDecodeError(
        status=response.status_code,
        headers=headers,
        cause=cause,
        operation=operation,
    )


@contextmanager
def _peer_decoder(
    response: httpx.Response, operation: str, limit: int, coding: str | None, headers: httpx.Headers
) -> Iterator[_PeerZlibDecoder | None]:
    if coding is None:
        yield None
        return
    try:
        yield _PeerZlibDecoder(coding, limit, response.status_code, operation)
    except (zlib.error, httpx.DecodingError) as error:
        cause = error if isinstance(error, httpx.DecodingError) else httpx.DecodingError(str(error))
        raise _decode_error(response, operation, cause, headers) from error


def _overflow(response: httpx.Response, operation: str, limit: int) -> ResponseBodyTooLargeError:
    error_type = (
        _InvocationResponseTooLargeError
        if operation == "a2a_invocation" and response.is_success
        else ResponseBodyTooLargeError
    )
    return error_type(status_code=response.status_code, limit=limit, operation=operation)


_Stream = TypeVar("_Stream", httpx.SyncByteStream, httpx.AsyncByteStream)


class _BoundedPeerStream(Generic[_Stream]):
    def __init__(
        self, source: _Stream, response: httpx.Response, operation: str, limit: int, coding: str | None
    ):
        self._source = source
        self._response = response
        self._operation = operation
        self._limit = self._remaining = limit
        self._coding = coding
        self._wire_headers = response.headers
        if coding is not None:
            response.headers = response.headers.copy()
            response.headers.pop("content-encoding", None)
            response.headers.pop("content-length", None)

    @contextmanager
    def _read(self) -> Iterator[_PeerZlibDecoder | None]:
        try:
            with _peer_decoder(
                self._response, self._operation, self._limit, self._coding, self._wire_headers
            ) as decoder:
                yield decoder
        except ResponseBodyTooLargeError as error:
            raise _overflow(self._response, self._operation, self._limit) from error

    def _chunk(self, data: bytes, decoder: _PeerZlibDecoder | None) -> bytes:
        if decoder is not None:
            decoder.feed(data)
            return decoder.take_chunk()
        self._remaining -= len(data)
        if self._remaining < 0:
            raise ResponseBodyTooLargeError(
                status_code=self._response.status_code, limit=self._limit, operation=self._operation
            )
        return data


class _BoundedSyncPeerStream(_BoundedPeerStream[httpx.SyncByteStream], httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        try:
            with self._read() as decoder:
                for raw in self._source:
                    if chunk := self._chunk(raw, decoder):
                        yield chunk
                if decoder is not None and (tail := decoder.finish()):
                    yield tail
        except BaseException:
            self._response.close()
            raise

    def close(self) -> None:
        self._source.close()


class _BoundedAsyncPeerStream(_BoundedPeerStream[httpx.AsyncByteStream], httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            with self._read() as decoder:
                async for raw in self._source:
                    if chunk := self._chunk(raw, decoder):
                        yield chunk
                if decoder is not None and (tail := decoder.finish()):
                    yield tail
        except BaseException:
            await self._response.aclose()
            raise

    async def aclose(self) -> None:
        await self._source.aclose()


class PeerClient(httpx.Client):
    def __init__(self, limit: int):
        self._limit = _validate_limit(limit)
        super().__init__(timeout=30.0, headers=_ACCEPT_ENCODING)

    def send(self, request: httpx.Request, *, stream: bool = False, **kwargs) -> httpx.Response:
        response = super().send(request, stream=True, **kwargs)
        operation = "a2a_invocation" if request.method == "POST" else "a2a_discovery"
        live = False
        try:
            if check_response := request.extensions.get("keycard_a2a_check_response"):
                check_response(response)
            coding = _peer_coding(response, operation)
            if stream:
                assert isinstance(response.stream, httpx.SyncByteStream)
                response.stream = _BoundedSyncPeerStream(response.stream, response, operation, self._limit, coding)
                live = True
                return response
            with _peer_decoder(response, operation, self._limit, coding, response.headers) as decoder:
                if decoder is None:
                    content = _read_bounded_sync(response, operation, self._limit)
                else:
                    for chunk in response.iter_raw():
                        decoder.feed(chunk)
                    content = decoder.finish()
            return _decoded_response(response, content)
        except ResponseBodyTooLargeError as error:
            if request.method == "POST" and response.is_success:
                raise _overflow(response, operation, self._limit) from error
            raise
        finally:
            if not live:
                response.close()


class AsyncPeerClient(httpx.AsyncClient):
    def __init__(self, limit: int):
        self._limit = _validate_limit(limit)
        super().__init__(timeout=30.0, headers=_ACCEPT_ENCODING)

    async def send(self, request: httpx.Request, *, stream: bool = False, **kwargs) -> httpx.Response:
        response = await super().send(request, stream=True, **kwargs)
        operation = "a2a_invocation" if request.method == "POST" else "a2a_discovery"
        live = False
        try:
            if check_response := request.extensions.get("keycard_a2a_check_response"):
                check_response(response)
            coding = _peer_coding(response, operation)
            if stream:
                assert isinstance(response.stream, httpx.AsyncByteStream)
                response.stream = _BoundedAsyncPeerStream(response.stream, response, operation, self._limit, coding)
                live = True
                return response
            with _peer_decoder(response, operation, self._limit, coding, response.headers) as decoder:
                if decoder is None:
                    content = await _read_bounded_async(response, operation, self._limit)
                else:
                    async for chunk in response.aiter_raw():
                        decoder.feed(chunk)
                    content = decoder.finish()
            return _decoded_response(response, content)
        except ResponseBodyTooLargeError as error:
            if request.method == "POST" and response.is_success:
                raise _overflow(response, operation, self._limit) from error
            raise
        finally:
            if not live:
                await response.aclose()
