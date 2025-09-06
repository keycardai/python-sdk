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


def build_http_context(
    endpoint: str,
    transport: HTTPTransport | AsyncHTTPTransport,
    auth: AuthStrategy,
    user_agent: str,
    custom_headers: dict[str, str] | None = None,
    timeout: float | None = None,
    additional_headers: dict[str, str] | None = None,
) -> HTTPContext:
    """Build HTTPContext with headers from configuration.

    Pure function to create HTTPContext with proper header management.
    Combines user agent, custom headers, and additional headers in the correct order.

    Args:
        endpoint: The endpoint URL for the HTTP context
        transport: HTTP transport implementation (sync or async)
        auth: Authentication strategy
        user_agent: User-Agent string from configuration
        custom_headers: Custom headers from configuration (optional)
        timeout: Optional timeout override
        additional_headers: Optional additional headers to merge

    Returns:
        HTTPContext configured with headers from config

    Example:
        context = build_http_context(
            endpoint="https://api.example.com/token",
            transport=transport,
            auth=auth_strategy,
            user_agent="MyApp/1.0",
            custom_headers={"X-API-Key": "key123"},
            additional_headers={"X-Request-ID": "req-456"}
        )
    """
    # Build headers in order of precedence
    headers = {"User-Agent": user_agent}

    # Add custom headers from config
    if custom_headers:
        headers.update(custom_headers)

    # Add operation-specific headers
    if additional_headers:
        headers.update(additional_headers)

    return HTTPContext(
        endpoint=endpoint,
        transport=transport,
        auth=auth,
        timeout=timeout,
        headers=headers,
    )
