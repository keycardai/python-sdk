"""Tests for Client.impersonate / AsyncClient.impersonate.

Covers the unit-test table in specs/delegated-access/impersonation.md.
"""

import base64
import json
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import pytest

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.exceptions import OAuthProtocolError
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.http.auth import BasicAuth
from keycardai.oauth.types.oauth import TokenType

CC_BODY = (
    b'{"access_token": "actor-access-token", "token_type": "Bearer", '
    b'"expires_in": 3600}'
)
EXCHANGE_BODY = (
    b'{"access_token": "issued-token", "token_type": "Bearer", "expires_in": 3600, '
    b'"scope": "read:mail"}'
)


def _ok(body: bytes) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={"Content-Type": "application/json"},
        body=body,
    )


def _err(status: int, code: str) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps({"error": code}).encode(),
    )


def _build_sync_client(transport: Mock) -> Client:
    return Client(
        base_url="https://zone.keycard.cloud",
        auth=BasicAuth("app", "secret"),
        transport=transport,
        config=ClientConfig(
            enable_metadata_discovery=False,
            auto_register_client=False,
        ),
    )


def _build_async_client(transport: AsyncMock) -> AsyncClient:
    return AsyncClient(
        base_url="https://zone.keycard.cloud",
        auth=BasicAuth("app", "secret"),
        transport=transport,
        config=ClientConfig(
            enable_metadata_discovery=False,
            auto_register_client=False,
        ),
    )


def _decode_payload(jwt: str) -> dict:
    parts = jwt.split(".")
    assert len(parts) == 3 and parts[2] == ""
    pad = "=" * ((4 - len(parts[1]) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


class TestImpersonateSync:
    """Spec table: rows 1–5, sync surface."""

    def test_full_call_sends_substitute_user_token_and_actor(self):
        transport = Mock()
        transport.request_raw.side_effect = [_ok(CC_BODY), _ok(EXCHANGE_BODY)]

        with _build_sync_client(transport) as client:
            response = client.impersonate(
                user_identifier="alice@example.com",
                resource="https://graph.microsoft.com",
                scopes=["read:mail"],
            )

        assert response.access_token == "issued-token"

        # Two HTTP calls: client_credentials then token exchange
        assert transport.request_raw.call_count == 2

        cc_call, exchange_call = transport.request_raw.call_args_list
        cc_form = parse_qs(cc_call.args[0].body.decode())
        assert cc_form["grant_type"] == ["client_credentials"]

        exchange_form = parse_qs(exchange_call.args[0].body.decode())
        assert exchange_form["subject_token_type"] == [TokenType.SUBSTITUTE_USER.value]
        assert exchange_form["actor_token"] == ["actor-access-token"]
        assert exchange_form["actor_token_type"] == [TokenType.ACCESS_TOKEN.value]
        assert exchange_form["resource"] == ["https://graph.microsoft.com"]
        assert exchange_form["scope"] == ["read:mail"]

        payload = _decode_payload(exchange_form["subject_token"][0])
        assert payload == {"sub": "alice@example.com"}

    def test_unknown_user_identifier_surfaces_invalid_grant(self):
        transport = Mock()
        transport.request_raw.side_effect = [_ok(CC_BODY), _err(400, "invalid_grant")]

        with _build_sync_client(transport) as client:
            with pytest.raises(OAuthProtocolError) as exc:
                client.impersonate(user_identifier="ghost@example.com")
        assert exc.value.error == "invalid_grant"

    def test_unauthorized_client_surfaces_oauth_error(self):
        transport = Mock()
        transport.request_raw.side_effect = [
            _ok(CC_BODY),
            _err(400, "unauthorized_client"),
        ]

        with _build_sync_client(transport) as client:
            with pytest.raises(OAuthProtocolError) as exc:
                client.impersonate(user_identifier="alice@example.com")
        assert exc.value.error == "unauthorized_client"

    def test_resource_omitted_sends_no_resource_param(self):
        transport = Mock()
        transport.request_raw.side_effect = [_ok(CC_BODY), _ok(EXCHANGE_BODY)]

        with _build_sync_client(transport) as client:
            client.impersonate(user_identifier="alice@example.com")

        exchange_form = parse_qs(transport.request_raw.call_args_list[1].args[0].body.decode())
        assert "resource" not in exchange_form

    def test_scopes_omitted_sends_no_scope_param(self):
        transport = Mock()
        transport.request_raw.side_effect = [_ok(CC_BODY), _ok(EXCHANGE_BODY)]

        with _build_sync_client(transport) as client:
            client.impersonate(
                user_identifier="alice@example.com",
                resource="https://api.example.com",
            )

        exchange_form = parse_qs(transport.request_raw.call_args_list[1].args[0].body.decode())
        assert "scope" not in exchange_form

    def test_empty_user_identifier_raises(self):
        transport = Mock()
        with _build_sync_client(transport) as client:
            with pytest.raises(ValueError, match="user_identifier"):
                client.impersonate(user_identifier="")
        transport.request_raw.assert_not_called()


class TestImpersonateAsync:
    """Spec table mirrored for the async surface."""

    @pytest.mark.asyncio
    async def test_full_call(self):
        transport = AsyncMock()
        transport.request_raw.side_effect = [_ok(CC_BODY), _ok(EXCHANGE_BODY)]

        async with _build_async_client(transport) as client:
            response = await client.impersonate(
                user_identifier="alice@example.com",
                resource="https://graph.microsoft.com",
                scopes=["read:mail", "read:calendar"],
            )

        assert response.access_token == "issued-token"
        assert transport.request_raw.await_count == 2

        exchange_form = parse_qs(transport.request_raw.await_args_list[1].args[0].body.decode())
        assert exchange_form["subject_token_type"] == [TokenType.SUBSTITUTE_USER.value]
        assert exchange_form["actor_token"] == ["actor-access-token"]
        assert exchange_form["scope"] == ["read:mail read:calendar"]

    @pytest.mark.asyncio
    async def test_unknown_user_identifier(self):
        transport = AsyncMock()
        transport.request_raw.side_effect = [_ok(CC_BODY), _err(400, "invalid_grant")]

        async with _build_async_client(transport) as client:
            with pytest.raises(OAuthProtocolError) as exc:
                await client.impersonate(user_identifier="ghost@example.com")
        assert exc.value.error == "invalid_grant"
