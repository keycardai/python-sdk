"""OAuth 2.0 client module.

Simple client structure with clean imports.
"""

from ..http import (
    AsyncHTTPTransport,
    AuthStrategy,
    BasicAuth,
    BearerAuth,
    HTTPContext,
    HTTPTransport,
    NoneAuth,
)
from .client import AsyncClient, Client

__all__ = [
    "AsyncClient",
    "Client",
    "AuthStrategy",
    "BasicAuth",
    "BearerAuth",
    "NoneAuth",
    "HTTPTransport",
    "AsyncHTTPTransport",
    "HTTPContext",
]
