"""OAuth 2.0 Authorization Code operations.

This module implements the authorization URL construction and authorization
code exchange for OAuth 2.0 authorization code flows (RFC 6749 Section 4.1)
with PKCE support (RFC 7636).
"""

import json
from urllib.parse import urlencode

from ..exceptions import OAuthHttpError, OAuthProtocolError, RefreshGrantError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..http.transport import AsyncHTTPTransport, HTTPTransport
from ..types.models import TokenResponse
from ..utils.pkce import PKCEChallenge


def build_authorize_url(
    authorize_endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    pkce: PKCEChallenge,
    resources: list[str] | None = None,
    scope: str | None = None,
    state: str | None = None,
) -> str:
    """Build an OAuth 2.0 authorization URL with PKCE.

    Constructs the full authorization URL including PKCE challenge parameters
    and multiple resource parameters per RFC 8707.

    Args:
        authorize_endpoint: The authorization endpoint URL.
        client_id: The OAuth client ID.
        redirect_uri: The redirect URI for the callback.
        pkce: PKCE challenge/verifier pair.
        resources: Resource URIs to request (each becomes a separate
            ``resource`` query parameter per RFC 8707).
        scope: Space-separated scope string.
        state: Opaque state value for CSRF protection.

    Returns:
        The complete authorization URL string.
    """
    params: dict[str, str | list[str]] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": pkce.code_challenge_method,
    }
    if resources:
        params["resource"] = resources
    if scope:
        params["scope"] = scope
    if state:
        params["state"] = state

    return f"{authorize_endpoint}?{urlencode(params, doseq=True)}"


# ---------------------------------------------------------------------------
# Authorization code exchange
# ---------------------------------------------------------------------------

def build_authorization_code_http_request(
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_id: str | None,
    context: HTTPContext,
    resource: str | None = None,
) -> HttpRequest:
    """Build the HTTP request for an authorization code exchange.

    Args:
        code: The authorization code from the callback.
        redirect_uri: The redirect URI used in the authorize request.
        code_verifier: The PKCE code verifier.
        client_id: Client ID to include in the form body (required for
            public clients, optional for confidential clients).
        context: HTTP context with endpoint, transport, and auth.
        resource: Optional RFC 8707 resource indicator. Scopes the issued
            token to a specific resource.

    Returns:
        HttpRequest ready to send.
    """
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    if resource is not None:
        payload["resource"] = resource

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if context.auth:
        headers.update(dict(context.auth.apply_headers(context.issuer)))

    form_data = urlencode(payload).encode("utf-8")

    return HttpRequest(
        method="POST",
        url=context.endpoint,
        headers=headers,
        body=form_data,
    )


def parse_authorization_code_http_response(res: HttpResponse) -> TokenResponse:
    """Parse the token endpoint response from an authorization code exchange.

    Args:
        res: HTTP response from the token endpoint.

    Returns:
        TokenResponse with tokens and metadata.

    Raises:
        OAuthProtocolError: If the response contains an OAuth error.
        OAuthHttpError: If the HTTP status indicates an error.
    """
    return _parse_token_http_response(
        res,
        operation="POST /token (authorization_code)",
        error_cls=OAuthProtocolError,
    )


def _parse_token_http_response(
    res: HttpResponse,
    *,
    operation: str,
    error_cls: type[OAuthProtocolError],
) -> TokenResponse:
    if res.status >= 400:
        full_body = res.body.decode("utf-8", "ignore")
        try:
            data = json.loads(full_body)
            if isinstance(data, dict) and "error" in data:
                raise error_cls(
                    error=data["error"],
                    error_description=data.get("error_description"),
                    error_uri=data.get("error_uri"),
                    operation=operation,
                )
        except (json.JSONDecodeError, ValueError):
            pass
        raise OAuthHttpError(
            status_code=res.status,
            response_body=full_body[:512],
            headers=dict(res.headers),
            operation=operation,
        )

    try:
        data = json.loads(res.body.decode("utf-8"))
    except Exception as e:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description=f"Invalid JSON in {operation} response",
            operation=operation,
        ) from e

    if isinstance(data, dict) and "error" in data:
        raise error_cls(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            operation=operation,
        )

    if not isinstance(data, dict) or "access_token" not in data:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description=f"Missing required 'access_token' in {operation} response",
            operation=operation,
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
        id_token=data.get("id_token"),
        scope=scope,
        raw=data,
        headers=dict(res.headers),
    )


def exchange_authorization_code(
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_id: str | None = None,
    context: HTTPContext[HTTPTransport],
    resource: str | None = None,
) -> TokenResponse:
    """Exchange an authorization code for tokens (sync).

    Args:
        code: The authorization code from the callback.
        redirect_uri: The redirect URI used in the authorize request.
        code_verifier: The PKCE code verifier.
        client_id: Client ID for the form body. Required for public clients.
        context: HTTP context with endpoint, transport, and auth.
        resource: Optional RFC 8707 resource indicator.

    Returns:
        TokenResponse with tokens.

    Raises:
        OAuthHttpError: If the token endpoint returns an HTTP error.
        OAuthProtocolError: If the response contains an OAuth error.
    """
    http_req = build_authorization_code_http_request(
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        client_id=client_id,
        context=context,
        resource=resource,
    )
    http_res = context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_authorization_code_http_response(http_res)


async def exchange_authorization_code_async(
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_id: str | None = None,
    context: HTTPContext[AsyncHTTPTransport],
    resource: str | None = None,
) -> TokenResponse:
    """Exchange an authorization code for tokens (async).

    Args:
        code: The authorization code from the callback.
        redirect_uri: The redirect URI used in the authorize request.
        code_verifier: The PKCE code verifier.
        client_id: Client ID for the form body. Required for public clients.
        context: HTTP context with endpoint, transport, and auth.
        resource: Optional RFC 8707 resource indicator.

    Returns:
        TokenResponse with tokens.

    Raises:
        OAuthHttpError: If the token endpoint returns an HTTP error.
        OAuthProtocolError: If the response contains an OAuth error.
    """
    http_req = build_authorization_code_http_request(
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        client_id=client_id,
        context=context,
        resource=resource,
    )
    http_res = await context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_authorization_code_http_response(http_res)


# ---------------------------------------------------------------------------
# Refresh-token grant
# ---------------------------------------------------------------------------

REFRESH_OPERATION = "POST /token (refresh_token)"


def build_refresh_token_http_request(
    *,
    refresh_token: str,
    client_id: str | None,
    context: HTTPContext,
    resources: list[str] | None = None,
    scope: str | None = None,
) -> HttpRequest:
    """Build the HTTP request for a refresh-token grant (RFC 6749 Section 6).

    Args:
        refresh_token: The refresh token returned by the authorization server.
        client_id: Client ID for the form body. Public clients send it; a
            confidential client authenticates through ``context.auth`` and
            passes None.
        context: HTTP context with endpoint, transport, and auth.
        resources: RFC 8707 resource indicators, one ``resource`` parameter
            per entry.
        scope: Space-separated scope string to narrow the refreshed token.

    Returns:
        HttpRequest ready to send.
    """
    payload: list[tuple[str, str]] = [
        ("grant_type", "refresh_token"),
        ("refresh_token", refresh_token),
    ]
    if client_id is not None:
        payload.append(("client_id", client_id))
    for resource in resources or []:
        payload.append(("resource", resource))
    if scope:
        payload.append(("scope", scope))

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if context.auth:
        headers.update(dict(context.auth.apply_headers(context.issuer)))

    return HttpRequest(
        method="POST",
        url=context.endpoint,
        headers=headers,
        body=urlencode(payload).encode("utf-8"),
    )


def parse_refresh_token_http_response(res: HttpResponse) -> TokenResponse:
    """Parse the token endpoint response to a refresh-token grant.

    Raises:
        RefreshGrantError: If the response carries an OAuth error.
            ``invalid_grant`` is not retryable; the user must authorize again.
        OAuthProtocolError: If the response body is malformed.
        OAuthHttpError: If the HTTP status indicates an error with no OAuth
            error body.
    """
    return _parse_token_http_response(
        res, operation=REFRESH_OPERATION, error_cls=RefreshGrantError
    )


def refresh_token_grant(
    *,
    refresh_token: str,
    client_id: str | None = None,
    context: HTTPContext[HTTPTransport],
    resources: list[str] | None = None,
    scope: str | None = None,
) -> TokenResponse:
    """Redeem a refresh token for a new access token (sync).

    A ``refresh_token`` on the returned TokenResponse means the server
    rotated it; the caller stores it in place of the old one. The SDK keeps
    no state.

    Raises:
        RefreshGrantError: If the token endpoint answers with an OAuth error.
        OAuthHttpError: If the token endpoint returns an HTTP error.
        OAuthProtocolError: If the response body is malformed.
    """
    http_req = build_refresh_token_http_request(
        refresh_token=refresh_token,
        client_id=client_id,
        context=context,
        resources=resources,
        scope=scope,
    )
    http_res = context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_refresh_token_http_response(http_res)


async def refresh_token_grant_async(
    *,
    refresh_token: str,
    client_id: str | None = None,
    context: HTTPContext[AsyncHTTPTransport],
    resources: list[str] | None = None,
    scope: str | None = None,
) -> TokenResponse:
    """Redeem a refresh token for a new access token (async).

    A ``refresh_token`` on the returned TokenResponse means the server
    rotated it; the caller stores it in place of the old one. The SDK keeps
    no state.

    Raises:
        RefreshGrantError: If the token endpoint answers with an OAuth error.
        OAuthHttpError: If the token endpoint returns an HTTP error.
        OAuthProtocolError: If the response body is malformed.
    """
    http_req = build_refresh_token_http_request(
        refresh_token=refresh_token,
        client_id=client_id,
        context=context,
        resources=resources,
        scope=scope,
    )
    http_res = await context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_refresh_token_http_response(http_res)
