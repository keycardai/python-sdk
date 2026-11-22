"""Tests for the stateless web-app authorization-code flow."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from keycardai.oauth import AsyncClient
from keycardai.oauth.exceptions import (
    AuthorizationDeniedError,
    ConfigError,
    OAuthProtocolError,
    RefreshGrantError,
    StateMismatchError,
)
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.http.auth import BasicAuth, NoneAuth
from keycardai.oauth.pkce import (
    AuthorizationRedirect,
    begin_authorization,
    complete_authorization,
    refresh_authorization,
)
from keycardai.oauth.server.credentials import (
    ClientSecret,
    WebIdentity,
    WorkloadIdentity,
)
from keycardai.oauth.server.exceptions import WorkloadIdentityRuntimeError
from keycardai.oauth.types.models import (
    AuthorizationServerMetadata,
    ClientConfig,
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
        "credential": None,
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
        (
            refresh_authorization,
            {"refresh_token": "rt", "client_id": "my-app"},
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


@pytest.mark.asyncio
async def test_refresh_public_client_sends_client_id_with_none_auth(monkeypatch):
    captured = {}
    token = TokenResponse(access_token="new", refresh_token="rt2")
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(captured=captured, exchange_response=token),
    )

    result = await refresh_authorization(
        refresh_token="rt1",
        client_id="my-app",
        issuer="https://auth.example.com",
        resources=["https://api.example.com", "https://files.example.com"],
        scopes=["read", "write"],
    )

    assert result is token
    assert result.refresh_token == "rt2"
    assert isinstance(captured["auth"], NoneAuth)
    assert captured["issuer"] == "https://auth.example.com"
    assert captured["refresh_kwargs"] == {
        "refresh_token": "rt1",
        "client_id": "my-app",
        "credential": None,
        "resources": ["https://api.example.com", "https://files.example.com"],
        "scopes": ["read", "write"],
    }


@pytest.mark.asyncio
async def test_refresh_confidential_client_uses_basic_and_no_body_client_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(
            captured=captured, exchange_response=TokenResponse(access_token="new")
        ),
    )

    result = await refresh_authorization(
        refresh_token="rt1",
        client_id="my-app",
        client_secret="secret",
        issuer="https://auth.example.com",
    )

    assert result.refresh_token is None
    assert isinstance(captured["auth"], BasicAuth)
    assert captured["auth"].client_id == "my-app"
    assert captured["refresh_kwargs"]["client_id"] is None


@pytest.mark.asyncio
async def test_refresh_uses_metadata_without_discovery(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(
            captured=captured, exchange_response=TokenResponse(access_token="new")
        ),
    )
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )

    await refresh_authorization(refresh_token="rt1", client_id="my-app", metadata=metadata)

    assert captured["endpoints"] == Endpoints(token="https://auth.example.com/token")
    assert captured["config"].enable_metadata_discovery is False
    captured["get_endpoints"].assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_endpoint",
    [pytest.param(None, id="absent"), pytest.param("", id="empty")],
)
async def test_refresh_rejects_metadata_without_token_endpoint(monkeypatch, token_endpoint):
    """Base spec: no convention-derived endpoint. An empty token_endpoint is
    missing too, so the refresh token is never sent to a guessed path."""
    captured: dict = {}
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient", _async_client_factory(captured=captured)
    )
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint=token_endpoint,
    )

    with pytest.raises(
        ConfigError,
        match="^Authorization server https://auth.example.com does not advertise "
        "a token_endpoint$",
    ):
        await refresh_authorization(refresh_token="rt1", client_id="my-app", metadata=metadata)

    assert captured == {}


@pytest.mark.asyncio
async def test_refresh_surfaces_invalid_grant_as_non_retryable(monkeypatch):
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        _async_client_factory(
            exchange=AsyncMock(
                side_effect=RefreshGrantError(error="invalid_grant", operation="refresh")
            )
        ),
    )
    with pytest.raises(RefreshGrantError) as exc:
        await refresh_authorization(
            refresh_token="rt1", client_id="my-app", issuer="https://auth.example.com"
        )
    assert exc.value.retryable is False


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
        instance.refresh_token_grant = (
            exchange if exchange is not None else AsyncMock(return_value=exchange_response)
        )
        if captured is not None:
            original_refresh = instance.refresh_token_grant

            async def capture_refresh(**kwargs):
                captured["refresh_kwargs"] = kwargs
                return await original_refresh(**kwargs)

            instance.refresh_token_grant = capture_refresh
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


# ---------------------------------------------------------------------------
# Code exchange with a credential (spec-version 6)
# ---------------------------------------------------------------------------

TOKEN_ENDPOINT = "https://auth.example.com/token"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _token_transport() -> AsyncMock:
    transport = AsyncMock()
    transport.request_raw.return_value = HttpResponse(
        status=200,
        headers={"Content-Type": "application/json"},
        body=b'{"access_token":"user-token","token_type":"Bearer"}',
    )
    return transport


def _sent_form(transport: AsyncMock) -> dict[str, list[str]]:
    return parse_qs(transport.request_raw.call_args.args[0].body.decode())


def _wire_client(transport: AsyncMock) -> AsyncClient:
    return AsyncClient(
        "https://auth.example.com",
        transport=transport,
        endpoints=Endpoints(token=TOKEN_ENDPOINT),
        config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False),
    )


@pytest.mark.asyncio
async def test_exchange_with_credential_sends_credential_fields():
    """Spec row 17."""
    transport = _token_transport()
    credential = WorkloadIdentity(lambda: "platform-token", client_id="acr_123")

    result = await _wire_client(transport).exchange_authorization_code(
        code="code",
        redirect_uri="https://app.example.com/callback",
        code_verifier="verifier",
        credential=credential,
    )

    assert result.access_token == "user-token"
    request = transport.request_raw.call_args.args[0]
    assert "Authorization" not in request.headers
    assert _sent_form(transport) == {
        "grant_type": ["authorization_code"],
        "code": ["code"],
        "redirect_uri": ["https://app.example.com/callback"],
        "code_verifier": ["verifier"],
        "client_assertion": ["platform-token"],
        "client_assertion_type": [JWT_BEARER],
        "client_id": ["acr_123"],
    }


@pytest.mark.asyncio
async def test_exchange_with_client_secret_credential_uses_its_headers():
    """Spec row 17: the headers are the credential's."""
    transport = _token_transport()

    await _wire_client(transport).exchange_authorization_code(
        code="code",
        redirect_uri="https://app.example.com/callback",
        code_verifier="verifier",
        credential=ClientSecret(("my-app", "secret")),
    )

    request = transport.request_raw.call_args.args[0]
    assert request.headers["Authorization"] == BasicAuth("my-app", "secret").apply_headers()["Authorization"]
    assert "client_id" not in _sent_form(transport)
    assert "subject_token_type" not in _sent_form(transport)


class _NoClientAuthentication:
    async def prepare_token_exchange_request(self, *args, **kwargs):
        raise AssertionError("not called")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"credential": WorkloadIdentity(lambda: "t"), "client_id": "my-app"},
        {"credential": _NoClientAuthentication()},
        {"credential": WorkloadIdentity(lambda: "t")},
    ],
)
async def test_exchange_rejects_invalid_credential_config(kwargs):
    """Spec row 18 for the code-exchange building block."""
    transport = _token_transport()

    with pytest.raises(ConfigError):
        await _wire_client(transport).exchange_authorization_code(
            code="code",
            redirect_uri="https://app.example.com/callback",
            code_verifier="verifier",
            **kwargs,
        )

    transport.request_raw.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"credential": WorkloadIdentity(lambda: "t"), "client_id": "my-app"},
        {"credential": WorkloadIdentity(lambda: "t"), "client_secret": "secret"},
        {"credential": _NoClientAuthentication()},
        {"credential": WorkloadIdentity(lambda: "t")},
        {},
    ],
)
async def test_complete_rejects_invalid_client_config(monkeypatch, kwargs):
    """Spec row 18 for web-app complete; no client authentication is also rejected."""
    async_client = MagicMock()
    monkeypatch.setattr("keycardai.oauth.pkce.web.AsyncClient", async_client)

    with pytest.raises(ConfigError):
        await complete_authorization(
            callback_params={"code": "code", "state": "state"},
            state="state",
            code_verifier="verifier",
            issuer="https://auth.example.com",
            redirect_uri="https://app.example.com/callback",
            **kwargs,
        )

    async_client.assert_not_called()


@pytest.mark.asyncio
async def test_exchange_with_web_identity_signs_for_token_endpoint(tmp_path):
    """Spec row 20."""
    transport = _token_transport()
    credential = WebIdentity(
        server_name="app", storage_dir=str(tmp_path), client_id="https://app.example.com"
    )

    await _wire_client(transport).exchange_authorization_code(
        code="code",
        redirect_uri="https://app.example.com/callback",
        code_verifier="verifier",
        credential=credential,
    )

    request = transport.request_raw.call_args.args[0]
    assert "Authorization" not in request.headers
    form = _sent_form(transport)
    assert form["client_assertion_type"] == [JWT_BEARER]
    payload = form["client_assertion"][0].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["aud"] == TOKEN_ENDPOINT
    assert claims["iss"] == claims["sub"] == "https://app.example.com"


@pytest.mark.asyncio
async def test_exchange_returns_credential_error_without_request():
    """Spec row 21."""
    transport = _token_transport()
    failure = WorkloadIdentityRuntimeError("token file is empty", source="file")

    def failing_source() -> str:
        raise failure

    with pytest.raises(WorkloadIdentityRuntimeError) as error:
        await _wire_client(transport).exchange_authorization_code(
            code="code",
            redirect_uri="https://app.example.com/callback",
            code_verifier="verifier",
            credential=WorkloadIdentity(failing_source, client_id="acr_123"),
        )

    assert error.value is failure
    transport.request_raw.assert_not_called()


@pytest.mark.asyncio
async def test_begin_then_complete_with_workload_identity(monkeypatch):
    """Spec rows 19 and 22: one client_id on the authorize URL and the exchange."""
    transport = _token_transport()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        lambda *args, **kwargs: AsyncClient(*args, transport=transport, **kwargs),
    )
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint=TOKEN_ENDPOINT,
    )
    credential = WorkloadIdentity(lambda: "platform-token", client_id="acr_123")

    redirect = await begin_authorization(
        client_id=credential.client_id,
        redirect_uri="https://app.example.com/callback",
        metadata=metadata,
    )
    authorize_params = parse_qs(urlsplit(redirect.url).query)
    assert authorize_params["client_id"] == ["acr_123"]
    assert "client_assertion" not in authorize_params

    result = await complete_authorization(
        callback_params={"code": "code", "state": redirect.state},
        state=redirect.state,
        code_verifier=redirect.code_verifier,
        redirect_uri="https://app.example.com/callback",
        metadata=metadata,
        credential=credential,
    )

    assert result.access_token == "user-token"
    request = transport.request_raw.call_args.args[0]
    assert request.url == TOKEN_ENDPOINT
    assert "Authorization" not in request.headers
    form = _sent_form(transport)
    assert form["client_id"] == ["acr_123"]
    assert form["client_assertion"] == ["platform-token"]
    assert form["client_assertion_type"] == [JWT_BEARER]
    assert form["code_verifier"] == [redirect.code_verifier]
