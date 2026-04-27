"""OAuth 2.0 Authorization Code operations.

This module implements the authorization URL construction and authorization
code exchange for OAuth 2.0 authorization code flows (RFC 6749 Section 4.1)
with PKCE support (RFC 7636).
"""

import json
from urllib.parse import urlencode

from ..exceptions import OAuthHttpError, OAuthProtocolError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
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
        headers.update(dict(context.auth.apply_headers()))

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
    if res.status >= 400:
        full_body = res.body.decode("utf-8", "ignore")
        try:
            data = json.loads(full_body)
            if isinstance(data, dict) and "error" in data:
                raise OAuthProtocolError(
                    error=data["error"],
                    error_description=data.get("error_description"),
                    error_uri=data.get("error_uri"),
                    operation="POST /token (authorization_code)",
                )
        except (json.JSONDecodeError, ValueError):
            pass
        raise OAuthHttpError(
            status_code=res.status,
            response_body=full_body[:512],
            headers=dict(res.headers),
            operation="POST /token (authorization_code)",
        )

    try:
        data = json.loads(res.body.decode("utf-8"))
    except Exception as e:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Invalid JSON in authorization code response",
            operation="POST /token (authorization_code)",
        ) from e

    if isinstance(data, dict) and "error" in data:
        raise OAuthProtocolError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            operation="POST /token (authorization_code)",
        )

    if not isinstance(data, dict) or "access_token" not in data:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Missing required 'access_token' in authorization code response",
            operation="POST /token (authorization_code)",
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
    context: HTTPContext,
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
    context: HTTPContext,
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
