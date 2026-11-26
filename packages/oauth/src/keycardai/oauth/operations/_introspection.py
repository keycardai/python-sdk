"""OAuth 2.0 token introspection (RFC 7662)."""

import json
from urllib.parse import urlencode

from pydantic import ValidationError

from ..client_auth import ClientAuthentication
from ..exceptions import ConfigError, OAuthHttpError, OAuthProtocolError
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import TokenIntrospection
from ..types.oauth import TokenTypeHint

INTROSPECTION_OPERATION = "POST /introspect"


def check_introspection_auth(client_auth: ClientAuthentication) -> None:
    if not (
        any(
            name.lower() == "authorization" and value
            for name, value in client_auth.headers.items()
        )
        or client_auth.form_fields.get("client_assertion")
    ):
        raise ConfigError("Token introspection requires client authentication.")


def build_introspection_http_request(
    *,
    endpoint: str,
    token: str,
    token_type_hint: str | TokenTypeHint | None,
    client_auth: ClientAuthentication,
) -> HttpRequest:
    payload = [("token", token)]
    if token_type_hint is not None:
        if isinstance(token_type_hint, TokenTypeHint):
            token_type_hint = token_type_hint.value
        payload.append(("token_type_hint", token_type_hint))
    payload.extend(client_auth.form_fields.items())
    return HttpRequest(
        method="POST",
        url=endpoint,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            **client_auth.headers,
        },
        body=urlencode(payload).encode("utf-8"),
    )


def invalid_introspection_response(status: int, description: str) -> OAuthProtocolError:
    return OAuthProtocolError(
        error="invalid_response",
        error_description=description,
        operation=INTROSPECTION_OPERATION,
        status_code=status,
    )


def parse_introspection_http_response(res: HttpResponse) -> TokenIntrospection:
    try:
        data = json.loads(res.body)
    except (ValueError, RecursionError):
        data = None

    if not 200 <= res.status < 300:
        if isinstance(data, dict) and isinstance(data.get("error"), str):
            raise OAuthProtocolError(
                error=data["error"],
                error_description=data.get("error_description"),
                error_uri=data.get("error_uri"),
                reason=data.get("reason"),
                operation=INTROSPECTION_OPERATION,
                status_code=res.status,
            )
        raise OAuthHttpError(
            status_code=res.status,
            headers=dict(res.headers),
            operation=INTROSPECTION_OPERATION,
        )

    if res.status != 200 or not isinstance(data, dict):
        raise invalid_introspection_response(
            res.status, "Introspection requires a 200 response with a JSON object."
        )
    active = data.get("active")
    if not isinstance(active, bool):
        raise invalid_introspection_response(
            res.status, "Introspection response must include a boolean 'active'."
        )
    if not active:
        return TokenIntrospection(active=False)

    if isinstance(data.get("scope"), str):
        data["scope"] = data["scope"].split()
    try:
        return TokenIntrospection.model_validate(data)
    except ValidationError:
        raise invalid_introspection_response(
            res.status, "Introspection response contains invalid claims."
        ) from None
