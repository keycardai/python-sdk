"""OpenID Connect UserInfo operations.

This module implements the client side of the UserInfo endpoint
(OpenID Connect Core 1.0 Section 5.3) using the HTTP transport layer with
byte-level operations.

Keycard zone access tokens are authorization-only: identity claims such as
``email`` or ``groups`` are not in the token and live behind the issuer's
``userinfo_endpoint``.
"""

import json
import re

from ..exceptions import (
    ConfigError,
    InvalidTokenError,
    OAuthHttpError,
    OAuthProtocolError,
)
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import (
    AuthorizationServerMetadata,
    UserInfoRequest,
    UserInfoResponse,
)

_OPERATION = "GET /userinfo"


def resolve_userinfo_endpoint(metadata: AuthorizationServerMetadata) -> str:
    """Resolve the UserInfo endpoint from discovered server metadata.

    Args:
        metadata: Authorization server metadata from discovery.

    Returns:
        The ``userinfo_endpoint`` URL.

    Raises:
        ConfigError: If the metadata has no ``userinfo_endpoint``.
    """
    if not metadata.userinfo_endpoint:
        raise ConfigError(
            f"Authorization server '{metadata.issuer}' does not advertise a "
            "'userinfo_endpoint'; UserInfo is unavailable for this issuer."
        )
    return metadata.userinfo_endpoint


def build_userinfo_http_request(
    request: UserInfoRequest, context: HTTPContext
) -> HttpRequest:
    """Build the HTTP request for a UserInfo fetch.

    The access token is presented as a Bearer credential (RFC 6750 Section 2.1),
    which is the form OIDC recommends for UserInfo. The client's own auth
    strategy is not applied: UserInfo authenticates the user, not the client.

    Args:
        request: UserInfo request carrying the access token.
        context: HTTP context with the resolved UserInfo endpoint and transport.

    Returns:
        HttpRequest for the UserInfo endpoint.
    """
    headers = {
        "Accept": "application/json",
    }
    if context.headers:
        headers.update(context.headers)
    headers["Authorization"] = f"Bearer {request.access_token}"

    return HttpRequest(
        method="GET",
        url=context.endpoint,
        headers=headers,
        body=None,
    )


def _header(res: HttpResponse, name: str) -> str | None:
    for key, value in res.headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _challenge_error(www_authenticate: str | None) -> str:
    """Extract the RFC 6750 ``error`` code from a ``WWW-Authenticate`` challenge."""
    if not www_authenticate:
        return "invalid_token"
    match = re.search(r'error\s*=\s*"?([^",\s]+)"?', www_authenticate)
    return match.group(1) if match else "invalid_token"


def parse_userinfo_http_response(res: HttpResponse) -> UserInfoResponse:
    """Parse the HTTP response from the UserInfo endpoint.

    Args:
        res: HTTP response from the UserInfo endpoint.

    Returns:
        UserInfoResponse with ``sub`` and the full claims document.

    Raises:
        InvalidTokenError: If the endpoint rejects the access token (HTTP 401).
        OAuthHttpError: If the endpoint returns any other non-2xx status.
        OAuthProtocolError: If the body is not a JSON claims object, is a signed
            (``application/jwt``) response, or omits ``sub``.
    """
    if res.status == 401:
        error = _challenge_error(_header(res, "WWW-Authenticate"))
        raise InvalidTokenError(
            f"UserInfo request rejected with '{error}': the access token is "
            "expired, revoked, or not accepted at the UserInfo endpoint.",
            error_code=error,
        )

    if res.status >= 400:
        raise OAuthHttpError(
            status_code=res.status,
            response_body=res.body[:512].decode("utf-8", "ignore"),
            headers=dict(res.headers),
            operation=_OPERATION,
        )

    content_type = _header(res, "Content-Type") or ""
    if "application/jwt" in content_type.lower():
        raise OAuthProtocolError(
            error="invalid_response",
            error_description=(
                f"Unsupported UserInfo response content type '{content_type}': "
                "signed and encrypted UserInfo responses are not supported."
            ),
            operation=_OPERATION,
        )

    try:
        claims = json.loads(res.body.decode("utf-8"))
    except Exception as e:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Invalid JSON in UserInfo response",
            operation=_OPERATION,
        ) from e

    if not isinstance(claims, dict):
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="UserInfo response must be a JSON object of claims",
            operation=_OPERATION,
        )

    if "error" in claims and "sub" not in claims:
        raise OAuthProtocolError(
            error=claims["error"],
            error_description=claims.get("error_description"),
            error_uri=claims.get("error_uri"),
            operation=_OPERATION,
        )

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="UserInfo response must include a 'sub' claim",
            operation=_OPERATION,
        )

    return UserInfoResponse(
        sub=sub,
        claims=claims,
        headers=dict(res.headers),
    )


def fetch_userinfo(
    request: UserInfoRequest,
    context: HTTPContext,
) -> UserInfoResponse:
    """Fetch the signed-in user's claims from the UserInfo endpoint (sync version).

    Args:
        request: UserInfo request carrying the access token.
        context: HTTP context with the resolved UserInfo endpoint and transport.

    Returns:
        UserInfoResponse with ``sub`` and all returned claims.

    Raises:
        InvalidTokenError: If the access token is not accepted (HTTP 401)
        OAuthHttpError: If the UserInfo endpoint returns another non-2xx status
        OAuthProtocolError: If the response is not a JSON claims object with 'sub'
        NetworkError: If the network request fails

    Reference: https://openid.net/specs/openid-connect-core-1_0.html#UserInfo
    """
    http_req = build_userinfo_http_request(request, context)
    http_res = context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_userinfo_http_response(http_res)


async def fetch_userinfo_async(
    request: UserInfoRequest,
    context: HTTPContext,
) -> UserInfoResponse:
    """Fetch the signed-in user's claims from the UserInfo endpoint (async version).

    Args:
        request: UserInfo request carrying the access token.
        context: HTTP context with the resolved UserInfo endpoint and transport.

    Returns:
        UserInfoResponse with ``sub`` and all returned claims.

    Raises:
        InvalidTokenError: If the access token is not accepted (HTTP 401)
        OAuthHttpError: If the UserInfo endpoint returns another non-2xx status
        OAuthProtocolError: If the response is not a JSON claims object with 'sub'
        NetworkError: If the network request fails

    Reference: https://openid.net/specs/openid-connect-core-1_0.html#UserInfo
    """
    http_req = build_userinfo_http_request(request, context)
    http_res = await context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_userinfo_http_response(http_res)
