"""Shared utilities for Starlette/FastAPI applications."""

from pydantic import AnyHttpUrl
from starlette.requests import Request

"""Supported protocols for the base URL."""
SUPPORTED_PROTOCOLS = ["http", "https"]


def get_base_url(request: Request) -> str:
    """Get the correct base URL considering proxy headers like X-Forwarded-Proto."""
    request_base_url = AnyHttpUrl(str(request.base_url))
    proto = request.headers.get("x-forwarded-proto") or request_base_url.scheme
    if proto not in SUPPORTED_PROTOCOLS:
        proto = "https"

    if request_base_url.port not in [443, 80]:
        base_url = f"{proto}://{request_base_url.host}:{request_base_url.port}"
    else:
        base_url = f"{proto}://{request_base_url.host}"

    return base_url
