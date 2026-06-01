"""Unit tests for exchange_tokens_for_resources, focused on request_scopes."""

from unittest.mock import AsyncMock

import pytest

from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.token_exchange import exchange_tokens_for_resources
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
