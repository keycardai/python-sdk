from ..http._wire import HttpRequest, HttpResponse
from ..http.transport import AsyncHTTPTransport, HTTPTransport


def execute_sync(transport: HTTPTransport, req: HttpRequest, timeout: float | None) -> HttpResponse:
    return transport.request_raw(req, timeout=timeout)

async def execute_async(transport: AsyncHTTPTransport, req: HttpRequest, timeout: float | None) -> HttpResponse:
    return await transport.request_raw(req, timeout=timeout)
