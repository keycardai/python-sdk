"""OAuth 2.0 Token Revocation operations (RFC 7009).

Request builder and response parser for the revocation request.
"""

import json
from urllib.parse import urlencode

from ..client_auth import ClientAuthentication
from ..exceptions import OAuthHttpError, TokenRevocationError
from ..http._wire import HttpRequest, HttpResponse

REVOCATION_OPERATION = "POST /revoke"


def build_revocation_http_request(
    *,
    endpoint: str,
    token: str,
    token_type_hint: str,
    client_auth: ClientAuthentication,
) -> HttpRequest:
    """Build the revocation request (RFC 7009 Section 2.1).

    Sends ``token``, ``token_type_hint``, and the client authentication.
    It does not send grant or token exchange parameters.
    """
    payload = [("token", token), ("token_type_hint", token_type_hint)]
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


def parse_revocation_http_response(res: HttpResponse) -> None:
    """Check the revocation endpoint's answer (RFC 7009 Section 2.2).

    A 2xx is success whatever its body; the body is not parsed. The zone
    also answers 200 for an unknown, expired, or already revoked token.

    Raises:
        TokenRevocationError: An OAuth error body on a status other than
            2xx. It keeps the response's ``status_code``.
        OAuthHttpError: A status other than 2xx without an OAuth error body.
    """
    if 200 <= res.status < 300:
        return
    try:
        data = json.loads(res.body)
    except (ValueError, RecursionError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        raise TokenRevocationError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            reason=data.get("reason"),
            operation=REVOCATION_OPERATION,
            status_code=res.status,
        )
    raise OAuthHttpError(
        status_code=res.status,
        response_body=res.body.decode("utf-8", "replace")[:512],
        headers=dict(res.headers),
        operation=REVOCATION_OPERATION,
    )
