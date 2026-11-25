"""Bounded default HTTP clients for peer replies."""

import httpx

from keycardai.oauth.exceptions import ResponseBodyTooLargeError
from keycardai.oauth.http._transports import _read_bounded_async, _read_bounded_sync

DEFAULT_PEER_RESPONSE_BYTES = 64 * 1024 * 1024
_ACCEPT_ENCODING = {"Accept-Encoding": "gzip, deflate"}


class _InvocationResponseTooLargeError(ResponseBodyTooLargeError):
    @property
    def retryable(self) -> bool:
        return False


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


def _check_encoding(response: httpx.Response) -> None:
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity", "gzip", "x-gzip", "deflate"):
        raise httpx.DecodingError(f"Unsupported peer content encoding: {encoding}", request=response.request)


class PeerClient(httpx.Client):
    def __init__(self, limit: int):
        self._limit = _validate_limit(limit)
        super().__init__(timeout=30.0, headers=_ACCEPT_ENCODING)

    def send(self, request: httpx.Request, *, stream: bool = False, **kwargs) -> httpx.Response:
        response = super().send(request, stream=True, **kwargs)
        operation = "a2a_invocation" if request.method == "POST" else "a2a_discovery"
        try:
            _check_encoding(response)
            content = _read_bounded_sync(response, operation, self._limit)
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
            _check_encoding(response)
            content = await _read_bounded_async(response, operation, self._limit)
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
