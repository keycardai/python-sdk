"""OAuth 2.0 Device Authorization Grant operations (RFC 8628).

Request builders and response parsers for the device authorization request
(Section 3.1) and the device token poll (Section 3.4). The polling loop is
``AsyncClient.wait_for_device_token``.
"""

import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from ..client_auth import ClientAuthentication
from ..exceptions import DeviceAuthorizationError, OAuthHttpError
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import DeviceAuthorization, TokenResponse
from ..types.oauth import GrantType
from ._authorize import _parse_token_http_response

DEVICE_AUTHORIZATION_OPERATION = "POST /device_authorization"
DEVICE_TOKEN_OPERATION = "POST /token (device_code)"
DEFAULT_POLL_INTERVAL = 5
SLOW_DOWN_INCREMENT = 5

_REQUIRED_STRINGS = ("device_code", "user_code", "verification_uri")


def _form_request(
    endpoint: str, payload: list[tuple[str, str]], client_auth: ClientAuthentication
) -> HttpRequest:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        **client_auth.headers,
    }
    payload = payload + list(client_auth.form_fields.items())
    return HttpRequest(
        method="POST",
        url=endpoint,
        headers=headers,
        body=urlencode(payload).encode("utf-8"),
    )


def build_device_authorization_http_request(
    *,
    endpoint: str,
    client_auth: ClientAuthentication,
    scopes: list[str] | None = None,
    resources: list[str] | None = None,
) -> HttpRequest:
    """Build the device authorization request (RFC 8628 Section 3.1).

    Sends a space-joined ``scope`` when ``scopes`` is set, one ``resource``
    per entry in ``resources``, and the client authentication.
    """
    payload: list[tuple[str, str]] = []
    if scopes:
        payload.append(("scope", " ".join(scopes)))
    for resource in resources or []:
        payload.append(("resource", resource))
    return _form_request(endpoint, payload, client_auth)


def _invalid_response(description: str) -> DeviceAuthorizationError:
    return DeviceAuthorizationError(
        error="invalid_response",
        error_description=description,
        operation=DEVICE_AUTHORIZATION_OPERATION,
    )


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def parse_device_authorization_http_response(
    res: HttpResponse, *, received_at: datetime
) -> DeviceAuthorization:
    """Parse the device authorization response (RFC 8628 Section 3.2).

    ``expires_at`` is ``received_at`` plus ``expires_in``. ``interval`` is 5
    when the response omits it.

    Raises:
        DeviceAuthorizationError: The response is an OAuth error, or is
            missing ``device_code``, ``user_code``, ``verification_uri``, or
            ``expires_in`` (code ``invalid_response``).
        OAuthHttpError: A non-2xx response without an OAuth error body.
    """
    body = res.body.decode("utf-8", "ignore")
    try:
        data = json.loads(body)
    except ValueError:
        data = None

    if res.status >= 400:
        if isinstance(data, dict) and "error" in data:
            raise DeviceAuthorizationError(
                error=data["error"],
                error_description=data.get("error_description"),
                error_uri=data.get("error_uri"),
                reason=data.get("reason"),
                operation=DEVICE_AUTHORIZATION_OPERATION,
            )
        raise OAuthHttpError(
            status_code=res.status,
            response_body=body[:512],
            headers=dict(res.headers),
            operation=DEVICE_AUTHORIZATION_OPERATION,
        )

    if not isinstance(data, dict):
        raise _invalid_response("Device authorization response is not a JSON object")

    missing = [
        name
        for name in _REQUIRED_STRINGS
        if not isinstance(data.get(name), str) or not data[name]
    ]
    if not _is_positive_int(data.get("expires_in")):
        missing.append("expires_in")
    if missing:
        raise _invalid_response(
            f"Device authorization response is missing {', '.join(missing)}"
        )

    interval = data.get("interval")
    if interval is None:
        interval = DEFAULT_POLL_INTERVAL
    elif not _is_positive_int(interval):
        raise _invalid_response("Device authorization response has an invalid interval")

    try:
        expires_at = received_at + timedelta(seconds=data["expires_in"])
    except OverflowError as e:
        raise _invalid_response(
            "Device authorization response has an invalid expires_in"
        ) from e

    verification_uri_complete = data.get("verification_uri_complete")
    return DeviceAuthorization(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        verification_uri_complete=(
            verification_uri_complete
            if isinstance(verification_uri_complete, str)
            else None
        ),
        expires_in=data["expires_in"],
        interval=interval,
        expires_at=expires_at,
    )


def build_device_token_http_request(
    *,
    endpoint: str,
    device_code: str,
    client_auth: ClientAuthentication,
) -> HttpRequest:
    """Build one device token poll (RFC 8628 Section 3.4).

    Sends ``grant_type``, ``device_code``, and the client authentication.
    It does not send ``resource`` or ``scope``.
    """
    payload = [
        ("grant_type", GrantType.DEVICE_CODE.value),
        ("device_code", device_code),
    ]
    return _form_request(endpoint, payload, client_auth)


def parse_device_token_http_response(res: HttpResponse) -> TokenResponse:
    """Parse the token endpoint's answer to one device token poll.

    Raises:
        DeviceAuthorizationError: The response is an OAuth error, including
            ``authorization_pending`` and ``slow_down``.
        OAuthProtocolError: The response body is malformed
            (``invalid_response``).
        OAuthHttpError: A non-2xx response without an OAuth error body.
    """
    return _parse_token_http_response(
        res, operation=DEVICE_TOKEN_OPERATION, error_cls=DeviceAuthorizationError
    )
