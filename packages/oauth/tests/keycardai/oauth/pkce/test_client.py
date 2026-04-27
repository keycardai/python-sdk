"""Tests for keycardai.oauth.pkce.authenticate.

Mocks the resource metadata fetch and the internal AsyncClient to drive the
full authentication flow without opening a browser or making network calls.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from keycardai.oauth.http.auth import BasicAuth, NoneAuth
from keycardai.oauth.pkce import OAuthCallbackServer, authenticate
from keycardai.oauth.pkce.client import _extract_resource_metadata_url
from keycardai.oauth.types.models import TokenResponse

WWW_AUTHENTICATE = (
    'Bearer error="invalid_token", '
    'error_description="No bearer token", '
    'resource_metadata="https://api.example.com/.well-known/oauth-protected-resource"'
)


def test_extract_resource_metadata_url_finds_url():
    url = _extract_resource_metadata_url(WWW_AUTHENTICATE)
    assert url == "https://api.example.com/.well-known/oauth-protected-resource"


def test_extract_resource_metadata_url_returns_none_when_missing():
    assert _extract_resource_metadata_url('Bearer error="invalid_token"') is None


@pytest.mark.asyncio
async def test_authenticate_raises_when_header_has_no_metadata_url():
    with pytest.raises(ValueError, match="No resource_metadata"):
        await authenticate(
            client_id="cid",
            resource_url="https://api.example.com",
            www_authenticate_header='Bearer error="invalid_token"',
        )


@pytest.mark.asyncio
async def test_authenticate_raises_when_metadata_has_no_auth_servers():
    http_mock = _http_client_mock([{"authorization_servers": []}])
    with pytest.raises(ValueError, match="No authorization_servers"):
        await authenticate(
            client_id="cid",
            resource_url="https://api.example.com",
            www_authenticate_header=WWW_AUTHENTICATE,
            http_client=http_mock,
        )


@pytest.mark.asyncio
async def test_authenticate_raises_when_auth_server_missing_endpoints(monkeypatch):
    http_mock = _http_client_mock(
        [{"authorization_servers": ["https://auth.example.com"]}]
    )
    fake_async_client = _async_client_factory(
        endpoints=MagicMock(authorize=None, token=None)
    )
    monkeypatch.setattr(
        "keycardai.oauth.pkce.client.AsyncClient", fake_async_client
    )

    with pytest.raises(
        ValueError, match="missing authorization_endpoint or token_endpoint"
    ):
        await authenticate(
            client_id="cid",
            resource_url="https://api.example.com",
            www_authenticate_header=WWW_AUTHENTICATE,
            http_client=http_mock,
        )


@pytest.mark.asyncio
async def test_authenticate_completes_full_flow(monkeypatch):
    """Drives the happy path through resource metadata + AsyncClient + callback."""
    callback_mock = _patch_callback_and_browser(monkeypatch, code="auth-code-123")

    token_response = TokenResponse(
        access_token="downstream-token",
        token_type="Bearer",
        expires_in=3600,
    )
    captured = {}
    fake_async_client = _async_client_factory(
        endpoints=MagicMock(
            authorize="https://auth.example.com/authorize",
            token="https://auth.example.com/token",
        ),
        exchange_response=token_response,
        capture=captured,
    )
    monkeypatch.setattr(
        "keycardai.oauth.pkce.client.AsyncClient", fake_async_client
    )

    http_mock = _http_client_mock(
        [{"authorization_servers": ["https://auth.example.com"]}]
    )

    result = await authenticate(
        client_id="my-app",
        client_secret="secret",
        resource_url="https://api.example.com",
        www_authenticate_header=WWW_AUTHENTICATE,
        http_client=http_mock,
    )

    assert result is token_response
    callback_mock.start.assert_awaited_once()
    callback_mock.wait_for_code.assert_awaited_once()
    callback_mock.stop.assert_called_once()
    # Resource metadata was fetched from the URL in the WWW-Authenticate header.
    http_mock.get.assert_awaited_once_with(
        "https://api.example.com/.well-known/oauth-protected-resource"
    )
    # AsyncClient was constructed against the discovered authorization server
    # with HTTP Basic auth (confidential client).
    assert captured["base_url"] == "https://auth.example.com"
    assert isinstance(captured["auth"], BasicAuth)
    assert captured["auth"].client_id == "my-app"
    assert captured["auth"].client_secret == "secret"
    # Token exchange was called with the right parameters, including the
    # RFC 8707 resource indicator.
    assert captured["exchange_kwargs"]["code"] == "auth-code-123"
    assert captured["exchange_kwargs"]["client_id"] == "my-app"
    assert captured["exchange_kwargs"]["resource"] == "https://api.example.com"


@pytest.mark.asyncio
async def test_authenticate_uses_none_auth_for_public_client(monkeypatch):
    _patch_callback_and_browser(monkeypatch, code="code")
    captured = {}
    fake_async_client = _async_client_factory(
        endpoints=MagicMock(
            authorize="https://auth.example.com/authorize",
            token="https://auth.example.com/token",
        ),
        exchange_response=TokenResponse(access_token="tok", token_type="Bearer"),
        capture=captured,
    )
    monkeypatch.setattr(
        "keycardai.oauth.pkce.client.AsyncClient", fake_async_client
    )
    http_mock = _http_client_mock(
        [{"authorization_servers": ["https://auth.example.com"]}]
    )

    await authenticate(
        client_id="public-app",
        resource_url="https://api.example.com",
        www_authenticate_header=WWW_AUTHENTICATE,
        http_client=http_mock,
    )

    assert isinstance(captured["auth"], NoneAuth)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _mock_json_response(body: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = body
    response.raise_for_status.return_value = None
    return response


def _http_client_mock(json_bodies: list[dict]) -> MagicMock:
    """Mock httpx.AsyncClient passed in for resource metadata fetch."""
    mock = MagicMock()
    mock.get = AsyncMock(
        side_effect=[_mock_json_response(b) for b in json_bodies]
    )
    return mock


def _patch_callback_and_browser(monkeypatch, *, code: str) -> MagicMock:
    callback_mock = MagicMock(spec=OAuthCallbackServer)
    callback_mock.start = AsyncMock()
    callback_mock.wait_for_code = AsyncMock(return_value=code)
    callback_mock.stop = MagicMock()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.client.OAuthCallbackServer",
        lambda port: callback_mock,
    )
    monkeypatch.setattr(
        "keycardai.oauth.pkce.client.webbrowser.open", lambda url: None
    )
    return callback_mock


def _async_client_factory(
    *,
    endpoints: MagicMock,
    exchange_response: TokenResponse | None = None,
    capture: dict | None = None,
):
    """Build a stand-in for keycardai.oauth.AsyncClient.

    Captures the constructor kwargs and the exchange_authorization_code call
    so tests can assert against them.
    """

    def factory(base_url, *, auth, config):
        if capture is not None:
            capture["base_url"] = base_url
            capture["auth"] = auth
            capture["config"] = config
        instance = MagicMock()
        instance.get_endpoints = AsyncMock(return_value=endpoints)

        async def _exchange(**kwargs):
            if capture is not None:
                capture["exchange_kwargs"] = kwargs
            return exchange_response

        instance.exchange_authorization_code = _exchange
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        return instance

    return factory
