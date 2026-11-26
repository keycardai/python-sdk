"""Token endpoint retryability from base.md and token-exchange.md rows 14 and 22."""

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
from keycardai.oauth.operations._token_exchange import (
    parse_token_exchange_http_response,
)

TOKEN_RESPONSE_PARSERS = [
    pytest.param(parse_token_exchange_http_response, id="token-exchange"),
    pytest.param(parse_client_credentials_http_response, id="client-credentials"),
    pytest.param(parse_refresh_token_http_response, id="refresh"),
    pytest.param(parse_authorization_code_http_response, id="authorization-code"),
]


@pytest.mark.parametrize("parse", TOKEN_RESPONSE_PARSERS)
@pytest.mark.parametrize("code", sorted(PERMANENT_ERROR_CODES))
@pytest.mark.parametrize("status", [429, 503])
def test_row_22_transient_http_status_takes_precedence_over_permanent_code(
    parse, code, status
):
    response = HttpResponse(
        status=status, headers={}, body=json.dumps({"error": code}).encode()
    )

    with pytest.raises(OAuthProtocolError) as exc:
        parse(response)

    assert exc.value.error == code
    assert exc.value.retryable is True
    assert exc.value.status_code == status


@pytest.mark.parametrize("parse", TOKEN_RESPONSE_PARSERS)
def test_row_14_permanent_code_on_400_is_not_retryable(parse):
    response = HttpResponse(status=400, headers={}, body=b'{"error":"access_denied"}')

    with pytest.raises(OAuthProtocolError) as exc:
        parse(response)

    assert exc.value.retryable is False
    assert exc.value.status_code == 400


@pytest.mark.parametrize("status", [429, 503])
def test_row_22_transient_http_status_takes_precedence_over_invalid_grant(status):
    response = HttpResponse(status=status, headers={}, body=b'{"error":"invalid_grant"}')

    with pytest.raises(RefreshGrantError) as exc:
        parse_refresh_token_http_response(response)

    assert exc.value.retryable is True
    assert exc.value.status_code == status


@pytest.mark.parametrize("parse", TOKEN_RESPONSE_PARSERS)
@pytest.mark.parametrize("body", [b"not json", b"{}"], ids=["invalid-json", "no-token"])
def test_row_16_malformed_2xx_token_response_keeps_status_and_is_retryable(parse, body):
    response = HttpResponse(status=200, headers={}, body=body)

    with pytest.raises(OAuthProtocolError) as exc:
        parse(response)

    assert exc.value.error == "invalid_response"
    assert exc.value.retryable is True
    assert exc.value.status_code == 200
