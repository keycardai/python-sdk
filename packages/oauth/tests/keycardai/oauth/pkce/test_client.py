"""Tests for keycardai.oauth.pkce.PKCEClient.

Mocks httpx and the local callback server to drive the full authentication
flow without opening a browser.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from keycardai.oauth.pkce import OAuthCallbackServer, PKCEClient
from keycardai.oauth.pkce.client import _extract_resource_metadata_url

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
    async with PKCEClient(client_id="cid") as pkce:
        with pytest.raises(ValueError, match="No resource_metadata"):
            await pkce.authenticate(
                resource_url="https://api.example.com",
                www_authenticate_header='Bearer error="invalid_token"',
            )


@pytest.mark.asyncio
async def test_authenticate_raises_when_metadata_has_no_auth_servers():
    async with PKCEClient(client_id="cid") as pkce:
        pkce._http = MagicMock(
            get=AsyncMock(return_value=_mock_json_response({"authorization_servers": []})),
            aclose=AsyncMock(),
        )
        with pytest.raises(ValueError, match="No authorization_servers"):
            await pkce.authenticate(
                resource_url="https://api.example.com",
                www_authenticate_header=WWW_AUTHENTICATE,
            )


@pytest.mark.asyncio
async def test_authenticate_raises_when_auth_server_missing_endpoints():
    async with PKCEClient(client_id="cid") as pkce:
        pkce._http = _http_mock(
            [
                {"authorization_servers": ["https://auth.example.com"]},
                {"issuer": "https://auth.example.com"},  # no endpoints
            ]
        )
        with pytest.raises(ValueError, match="Missing authorization_endpoint"):
            await pkce.authenticate(
                resource_url="https://api.example.com",
                www_authenticate_header=WWW_AUTHENTICATE,
            )


@pytest.mark.asyncio
async def test_authenticate_completes_full_flow(monkeypatch):
    """Drives the happy path with mocked metadata, callback, and token endpoint."""
    callback_mock = MagicMock(spec=OAuthCallbackServer)
    callback_mock.start = AsyncMock()
    callback_mock.wait_for_code = AsyncMock(return_value="auth-code-123")
    callback_mock.stop = MagicMock()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.client.OAuthCallbackServer",
        lambda port: callback_mock,
    )
    monkeypatch.setattr("keycardai.oauth.pkce.client.webbrowser.open", lambda url: None)

    token_response_body = {
        "access_token": "downstream-token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    http_mock = MagicMock()
    http_mock.get = AsyncMock(
        side_effect=[
            _mock_json_response(
                {"authorization_servers": ["https://auth.example.com"]}
            ),
            _mock_json_response(
                {
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                }
            ),
        ]
    )
    http_mock.post = AsyncMock(return_value=_mock_json_response(token_response_body))
    http_mock.aclose = AsyncMock()

    async with PKCEClient(
        client_id="my-app", client_secret="secret"
    ) as pkce:
        pkce._http = http_mock
        result = await pkce.authenticate(
            resource_url="https://api.example.com",
            www_authenticate_header=WWW_AUTHENTICATE,
        )

    assert result == token_response_body
    # Browser was opened, callback served, token exchanged.
    callback_mock.start.assert_called_once()
    callback_mock.wait_for_code.assert_called_once()
    callback_mock.stop.assert_called_once()
    http_mock.post.assert_called_once()
    posted = http_mock.post.call_args
    assert posted.args[0] == "https://auth.example.com/token"
    assert posted.kwargs["data"]["grant_type"] == "authorization_code"
    assert posted.kwargs["data"]["code"] == "auth-code-123"
    assert posted.kwargs["data"]["resource"] == "https://api.example.com"
    assert posted.kwargs["data"]["client_id"] == "my-app"
    # Confidential client uses HTTP Basic auth on the token endpoint.
    assert posted.kwargs["auth"] == ("my-app", "secret")


@pytest.mark.asyncio
async def test_authenticate_omits_basic_auth_for_public_client(monkeypatch):
    callback_mock = MagicMock(spec=OAuthCallbackServer)
    callback_mock.start = AsyncMock()
    callback_mock.wait_for_code = AsyncMock(return_value="code")
    callback_mock.stop = MagicMock()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.client.OAuthCallbackServer",
        lambda port: callback_mock,
    )
    monkeypatch.setattr("keycardai.oauth.pkce.client.webbrowser.open", lambda url: None)

    http_mock = MagicMock()
    http_mock.get = AsyncMock(
        side_effect=[
            _mock_json_response(
                {"authorization_servers": ["https://auth.example.com"]}
            ),
            _mock_json_response(
                {
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                }
            ),
        ]
    )
    http_mock.post = AsyncMock(
        return_value=_mock_json_response({"access_token": "tok"})
    )
    http_mock.aclose = AsyncMock()

    async with PKCEClient(client_id="public-app") as pkce:
        pkce._http = http_mock
        await pkce.authenticate(
            resource_url="https://api.example.com",
            www_authenticate_header=WWW_AUTHENTICATE,
        )

    assert http_mock.post.call_args.kwargs["auth"] is None


def _mock_json_response(body: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = body
    response.raise_for_status.return_value = None
    return response


def _http_mock(json_bodies: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.get = AsyncMock(
        side_effect=[_mock_json_response(b) for b in json_bodies]
    )
    mock.aclose = AsyncMock()
    return mock
