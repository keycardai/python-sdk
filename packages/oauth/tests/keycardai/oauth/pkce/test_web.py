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
from keycardai.oauth.types.models import (
    AuthorizationServerMetadata,
    Endpoints,
    TokenResponse,
)
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
        resources=["https://api.example.com", "https://files.example.com"],
    )

    assert isinstance(result, AuthorizationRedirect)
    assert result.state
    assert result.code_verifier
    assert result.resources == [
        "https://api.example.com",
        "https://files.example.com",
    ]
    params = parse_qs(urlsplit(result.url).query)
    assert params["state"] == [result.state]
    assert params["code_challenge"] == [
        PKCEGenerator.generate_code_challenge(result.code_verifier)
    ]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid profile"]
    assert params["resource"] == [
        "https://api.example.com",
        "https://files.example.com",
    ]
    assert captured["issuer"] == "https://auth.example.com"


@pytest.mark.asyncio
async def test_begin_rejects_removed_resource_url():
    with pytest.raises(TypeError, match="resource_url"):
        await begin_authorization(
            client_id="my-app",
            issuer="https://auth.example.com",
            redirect_uri="https://app.example.com/callback",
            resource_url="https://api.example.com",
        )


@pytest.mark.asyncio
async def test_begin_without_resources_sends_no_resource_parameter(monkeypatch):
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(),
    )

    result = await begin_authorization(
        client_id="my-app",
        issuer="https://auth.example.com",
        redirect_uri="https://app.example.com/callback",
    )

    assert result.resources is None
    assert "resource" not in parse_qs(urlsplit(result.url).query)


@pytest.mark.asyncio
async def test_begin_uses_metadata_without_constructing_client(monkeypatch):
    async_client = MagicMock()
    monkeypatch.setattr("keycardai.oauth.pkce.web.AsyncClient", async_client)
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )

    result = await begin_authorization(
        client_id="my-app",
        redirect_uri="https://app.example.com/callback",
        metadata=metadata,
        scopes=["openid"],
    )

    assert urlsplit(result.url).netloc == "auth.example.com"
    assert urlsplit(result.url).path == "/authorize"
    assert parse_qs(urlsplit(result.url).query)["scope"] == ["openid"]
    async_client.assert_not_called()


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
    )

    assert result is token
    assert captured["exchange_kwargs"] == {
        "code": "auth-code",
        "redirect_uri": "https://app.example.com/callback",
        "code_verifier": "stored-verifier",
        "client_id": "my-app",
    }


@pytest.mark.asyncio
async def test_complete_rejects_removed_resource_url():
    with pytest.raises(TypeError, match="resource_url"):
        await complete_authorization(
            callback_params={"code": "auth-code", "state": "stored-state"},
            state="stored-state",
            code_verifier="stored-verifier",
            client_id="my-app",
            issuer="https://auth.example.com",
            redirect_uri="https://app.example.com/callback",
            resource_url="https://api.example.com",
        )


@pytest.mark.asyncio
async def test_complete_uses_metadata_without_discovery(monkeypatch):
    captured = {}
    token = TokenResponse(access_token="token")
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(captured=captured, exchange_response=token),
    )
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )

    result = await complete_authorization(
        callback_params={"code": "auth-code", "state": "stored-state"},
        state="stored-state",
        code_verifier="stored-verifier",
        client_id="my-app",
        redirect_uri="https://app.example.com/callback",
        metadata=metadata,
    )

    assert result is token
    assert captured["config"].enable_metadata_discovery is False
    assert captured["endpoints"] == Endpoints(token=metadata.token_endpoint)
    captured["get_endpoints"].assert_not_awaited()


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
        resources=["https://api.example.com"],
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
async def test_begin_challenge_mode_requires_resources():
    with pytest.raises(ConfigError, match="resources"):
        await begin_authorization(
            client_id="my-app",
            redirect_uri="https://app.example.com/callback",
            www_authenticate_header=WWW_AUTHENTICATE,
        )


@pytest.mark.asyncio
async def test_begin_rejects_missing_authorization_endpoint(monkeypatch):
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(
            endpoint_result=MagicMock(
                authorize=None, token="https://auth.example.com/token"
            )
        ),
    )

    with pytest.raises(ValueError, match="authorization_endpoint"):
        await begin_authorization(
            client_id="my-app",
            redirect_uri="https://app.example.com/callback",
            issuer="https://auth.example.com",
        )


@pytest.mark.asyncio
async def test_complete_rejects_missing_token_endpoint(monkeypatch):
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(
            endpoint_result=MagicMock(
                authorize="https://auth.example.com/authorize", token=None
            )
        ),
    )

    with pytest.raises(ValueError, match="token_endpoint"):
        await complete_authorization(
            callback_params={"code": "code", "state": "state"},
            state="state",
            code_verifier="verifier",
            client_id="my-app",
            redirect_uri="https://app.example.com/callback",
            issuer="https://auth.example.com",
        )


@pytest.mark.asyncio
async def test_begin_rejects_metadata_without_authorization_endpoint():
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint=None,
        token_endpoint="https://auth.example.com/token",
    )

    with pytest.raises(ValueError, match="authorization_endpoint"):
        await begin_authorization(
            client_id="my-app",
            redirect_uri="https://app.example.com/callback",
            metadata=metadata,
        )


@pytest.mark.asyncio
async def test_complete_rejects_metadata_without_token_endpoint():
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint=None,
    )

    with pytest.raises(ValueError, match="token_endpoint"):
        await complete_authorization(
            callback_params={"code": "code", "state": "state"},
            state="state",
            code_verifier="verifier",
            client_id="my-app",
            redirect_uri="https://app.example.com/callback",
            metadata=metadata,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function, base_kwargs",
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
@pytest.mark.parametrize(
    "entry_kwargs",
    [
        {"issuer": "https://auth.example.com"},
        {"www_authenticate_header": WWW_AUTHENTICATE},
    ],
)
async def test_metadata_cannot_be_combined_with_other_entry_modes(
    function, base_kwargs, entry_kwargs
):
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )

    with pytest.raises(ConfigError, match="exactly one"):
        await function(
            **base_kwargs,
            metadata=metadata,
            **entry_kwargs,
        )


def _async_client_factory(
    *,
    captured: dict | None = None,
    exchange_response: TokenResponse | None = None,
    exchange: AsyncMock | None = None,
    endpoint_result: MagicMock | None = None,
):
    def factory(issuer=None, *, auth, config, endpoints=None):
        if captured is not None:
            captured["issuer"] = issuer
            captured["auth"] = auth
            captured["config"] = config
            captured["endpoints"] = endpoints
        instance = MagicMock()
        instance.get_endpoints = AsyncMock(
            return_value=endpoint_result
            or MagicMock(
                authorize="https://auth.example.com/authorize",
                token="https://auth.example.com/token",
            )
        )
        if captured is not None:
            captured["get_endpoints"] = instance.get_endpoints
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
