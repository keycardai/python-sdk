"""OAuth 2.0 Device Authorization Grant operations (RFC 8628).

Request builders and response parsers for the device authorization request
(Section 3.1) and the device token poll (Section 3.4).
"""

import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from ..client_auth import ClientAuthentication
from ..exceptions import (
    ConfigError,
    DeviceAuthorizationError,
    OAuthError,
    OAuthHttpError,
)
from ..http._transports import ResponseBodyDecodeError
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import DeviceAuthorization, TokenResponse
from ..types.oauth import GrantType
from ._authorize import token_response_from_json

DEVICE_AUTHORIZATION_OPERATION = "POST /device_authorization"
DEVICE_TOKEN_OPERATION = "POST /token (device_code)"
DEFAULT_POLL_INTERVAL = 5
SLOW_DOWN_INCREMENT = 5

_REQUIRED_STRINGS = ("device_code", "user_code", "verification_uri")


def check_endpoint(issuer: str, name: str, url: str | None) -> str:
    """Return ``url`` if it is an http or https URL.

    Raises:
        ConfigError: ``url`` is missing or is not an http or https URL.
    """
    if not url:
        raise ConfigError(f"Authorization server {issuer} does not advertise a {name}")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(
            f"Authorization server {issuer} has a {name} that is not an http "
            f"or https URL: {url!r}"
        )
    return url


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


def _invalid_response(
    operation: str, description: str, status_code: int
) -> DeviceAuthorizationError:
    return DeviceAuthorizationError(
        error="invalid_response",
        error_description=description,
        operation=operation,
        status_code=status_code,
    )


def _is_success(status: int) -> bool:
    return 200 <= status < 300


def undecodable_body_error(error: ResponseBodyDecodeError, operation: str) -> OAuthError:
    """Return the error for a response whose body did not decode.

    A 2xx is ``invalid_response``. Any other status is an OAuthHttpError,
    as for a response without an OAuth error body.
    """
    if _is_success(error.status):
        return _invalid_response(
            operation, "The response body does not decode", error.status
        )
    return OAuthHttpError(
        status_code=error.status, headers=error.headers, operation=operation
    )


def _json_object(res: HttpResponse, operation: str) -> dict[str, Any]:
    """Return the JSON object in a 2xx response.

    Raises:
        DeviceAuthorizationError: An OAuth error body, or a 2xx body that is
            not a JSON object (``invalid_response``).
        OAuthHttpError: A status other than 2xx without an OAuth error body.
    """
    try:
        data = json.loads(res.body)
    except (ValueError, RecursionError):
        data = None

    if isinstance(data, dict) and isinstance(data.get("error"), str):
        raise DeviceAuthorizationError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            reason=data.get("reason"),
            operation=operation,
            status_code=res.status,
        )
    if not _is_success(res.status):
        raise OAuthHttpError(
            status_code=res.status,
            response_body=res.body.decode("utf-8", "replace")[:512],
            headers=dict(res.headers),
            operation=operation,
        )
    if not isinstance(data, dict):
        raise _invalid_response(
            operation, "The response body is not a JSON object", res.status
        )
    return data


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def parse_device_authorization_http_response(
    res: HttpResponse, *, received_at: datetime
) -> DeviceAuthorization:
    """Parse the device authorization response (RFC 8628 Section 3.2).

    ``expires_at`` is ``received_at`` plus ``expires_in``. ``interval`` is 5
    when the response omits it.

    Raises:
        DeviceAuthorizationError: An OAuth error response, or a response
            missing ``device_code``, ``user_code``, ``verification_uri``, or
            ``expires_in``, or with one of them or ``interval`` of the wrong
            type or range (``invalid_response``).
        OAuthHttpError: A status other than 2xx without an OAuth error
            body.
    """
    operation = DEVICE_AUTHORIZATION_OPERATION
    data = _json_object(res, operation)

    missing = [
        name
        for name in (*_REQUIRED_STRINGS, "expires_in")
        if data.get(name) in (None, "")
    ]
    if missing:
        raise _invalid_response(
            operation,
            f"Device authorization response is missing {', '.join(missing)}",
            res.status,
        )
    invalid = [name for name in _REQUIRED_STRINGS if not isinstance(data[name], str)]
    if not _is_positive_int(data["expires_in"]):
        invalid.append("expires_in")
    if invalid:
        raise _invalid_response(
            operation,
            f"Device authorization response has an invalid {', '.join(invalid)}",
            res.status,
        )

    interval = data.get("interval")
    if interval is None:
        interval = DEFAULT_POLL_INTERVAL
    elif not _is_positive_int(interval):
        raise _invalid_response(
            operation,
            "Device authorization response has an invalid interval",
            res.status,
        )

    try:
        expires_at = received_at + timedelta(seconds=data["expires_in"])
    except OverflowError as e:
        raise _invalid_response(
            operation,
            "Device authorization response has an invalid expires_in",
            res.status,
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


def parse_device_token_http_response(res: HttpResponse) -> TokenResponse:
    """Parse the token endpoint's answer to one device token poll.

    Returns ``refresh_token`` and ``id_token`` as received. ``raw`` keeps
    the body, including any ``resource`` field.

    Raises:
        DeviceAuthorizationError: An OAuth error response, including
            ``authorization_pending`` and ``slow_down``, or a malformed
            response (``invalid_response``).
        OAuthHttpError: A status other than 2xx without an OAuth error
            body.
    """
    operation = DEVICE_TOKEN_OPERATION
    data = _json_object(res, operation)
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise _invalid_response(
            operation, "Device token response is missing access_token", res.status
        )
    return token_response_from_json(data, res.headers)
