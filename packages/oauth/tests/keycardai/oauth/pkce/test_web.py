"""Tests for the stateless web-app authorization-code flow."""

from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from keycardai.oauth.exceptions import (
    AuthorizationDeniedError,
    ConfigError,
    OAuthProtocolError,
    StateMismatchError,
)
from keycardai.oauth.http.auth import BasicAuth, NoneAuth
from keycardai.oauth.pkce import (
    AuthorizationRedirect,
    begin_authorization,
    complete_authorization,
)
from keycardai.oauth.types.models import TokenResponse
from keycardai.oauth.utils.pkce import PKCEGenerator

WWW_AUTHENTICATE = (
    'Bearer resource_metadata="https://api.example.com/'
    '.well-known/oauth-protected-resource"'
)


@pytest.mark.asyncio
async def test_begin_returns_redirect_and_pkce_values(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(captured=captured),
    )

    result = await begin_authorization(
        client_id="my-app",
        issuer="https://auth.example.com",
        redirect_uri="https://app.example.com/callback",
        scopes=["openid", "profile"],
        resource_url="https://api.example.com",
    )

    assert isinstance(result, AuthorizationRedirect)
    assert result.state
    assert result.code_verifier
    params = parse_qs(urlsplit(result.url).query)
    assert params["state"] == [result.state]
    assert params["code_challenge"] == [
        PKCEGenerator.generate_code_challenge(result.code_verifier)
    ]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid profile"]
    assert params["resource"] == ["https://api.example.com"]
    assert captured["issuer"] == "https://auth.example.com"


@pytest.mark.asyncio
async def test_complete_exchanges_matching_state(monkeypatch):
    captured = {}
    token = TokenResponse(access_token="token")
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(captured=captured, exchange_response=token),
    )

    result = await complete_authorization(
        callback_params={"code": "auth-code", "state": "stored-state"},
        state="stored-state",
        code_verifier="stored-verifier",
        client_id="my-app",
        issuer="https://auth.example.com",
        redirect_uri="https://app.example.com/callback",
        resource_url="https://api.example.com",
    )

    assert result is token
    assert captured["exchange_kwargs"] == {
        "code": "auth-code",
        "redirect_uri": "https://app.example.com/callback",
        "code_verifier": "stored-verifier",
        "client_id": "my-app",
        "resource": "https://api.example.com",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_params",
    [{"code": "auth-code", "state": "wrong-state"}, {"code": "auth-code"}],
)
async def test_complete_rejects_wrong_or_missing_state_without_exchange(
    monkeypatch, callback_params
):
    exchange = AsyncMock()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(exchange=exchange),
    )

    with pytest.raises(StateMismatchError):
        await complete_authorization(
            callback_params=callback_params,
            state="stored-state",
            code_verifier="stored-verifier",
            client_id="my-app",
            issuer="https://auth.example.com",
            redirect_uri="https://app.example.com/callback",
        )

    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_rejects_non_ascii_callback_state_without_exchange(monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(exchange=exchange),
    )

    with pytest.raises(StateMismatchError):
        await complete_authorization(
            callback_params={"code": "auth-code", "state": "attacker-☃"},
            state="stored-state",
            code_verifier="stored-verifier",
            client_id="my-app",
            issuer="https://auth.example.com",
            redirect_uri="https://app.example.com/callback",
        )

    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_surfaces_authorization_denial_without_exchange(monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(exchange=exchange),
    )

    with pytest.raises(AuthorizationDeniedError) as error:
        await complete_authorization(
            callback_params={
                "error": "access_denied",
                "error_description": "The user declined",
            },
            state="stored-state",
            code_verifier="stored-verifier",
            client_id="my-app",
            issuer="https://auth.example.com",
            redirect_uri="https://app.example.com/callback",
        )

    assert error.value.error == "access_denied"
    assert error.value.error_description == "The user declined"
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_rejects_missing_code_without_exchange(monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(exchange=exchange),
    )

    with pytest.raises(OAuthProtocolError) as error:
        await complete_authorization(
            callback_params={"state": "stored-state"},
            state="stored-state",
            code_verifier="stored-verifier",
            client_id="my-app",
            issuer="https://auth.example.com",
            redirect_uri="https://app.example.com/callback",
        )

    assert error.value.error == "invalid_request"
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_uses_basic_auth_for_confidential_client(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(
            captured=captured, exchange_response=TokenResponse(access_token="token")
        ),
    )

    await complete_authorization(
        callback_params={"code": "code", "state": "state"},
        state="state",
        code_verifier="verifier",
        client_id="my-app",
        client_secret="secret",
        issuer="https://auth.example.com",
        redirect_uri="https://app.example.com/callback",
    )

    assert isinstance(captured["auth"], BasicAuth)
    assert captured["auth"].client_id == "my-app"
    assert captured["auth"].client_secret == "secret"


@pytest.mark.asyncio
async def test_complete_uses_none_auth_for_public_client(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(
            captured=captured, exchange_response=TokenResponse(access_token="token")
        ),
    )

    await complete_authorization(
        callback_params={"code": "code", "state": "state"},
        state="state",
        code_verifier="verifier",
        client_id="my-app",
        issuer="https://auth.example.com",
        redirect_uri="https://app.example.com/callback",
    )

    assert isinstance(captured["auth"], NoneAuth)


@pytest.mark.asyncio
async def test_begin_resolves_issuer_from_challenge(monkeypatch):
    captured = {}
    http_client = _http_client_mock(
        [{"authorization_servers": ["https://auth.example.com/"]}]
    )
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(captured=captured),
    )

    await begin_authorization(
        client_id="my-app",
        redirect_uri="https://app.example.com/callback",
        resource_url="https://api.example.com",
        www_authenticate_header=WWW_AUTHENTICATE,
        http_client=http_client,
    )

    assert captured["issuer"] == "https://auth.example.com"
    http_client.get.assert_awaited_once_with(
        "https://api.example.com/.well-known/oauth-protected-resource"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function,kwargs",
    [
        (
            begin_authorization,
            {
                "client_id": "my-app",
                "redirect_uri": "https://app.example.com/callback",
            },
        ),
        (
            complete_authorization,
            {
                "callback_params": {"code": "code", "state": "state"},
                "state": "state",
                "code_verifier": "verifier",
                "client_id": "my-app",
                "redirect_uri": "https://app.example.com/callback",
            },
        ),
    ],
)
async def test_flow_requires_exactly_one_issuer_entry(function, kwargs):
    with pytest.raises(ConfigError, match="exactly one"):
        await function(**kwargs)

    with pytest.raises(ConfigError, match="exactly one"):
        await function(
            **kwargs,
            issuer="https://auth.example.com",
            www_authenticate_header=WWW_AUTHENTICATE,
        )


@pytest.mark.asyncio
async def test_begin_challenge_mode_requires_resource_url():
    with pytest.raises(ConfigError, match="resource_url"):
        await begin_authorization(
            client_id="my-app",
            redirect_uri="https://app.example.com/callback",
            www_authenticate_header=WWW_AUTHENTICATE,
        )


def _async_client_factory(
    *,
    captured: dict | None = None,
    exchange_response: TokenResponse | None = None,
    exchange: AsyncMock | None = None,
):
    def factory(issuer=None, *, auth, config):
        if captured is not None:
            captured["issuer"] = issuer
            captured["auth"] = auth
            captured["config"] = config
        instance = MagicMock()
        instance.get_endpoints = AsyncMock(
            return_value=MagicMock(
                authorize="https://auth.example.com/authorize",
                token="https://auth.example.com/token",
            )
        )
        instance.exchange_authorization_code = (
            exchange
            if exchange is not None
            else AsyncMock(return_value=exchange_response)
        )
        if captured is not None:
            original_exchange = instance.exchange_authorization_code

            async def capture_exchange(**kwargs):
                captured["exchange_kwargs"] = kwargs
                return await original_exchange(**kwargs)

            instance.exchange_authorization_code = capture_exchange
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        return instance

    return factory


def _mock_json_response(body: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = body
    response.raise_for_status.return_value = None
    return response


def _http_client_mock(json_bodies: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.get = AsyncMock(
        side_effect=[_mock_json_response(body) for body in json_bodies]
    )
    return mock
