"""Unit tests for exchange_tokens_for_resources, focused on request_scopes."""

from unittest.mock import AsyncMock

import pytest

from keycardai.oauth.exceptions import (
    AuthenticationError,
    ConfigError,
    NetworkError,
    OAuthHttpError,
    OAuthProtocolError,
)
from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.token_exchange import (
    error_retryable,
    exchange_tokens_for_resources,
)
from keycardai.oauth.types.models import TokenExchangeRequest, TokenResponse


def _capturing_client():
    """Return (client, captured) where captured records exchange/impersonate calls."""
    captured: dict[str, object] = {"exchange": {}, "impersonate": []}

    async def capturing_exchange(request: TokenExchangeRequest):
        captured["exchange"][request.resource] = request
        return TokenResponse(
            access_token="exchanged", token_type="Bearer", expires_in=3600
        )

    async def capturing_impersonate(*, user_identifier, resource, scope=None, **kwargs):
        captured["impersonate"].append(
            {"user_identifier": user_identifier, "resource": resource, "scope": scope}
        )
        return TokenResponse(
            access_token="impersonated", token_type="Bearer", expires_in=3600
        )

    client = AsyncMock()
    client.exchange_token.side_effect = capturing_exchange
    client.impersonate.side_effect = capturing_impersonate
    return client, captured


@pytest.mark.asyncio
async def test_basic_exchange_forwards_string_scope():
    client, captured = _capturing_client()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
        request_scopes="read",
    )
    assert (
        captured["exchange"]["https://api.example.com"].scope
        == "read"
    )


@pytest.mark.asyncio
async def test_basic_exchange_forwards_list_scope():
    client, captured = _capturing_client()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
        request_scopes=["read", "write"],
    )
    assert captured["exchange"]["https://api.example.com"].scope == "read write"


@pytest.mark.asyncio
async def test_per_resource_scopes_dict():
    client, captured = _capturing_client()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api1.example.com", "https://api2.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
        request_scopes={"https://api1.example.com": "read"},
    )
    assert captured["exchange"]["https://api1.example.com"].scope == "read"
    assert captured["exchange"]["https://api2.example.com"].scope is None


@pytest.mark.asyncio
async def test_no_scope_default():
    client, captured = _capturing_client()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
    )
    assert captured["exchange"]["https://api.example.com"].scope is None


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_scope", ["", []])
async def test_basic_exchange_treats_empty_scope_as_absent(empty_scope):
    client, captured = _capturing_client()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
        request_scopes=empty_scope,
    )
    assert captured["exchange"]["https://api.example.com"].scope is None


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_scope", ["", []])
async def test_impersonation_treats_empty_scope_as_absent(empty_scope):
    client, captured = _capturing_client()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
        user_identifier="user@example.com",
        request_scopes=empty_scope,
    )
    assert captured["impersonate"] == [
        {
            "user_identifier": "user@example.com",
            "resource": "https://api.example.com",
            "scope": None,
        }
    ]


@pytest.mark.asyncio
async def test_impersonation_forwards_scope():
    client, captured = _capturing_client()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
        user_identifier="user@example.com",
        request_scopes="read",
    )
    assert captured["impersonate"] == [
        {
            "user_identifier": "user@example.com",
            "resource": "https://api.example.com",
            "scope": "read",
        }
    ]


@pytest.mark.asyncio
async def test_application_credential_sets_scope():
    client, captured = _capturing_client()

    class FakeCredential:
        async def prepare_token_exchange_request(
            self, *, client, subject_token, resource, auth_info=None
        ):
            return TokenExchangeRequest(
                subject_token=subject_token,
                resource=resource,
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            )

    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="subject",
        access_context=AccessContext(),
        application_credential=FakeCredential(),
        request_scopes="read",
    )
    assert captured["exchange"]["https://api.example.com"].scope == "read"


# --- retryable classification recorded in the error dict ---------------------


async def _capture_failure(error: Exception) -> dict[str, str | bool]:
    client = AsyncMock()
    client.exchange_token.side_effect = error
    ctx = AccessContext()
    await exchange_tokens_for_resources(
        client=client,
        resources=["https://api.example.com"],
        subject_token="tok",
        access_context=ctx,
    )
    recorded = ctx.get_resource_error("https://api.example.com")
    assert recorded is not None
    return recorded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(
            OAuthProtocolError(error="access_denied"), False, id="access_denied"
        ),
        pytest.param(
            OAuthProtocolError(error="invalid_response"), True, id="invalid_response"
        ),
        pytest.param(NetworkError("dns failed"), True, id="network"),
        pytest.param(OAuthHttpError(status_code=503), True, id="http_503"),
        pytest.param(OAuthHttpError(status_code=401), False, id="http_401"),
        pytest.param(ConfigError("no endpoint"), False, id="config"),
        pytest.param(ValueError("unexpected"), True, id="plain_exception_default"),
    ],
)
async def test_error_dict_carries_retryable_from_the_exception(error, expected):
    recorded = await _capture_failure(error)
    assert recorded["retryable"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_http_4xx_with_non_oauth_body_is_permanent(status):
    # Historically consumers retried these; the typed property classifies them
    # permanent from the status and the dict now carries that decision.
    recorded = await _capture_failure(
        OAuthHttpError(status_code=status, response_body="<html>nope</html>")
    )
    assert recorded["retryable"] is False
    assert "code" not in recorded


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConfigError("bad"), AuthenticationError("bad")])
async def test_config_and_authentication_errors_are_permanent(error):
    recorded = await _capture_failure(error)
    assert recorded["retryable"] is False


def test_error_retryable_defaults_true_without_the_property():
    assert error_retryable(RuntimeError("boom")) is True
    assert error_retryable(OAuthProtocolError(error="invalid_client")) is False


def test_set_error_accepts_plain_string_dicts():
    ctx = AccessContext()
    legacy: dict[str, str] = {"message": "boom", "code": "missing_identity"}
    ctx.set_error(legacy)
    ctx.set_resource_error("https://api.example.com", legacy)
    assert ctx.get_error() == legacy
    assert ctx.get_resource_error("https://api.example.com") == legacy
