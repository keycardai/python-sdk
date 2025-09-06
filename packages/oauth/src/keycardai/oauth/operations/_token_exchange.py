"""OAuth 2.0 Token Exchange operations.

This module implements RFC 8693 OAuth 2.0 Token Exchange operations
using the new HTTP transport layer with byte-level operations.
"""

import json
from urllib.parse import urlencode

from ..exceptions import OAuthHttpError, OAuthProtocolError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..http.auth import NoneAuth
from ..types.models import TokenExchangeRequest, TokenResponse
from ..utils.jwt import get_claims


def build_token_exchange_http_request(
    request: TokenExchangeRequest, context: HTTPContext
) -> HttpRequest:
    """Build HTTP request for token exchange.

    Args:
        request: Token exchange request parameters
        endpoint: Token exchange endpoint URL
        auth_headers: Authentication headers from auth strategy

    Returns:
        HttpRequest for the token exchange endpoint
    """
    # Impersonate the client if not explicitly set to delegations
    if request.actor_token is None and request.client_id is None and isinstance(context.auth, NoneAuth):
        claims = get_claims(request.subject_token)
        request.client_id = claims.get("client_id")


    payload = request.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"timeout"}
    )

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    if context.auth:
        headers.update(dict(context.auth.apply_headers()))

    # Convert to properly URL-encoded form data as required by OAuth 2.0 RFC 8693
    form_data = urlencode(payload).encode("utf-8")

    return HttpRequest(
        method="POST",
        url=context.endpoint,
        headers=headers,
        body=form_data
    )


def parse_token_exchange_http_response(res: HttpResponse) -> TokenResponse:
    """Parse HTTP response from token exchange endpoint.

    Args:
        res: HTTP response from token exchange endpoint

    Returns:
        TokenResponse with exchange results

    Raises:
        OAuthHttpError: If HTTP error status
        OAuthProtocolError: If invalid response format
    """
    # TODO: Handle errors more granularly
    if res.status >= 400:
        response_body = res.body[:512].decode("utf-8", "ignore")
        raise OAuthHttpError(
            status_code=res.status,
            response_body=response_body,
            headers=dict(res.headers),
            operation="POST /token (exchange)"
        )

    try:
        data = json.loads(res.body.decode("utf-8"))
    except Exception as e:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Invalid JSON in token exchange response",
            operation="POST /token (exchange)"
        ) from e

    if isinstance(data, dict) and "error" in data:
        raise OAuthProtocolError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            operation="POST /token (exchange)"
        )

    if not isinstance(data, dict) or "access_token" not in data:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Missing required 'access_token' in token exchange response",
            operation="POST /token (exchange)"
        )

    scope = data.get("scope")
    if isinstance(scope, str):
        scope = scope.split() if scope else None
    elif isinstance(scope, list):
        scope = scope if scope else None

    return TokenResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "Bearer"),
        expires_in=data.get("expires_in"),
        refresh_token=data.get("refresh_token"),
        scope=scope,
        issued_token_type=data.get("issued_token_type"),
        subject_issuer=data.get("subject_issuer"),
        raw=data,
        headers=dict(res.headers),
    )


def exchange_token(
    request: TokenExchangeRequest,
    context: HTTPContext,
) -> TokenResponse:
    """Perform OAuth 2.0 Token Exchange (sync version).

    Implements RFC 8693 OAuth 2.0 Token Exchange with comprehensive parameter
    support and graceful error handling using the new HTTP transport layer.

    Args:
        request: Token exchange request with all exchange parameters
        context: Operation context with transport and configuration

    Returns:
        TokenResponse with the exchanged token and metadata

    Raises:
        ValueError: If required parameters are missing
        OAuthHttpError: If token endpoint is unreachable or returns non-200
        OAuthProtocolError: If response format is invalid or contains OAuth errors
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc8693#section-2.1
    """
    http_req = build_token_exchange_http_request(request, context)

    # Execute HTTP request using transport
    http_res = context.transport.request_raw(http_req, timeout=context.timeout)

    # Parse and return token response
    return parse_token_exchange_http_response(http_res)


async def exchange_token_async(
    request: TokenExchangeRequest,
    context: HTTPContext,
) -> TokenResponse:
    """Perform OAuth 2.0 Token Exchange (async version).

    Implements RFC 8693 OAuth 2.0 Token Exchange with comprehensive parameter
    support and graceful error handling using the new HTTP transport layer.

    Args:
        request: Token exchange request with all exchange parameters
        context: Operation context with transport and configuration

    Returns:
        TokenResponse with the exchanged token and metadata

    Raises:
        ValueError: If required parameters are missing
        OAuthHttpError: If token endpoint is unreachable or returns non-200
        OAuthProtocolError: If response format is invalid or contains OAuth errors
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc8693#section-2.1
    """
    # Build HTTP request

    http_req = build_token_exchange_http_request(request, context)

    # Execute HTTP request using async transport
    http_res = await context.transport.request_raw(http_req, timeout=context.timeout)

    # Parse and return token response
    return parse_token_exchange_http_response(http_res)
