"""Base spec, Retryability: the HTTP status decides before the OAuth code.

A 429 or 5xx with an OAuth error body is retryable whatever its code, so a
``503 access_denied`` is retryable. On any other status the code decides.
Token exchange unit rows 14 and 22.
"""

import json

import pytest

from keycardai.oauth.exceptions import (
    PERMANENT_ERROR_CODES,
    OAuthProtocolError,
    RefreshGrantError,
)
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.operations._authorize import (
    parse_authorization_code_http_response,
    parse_refresh_token_http_response,
)
from keycardai.oauth.operations._client_credentials import (
    parse_client_credentials_http_response,
)
from keycardai.oauth.operations._registration import (
    parse_client_registration_http_response,
)
from keycardai.oauth.operations._token_exchange import (
    parse_token_exchange_http_response,
)

PARSERS = [
    pytest.param(parse_token_exchange_http_response, OAuthProtocolError, id="exchange"),
    pytest.param(
        parse_client_credentials_http_response,
        OAuthProtocolError,
        id="client_credentials",
    ),
    pytest.param(parse_refresh_token_http_response, RefreshGrantError, id="refresh"),
    pytest.param(
        parse_authorization_code_http_response,
        OAuthProtocolError,
        id="authorization_code",
    ),
    pytest.param(
        parse_client_registration_http_response,
        OAuthProtocolError,
        id="registration",
    ),
]

# invalid_grant is permanent on the refresh grant, so it is covered here too.
NON_RETRYABLE_ON_4XX = sorted(PERMANENT_ERROR_CODES | {"invalid_grant"})


def _error_response(status: int, code: str) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps({"error": code}).encode(),
    )


def _raise(parse, status: int, code: str, error_cls) -> OAuthProtocolError:
    with pytest.raises(error_cls) as exc:
        parse(_error_response(status, code))
    return exc.value


@pytest.mark.parametrize(("parse", "error_cls"), PARSERS)
@pytest.mark.parametrize("status", [429, 500, 502, 503])
@pytest.mark.parametrize("code", NON_RETRYABLE_ON_4XX)
def test_429_and_5xx_are_retryable_whatever_the_code(parse, error_cls, status, code):
    error = _raise(parse, status, code, error_cls)
    assert type(error) is error_cls
    assert error.error == code
    assert error.status_code == status
    assert error.retryable is True


@pytest.mark.parametrize(("parse", "error_cls"), PARSERS)
@pytest.mark.parametrize("code", sorted(PERMANENT_ERROR_CODES))
def test_permanent_codes_on_4xx_are_not_retryable(parse, error_cls, code):
    status = 401 if code == "invalid_client" else 400
    error = _raise(parse, status, code, error_cls)
    assert error.status_code == status
    assert error.retryable is False


@pytest.mark.parametrize(("parse", "error_cls"), PARSERS)
def test_other_codes_on_4xx_follow_the_code(parse, error_cls):
    error = _raise(parse, 400, "invalid_request", error_cls)
    assert error.status_code == 400
    assert error.retryable is True


@pytest.mark.parametrize(
    "parse",
    [parse_token_exchange_http_response, parse_client_credentials_http_response],
)
@pytest.mark.parametrize("code", sorted(PERMANENT_ERROR_CODES))
def test_error_body_on_2xx_keeps_the_status_and_follows_the_code(parse, code):
    error = _raise(parse, 200, code, OAuthProtocolError)
    assert error.status_code == 200
    assert error.retryable is False
