"""OAuth 2.0 Client Credentials Grant operations.

This module implements RFC 6749 Section 4.4 Client Credentials Grant operations
using the HTTP transport layer with byte-level operations.
"""

import json
from urllib.parse import urlencode

from ..exceptions import OAuthHttpError, OAuthProtocolError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import ClientCredentialsRequest, TokenResponse


def build_client_credentials_http_request(
    request: ClientCredentialsRequest, context: HTTPContext
) -> HttpRequest:
    """Build HTTP request for the client credentials grant.

    Args:
        request: Client credentials grant request parameters
        context: HTTP context with endpoint, auth strategy, and optional
            issuer selector. The issuer is passed to the auth strategy so
            zone-aware strategies apply the credentials for that issuer.

    Returns:
        HttpRequest for the token endpoint
    """
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
        headers.update(dict(context.auth.apply_headers(context.issuer)))

    # Convert to properly URL-encoded form data as required by OAuth 2.0 RFC 6749
    form_data = urlencode(payload).encode("utf-8")

    return HttpRequest(
        method="POST",
        url=context.endpoint,
        headers=headers,
        body=form_data
    )


def parse_client_credentials_http_response(res: HttpResponse) -> TokenResponse:
    """Parse HTTP response from the token endpoint.

    Args:
        res: HTTP response from the token endpoint

    Returns:
        TokenResponse with grant results

    Raises:
        OAuthHttpError: If HTTP error status
        OAuthProtocolError: If invalid response format
    """
    if res.status >= 400:
        full_body = res.body.decode("utf-8", "ignore")
        try:
            data = json.loads(full_body)
            if isinstance(data, dict) and "error" in data:
                raise OAuthProtocolError(
                    error=data["error"],
                    error_description=data.get("error_description"),
                    error_uri=data.get("error_uri"),
                    operation="POST /token (client_credentials)",
                )
        except (json.JSONDecodeError, ValueError):
            pass
        raise OAuthHttpError(
            status_code=res.status,
            response_body=full_body[:512],
            headers=dict(res.headers),
            operation="POST /token (client_credentials)",
        )

    try:
        data = json.loads(res.body.decode("utf-8"))
    except Exception as e:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Invalid JSON in client credentials response",
            operation="POST /token (client_credentials)"
        ) from e

    if isinstance(data, dict) and "error" in data:
        raise OAuthProtocolError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            operation="POST /token (client_credentials)"
        )

    if not isinstance(data, dict) or "access_token" not in data:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Missing required 'access_token' in client credentials response",
            operation="POST /token (client_credentials)"
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
        raw=data,
        headers=dict(res.headers),
    )


def client_credentials_grant(
    request: ClientCredentialsRequest,
    context: HTTPContext,
) -> TokenResponse:
    """Perform OAuth 2.0 Client Credentials Grant (sync version).

    Implements RFC 6749 Section 4.4 Client Credentials Grant with graceful
    error handling using the HTTP transport layer.

    Args:
        request: Client credentials grant request parameters
        context: Operation context with transport and configuration

    Returns:
        TokenResponse with the issued token and metadata

    Raises:
        ValueError: If required parameters are missing
        OAuthHttpError: If token endpoint is unreachable or returns non-200
        OAuthProtocolError: If response format is invalid or contains OAuth errors
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc6749#section-4.4
    """
    http_req = build_client_credentials_http_request(request, context)

    # Execute HTTP request using transport
    http_res = context.transport.request_raw(http_req, timeout=context.timeout)

    # Parse and return token response
    return parse_client_credentials_http_response(http_res)


async def client_credentials_grant_async(
    request: ClientCredentialsRequest,
    context: HTTPContext,
) -> TokenResponse:
    """Perform OAuth 2.0 Client Credentials Grant (async version).

    Implements RFC 6749 Section 4.4 Client Credentials Grant with graceful
    error handling using the HTTP transport layer.

    Args:
        request: Client credentials grant request parameters
        context: Operation context with transport and configuration

    Returns:
        TokenResponse with the issued token and metadata

    Raises:
        ValueError: If required parameters are missing
        OAuthHttpError: If token endpoint is unreachable or returns non-200
        OAuthProtocolError: If response format is invalid or contains OAuth errors
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc6749#section-4.4
    """
    http_req = build_client_credentials_http_request(request, context)

    # Execute HTTP request using async transport
    http_res = await context.transport.request_raw(http_req, timeout=context.timeout)

    # Parse and return token response
    return parse_client_credentials_http_response(http_res)
