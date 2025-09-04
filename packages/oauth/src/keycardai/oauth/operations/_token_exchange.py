"""OAuth 2.0 Token Exchange operations.

This module implements RFC 8693 OAuth 2.0 Token Exchange operations
using the new HTTP transport layer with byte-level operations.
"""

import json
from urllib.parse import urlencode

from ..exceptions import OAuthHttpError, OAuthProtocolError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import TokenExchangeRequest, TokenResponse
from ._exec import execute_async, execute_sync


def _validate_token_exchange_params(request: TokenExchangeRequest) -> None:
    """Validate token exchange parameters.

    Args:
        request: The token exchange request to validate

    Raises:
        ValueError: If required parameters are missing or invalid
    """
    if not request.subject_token:
        raise ValueError("subject_token is required")
    if not request.subject_token_type:
        raise ValueError("subject_token_type is required")

def _build_token_exchange_request_from_kwargs(**kwargs) -> TokenExchangeRequest:
    """Build a TokenExchangeRequest from keyword arguments.

    Args:
        **kwargs: Keyword arguments matching TokenExchangeRequest fields

    Returns:
        TokenExchangeRequest built from the provided kwargs

    Raises:
        TypeError: If required parameters are missing
    """
    # Extract required parameters
    subject_token = kwargs.get("subject_token")
    if subject_token is None:
        raise TypeError("subject_token is required when not using a request object")

    subject_token_type = kwargs.get("subject_token_type")
    if subject_token_type is None:
        raise TypeError("subject_token_type is required when not using a request object")

    # Build the request with provided values, falling back to defaults
    # Only pass kwargs that are not None to let the model use its defaults
    request_kwargs = {
        "subject_token": subject_token,
        "subject_token_type": subject_token_type
    }

    for field_name in [
        "grant_type", "resource", "audience", "scope", "requested_token_type",
        "actor_token", "actor_token_type", "timeout", "client_id"
    ]:
        value = kwargs.get(field_name)
        if value is not None:
            request_kwargs[field_name] = value

    return TokenExchangeRequest(**request_kwargs)


def build_token_exchange_http_request(
    request: TokenExchangeRequest, endpoint: str, auth_headers: dict[str, str]
) -> HttpRequest:
    """Build HTTP request for token exchange.

    Args:
        request: Token exchange request parameters
        endpoint: Token exchange endpoint URL
        auth_headers: Authentication headers from auth strategy

    Returns:
        HttpRequest for the token exchange endpoint

    Raises:
        ValueError: If request parameters are invalid
    """
    _validate_token_exchange_params(request)

    payload = request.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"timeout"}
    )

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        **auth_headers
    }

    # Convert to properly URL-encoded form data as required by OAuth 2.0 RFC 8693
    form_data = urlencode(payload).encode("utf-8")

    return HttpRequest(
        method="POST",
        url=endpoint,
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


def token_exchange(
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
    # Build HTTP request
    auth_headers = dict(context.auth.apply_headers())

    http_req = build_token_exchange_http_request(
        request, context.endpoint, auth_headers
    )

    # Execute HTTP request using transport
    http_res = execute_sync(context.transport, http_req, context.timeout)

    # Parse and return token response
    return parse_token_exchange_http_response(http_res)


async def token_exchange_async(
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
    auth_headers = dict(context.auth.apply_headers())

    http_req = build_token_exchange_http_request(
        request, context.endpoint, auth_headers
    )

    # Execute HTTP request using async transport
    http_res = await execute_async(context.transport, http_req, context.timeout)

    # Parse and return token response
    return parse_token_exchange_http_response(http_res)
