"""HTTP context patterns for OAuth 2.0 operations.

This module implements the HTTPContext pattern to address signature bloat
and primitive obsession in OAuth 2.0 client operations. By wrapping HTTP-related
concerns like endpoints, transport, auth, timeouts into immutable context
objects, we create a more maintainable and scalable API.
"""

from dataclasses import dataclass

from .auth import AuthStrategy
from .transport import AsyncHTTPTransport, HTTPTransport


@dataclass(frozen=True)
class HTTPContext:
    """HTTP context for OAuth 2.0 operations.

    Encapsulates HTTP-related concerns for OAuth operations to reduce
    signature bloat and provide a clean, maintainable API surface.

    This context object is immutable and acts as a simple parameter carrier.

    Example:
        context = HTTPContext(
            endpoint="https://auth.example.com/oauth2/introspect",
            transport=HTTPClient(),
            auth=BasicAuth("client_id", "client_secret"),
            timeout=30.0,
            headers={"User-Agent": "MyApp/1.0"}
        )

        response = introspect_token(request, context)
    """

    endpoint: str
    transport: HTTPTransport | AsyncHTTPTransport
    auth: AuthStrategy
    timeout: float | None = None
    retries: int = 0
    headers: dict[str, str] | None = None
