"""HTTP transport layer for OAuth operations.

This package provides HTTP transport abstractions and implementations
for OAuth 2.0 operations. The transport layer operates at the byte level
and provides both synchronous and asynchronous interfaces.

Key components:
- Transport protocols (HTTPTransport, AsyncHTTPTransport)
- Concrete implementations (HttpxTransport, HttpxAsyncTransport)
- Authentication strategies (AuthStrategy, NoneAuth, BasicAuth, BearerAuth)
- Wire types for raw HTTP data (HttpRequest, HttpResponse)
"""

from ._context import HTTPContext
from ._transports import HttpxAsyncTransport, HttpxTransport
from .auth import AuthStrategy, BasicAuth, BearerAuth, NoneAuth
from .transport import AsyncHTTPTransport, HTTPTransport

__all__ = [
    # Transport protocols and implementations
    "HTTPTransport",
    "AsyncHTTPTransport",
    # Authentication strategies
    "AuthStrategy",
    "HTTPContext",
    "NoneAuth",
    "BasicAuth",
    "BearerAuth",
    # Transport implementations
    "HttpxTransport",
    "HttpxAsyncTransport",
]
