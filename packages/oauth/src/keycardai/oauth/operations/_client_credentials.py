"""OAuth 2.0 Client Credentials grant (RFC 6749 Section 4.4).

Used internally by impersonation to mint the actor access token derived from
the client's application credential.
"""

import json
from urllib.parse import urlencode

from ..exceptions import OAuthHttpError, OAuthProtocolError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import TokenResponse


def build_client_credentials_http_request(
    context: HTTPContext,
    *,
    scope: str | None = None,
    resource: str | None = None,
) -> HttpRequest:
    payload: dict[str, str] = {"grant_type": "client_credentials"}
    if scope:
        payload["scope"] = scope
    if resource:
        payload["resource"] = resource

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if context.auth:
        headers.update(dict(context.auth.apply_headers()))

    return HttpRequest(
        method="POST",
        url=context.endpoint,
        headers=headers,
        body=urlencode(payload).encode("utf-8"),
    )


def parse_client_credentials_http_response(res: HttpResponse) -> TokenResponse:
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
            error_description="Invalid JSON in client_credentials response",
            operation="POST /token (client_credentials)",
        ) from e

    if isinstance(data, dict) and "error" in data:
        raise OAuthProtocolError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            operation="POST /token (client_credentials)",
        )

    if not isinstance(data, dict) or "access_token" not in data:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Missing required 'access_token' in client_credentials response",
            operation="POST /token (client_credentials)",
        )

    scope = data.get("scope")
    if isinstance(scope, str):
        scope = scope.split() if scope else None

    return TokenResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "Bearer"),
        expires_in=data.get("expires_in"),
        refresh_token=data.get("refresh_token"),
        scope=scope,
        issued_token_type=data.get("issued_token_type"),
        raw=data,
        headers=dict(res.headers),
    )


def grant_client_credentials(
    context: HTTPContext,
    *,
    scope: str | None = None,
    resource: str | None = None,
) -> TokenResponse:
    http_req = build_client_credentials_http_request(
        context, scope=scope, resource=resource
    )
    http_res = context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_client_credentials_http_response(http_res)


async def grant_client_credentials_async(
    context: HTTPContext,
    *,
    scope: str | None = None,
    resource: str | None = None,
) -> TokenResponse:
    http_req = build_client_credentials_http_request(
        context, scope=scope, resource=resource
    )
    http_res = await context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_client_credentials_http_response(http_res)
